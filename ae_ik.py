# SPDX-License-Identifier: GPL-2.0-or-later
# -*- coding: utf-8 -*-
"""Endfield left-arm FK/IK extension with pose-preserving mode switches.

IK is opt-in and must not disturb the initial FK pose. Generated controls and
solver bones are separate from the imported deform skeleton. Joint spans use
the audited source joint positions rather than imported bone.length values.

Preflight, snap tolerances and manifest ownership protect existing animation.
A failed pose match must report an error instead of silently shifting the limb.
Legacy internal component IDs are retained for existing Blender scenes.
"""

import json
import math

import bpy
from bpy.types import Operator
from mathutils import Matrix, Vector
from . import ae_biped, engine

KEY = "ik_arm_l"
VERSION = 2
COMPONENT = "AE_BIPED_IK_L_E1"
CTRL_COLLECTION = "SaberRig AE IK Left Controls"
FK_COLLECTION = "SaberRig AE Left Arm FK"
FK_LEFT = ("SR_CTRL_AE_UpperArm.L", "SR_CTRL_AE_Forearm.L", "SR_CTRL_AE_Hand.L")
MCH_COLLECTION = "SaberRig AE IK Left Mechanisms"
IK_HAND = "SR_CTRL_AE_IK_Hand.L"
IK_POLE = "SR_CTRL_AE_IK_ElbowPole.L"
SOLVER_UPPER = "SR_MCH_AE_IK_UpperArm.L"
SOLVER_LOWER = "SR_MCH_AE_IK_Forearm.L"
DRIVE_UPPER = "SR_MCH_AE_IK_DriveUpperArm.L"
DRIVE_LOWER = "SR_MCH_AE_IK_DriveForearm.L"
DRIVE_HAND = "SR_MCH_AE_IK_DriveHand.L"
BONES = (IK_HAND, IK_POLE, SOLVER_UPPER, SOLVER_LOWER, DRIVE_UPPER, DRIVE_LOWER, DRIVE_HAND)
CONTROLS = (IK_HAND, IK_POLE)
MECHANISMS = (SOLVER_UPPER, SOLVER_LOWER, DRIVE_UPPER, DRIVE_LOWER, DRIVE_HAND)
OUTPUTS = (
    ("UpperArm.L", DRIVE_UPPER),
    ("Forearm.L", DRIVE_LOWER),
    ("Hand.L", DRIVE_HAND),
)
JOINTS = ("Bip001_L_UpperArm", "Bip001_L_Forearm", "Bip001_L_Hand")
SOLVER_CONSTRAINT = "SR_AE_IK_L_SOLVER"
HAND_ROTATION = "SR_AE_IK_L_HAND_ROTATION"
COPY_PREFIX = "SR_AE_IK_L_OUTPUT_"
# Snap checks separate position (relative to limb reach), rotation and scale.
# Validated in armature POSE space, regardless of object-space world scale.
SNAP_POSITION_FRACTION = 0.002
SNAP_ROTATION_RADIANS = math.radians(0.75)
SNAP_SCALE_FRACTION = 0.002
REST_SIN_BEND_MIN = 0.01   # reject an anatomical rest bend below ~0.57 degrees
POSE_BEND_FRACTION = 0.02  # ignore near-straight measured elbow positions


def _manifest(arm):
    return json.loads(str(arm[ae_biped.MANIFEST_KEY]))


def _save_manifest(arm, data):
    arm[ae_biped.MANIFEST_KEY] = json.dumps(data, sort_keys=True)


def _as_matrix(flat):
    return Matrix(tuple(tuple(flat[4*i+j] for j in range(4)) for i in range(4)))


def _set_pose(context, pb, desired):
    """Assign a pose matrix through Blender's rest-aware conversion."""
    if pb.parent:
        local = pb.bone.convert_local_to_pose(
            desired, pb.bone.matrix_local,
            parent_matrix=pb.parent.matrix,
            parent_matrix_local=pb.parent.bone.matrix_local,
            invert=True)
    else:
        local = pb.bone.convert_local_to_pose(
            desired, pb.bone.matrix_local, invert=True)
    if not ae_biped._pose_matrix_is_finite(local):
        raise RuntimeError("Non-finite IK pose calibration for " + pb.name)
    pb.matrix_basis = local
    context.view_layer.update()


def _move_head(context, pb, position):
    pose = pb.matrix.copy()
    pose.translation = position
    _set_pose(context, pb, pose)


def _rest_hinge(arm):
    """Rest hinge axis from true joint-head centers; dimensionless threshold."""
    a, b, c = (Vector(arm.data.bones[n].head_local) for n in JOINTS)
    proximal, distal = b - a, c - b
    length_product = proximal.length * distal.length
    if length_product <= 1e-10:
        raise RuntimeError("Zero-length AE left arm joint segment")
    cross = proximal.cross(distal)
    if cross.length / length_product < REST_SIN_BEND_MIN:
        raise RuntimeError("AE rest elbow hinge is indeterminate; inspect geometry")
    return cross.normalized()


def _pole_direction(arm):
    """Transport the anatomical hinge with the *evaluated native* upper arm.

    The FK upper-arm control may be stale while IK is active (its output
    influence is zero). The native source bone receives the selected FK/IK
    output and therefore supplies the actual arm rotation in both modes.
    Work in armature object space, with rotation only; the rest hinge is based
    on the native joint-head centers and is meaningful near pose singularity.
    """
    shoulder = arm.pose.bones[JOINTS[0]].head.copy()
    wrist = arm.pose.bones[JOINTS[2]].head.copy()
    axis = wrist - shoulder
    if axis.length < 1e-8:
        raise RuntimeError("AE shoulder-to-wrist axis collapsed")
    axis.normalize()
    upper = arm.pose.bones[JOINTS[0]]
    # Rotation-only rest-to-evaluated transport. Never use a stale FK control
    # after switching to IK; native source .matrix includes its constraints.
    relative = (upper.matrix.to_quaternion() @
                upper.bone.matrix_local.to_quaternion().inverted())
    hinge = relative @ _rest_hinge(arm)
    hinge -= axis * hinge.dot(axis)
    if hinge.length <= 1e-4:
        # Geometric rest bend, carried with evaluated native arm (not world -Z).
        a, b, c = (Vector(arm.data.bones[n].head_local) for n in JOINTS)
        rest_axis = (c-a).normalized()
        rest_bend = (b-a) - rest_axis * (b-a).dot(rest_axis)
        transported = relative @ rest_bend.normalized()
        transported -= axis * transported.dot(axis)
        if transported.length <= 1e-4:
            raise RuntimeError("IK pole plane degenerate after evaluated upper-arm rotation")
        return transported.normalized()
    bend = axis.cross(hinge)
    if bend.length <= 1e-4:
        raise RuntimeError("IK pole bend direction degenerate")
    return bend.normalized()


def _pole_position(arm):
    shoulder = arm.pose.bones[JOINTS[0]].head.copy()
    elbow = arm.pose.bones[JOINTS[1]].head.copy()
    wrist = arm.pose.bones[JOINTS[2]].head.copy()
    length = (elbow - shoulder).length + (wrist - elbow).length
    axis = (wrist - shoulder)
    if length <= 1e-8 or axis.length <= 1e-8:
        raise RuntimeError("Degenerate left arm IK pole geometry")
    axis.normalize()
    mid = shoulder + axis * (elbow - shoulder).length
    offset = elbow - shoulder - axis * ((elbow - shoulder).dot(axis))
    # A visibly bent FK elbow already determines the pole plane. Do not
    # require a usable REST hinge in this branch: stylized models may have
    # nearly collinear rest heads yet arrive with a meaningful posed elbow.
    if offset.length / length > POSE_BEND_FRACTION:
        direction = offset.normalized()
    else:
        # Only near the pose singularity use transported rest anatomy.
        direction = _pole_direction(arm)
    return mid + direction * (length * 0.75), direction


def _snap_pose_error(arm, measured, expected):
    """Separate unitless translation, angular and scale errors."""
    reach = sum((Vector(arm.data.bones[b].head_local) -
                 Vector(arm.data.bones[a].head_local)).length
                for a, b in ((JOINTS[0], JOINTS[1]), (JOINTS[1], JOINTS[2])))
    if reach <= 1e-8:
        raise RuntimeError("Invalid reference arm reach")
    displacement = (measured.translation - expected.translation).length / reach
    rotation = measured.to_quaternion().rotation_difference(
        expected.to_quaternion()).angle
    measured_scale, expected_scale = measured.to_scale(), expected.to_scale()
    scale = max(abs(x - y) / max(abs(y), 1e-6)
                for x, y in zip(measured_scale, expected_scale))
    return displacement, rotation, scale


def _verify_snap(arm, expected_by_bone, message):
    worst = (0.0, 0.0, 0.0)
    for name, expected_matrix in expected_by_bone.items():
        err = _snap_pose_error(arm, arm.pose.bones[name].matrix, expected_matrix)
        worst = tuple(max(a, b) for a, b in zip(worst, err))
    if (worst[0] > SNAP_POSITION_FRACTION or
            worst[1] > SNAP_ROTATION_RADIANS or
            worst[2] > SNAP_SCALE_FRACTION):
        raise RuntimeError(
            f"{message}: relative position={worst[0]:.6g}, "
            f"rotation={math.degrees(worst[1]):.5g} deg, "
            f"relative scale={worst[2]:.6g}")
    return worst


def _set_visibility(arm, mode):
    fk = engine._find_bone_collection(arm.data, FK_COLLECTION)
    ik = engine._find_bone_collection(arm.data, CTRL_COLLECTION)
    if fk is None or ik is None:
        raise RuntimeError("Missing FK/IK visibility collections")
    fk.is_visible = (mode == "FK")
    ik.is_visible = (mode == "IK")


def _original_fk_memberships(arm):
    return {name: sorted(c.name for c in arm.data.bones[name].collections)
            for name in FK_LEFT}


def _move_fk_to_collection(arm):
    """One membership per FK limb control, so hidden actually means hidden."""
    coll = engine._find_bone_collection(arm.data, FK_COLLECTION)
    if coll is None:
        raise RuntimeError("Missing left-arm FK collection")
    for name in FK_LEFT:
        bone = arm.data.bones[name]
        for old in list(bone.collections):
            old.unassign(bone)
        coll.assign(bone)


def _restore_fk_memberships(arm, membership):
    """Exact restore of the three existing FK bones, preserving other controls."""
    for name in FK_LEFT:
        bone = arm.data.bones[name]
        desired = membership.get(name)
        if not isinstance(desired, list):
            raise RuntimeError("Missing left FK original memberships: " + name)
        targets = [engine._find_bone_collection(arm.data, n) for n in desired]
        if any(target is None for target in targets):
            raise RuntimeError("An original FK collection was deleted: " + name)
        for old in list(bone.collections):
            old.unassign(bone)
        for target in targets:
            target.assign(bone)


def _calibrate_pole_angle(context, arm, ik):
    """Sweep the REAL Blender IK evaluation while FK remains the sole output.

    A temporary reachable end target avoids the fully-extended singularity.
    No original bone or FK control is modified by this calibration.
    """
    target = arm.pose.bones[IK_HAND]
    saved = target.matrix_basis.copy()
    shoulder = arm.pose.bones[JOINTS[0]].head.copy()
    wrist = arm.pose.bones[JOINTS[2]].head.copy()
    axis = (wrist - shoulder).normalized()
    l1 = (Vector(arm.data.bones[JOINTS[1]].head_local) -
          Vector(arm.data.bones[JOINTS[0]].head_local)).length
    l2 = (Vector(arm.data.bones[JOINTS[2]].head_local) -
          Vector(arm.data.bones[JOINTS[1]].head_local)).length
    desired = arm.pose.bones[IK_POLE].head - shoulder
    desired -= axis * desired.dot(axis)
    desired.normalize()
    try:
        # Well within reach and above |l1-l2|: produces a measurable bend.
        _move_head(context, target, shoulder + axis * (0.90 * (l1 + l2)))
        best = (-float("inf"), 0.0)
        for k in range(32):
            angle = -math.pi + k * (2.0 * math.pi / 32)
            ik.pole_angle = angle
            context.view_layer.update()
            top = arm.pose.bones[SOLVER_UPPER].head
            middle = arm.pose.bones[SOLVER_LOWER].head
            distal = arm.pose.bones[SOLVER_LOWER].tail
            direction = distal - top
            if direction.length < 1e-5:
                continue
            direction.normalize()
            projected = middle - top
            projected -= direction * projected.dot(direction)
            if projected.length < 1e-4:
                continue
            score = projected.normalized().dot(desired)
            if score > best[0]:
                best = (score, angle)
        if best[0] < 0.5:
            raise RuntimeError(f"AE IK pole calibration ambiguous: score={best[0]:.3f}")
        # Refinement around nearest coarse result.
        center = best[1]
        for step in (math.pi / 64, math.pi / 256):
            candidates = (center-step, center-step/2, center,
                          center+step/2, center+step)
            for angle in candidates:
                ik.pole_angle = angle
                context.view_layer.update()
                top = arm.pose.bones[SOLVER_UPPER].head
                middle = arm.pose.bones[SOLVER_LOWER].head
                distal = arm.pose.bones[SOLVER_LOWER].tail
                direction = distal-top
                direction.normalize()
                projected = middle-top-direction*((middle-top).dot(direction))
                if projected.length > 1e-5:
                    score = projected.normalized().dot(desired)
                    if score > best[0]:
                        best = (score, angle)
            center = best[1]
        ik.pole_angle = best[1]
        return best
    finally:
        target.matrix_basis = saved
        context.view_layer.update()


def _audit(arm):
    if not arm or arm.type != "ARMATURE" or not arm.get(ae_biped.MANIFEST_KEY):
        return {"status": "NOT_INSTALLED"}
    data = _manifest(arm).get(KEY)
    if data is None:
        return {"status": "NOT_INSTALLED"}
    faults = []
    if (not isinstance(data, dict) or data.get("version") != VERSION
            or data.get("bones") != list(BONES)
            or data.get("collections") != [CTRL_COLLECTION, MCH_COLLECTION, FK_COLLECTION]
            or data.get("mode") not in ("FK", "IK")
            or not isinstance(data.get("fk_original_memberships"), dict)
            or set(data["fk_original_memberships"]) != set(FK_LEFT)
            or any(not isinstance(v, list) or any(not isinstance(c, str) for c in v)
                   for v in data["fk_original_memberships"].values())):
        return {"status": "INVALID_MANIFEST"}
    expected_parents = {
        IK_HAND: ae_biped._ctrl("Master"),
        IK_POLE: ae_biped._ctrl("Master"),
        SOLVER_UPPER: ae_biped._ctrl("Clavicle.L"),
        SOLVER_LOWER: SOLVER_UPPER,
        DRIVE_UPPER: SOLVER_UPPER,
        DRIVE_LOWER: SOLVER_LOWER,
        DRIVE_HAND: SOLVER_LOWER,
    }
    for name in BONES:
        bone = arm.data.bones.get(name)
        if bone is None or bone.get("saberrig_component") != COMPONENT:
            faults.append("bone:" + name)
            continue
        if bone.use_deform or not bone.parent or bone.parent.name != expected_parents[name]:
            faults.append("parent_or_deform:" + name)
        expected_collection = CTRL_COLLECTION if name in CONTROLS else MCH_COLLECTION
        if [c.name for c in bone.collections] != [expected_collection]:
            faults.append("membership:" + name)
    for solver_name, start, end in ((SOLVER_UPPER, JOINTS[0], JOINTS[1]),
                                    (SOLVER_LOWER, JOINTS[1], JOINTS[2])):
        solver = arm.data.bones.get(solver_name)
        if solver is None:
            continue
        for name, a, b in (("head", solver.head_local, arm.data.bones[start].head_local),
                           ("tail", solver.tail_local, arm.data.bones[end].head_local)):
            if (a - b).length > 1e-4:
                faults.append("solver_rest:" + solver_name + ":" + name)
    lower_bone = arm.data.bones.get(SOLVER_LOWER)
    if lower_bone and not lower_bone.use_connect:
        faults.append("solver:disconnected")
    hand_bone = arm.data.bones.get(DRIVE_HAND)
    if hand_bone and not hand_bone.use_connect:
        faults.append("drive_hand:disconnected")
    for cname in (CTRL_COLLECTION, MCH_COLLECTION, FK_COLLECTION):
        if engine._find_bone_collection(arm.data, cname) is None:
            faults.append("collection:"+cname)
    for name in FK_LEFT:
        b = arm.data.bones.get(name)
        if b is None or sorted(c.name for c in b.collections) != [FK_COLLECTION]:
            faults.append("fk_membership:"+name)
        for old_name in data["fk_original_memberships"][name]:
            if engine._find_bone_collection(arm.data, old_name) is None:
                faults.append("fk_original_collection_missing:" + old_name)
    ikpb = arm.pose.bones.get(SOLVER_LOWER)
    ik = ikpb.constraints.get(SOLVER_CONSTRAINT) if ikpb else None
    if (ik is None or ik.type != "IK" or ik.target != arm or ik.subtarget != IK_HAND
            or ik.pole_target != arm or ik.pole_subtarget != IK_POLE
            or ik.chain_count != 2 or ik.use_stretch):
        faults.append("solver")
    handpb = arm.pose.bones.get(DRIVE_HAND)
    handrot = handpb.constraints.get(HAND_ROTATION) if handpb else None
    if (handrot is None or handrot.type != "COPY_ROTATION" or handrot.target != arm
            or handrot.subtarget != IK_HAND or handrot.owner_space != "POSE"
            or handrot.target_space != "POSE"):
        faults.append("hand_rotation")
    expected_fk = 1.0 if data["mode"] == "FK" else 0.0
    for label, target in OUTPUTS:
        pb = arm.pose.bones.get(ae_biped._mch(label))
        fkc = pb.constraints.get(ae_biped._mechanism_constraint(label)) if pb else None
        ikc = pb.constraints.get(COPY_PREFIX + label) if pb else None
        if (fkc is None or fkc.type != "COPY_TRANSFORMS" or
                abs(fkc.influence - expected_fk) > 1e-5 or
                ikc is None or ikc.type != "COPY_TRANSFORMS" or
                ikc.target != arm or ikc.subtarget != target or
                ikc.owner_space != "POSE" or ikc.target_space != "POSE" or
                abs(ikc.influence - (1.0-expected_fk)) > 1e-5):
            faults.append("output:" + label)
    return {"status": "COMPLETE" if not faults else "INCOMPLETE",
            "faults": faults, "mode": data["mode"]}


def _preflight(arm):
    ae_biped._require_local(arm)
    if ae_biped.audit(arm)["status"] != "COMPLETE":
        raise RuntimeError("Build and audit the intact AE FK foundation first")
    if _audit(arm)["status"] != "NOT_INSTALLED":
        raise RuntimeError("AE left-arm IK already installed or needs recovery")
    if arm.mode not in {"POSE", "OBJECT"}:
        raise RuntimeError("Leave Edit Mode")
    ad = arm.animation_data
    if ad and (ad.action is not None or len(ad.nla_tracks) or len(ad.drivers)):
        raise RuntimeError("IK pilot requires a non-animated test copy")
    for name in BONES:
        if arm.data.bones.get(name):
            raise RuntimeError("IK generated name already occupied: " + name)
    for cname in (CTRL_COLLECTION, MCH_COLLECTION, FK_COLLECTION):
        if engine._find_bone_collection(arm.data, cname):
            raise RuntimeError("IK collection name occupied: " + cname)
    for joint in JOINTS:
        if arm.pose.bones.get(joint) is None:
            raise RuntimeError("Missing native joint: " + joint)


def _add_ik_output(pb, arm, target, label):
    con = ae_biped._add_copy(pb, arm, target, COPY_PREFIX + label)
    con.influence = 0.0
    return con


def build(context, arm):
    """Add IK to the existing FK arm. No original or FK source changes."""
    _preflight(arm)
    before_rest = ae_biped._rest_snapshot(arm)
    before_source = {name: arm.pose.bones[name].matrix.copy() for name in before_rest}
    # No user rig poses or FK control changes are required to install IK.
    master = ae_biped._ctrl("Master")
    clavicle = ae_biped._ctrl("Clavicle.L")
    original_manifest = str(arm[ae_biped.MANIFEST_KEY])
    fk_before = _original_fk_memberships(arm)
    fk_visibility_before = {c.name: c.is_visible for c in arm.data.collections}
    # Pole edit-rest head is chosen to land at the calibrated IK position when
    # the FK Master is in the imported pose. Alt+G is therefore meaningful.
    pole_current, _direction = _pole_position(arm)
    master_pb = arm.pose.bones[master]
    pole_edit_rest = (master_pb.bone.matrix_local @
                      master_pb.matrix.inverted()) @ pole_current
    active, selected, mode = context.view_layer.objects.active, list(context.selected_objects), arm.mode
    created_constraints = []
    try:
        ae_biped._enter_mode(context, arm, "OBJECT")
        cc = arm.data.collections.new(CTRL_COLLECTION)
        mc = arm.data.collections.new(MCH_COLLECTION)
        fc = arm.data.collections.new(FK_COLLECTION)
        ae_biped._enter_mode(context, arm, "EDIT")
        eb = arm.data.edit_bones
        shoulder, elbow, wrist = (Vector(eb[name].head) for name in JOINTS)
        length = (wrist - shoulder).length + (elbow - shoulder).length
        if length < 0.01 or (wrist-elbow).length < 0.01:
            raise RuntimeError("Invalid arm joint geometry")
        hand = ae_biped._clone_edit(eb, IK_HAND, eb[JOINTS[2]])
        hand.parent = eb[master]
        cc.assign(hand)
        pole = ae_biped._clone_edit(eb, IK_POLE, eb[JOINTS[1]])
        pole.head = pole_edit_rest
        pole.tail = pole.head + Vector((0,0,0.04))
        pole.parent = eb[master]
        cc.assign(pole)
        up = eb.new(SOLVER_UPPER)
        up.head, up.tail = shoulder, elbow
        up.parent = eb[clavicle]
        up.use_connect = False
        up.use_deform = False
        mc.assign(up)
        low = eb.new(SOLVER_LOWER)
        low.head, low.tail = elbow, wrist
        low.parent = up
        low.use_connect = True
        low.use_deform = False
        mc.assign(low)
        driveup = ae_biped._clone_edit(eb, DRIVE_UPPER, eb[JOINTS[0]])
        driveup.parent = up
        mc.assign(driveup)
        drivelow = ae_biped._clone_edit(eb, DRIVE_LOWER, eb[JOINTS[1]])
        drivelow.parent = low
        mc.assign(drivelow)
        drivehand = ae_biped._clone_edit(eb, DRIVE_HAND, eb[JOINTS[2]])
        drivehand.parent = low
        drivehand.use_connect = True
        mc.assign(drivehand)
        bpy.ops.object.mode_set(mode="OBJECT")
        cc.is_visible = True
        mc.is_visible = False
        fc.is_visible = True
        for name in BONES:
            bone = arm.data.bones[name]
            bone["saberrig_generated"] = True
            bone["saberrig_component"] = COMPONENT
            bone.use_deform = False
        # Control target is calibrated to wrist pose even though its rest
        # axes differ substantially from the T-posed imported pose.
        _set_pose(context, arm.pose.bones[IK_HAND], before_source[JOINTS[2]])
        pole_position, pole_dir = _pole_position(arm)
        _move_head(context, arm.pose.bones[IK_POLE], pole_position)
        lowpb = arm.pose.bones[SOLVER_LOWER]
        ik = lowpb.constraints.new("IK")
        ik.name = SOLVER_CONSTRAINT
        created_constraints.append((SOLVER_LOWER, SOLVER_CONSTRAINT))
        ik.target, ik.subtarget = arm, IK_HAND
        ik.pole_target, ik.pole_subtarget = arm, IK_POLE
        ik.chain_count = 2
        ik.use_stretch = False
        ik.use_rotation = False
        for name in (SOLVER_UPPER, SOLVER_LOWER):
            arm.pose.bones[name].ik_stretch = 0.0
        context.view_layer.update()
        pole_score, pole_angle = _calibrate_pole_angle(context, arm, ik)
        # The IK hand output must be attached to the solver wrist even when
        # the animator moves the target OUTSIDE reach. Copy only orientation
        # from target; the hand-drive HEAD follows the connected solver tip.
        hrot = arm.pose.bones[DRIVE_HAND].constraints.new("COPY_ROTATION")
        hrot.name = HAND_ROTATION
        hrot.target = arm
        hrot.subtarget = IK_HAND
        hrot.mix_mode = "REPLACE"
        hrot.owner_space = "POSE"
        hrot.target_space = "POSE"
        hrot.influence = 1.0
        created_constraints.append((DRIVE_HAND, HAND_ROTATION))
        for label, driver in OUTPUTS:
            pb = arm.pose.bones[ae_biped._mch(label)]
            _add_ik_output(pb, arm, driver, label)
            created_constraints.append((pb.name, COPY_PREFIX + label))
        context.view_layer.update()
        # All native bones must remain bit-for-bit rest-consistent and still
        # evaluate identically; IK is built INACTIVE and FK stays operational.
        ae_biped._assert_rest_unchanged(arm, before_rest)
        worst = max(ae_biped._matrix_difference(arm.pose.bones[name].matrix, pose)
                    for name, pose in before_source.items())
        if worst > ae_biped.POSE_TOLERANCE:
            raise RuntimeError(f"IK install changed native pose Δ={worst:.6g}")
        _move_fk_to_collection(arm)
        _set_visibility(arm, "FK")
        record = {
            "version": VERSION, "bones": list(BONES), "mode": "FK",
            "collections": [CTRL_COLLECTION, MCH_COLLECTION, FK_COLLECTION],
            "fk_original_memberships": fk_before,
            "solver": SOLVER_CONSTRAINT, "pole_angle": float(pole_angle),
            "pole_calibration_score": float(pole_score),
            "joint_lengths": [float((elbow-shoulder).length), float((wrist-elbow).length)],
        }
        manifest = json.loads(original_manifest)
        manifest[KEY] = record
        _save_manifest(arm, manifest)
        if ae_biped.audit(arm)["status"] != "COMPLETE" or _audit(arm)["status"] != "COMPLETE":
            raise RuntimeError("IK install integrity audit failed")
        return {"bones": len(BONES), "neutral_delta": worst,
                "pole_score": pole_score, "pole_angle": pole_angle}
    except Exception as exc:
        errors = []
        try:
            for owner, cname in reversed(created_constraints):
                pb = arm.pose.bones.get(owner)
                c = pb.constraints.get(cname) if pb else None
                if c:
                    pb.constraints.remove(c)
            ae_biped._remove_own_bones(context, arm, names=BONES)
            # Restore FK controls before deleting the temporary FK collection.
            _restore_fk_memberships(arm, fk_before)
            for cname in (MCH_COLLECTION, CTRL_COLLECTION, FK_COLLECTION):
                coll = engine._find_bone_collection(arm.data, cname)
                if coll is not None and not len(coll.bones):
                    arm.data.collections.remove(coll)
            arm[ae_biped.MANIFEST_KEY] = original_manifest
            for cname, visible in fk_visibility_before.items():
                col = engine._find_bone_collection(arm.data, cname)
                if col is not None:
                    col.is_visible = visible
        except Exception as recovery:
            errors.append(str(recovery))
        if errors:
            raise RuntimeError("IK build failed, ROLLBACK INCOMPLETE: " +
                               str(exc) + "; " + "; ".join(errors)) from exc
        raise
    finally:
        ae_biped._restore_context(context, arm, active, selected, mode)


def _set_mode(arm, mode):
    for label, _driver in OUTPUTS:
        pb = arm.pose.bones[ae_biped._mch(label)]
        pb.constraints[ae_biped._mechanism_constraint(label)].influence = 1.0 if mode == "FK" else 0.0
        pb.constraints[COPY_PREFIX + label].influence = 0.0 if mode == "FK" else 1.0
    data = _manifest(arm)
    data[KEY]["mode"] = mode
    _save_manifest(arm, data)
    _set_visibility(arm, mode)


def switch(context, arm, mode):
    """Match destination controls, then change outputs atomically on failure."""
    if mode not in ("FK", "IK"):
        raise RuntimeError("Unsupported FK/IK mode")
    if ae_biped.audit(arm)["status"] != "COMPLETE":
        raise RuntimeError("AE foundation is not complete")
    status = _audit(arm)
    if status["status"] != "COMPLETE":
        raise RuntimeError("IK pilot incomplete: " + str(status))
    if status["mode"] == mode:
        return {"mode": mode, "already": True}
    ad = arm.animation_data
    if ad and (ad.action is not None or len(ad.nla_tracks) or len(ad.drivers)):
        raise RuntimeError("E1 snap is not animation-aware; use an unanimated .blend copy")
    if arm.mode not in {"POSE", "OBJECT"}:
        raise RuntimeError("Leave Edit Mode before switching")
    # Snapshot source + all controls/driver matrices; snap is rolled back if
    # the copied output does not match pose to within numeric tolerance.
    joints_before = {name: arm.pose.bones[name].matrix.copy() for name in JOINTS}
    touched = ([IK_HAND, IK_POLE, DRIVE_UPPER, DRIVE_LOWER]
               if mode == "IK" else [ae_biped._ctrl(x) for x in
                                      ("UpperArm.L", "Forearm.L", "Hand.L")])
    saved = {name: arm.pose.bones[name].matrix_basis.copy() for name in touched}
    original_record = str(arm[ae_biped.MANIFEST_KEY])
    visibility_before = {n: engine._find_bone_collection(arm.data, n).is_visible
                         for n in (FK_COLLECTION, CTRL_COLLECTION)}
    try:
        if mode == "IK":
            # Native geometry is used to refresh the pole. When almost
            # straight, its fallback is the transported non-singular rest bend.
            _set_pose(context, arm.pose.bones[IK_HAND], joints_before[JOINTS[2]])
            pole_position, _d = _pole_position(arm)
            _move_head(context, arm.pose.bones[IK_POLE], pole_position)
            # IK is evaluated in parallel while FK still owns the outputs.
            # Each driven bone is calibrated to match the CURRENT FK world
            # matrix, retaining any twist that joint-head positions omit.
            for native, driver in ((JOINTS[0], DRIVE_UPPER), (JOINTS[1], DRIVE_LOWER)):
                _set_pose(context, arm.pose.bones[driver], joints_before[native])
            expected = {JOINTS[0]: DRIVE_UPPER, JOINTS[1]: DRIVE_LOWER,
                        JOINTS[2]: DRIVE_HAND}
        else:
            # Parent-first FK snap: upper->forearm->hand. FK controls remain
            # independent of the IK output, so assigning these cannot cycle.
            expected = {}
            for native, ctrl in zip(JOINTS, ("UpperArm.L", "Forearm.L", "Hand.L")):
                cname = ae_biped._ctrl(ctrl)
                _set_pose(context, arm.pose.bones[cname], joints_before[native])
                expected[native] = cname
        _verify_snap(arm, {ctrl: joints_before[native]
                           for native, ctrl in expected.items()},
                     f"Target {mode} snap mismatch")
        _set_mode(arm, mode)
        context.view_layer.update()
        worst_source = _verify_snap(arm, joints_before,
                                    "IK mode change moved the arm")
        return {"mode": mode, "relative_pos_delta": worst_source[0],
                "rotation_delta_deg": math.degrees(worst_source[1])}
    except Exception:
        for name, basis in saved.items():
            try:
                arm.pose.bones[name].matrix_basis = basis
            except Exception:
                pass
        for label, _driver in OUTPUTS:
            pb = arm.pose.bones.get(ae_biped._mch(label))
            if pb:
                current = json.loads(original_record)[KEY]["mode"]
                pb.constraints[ae_biped._mechanism_constraint(label)].influence = (
                    1.0 if current == "FK" else 0.0)
                pb.constraints[COPY_PREFIX + label].influence = (0.0 if current == "FK" else 1.0)
        arm[ae_biped.MANIFEST_KEY] = original_record
        for cname, visible in visibility_before.items():
            col = engine._find_bone_collection(arm.data, cname)
            if col is not None:
                col.is_visible = visible
        context.view_layer.update()
        raise



def reset_pole(context, arm):
    """Place the elbow pole at the current pose's calibrated position.

    Unlike Alt+G, which restores the build-time bone rest placement, this
    re-samples the evaluated elbow plane. With a well-bent elbow it preserves
    that measured plane; with a nearly straight elbow it transports the
    anatomical rest hinge with the native evaluated upper-arm rotation. This operation does NOT
    attempt to force a different bend direction for an already folded IK arm.
    """
    if ae_biped.audit(arm)["status"] != "COMPLETE":
        raise RuntimeError("AE FK foundation is not complete")
    status = _audit(arm)
    if status["status"] != "COMPLETE":
        raise RuntimeError("AE left-arm IK is incomplete: " + str(status))
    if arm.mode not in {"POSE", "OBJECT"}:
        raise RuntimeError("Leave Edit Mode before resetting the IK pole")
    ad = arm.animation_data
    if ad and (ad.action is not None or len(ad.nla_tracks) or len(ad.drivers)):
        raise RuntimeError("Pole reset is not animation-aware; use an unanimated test copy")
    # Re-evaluate constrained native matrices BEFORE capturing the baseline.
    # In IK mode the native upper arm is authoritative, not the inactive FK.
    context.view_layer.update()
    pole = arm.pose.bones[IK_POLE]
    before_basis = pole.matrix_basis.copy()
    prior_sources = {n: arm.pose.bones[n].matrix.copy() for n in JOINTS}
    prior_target = arm.pose.bones[IK_HAND].matrix.copy()
    try:
        new_position, bend_direction = _pole_position(arm)
        previous_position = pole.head.copy()
        _move_head(context, pole, new_position)
        # Pole repositioning must never alter the target control itself.
        _verify_snap(arm, {IK_HAND: prior_target}, "Pole reset moved IK hand target")
        if status["mode"] == "FK":
            # The inactive IK must never alter any native FK pose.
            _verify_snap(arm, prior_sources, "Pole reset changed FK native pose")
        else:
            # An IK elbow is allowed to reorient; the wrist/hand should remain
            # at its previous evaluated transform (the hand goal is unchanged).
            _verify_snap(arm, {JOINTS[2]: prior_sources[JOINTS[2]]},
                         "Pole reset moved IK wrist")
        if _audit(arm)["status"] != "COMPLETE":
            raise RuntimeError("Pole reset invalidated the IK rig audit")
        return {"mode": status["mode"],
                "distance": (pole.head - previous_position).length,
                "pole_direction": tuple(float(v) for v in bend_direction)}
    except Exception:
        pole.matrix_basis = before_basis
        context.view_layer.update()
        raise


def remove(context, arm):
    if ae_biped.audit(arm)["status"] != "COMPLETE" or _audit(arm)["status"] != "COMPLETE":
        raise RuntimeError("AE IK cannot safely be removed: incomplete manifest/constraints")
    # Stop if the user has attached anything else to the experimental IK bones.
    # Removing them would otherwise silently break custom rigs and constraints.
    for bone in arm.data.bones:
        if bone.name not in BONES and bone.parent and bone.parent.name in BONES:
            raise RuntimeError("An external bone is parented to IK: " + bone.name)
    for pb in arm.pose.bones:
        if pb.name in BONES:
            allowed = ({SOLVER_CONSTRAINT} if pb.name == SOLVER_LOWER
                       else {HAND_ROTATION} if pb.name == DRIVE_HAND else set())
            if set(c.name for c in pb.constraints) != allowed:
                raise RuntimeError("Unexpected IK mechanism constraints: " + pb.name)
        for c in pb.constraints:
            if (getattr(c, "target", None) == arm and
                    getattr(c, "subtarget", None) in BONES and
                    not (pb.name in {ae_biped._mch(x) for x, _ in OUTPUTS}
                             and c.name == COPY_PREFIX + pb.name[len("SR_MCH_AE_"):])):
                if not ((pb.name == SOLVER_LOWER and c.name == SOLVER_CONSTRAINT)
                        or (pb.name == DRIVE_HAND and c.name == HAND_ROTATION)):
                    raise RuntimeError("External constraint depends on IK: " + pb.name + ":" + c.name)
    if _audit(arm)["mode"] != "FK":
        raise RuntimeError("Snap to FK before removing IK")
    ad = arm.animation_data
    if ad and (ad.action is not None or len(ad.nla_tracks) or len(ad.drivers)):
        raise RuntimeError("IK pilot removal blocked by Actions/NLA/drivers")
    base = {name: arm.pose.bones[name].matrix.copy() for name in JOINTS}
    fk_memberships = _manifest(arm)[KEY]["fk_original_memberships"]
    # Fail before deleting anything if restoration cannot resolve all original
    # FK collection names; the final assignment remains after bone deletion.
    for fk_name in FK_LEFT:
        if fk_name not in arm.data.bones:
            raise RuntimeError("Missing FK control prior to IK removal: " + fk_name)
        for collection_name in fk_memberships[fk_name]:
            if engine._find_bone_collection(arm.data, collection_name) is None:
                raise RuntimeError("Cannot restore missing original FK collection: " + collection_name)
    active, selected, mode = context.view_layer.objects.active, list(context.selected_objects), arm.mode
    destructive_started = False
    try:
        # All checks above are read-only; failures here leave the rig unchanged.
        # The operation below cannot reconstruct deleted Blender edit bones if
        # interrupted. Its operator has UNDO, but do not promise transactional
        # recovery from Python exceptions (or a Blender crash).
        destructive_started = True
        for label, _driver in OUTPUTS:
            pb = arm.pose.bones[ae_biped._mch(label)]
            pb.constraints.remove(pb.constraints[COPY_PREFIX + label])
        arm.pose.bones[SOLVER_LOWER].constraints.remove(
            arm.pose.bones[SOLVER_LOWER].constraints[SOLVER_CONSTRAINT])
        arm.pose.bones[DRIVE_HAND].constraints.remove(
            arm.pose.bones[DRIVE_HAND].constraints[HAND_ROTATION])
        ae_biped._remove_own_bones(context, arm, names=BONES)
        _restore_fk_memberships(arm, fk_memberships)
        for cname in (MCH_COLLECTION, CTRL_COLLECTION, FK_COLLECTION):
            col = engine._find_bone_collection(arm.data, cname)
            if col is None or len(col.bones):
                raise RuntimeError("IK collection not empty after removal: " + cname)
            arm.data.collections.remove(col)
        # The foundation collection was never changed by this extension.
        # Leave its current visibility intact, including user adjustments.
        record = _manifest(arm)
        record.pop(KEY)
        _save_manifest(arm, record)
        context.view_layer.update()
        worst = max(ae_biped._matrix_difference(
            arm.pose.bones[name].matrix, pose) for name, pose in base.items())
        if worst > ae_biped.POSE_TOLERANCE:
            raise RuntimeError(f"IK removal changed FK pose: Δ={worst:.6g}")
        if ae_biped.audit(arm)["status"] != "COMPLETE":
            raise RuntimeError("FK foundation incomplete after IK removal")
        return {"removed": len(BONES), "pose_delta": worst}
    except Exception as exc:
        if destructive_started:
            raise RuntimeError(
                "IK removal interrupted AFTER destructive changes; no safe "
                "automatic rollback is available. Do not retry Build/Remove "
                "on this rig; reopen the independent saved .blend. "
                "Original error: " + str(exc)) from exc
        raise
    finally:
        ae_biped._restore_context(context, arm, active, selected, mode)


class SR_OT_AE_IK_L_Build(Operator):
    bl_idname = "saberrig.ae_ik_l_build"
    bl_label = "Build AE Left Arm IK (Experimental)"
    bl_description = "Add an isolated 2-bone IK solver to the working FK layer; off by default"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm = engine._resolve_armature(context, context.scene.saberrig_state)
        try:
            result = build(context, arm)
        except Exception as exc:
            self.report({"ERROR"}, "AE IK build stopped: " + str(exc)[:450])
            return {"CANCELLED"}
        self.report({"INFO"}, f"Left IK built (+{result['bones']} bones), FK preserved; pole score={result['pole_score']:.3f}")
        return {"FINISHED"}


class SR_OT_AE_IK_L_Switch(Operator):
    bl_idname = "saberrig.ae_ik_l_switch"
    bl_label = "Snap AE Left Arm FK/IK"
    bl_description = "Match destination controls, verify native pose, then switch left arm mode"
    bl_options = {"REGISTER", "UNDO"}
    target_mode: bpy.props.EnumProperty(
        name="Mode", items=(("FK", "FK", "Forward Kinematics"),
                             ("IK", "IK", "Inverse Kinematics")))

    def execute(self, context):
        arm = engine._resolve_armature(context, context.scene.saberrig_state)
        try:
            result = switch(context, arm, self.target_mode)
        except Exception as exc:
            self.report({"ERROR"}, "AE FK/IK snap stopped: " + str(exc)[:450])
            return {"CANCELLED"}
        self.report({"INFO"}, f"Left arm {result['mode']} selected (snap preserved source pose)")
        return {"FINISHED"}


class SR_OT_AE_IK_L_ResetPole(Operator):
    bl_idname = "saberrig.ae_ik_l_reset_pole"
    bl_label = "Reset AE Left Elbow Pole to Current Pose"
    bl_description = ("Reposition the elbow pole using the evaluated arm pose "
                      "instead of its build-time rest location (Alt+G)")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm = engine._resolve_armature(context, context.scene.saberrig_state)
        try:
            result = reset_pole(context, arm)
        except Exception as exc:
            self.report({"ERROR"}, "AE elbow pole reset stopped: " + str(exc)[:450])
            return {"CANCELLED"}
        self.report({"INFO"},
                    f"Elbow pole reset for current {result['mode']} pose "
                    f"(moved {result['distance']:.5g} armature units)")
        return {"FINISHED"}


class SR_OT_AE_IK_L_Remove(Operator):
    bl_idname = "saberrig.ae_ik_l_remove"
    bl_label = "Remove AE Left Arm IK Pilot"
    bl_description = "Remove seven owned IK bones and five constraints only after switching back to FK"
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        arm = engine._resolve_armature(context, context.scene.saberrig_state)
        try:
            result = remove(context, arm)
        except Exception as exc:
            self.report({"ERROR"}, "AE IK removal stopped: " + str(exc)[:450])
            return {"CANCELLED"}
        self.report({"INFO"}, f"Removed {result['removed']} IK bones; left arm FK retained")
        return {"FINISHED"}
