# SPDX-License-Identifier: GPL-2.0-or-later
# -*- coding: utf-8 -*-
"""Arknights: Endfield biped FK foundation and reversible control setup.

The builder creates its own control/mechanism layers and records ownership in
an armature manifest. Imported hierarchy, rest matrices, skin weights, deform
flags, and facial/twist/corrective bones are outside this module's write scope.

A preflight must succeed before mutation. Removal or repeat Build relies on
that manifest; do not reinterpret incomplete existing rigs as a blank install.
Native animation/deformation behavior must still be checked in Blender.
"""

import json
import math

import bpy
from bpy.types import Operator
from . import engine, ae_collection_org

MANIFEST_KEY = "saberrig_ae_biped_foundation"
COMPONENT = "AE_BIPED_FOUNDATION"
COLLECTION_CONTROLS = "SaberRig AE Biped Controls"
COLLECTION_MECHANISM = "SaberRig AE Biped Mechanism"
POSE_TOLERANCE = 1.0e-4

# Imported 3ds Max Biped names and parent links audited against Wulfa.
# These are detection/source references, not instructions to reparent bones.
# Do not infer control span from importer-specific bone.length values.
ROLES = (
    ("root", "Bip001", None, "Root"),
    ("pelvis", "Bip001_Pelvis", "root", "Pelvis"),
    ("spine_01", "Bip001_Spine", "pelvis", "Spine"),
    ("spine_02", "Bip001_Spine1", "spine_01", "Spine1"),
    ("chest", "Bip001_Spine2", "spine_02", "Chest"),
    ("neck", "Bip001_Neck", "chest", "Neck"),
    ("head", "Bip001_Head", "neck", "Head"),
    ("clavicle.L", "Bip001_L_Clavicle", "chest", "Clavicle.L"),
    ("upper_arm.L", "Bip001_L_UpperArm", "clavicle.L", "UpperArm.L"),
    ("forearm.L", "Bip001_L_Forearm", "upper_arm.L", "Forearm.L"),
    ("hand.L", "Bip001_L_Hand", "forearm.L", "Hand.L"),
    ("clavicle.R", "Bip001_R_Clavicle", "chest", "Clavicle.R"),
    ("upper_arm.R", "Bip001_R_UpperArm", "clavicle.R", "UpperArm.R"),
    ("forearm.R", "Bip001_R_Forearm", "upper_arm.R", "Forearm.R"),
    ("hand.R", "Bip001_R_Hand", "forearm.R", "Hand.R"),
    ("thigh.L", "Bip001_L_Thigh", "pelvis", "Thigh.L"),
    ("shin.L", "Bip001_L_Calf", "thigh.L", "Calf.L"),
    ("foot.L", "Bip001_L_Foot", "shin.L", "Foot.L"),
    ("toe.L", "Bip001_L_Toe0", "foot.L", "Toe.L"),
    ("thigh.R", "Bip001_R_Thigh", "pelvis", "Thigh.R"),
    ("shin.R", "Bip001_R_Calf", "thigh.R", "Calf.R"),
    ("foot.R", "Bip001_R_Foot", "shin.R", "Foot.R"),
    ("toe.R", "Bip001_R_Toe0", "foot.R", "Toe.R"),
)


def _ctrl(label):
    return "SR_CTRL_AE_" + label


def _mch(label):
    return "SR_MCH_AE_" + label


def _source_constraint(label):
    return "SR_AE_BIPED_SOURCE_" + label


def _mechanism_constraint(label):
    return "SR_AE_BIPED_CONTROL_" + label


def expected_names():
    controls = [_ctrl("Master"), _ctrl("COG")]
    controls.extend(_ctrl(row[3]) for row in ROLES)
    mechanisms = [_mch(row[3]) for row in ROLES]
    return controls, mechanisms


def _matrix_difference(a, b):
    return max(abs(float(a[i][j]) - float(b[i][j]))
               for i in range(4) for j in range(4))


def _pose_is_neutral(pb):
    basis = pb.matrix_basis
    return max(abs(float(basis[i][j]) - (1.0 if i == j else 0.0))
               for i in range(4) for j in range(4)) <= POSE_TOLERANCE


def _flat_matrix(matrix):
    return [float(matrix[i][j]) for i in range(4) for j in range(4)]


def _pose_matrix_is_finite(matrix):
    return all(math.isfinite(float(matrix[i][j]))
               for i in range(4) for j in range(4))


def _basis_matches(pb, flat):
    return (isinstance(flat, list) and len(flat) == 16
            and all(isinstance(v, (int, float)) and math.isfinite(float(v))
                    for v in flat)
            and max(abs(float(pb.matrix_basis[i][j]) - float(flat[4*i+j]))
                    for i in range(4) for j in range(4)) <= POSE_TOLERANCE)


def _calibrate_control_poses(context, arm, source_matrices):
    """Match the imported native pose, not an assumed identity/Rest pose.

    Use Blender's inverse Bone.convert_local_to_pose in FK parent-first order,
    accounting for each NEW control's rest matrix and actual parent pose.
    This is intentionally performed before installing source constraints.
    """
    targets = {
        _ctrl("Master"): source_matrices["Bip001"],
        _ctrl("COG"): source_matrices["Bip001_Pelvis"],
    }
    for _role, native, _parent, label in ROLES:
        targets[_ctrl(label)] = source_matrices[native]
    ordered = [_ctrl("Master"), _ctrl("Root"), _ctrl("COG")]
    ordered += [_ctrl(label) for role, _native, _parent, label in ROLES if role != "root"]
    for name in ordered:
        pb = arm.pose.bones[name]
        target = targets[name]
        parent = pb.parent
        if parent is None:
            basis = pb.bone.convert_local_to_pose(
                target, pb.bone.matrix_local, invert=True)
        else:
            parent_target = targets.get(parent.name)
            if parent_target is None:
                raise RuntimeError("Missing AE parent calibration pose: " + parent.name)
            basis = pb.bone.convert_local_to_pose(
                target, pb.bone.matrix_local,
                parent_matrix=parent_target,
                parent_matrix_local=parent.bone.matrix_local,
                invert=True)
        if not _pose_matrix_is_finite(basis):
            raise RuntimeError("Non-finite AE control calibration: " + name)
        pb.matrix_basis = basis
    context.view_layer.update()
    for name in ordered:
        err = _matrix_difference(arm.pose.bones[name].matrix, targets[name])
        if err > POSE_TOLERANCE:
            raise RuntimeError(f"AE imported-pose calibration mismatch: {name} Δ={err:.6g}")
    return {name: _flat_matrix(arm.pose.bones[name].matrix_basis)
            for name in targets}


def _require_local(arm):
    if arm is None or getattr(arm, "type", None) != "ARMATURE" or getattr(arm, "data", None) is None:
        raise RuntimeError("Select an Endfield Biped armature")
    for item in (arm, arm.data):
        if getattr(item, "library", None) is not None or getattr(item, "override_library", None) is not None:
            raise RuntimeError("AE controls require local, non-overridden armature data")


def _validated_root_wrappers(arm):
    """Read-only recognition of importer root wrappers above native Bip001.

    Approved shapes (all within the SAME armature):
      Bip001                           -- original Wulfa
      Root -> Bip001                   -- single FBX wrapper
      chr_*_postmodel -> Root -> Bip001 -- verified Si import family

    The native bone hierarchy is NEVER changed. Wrapper poses/constraints must
    be finite and unmodified by other constraints; animations are separately
    blocked by the global AE preflight. Other parent shapes fail closed.
    """
    native = arm.data.bones.get("Bip001")
    if native is None:
        return []  # Standard missing-role check reports the native name.
    wrapper = native.parent
    if wrapper is None:
        return []
    if wrapper.name != "Root":
        raise RuntimeError(
            f"Unexpected parent on Bip001: expected None or Root wrapper, got {wrapper.name}")
    chain = [wrapper]
    if wrapper.parent is not None:
        outer = wrapper.parent
        if (outer.parent is not None or not outer.name.startswith("chr_")
                or not outer.name.endswith("_postmodel")):
            raise RuntimeError(
                "Unsupported AE root wrapper above Root: " + outer.name +
                " (requires a root-level chr_*_postmodel bone)")
        chain.append(outer)
    for bone in chain:
        pb = arm.pose.bones.get(bone.name)
        if pb is None:
            raise RuntimeError("AE root wrapper has no pose channel: " + bone.name)
        if (not _pose_matrix_is_finite(pb.matrix_basis)
                or not _pose_matrix_is_finite(pb.matrix)):
            raise RuntimeError("AE root wrapper has invalid pose: " + bone.name)
        if len(pb.constraints):
            raise RuntimeError("AE root wrapper has existing constraints: " + bone.name)
    return [b.name for b in chain]


def _preflight(arm):
    """Pure read-only gate; detects profile by original bones, not a stale flag."""
    _require_local(arm)
    if getattr(arm, "mode", "OBJECT") not in {"OBJECT", "POSE"}:
        raise RuntimeError("Switch to Object or Pose Mode before AE build")
    if arm.get(MANIFEST_KEY):
        raise RuntimeError("AE Biped foundation is already installed; use Audit or Remove")
    bones = arm.data.bones
    pose = arm.pose.bones
    mapping = {role: name for role, name, _parent, _label in ROLES}
    root_wrappers = _validated_root_wrappers(arm)
    for role, name, parent_role, _label in ROLES:
        bone = bones.get(name)
        if bone is None or pose.get(name) is None:
            raise RuntimeError(f"Missing Endfield Biped bone: {name}")
        expected_parent = mapping.get(parent_role)
        if role == "root" and root_wrappers:
            expected_parent = root_wrappers[0]
        actual_parent = bone.parent.name if bone.parent else None
        if actual_parent != expected_parent:
            raise RuntimeError(f"Unexpected parent on {name}: expected {expected_parent}, got {actual_parent}")
        if (not _pose_matrix_is_finite(pose[name].matrix_basis)
                or not _pose_matrix_is_finite(pose[name].matrix)):
            raise RuntimeError(f"Invalid/non-finite native pose on {name}")
        if len(pose[name].constraints):
            raise RuntimeError(f"Existing constraints on {name}; refusing to overwrite native rig behaviour")
    profile, _score, _evidence, _scores = engine._detect_profile([b.name for b in bones])
    if profile["id"] != "ENDFIELD_BIPED":
        raise RuntimeError(f"Detected {profile['id']}, not ENDFIELD_BIPED")
    if any(bool(b.get("saberrig_generated", False)) for b in bones):
        raise RuntimeError("Another SaberRig component already owns generated bones; use a clean rig")
    controls, mechanisms = expected_names()
    for name in controls + mechanisms:
        if bones.get(name) is not None:
            raise RuntimeError(f"Generated bone name is already occupied: {name}")
    for name in (COLLECTION_CONTROLS, COLLECTION_MECHANISM,
                 ae_collection_org.AUX_COLLECTION, ae_collection_org.DEFORM_COLLECTION):
        if engine._find_bone_collection(arm.data, name) is not None:
            raise RuntimeError(f"AE bone collection name already occupied: {name}")
    ad = arm.animation_data
    if ad and (ad.action is not None or len(ad.nla_tracks) or len(ad.drivers)):
        raise RuntimeError("Native armature animation exists; test this first in an unanimated copy")
    return mapping


def _rest_snapshot(arm):
    """Exact original bone topology/rest sample before creating anything."""
    return {
        b.name: (b.parent.name if b.parent else None,
                 tuple(float(v) for v in b.head_local),
                 tuple(float(v) for v in b.tail_local),
                 tuple(float(v) for row in b.matrix_local for v in row),
                 bool(b.use_deform))
        for b in arm.data.bones
    }


def _assert_rest_unchanged(arm, snapshot):
    for name, old in snapshot.items():
        bone = arm.data.bones.get(name)
        if bone is None:
            raise RuntimeError(f"Native bone disappeared: {name}")
        new = (bone.parent.name if bone.parent else None,
               tuple(float(v) for v in bone.head_local),
               tuple(float(v) for v in bone.tail_local),
               tuple(float(v) for row in bone.matrix_local for v in row),
               bool(bone.use_deform))
        if old[0] != new[0] or old[-1] != new[-1] or any(
                abs(a - b) > 1e-6 for oldvals, newvals in zip(old[1:-1], new[1:-1])
                for a, b in zip(oldvals, newvals)):
            raise RuntimeError(f"Native rest data changed unexpectedly: {name}")


def _enter_mode(context, arm, mode):
    active = context.view_layer.objects.active
    if active is not arm:
        if active and getattr(active, "mode", "OBJECT") != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in list(context.selected_objects):
            obj.select_set(False)
        arm.select_set(True)
        context.view_layer.objects.active = arm
    if arm.mode != mode:
        bpy.ops.object.mode_set(mode=mode)


def _restore_context(context, arm, active, selected, mode):
    """Best-effort UI restoration; not a proof of native data rollback."""
    try:
        if arm.mode != "OBJECT":
            _enter_mode(context, arm, "OBJECT")
        for obj in list(context.selected_objects):
            obj.select_set(False)
        for obj in selected:
            if obj.name in bpy.data.objects:
                obj.select_set(True)
        if active and active.name in bpy.data.objects:
            context.view_layer.objects.active = active
        if arm is active and mode == "POSE":
            bpy.ops.object.mode_set(mode="POSE")
    except Exception as exc:
        print("SaberRig AE: could not completely restore UI selection/mode:", exc)


def _clone_edit(edit_bones, new_name, source):
    new = edit_bones.new(new_name)
    new.head = source.head.copy()
    new.tail = source.tail.copy()
    new.roll = source.roll
    new.use_deform = False
    new.use_connect = False
    new.inherit_scale = source.inherit_scale
    return new


def _add_copy(pb, arm, target_bone, name):
    constraint = pb.constraints.new("COPY_TRANSFORMS")
    constraint.name = name
    constraint.target = arm
    constraint.subtarget = target_bone
    constraint.target_space = "POSE"
    constraint.owner_space = "POSE"
    constraint.mix_mode = "REPLACE"
    constraint.influence = 1.0
    return constraint


def _expected_manifest():
    controls, mechanisms = expected_names()
    return {
        "profile": "ENDFIELD_BIPED",
        "component": COMPONENT,
        "controls": controls,
        "mechanisms": mechanisms,
        "sources": [row[1] for row in ROLES],
        "source_constraints": [_source_constraint(row[3]) for row in ROLES],
        "mechanism_constraints": [_mechanism_constraint(row[3]) for row in ROLES],
        "collections": [COLLECTION_CONTROLS, COLLECTION_MECHANISM],
    }


def _ik_modules():
    """Return the four independently removable IK extensions (left E1.4 unchanged)."""
    from . import ae_ik, ae_ik_arm_r, ae_ik_leg_l, ae_ik_leg_r
    return (ae_ik, ae_ik_arm_r, ae_ik_leg_l, ae_ik_leg_r)


def _installed_ik_modules(manifest):
    # Fast path: legacy FK-only audit must not import optional IK modules.
    # This keeps old library/QA stubs compatible and avoids extra imports.
    keys = ("ik_arm_l", "ik_arm_r", "ik_leg_l", "ik_leg_r")
    if not any(key in manifest for key in keys):
        return ()
    return tuple(module for module in _ik_modules() if module.KEY in manifest)


def audit(arm):
    if arm is None or getattr(arm, "type", None) != "ARMATURE":
        return {"status": "NO_ARMATURE"}
    value = arm.get(MANIFEST_KEY)
    if not value:
        return {"status": "NOT_INSTALLED", "native_bones": len(arm.data.bones)}
    try:
        manifest = json.loads(str(value))
    except (ValueError, TypeError):
        return {"status": "INVALID_MANIFEST"}
    expected = _expected_manifest()
    if any(manifest.get(k) != v for k, v in expected.items()):
        return {"status": "MANIFEST_MISMATCH"}
    # Optional E2.2 metadata: older Wulfa/IK manifests remain valid as-is.
    if "source_root_wrappers" in manifest:
        try:
            actual_wrappers = _validated_root_wrappers(arm)
        except RuntimeError as exc:
            return {"status": "ROOT_WRAPPER_CHANGED", "reason": str(exc)}
        if manifest["source_root_wrappers"] != actual_wrappers:
            return {"status": "ROOT_WRAPPER_CHANGED",
                    "expected": manifest["source_root_wrappers"],
                    "actual": actual_wrappers}
    if "calibration_version" in manifest:
        if manifest["calibration_version"] != 1:
            return {"status": "INVALID_CALIBRATION"}
        for key, names in (("control_baselines", expected["controls"]),
                           ("source_basis_baselines", expected["sources"]),
                           ("source_pose_baselines", expected["sources"])):
            values = manifest.get(key)
            if (not isinstance(values, dict) or set(values) != set(names)
                    or any(not isinstance(v, list) or len(v) != 16
                           or any(not isinstance(x, (int, float))
                                  or not math.isfinite(float(x)) for x in v)
                           for v in values.values())):
                return {"status": "INVALID_CALIBRATION"}
    missing = []
    generated = set(expected["controls"] + expected["mechanisms"])
    ik_modules = _installed_ik_modules(manifest)
    for module in ik_modules:
        # The AE organization inventory must exclude *every* installed IK
        # extension, never treating generated solver bones as native Wulfa.
        ik_extension = manifest[module.KEY]
        if not isinstance(ik_extension, dict) or ik_extension.get("bones") != list(module.BONES):
            return {"status": "INVALID_IK_MANIFEST", "module": module.KEY}
        generated.update(module.BONES)
        # E2.4 guides are installed lazily; exclude only an explicitly owned
        # guide with matching name from the original-source inventory.
        ff = ik_extension.get("foot_follow")
        if ff is not None:
            if (not isinstance(ff, dict) or ff.get("version") != 1 or
                    ff.get("guide") != getattr(module, "FOLLOW_GUIDE", None)):
                return {"status": "INVALID_FOOT_FOLLOW_MANIFEST", "module": module.KEY}
            generated.add(module.FOLLOW_GUIDE)
    organization = manifest.get(ae_collection_org.ORG_KEY)
    if organization is not None:
        missing.extend(ae_collection_org.validate(arm, organization,
                      set(expected["sources"]), generated))
    for name in expected["controls"] + expected["mechanisms"]:
        bone = arm.data.bones.get(name)
        if bone is None or bone.get("saberrig_component") != COMPONENT:
            missing.append("bone:" + name)
    for name in expected["collections"]:
        if engine._find_bone_collection(arm.data, name) is None:
            missing.append("collection:" + name)
    for role, source_name, _parent, label in ROLES:
        for owner_name, constraint_name, target_name in (
                (source_name, _source_constraint(label), _mch(label)),
                (_mch(label), _mechanism_constraint(label), _ctrl(label))):
            pb = arm.pose.bones.get(owner_name)
            c = pb.constraints.get(constraint_name) if pb else None
            if (c is None or c.type != "COPY_TRANSFORMS" or c.target != arm
                    or c.subtarget != target_name or c.owner_space != "POSE"
                    or c.target_space != "POSE"):
                missing.append("constraint:" + owner_name + ":" + constraint_name)
    from . import ae_auto_attachments
    if 'auto_rigid_attachments' in manifest:
        meta = manifest['auto_rigid_attachments']
        if (not isinstance(meta, dict) or meta.get('version') != 1
                or meta.get('profile') != ae_auto_attachments.PROFILE
                or meta.get('count') != len(ae_auto_attachments.si.TARGETS)
                or not arm.get(ae_auto_attachments.si.PILOT_KEY)):
            missing.append('rigid:auto_manifest')
        else:
            missing.extend(ae_auto_attachments.audit_faults(arm))
    for module in ik_modules:
        # Each limb audits only the outputs it owns. Other limbs can remain FK.
        ik_status = module._audit(arm)
        missing.extend(module.KEY + ":" + msg for msg in ik_status.get("faults", []))
        if ik_status["status"] != "COMPLETE":
            missing.append(module.KEY + ":invalid")
    return {"status": "COMPLETE" if not missing else "INCOMPLETE",
            "missing": missing, "controls": len(expected["controls"]),
            "mechanisms": len(expected["mechanisms"]), "sources": len(ROLES)}


def _remove_own_constraints(arm):
    for _role, source_name, _parent, label in ROLES:
        for owner_name, constraint_name in ((source_name, _source_constraint(label)),
                                            (_mch(label), _mechanism_constraint(label))):
            pb = arm.pose.bones.get(owner_name)
            if pb:
                constraint = pb.constraints.get(constraint_name)
                if constraint:
                    pb.constraints.remove(constraint)


def _remove_own_bones(context, arm, *, names):
    # A failed build may leave new bones only in Edit Mode. In that state
    # arm.data.bones is stale; the EditBone collection must be inspected.
    lookup = arm.data.edit_bones if arm.mode == "EDIT" else arm.data.bones
    if not any(lookup.get(n) is not None for n in names):
        return
    _enter_mode(context, arm, "EDIT")
    primary = None
    try:
        bones = arm.data.edit_bones
        for name in names:
            b = bones.get(name)
            if b is not None:
                bones.remove(b)
    except Exception as exc:
        primary = exc
        raise
    finally:
        if arm.mode == "EDIT":
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except Exception as mode_exc:
                if primary is None:
                    raise
                if hasattr(primary, "add_note"):
                    primary.add_note("Could not leave AE Edit Mode: " + str(mode_exc))


def _remove_own_collections(arm):
    for name in (COLLECTION_MECHANISM, COLLECTION_CONTROLS):
        collection = engine._find_bone_collection(arm.data, name)
        if collection is not None and len(collection.bones) == 0:
            arm.data.collections.remove(collection)


def _rollback(context, arm, *, restore_new_attachments=True):
    """Best-effort rollback. Never discard AE bones after source restore failed."""
    errors = []
    from . import ae_auto_attachments, ae_fx_attachments
    if restore_new_attachments and arm.get(ae_fx_attachments.PILOT_KEY):
        try:
            ae_auto_attachments.restore(context, arm)
        except Exception as exc:
            return ['rigid attachments restore failed; AE rig retained: ' + str(exc)]
    if arm.get(MANIFEST_KEY):
        try:
            ae_collection_org.restore(arm, MANIFEST_KEY,
                {row[1] for row in ROLES},
                set(sum((list(x) for x in expected_names()), [])))
        except Exception as exc:
            return ["source Bone Collection restore failed; AE rig left in place: " + str(exc)]
    for action in (
            lambda: _remove_own_constraints(arm),
            lambda: _remove_own_bones(context, arm, names=sum((list(x) for x in expected_names()), [])),
            lambda: _remove_own_collections(arm)):
        try:
            action()
        except Exception as exc:
            errors.append(str(exc))
    if MANIFEST_KEY in arm and not errors:
        try:
            del arm[MANIFEST_KEY]
        except Exception as exc:
            errors.append(str(exc))
    return errors


def build(context, arm):
    mapping = _preflight(arm)
    # Build-time preparation: analyze attachments before creating any bones.
    # Unknown AE model variants are skipped; partial/unsafe Si signatures fail.
    from . import ae_auto_attachments
    attachment_plan = ae_auto_attachments.preflight(context, arm)
    # Never roll back attachments that were present before this Build.
    attachment_existed_before_build = (attachment_plan['status'] == 'APPLIED')
    old_rest = _rest_snapshot(arm)
    old_matrices = {name: arm.pose.bones[name].matrix.copy() for name in mapping.values()}
    source_basis_baselines = {name: _flat_matrix(arm.pose.bones[name].matrix_basis)
                              for name in mapping.values()}
    old_bone_count = len(arm.data.bones)
    active = context.view_layer.objects.active
    selected = list(context.selected_objects)
    mode = arm.mode
    built = []
    try:
        _enter_mode(context, arm, "OBJECT")
        controls_collection = arm.data.collections.new(COLLECTION_CONTROLS)
        mechanism_collection = arm.data.collections.new(COLLECTION_MECHANISM)
        _enter_mode(context, arm, "EDIT")
        eb = arm.data.edit_bones
        master = _clone_edit(eb, _ctrl("Master"), eb[mapping["root"]])
        cog = _clone_edit(eb, _ctrl("COG"), eb[mapping["pelvis"]])
        controls_collection.assign(master)
        controls_collection.assign(cog)
        created_ctrl = {}
        for role, native, _parent, label in ROLES:
            cb = _clone_edit(eb, _ctrl(label), eb[native])
            created_ctrl[role] = cb
            controls_collection.assign(cb)
            mech = _clone_edit(eb, _mch(label), eb[native])
            mechanism_collection.assign(mech)
        # Parent only NEW controls. No native-bone parenting changes.
        created_ctrl["root"].parent = master
        cog.parent = created_ctrl["root"]
        for role, _native, parent_role, _label in ROLES:
            cb = created_ctrl[role]
            if role == "root":
                continue
            cb.parent = cog if role == "pelvis" else created_ctrl[parent_role]
            cb.use_connect = False
        bpy.ops.object.mode_set(mode="OBJECT")
        # Hide solver mechanisms without disabling their evaluation. Only
        # animator-facing AE controls should clutter Pose Mode.
        controls_collection.is_visible = True
        mechanism_collection.is_visible = False
        for name in sum((list(x) for x in expected_names()), []):
            b = arm.data.bones[name]
            b["saberrig_generated"] = True
            b["saberrig_component"] = COMPONENT
            b.use_deform = False
            built.append(name)
        # Wulfa's imported pose channels are not identity (e.g. shoulders).
        # Align the NEW controls to the source's CURRENT evaluated pose before
        # creating constraints, or their neutral/rest state would snap the skin.
        control_baselines = _calibrate_control_poses(context, arm, old_matrices)
        for name, baseline in source_basis_baselines.items():
            if not _basis_matches(arm.pose.bones[name], baseline):
                raise RuntimeError("Native source pose changed during calibration: " + name)
        # The control -> independent mechanism -> source route keeps all
        # original bones and skinning untouched, and allows future IK isolation.
        for _role, native, _parent, label in ROLES:
            _add_copy(arm.pose.bones[_mch(label)], arm, _ctrl(label), _mechanism_constraint(label))
            _add_copy(arm.pose.bones[native], arm, _mch(label), _source_constraint(label))
        context.view_layer.update()
        _assert_rest_unchanged(arm, old_rest)
        deviations = {name: _matrix_difference(arm.pose.bones[name].matrix, before)
                      for name, before in old_matrices.items()}
        worst = max(deviations.values())
        if worst > POSE_TOLERANCE:
            role = max(deviations, key=deviations.get)
            raise RuntimeError(f"Neutral pose mismatch after control build: {role} Δ={worst:.6g}")
        expected = _expected_manifest()
        expected["calibration_version"] = 1
        expected["source_root_wrappers"] = _validated_root_wrappers(arm)
        expected["control_baselines"] = control_baselines
        expected["source_basis_baselines"] = source_basis_baselines
        expected["source_pose_baselines"] = {
            name: _flat_matrix(matrix) for name, matrix in old_matrices.items()}
        arm[MANIFEST_KEY] = json.dumps(expected, sort_keys=True)
        result = audit(arm)
        if result["status"] != "COMPLETE" or len(arm.data.bones) != old_bone_count + len(built):
            raise RuntimeError("AE foundation integrity check failed: " + str(result))
        ae_collection_org.organize(arm, MANIFEST_KEY, {row[1] for row in ROLES},
            set(built), COLLECTION_CONTROLS, COLLECTION_MECHANISM)
        context.view_layer.update()
        final_worst = max(_matrix_difference(arm.pose.bones[name].matrix, old)
                          for name, old in old_matrices.items())
        if final_worst > POSE_TOLERANCE:
            raise RuntimeError(f"AE collection organization changed source pose: Δ={final_worst:.6g}")
        if audit(arm)["status"] != "COMPLETE":
            raise RuntimeError("AE organization post-build audit failed")
        # E2.5: verified rigid eye/accessory attachments belong to the AE Build,
        # not to a separate character-specific manual panel. Preserve the
        # legacy E2.3 attachment manifest for reversible removal.
        # apply() is the single ownership commit shared with Sync (E2.6).
        attachment = ae_auto_attachments.apply(context, arm)
        if audit(arm)['status'] != 'COMPLETE':
            raise RuntimeError('AE attachment post-build audit failed')
        return {"created": len(built), "native": old_bone_count,
                "neutral_max_delta": worst,
                "rigid_attachments": attachment['count'] if attachment['status'] == 'APPLIED' else 0}
    except Exception as exc:
        errors = _rollback(context, arm,
                           restore_new_attachments=not attachment_existed_before_build)
        if errors:
            raise RuntimeError("AE build failed, rollback INCOMPLETE: " + str(exc)
                               + "; recovery errors: " + "; ".join(errors)) from exc
        raise
    finally:
        _restore_context(context, arm, active, selected, mode)


def remove(context, arm):
    _require_local(arm)
    # Attachment restoration is automatic on Remove, but happens ONLY after
    # the AE and baseline guards and BEFORE deleting any source drivers.
    from . import ae_auto_attachments
    result = audit(arm)
    if result["status"] != "COMPLETE":
        raise RuntimeError(f"AE foundation cannot be safely removed: {result['status']}; "
                           "use original .blend/Undo if a build was interrupted")
    ad = arm.animation_data
    if ad and (ad.action is not None or len(ad.nla_tracks) or len(ad.drivers)):
        raise RuntimeError("Remove disabled while Actions/NLA/drivers exist; preserve animation first")
    manifest = json.loads(str(arm[MANIFEST_KEY]))
    installed = _installed_ik_modules(manifest)
    if installed:
        raise RuntimeError("Remove all AE IK extensions in FK mode first: " +
                           ", ".join(m.KEY for m in installed))
    control_baselines = manifest.get("control_baselines")
    if control_baselines is None:
        # Legacy AE foundations assumed identity source pose.
        for name in expected_names()[0]:
            if not _pose_is_neutral(arm.pose.bones[name]):
                raise RuntimeError(f"Control {name} has a pose; reset it before removal")
    else:
        for name, baseline in control_baselines.items():
            if not _basis_matches(arm.pose.bones[name], baseline):
                raise RuntimeError(f"Control {name} differs from calibrated baseline; "
                                   "use Restore AE Baseline before removal")
        for name, baseline in manifest["source_basis_baselines"].items():
            if not _basis_matches(arm.pose.bones[name], baseline):
                raise RuntimeError(f"Native source {name} was edited; refusing destructive removal")
    original_names = set(arm.data.bones.keys()) - set(sum((list(x) for x in expected_names()), []))
    rest = _rest_snapshot(arm)
    native_rest = {name: rest[name] for name in original_names}
    active, selected, mode = context.view_layer.objects.active, list(context.selected_objects), arm.mode
    try:
        # First restore attachments and immediately release the AE ownership
        # key; this leaves an auditable foundation if a later pre-deletion step
        # fails. Source constraints and bones have not been touched yet.
        ae_auto_attachments.restore(context, arm)
        _enter_mode(context, arm, "OBJECT")
        # Restore original source Bone Collections before deleting AE bones.
        ae_collection_org.restore(arm, MANIFEST_KEY, {row[1] for row in ROLES},
                                  set(sum((list(x) for x in expected_names()), [])))
        # Prevalidated names/types make this the only normal removal route.
        _remove_own_constraints(arm)
        _remove_own_bones(context, arm, names=sum((list(x) for x in expected_names()), []))
        _remove_own_collections(arm)
        if MANIFEST_KEY in arm:
            del arm[MANIFEST_KEY]
        _assert_rest_unchanged(arm, native_rest)
        context.view_layer.update()
        return {"removed": 2 + 2 * len(ROLES), "native": len(original_names)}
    finally:
        _restore_context(context, arm, active, selected, mode)


def restore_calibrated_controls(context, arm):
    """Explicitly return only AE controls to their recorded build-time pose."""
    _require_local(arm)
    result = audit(arm)
    if result["status"] != "COMPLETE":
        raise RuntimeError("AE calibration unavailable: " + result["status"])
    ad = arm.animation_data
    if ad and (ad.action is not None or len(ad.nla_tracks) or len(ad.drivers)):
        raise RuntimeError("Restore AE Baseline disabled with Action/NLA/drivers")
    manifest = json.loads(str(arm[MANIFEST_KEY]))
    installed = _installed_ik_modules(manifest)
    if installed:
        raise RuntimeError("Remove all AE IK extensions before restoring the FK baseline: " +
                           ", ".join(m.KEY for m in installed))
    baselines = manifest.get("control_baselines")
    if not baselines:
        raise RuntimeError("This AE layer has no calibrated baseline")
    for name, baseline in manifest["source_basis_baselines"].items():
        if not _basis_matches(arm.pose.bones[name], baseline):
            raise RuntimeError("Native bone has been edited: " + name)
    from mathutils import Matrix
    # Same parent-first order as the calibration.
    ordered = [_ctrl("Master"), _ctrl("Root"), _ctrl("COG")]
    ordered += [_ctrl(label) for role, _native, _parent, label in ROLES if role != "root"]
    saved = {name: arm.pose.bones[name].matrix_basis.copy() for name in ordered}
    try:
        for name in ordered:
            flat = baselines[name]
            arm.pose.bones[name].matrix_basis = Matrix(
                tuple(tuple(flat[4*i+j] for j in range(4)) for i in range(4)))
        context.view_layer.update()
        for name in ordered:
            if not _basis_matches(arm.pose.bones[name], baselines[name]):
                raise RuntimeError("AE baseline restore verification failed: " + name)
    except Exception:
        # Restore the previous animator control state if the reset is interrupted.
        for name, basis in saved.items():
            try:
                arm.pose.bones[name].matrix_basis = basis
            except Exception:
                pass
        context.view_layer.update()
        raise
    return {"restored": len(ordered)}


class SR_OT_AE_Biped_RestoreBaseline(Operator):
    bl_idname = "saberrig.ae_biped_restore_baseline"
    bl_label = "Restore AE Calibrated Pose"
    bl_description = "Reset AE FK controls to their calibrated imported pose, not Blender identity"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm = engine._resolve_armature(context, context.scene.saberrig_state)
        try:
            result = restore_calibrated_controls(context, arm)
        except Exception as exc:
            self.report({"ERROR"}, "AE baseline restore stopped: " + str(exc)[:450])
            return {"CANCELLED"}
        self.report({"INFO"}, "Restored " + str(result["restored"]) + " AE controls to imported pose")
        return {"FINISHED"}


class SR_OT_AE_Biped_Audit(Operator):
    bl_idname = "saberrig.ae_biped_audit"
    bl_label = "Audit AE Biped Foundation"
    bl_description = "Read-only verification of the Endfield Biped control layer"
    bl_options = {"REGISTER"}

    def execute(self, context):
        arm = engine._resolve_armature(context, context.scene.saberrig_state)
        if not arm:
            self.report({"ERROR"}, "Select an Endfield Biped armature")
            return {"CANCELLED"}
        result = audit(arm)
        if result["status"] == "NOT_INSTALLED":
            try:
                _preflight(arm)
                from . import ae_auto_attachments
                attachment_plan = ae_auto_attachments.preflight(context, arm)
            except Exception as exc:
                self.report({"WARNING"}, "AE preflight: " + str(exc)[:300])
            else:
                self.report({"INFO"}, "AE Biped preflight passed; rigid attachment plan: "
                            + attachment_plan['status'] + " (" + str(attachment_plan['count']) + ")")
        elif result["status"] == "COMPLETE":
            self.report({"INFO"}, "AE Biped controls complete (25 controls, 23 MCH, 23 source links)")
        else:
            self.report({"WARNING"}, "AE audit: " + str(result)[:350])
        return {"FINISHED"}


class SR_OT_AE_Biped_Build(Operator):
    bl_idname = "saberrig.ae_biped_build"
    bl_label = "Build AE Biped Control Foundation"
    bl_description = "Create reversible Endfield Biped FK controls without modifying source bone rest data or skin weights"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm = engine._resolve_armature(context, context.scene.saberrig_state)
        try:
            result = build(context, arm)
        except Exception as exc:
            self.report({"ERROR"}, "AE build stopped: " + str(exc)[:500])
            return {"CANCELLED"}
        self.report({"INFO"}, "AE Biped foundation built: " + str(result["created"])
                    + " helper bones; neutral Δ=" + str(round(result["neutral_max_delta"], 7))
                    + "; auto rigid bindings=" + str(result.get('rigid_attachments', 0)))
        return {"FINISHED"}


class SR_OT_AE_Biped_Remove(Operator):
    bl_idname = "saberrig.ae_biped_remove"
    bl_label = "Remove AE Biped Control Foundation"
    bl_description = "Remove only the verified AE Biped controls and their own constraints; block animated removal"
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        arm = engine._resolve_armature(context, context.scene.saberrig_state)
        try:
            result = remove(context, arm)
        except Exception as exc:
            self.report({"ERROR"}, "AE removal stopped: " + str(exc)[:500])
            return {"CANCELLED"}
        self.report({"INFO"}, "Removed " + str(result["removed"]) + " AE Biped helper bones; native rig preserved")
        return {"FINISHED"}


def organize_existing(context, arm):
    """Migrate an existing AE foundation in place without deleting its controls."""
    _require_local(arm)
    result = audit(arm)
    if result["status"] != "COMPLETE":
        raise RuntimeError("AE controls must be complete before organization: " + result["status"])
    generated = set(sum((list(x) for x in expected_names()), []))
    for module in _installed_ik_modules(json.loads(str(arm[MANIFEST_KEY]))):
        generated.update(module.BONES)
    result = ae_collection_org.organize(arm, MANIFEST_KEY, {row[1] for row in ROLES},
        generated,
        COLLECTION_CONTROLS, COLLECTION_MECHANISM)
    if audit(arm)["status"] != "COMPLETE":
        raise RuntimeError("AE organization verification failed")
    return result


class SR_OT_AE_Biped_OrganizeCollections(Operator):
    bl_idname = "saberrig.ae_biped_organize_collections"
    bl_label = "Organize AE Bone Collections"
    bl_description = "Organize existing AE source bones into reversible hidden Bone Collections"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm = engine._resolve_armature(context, context.scene.saberrig_state)
        try:
            result = organize_existing(context, arm)
        except Exception as exc:
            self.report({"ERROR"}, "AE collection organization stopped: " + str(exc)[:450])
            return {"CANCELLED"}
        message = ("AE collections already organized" if result.get("already_organized")
                   else "AE source bones organized: " + str(result["original"]))
        self.report({"INFO"}, message)
        return {"FINISHED"}


def _set_ae_view(context, operator, mode):
    arm = engine._resolve_armature(context, context.scene.saberrig_state)
    try:
        _require_local(arm)
        result = audit(arm)
        if result["status"] != "COMPLETE":
            raise RuntimeError("AE Biped incomplete: " + result["status"])
        state = ae_collection_org.set_view(arm, COLLECTION_CONTROLS, COLLECTION_MECHANISM, mode)
    except Exception as exc:
        operator.report({"ERROR"}, "AE collection visibility stopped: " + str(exc)[:400])
        return {"CANCELLED"}
    operator.report({"INFO"}, "AE visibility: " + str(state))
    return {"FINISHED"}


class SR_OT_AE_Biped_ShowControls(Operator):
    bl_idname = "saberrig.ae_biped_show_controls"
    bl_label = "Show AE Controls Only"
    bl_description = "Show animator controls; hide mechanism and original AE bones"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        return _set_ae_view(context, self, "CONTROLS")


class SR_OT_AE_Biped_ToggleAuxiliary(Operator):
    bl_idname = "saberrig.ae_biped_toggle_auxiliary"
    bl_label = "Toggle AE Auxiliary Bones"
    bl_description = "Show or hide original AE auxiliary, corrective, twist and secondary bones"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        return _set_ae_view(context, self, "AUX")


class SR_OT_AE_Biped_ToggleSource(Operator):
    bl_idname = "saberrig.ae_biped_toggle_source"
    bl_label = "Toggle AE Original Skeleton"
    bl_description = "Show or hide the 23 main original AE deform bones"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        return _set_ae_view(context, self, "SOURCE")
