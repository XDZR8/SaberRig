# SPDX-License-Identifier: GPL-2.0-or-later
# -*- coding: utf-8 -*-
"""Endfield leg IK: optional Foot Follow for knee-pole stability.

An animator's preference chooses between a foot-following guide and the
original knee pole. The guide is created lazily and does not replace either
leg IK solver. Existing manifests without this optional record remain valid.

Ownership and restoration are tracked on the AE foundation. Native bone
parents, rest matrices and mesh weights are not modified by this helper.
"""

import json

import bpy
from bpy.types import Operator

from . import ae_biped, engine

VERSION = 1
COMPONENT = "AE_BIPED_FOOT_FOLLOW_E2_4"


def guide_name(s):
    return "SR_MCH_AE_IK_FootFollowPole." + ("L" if s["KEY"].endswith("_l") else "R")


def _manifest(arm):
    return json.loads(str(arm[ae_biped.MANIFEST_KEY]))


def _record(arm, s):
    return _manifest(arm).get(s["KEY"], {}).get("foot_follow")


def preference(arm, s):
    """Default to Foot Follow unless the armature stores an explicit choice.

    Older manifests without a preference are intentionally interpreted as ON.
    """
    if not arm or not arm.get(ae_biped.MANIFEST_KEY):
        return True
    try:
        record = _record(arm, s)
        return bool(record["preferred"]) if isinstance(record, dict) else True
    except (KeyError, TypeError, ValueError):
        return True


def owned_bones(arm, s):
    return (guide_name(s),) if _record(arm, s) is not None else ()


def active(arm, s):
    record = _record(arm, s)
    return bool(record and record.get("enabled"))


def audit_faults(arm, s):
    issues = []
    name = guide_name(s)
    data = _manifest(arm)
    leg = data.get(s["KEY"], {})
    item = leg.get("foot_follow")
    pb = arm.pose.bones.get(s["SOLVER_LOWER"])
    ik = pb.constraints.get(s["SOLVER_CONSTRAINT"]) if pb else None
    if item is None:
        if arm.data.bones.get(name) is not None:
            issues.append("follow:orphan_guide:" + name)
        if ik is not None and ik.pole_subtarget != s["IK_POLE"]:
            issues.append("follow:unexpected_solver_pole")
        return issues
    if (not isinstance(item, dict) or item.get("version") != VERSION
            or item.get("guide") != name
            or type(item.get("preferred")) is not bool
            or type(item.get("enabled")) is not bool):
        return ["follow:invalid_manifest"]
    bone = arm.data.bones.get(name)
    if bone is None:
        issues.append("follow:missing_guide")
    else:
        if (bone.parent is None or bone.parent.name != s["IK_HAND"] or
                bone.use_inherit_rotation or bone.inherit_scale != "NONE" or
                bone.use_deform or bone.get("saberrig_component") != COMPONENT):
            issues.append("follow:guide_properties")
        if sorted(c.name for c in bone.collections) != [s["MCH_COLLECTION"]]:
            issues.append("follow:guide_collection")
        gpb = arm.pose.bones.get(name)
        if gpb and len(gpb.constraints):
            issues.append("follow:guide_constraints")
    should_enable = bool(leg.get("mode") == "IK" and item["preferred"])
    if item["enabled"] != should_enable:
        issues.append("follow:enabled_mode_mismatch")
    expected_pole = name if should_enable else s["IK_POLE"]
    if ik is None or ik.pole_subtarget != expected_pole:
        issues.append("follow:solver_pole_mismatch")
    for other in arm.pose.bones:
        for c in other.constraints:
            if (getattr(c, "target", None) == arm and
                getattr(c, "subtarget", None) == name and
                not (other.name == s["SOLVER_LOWER"] and c.name == s["SOLVER_CONSTRAINT"])):
                issues.append("follow:external_constraint:" + other.name)
            if (getattr(c, "pole_target", None) == arm and
                    getattr(c, "pole_subtarget", None) == name and
                    not (other.name == s["SOLVER_LOWER"] and c.name == s["SOLVER_CONSTRAINT"])):
                issues.append("follow:external_ik_pole:" + other.name)
    return issues


def _no_animation_or_edit(arm):
    if arm.mode not in {"POSE", "OBJECT"}:
        raise RuntimeError("Leave Edit Mode before changing AE Foot Follow")
    ae_biped._require_local(arm)
    ad = arm.animation_data
    if ad and (ad.action is not None or len(ad.nla_tracks) or len(ad.drivers)):
        raise RuntimeError("E2.4 Foot Follow is not animation-aware; use a non-animated .blend copy")


def _ik_constraint(arm, s):
    pb = arm.pose.bones.get(s["SOLVER_LOWER"])
    ik = pb.constraints.get(s["SOLVER_CONSTRAINT"]) if pb else None
    if not ik or ik.type != "IK" or ik.target != arm or ik.pole_target != arm:
        raise RuntimeError("AE leg IK solver is missing or altered")
    return ik


def install(context, arm, s):
    """Create one no-deform pole guide, preserving source and animator transforms.

    Can be called by the existing Snap IK button on an already-built E2.3 rig.
    Note that installation alone does not redirect the solver's pole target.
    """
    if _record(arm, s) is not None:
        errors = audit_faults(arm, s)
        if errors:
            raise RuntimeError("Foot Follow guide requires recovery: " + ", ".join(errors))
        return False
    _no_animation_or_edit(arm)
    if s["_audit"](arm)["status"] != "COMPLETE":
        raise RuntimeError("Build and audit the AE leg IK before installing Foot Follow")
    name = guide_name(s)
    if arm.data.bones.get(name) is not None:
        raise RuntimeError("Foot Follow bone name is already occupied: " + name)
    coll = engine._find_bone_collection(arm.data, s["MCH_COLLECTION"])
    if coll is None:
        raise RuntimeError("Missing AE leg mechanism collection")
    original_manifest = str(arm[ae_biped.MANIFEST_KEY])
    original_pole = arm.pose.bones[s["IK_POLE"]].matrix.copy()
    original_goal = arm.pose.bones[s["IK_HAND"]].matrix.copy()
    before_native = {n: arm.pose.bones[n].matrix.copy() for n in s["POSE_JOINTS"]}
    active_obj = context.view_layer.objects.active
    selected = list(context.selected_objects)
    old_mode = arm.mode
    made = False
    try:
        ae_biped._enter_mode(context, arm, "EDIT")
        eb = arm.data.edit_bones
        bone = eb.new(name)
        made = True
        # The rest position starts at the current fixed Pole Target. A local
        # pose offset is calibrated below for the animated current pose.
        pole_edit = eb[s["IK_POLE"]]
        bone.matrix = pole_edit.matrix.copy()
        bone.length = max(float(pole_edit.length), 0.05)
        bone.parent = eb[s["IK_HAND"]]
        bone.use_connect = False
        bone.use_deform = False
        bone.use_inherit_rotation = False
        bone.inherit_scale = "NONE"
        coll.assign(bone)
        ae_biped._enter_mode(context, arm, "OBJECT")
        b = arm.data.bones[name]
        b["saberrig_generated"] = True
        b["saberrig_component"] = COMPONENT
        b.use_deform = False
        # A parented EditBone doesn't necessarily evaluate at the Pole target.
        s["_set_pose"](context, arm.pose.bones[name], original_pole)
        s["_verify_snap"](arm, {name: original_pole}, "Foot Follow calibration mismatch")
        s["_verify_snap"](arm, before_native, "Guide install moved AE native bones")
        s["_verify_snap"](arm, {s["IK_HAND"]: original_goal,
                                  s["IK_POLE"]: original_pole}, "Guide install moved controls")
        data = _manifest(arm)
        # When installing onto an E2.3 limb that is ALREADY in IK, keep
        # its original manual pole active until explicit ON/snap. This is
        # necessary to make the migration itself pose-neutral and auditable.
        already_ik = data[s["KEY"]]["mode"] == "IK"
        data[s["KEY"]]["foot_follow"] = {
            "version": VERSION, "guide": name, "preferred": not already_ik,
            "enabled": False, "mechanism": "FOOT_TRANSLATION_FOLLOW_NO_INHERITED_ROTATION"}
        arm[ae_biped.MANIFEST_KEY] = json.dumps(data, sort_keys=True)
        # Main audit needs to count our helper as generated, not as source.
        errors = audit_faults(arm, s)
        if errors or ae_biped.audit(arm)["status"] != "COMPLETE":
            raise RuntimeError("Foot Follow installation audit: " + str(errors))
        return True
    except Exception as exc:
        failures = []
        try:
            arm[ae_biped.MANIFEST_KEY] = original_manifest
            if made:
                ae_biped._remove_own_bones(context, arm, names=(name,))
        except Exception as rollback:
            failures.append(str(rollback))
        if failures:
            raise RuntimeError("Foot Follow install rollback INCOMPLETE: " +
                               str(exc) + "; " + "; ".join(failures)) from exc
        raise
    finally:
        ae_biped._restore_context(context, arm, active_obj, selected, old_mode)


def match_guide_to_pole(context, arm, s):
    """Align follow guide to the fixed knee pole after FK->IK control snap."""
    if _record(arm, s) is None:
        return
    pole = arm.pose.bones[s["IK_POLE"]].matrix.copy()
    s["_set_pose"](context, arm.pose.bones[guide_name(s)], pole)
    s["_verify_snap"](arm, {guide_name(s): pole}, "Foot Follow guide/pole mismatch")


def apply_mode(arm, data, s, mode):
    """Called inside the leg switch before saving its manifest."""
    row = data[s["KEY"]].get("foot_follow")
    if row is None:
        return
    on = mode == "IK" and row["preferred"]
    _ik_constraint(arm, s).pole_subtarget = guide_name(s) if on else s["IK_POLE"]
    row["enabled"] = bool(on)


def set_preference(context, arm, s, enabled):
    """UI ON/OFF while FK or IK; preserve evaluated native pose on change."""
    _no_animation_or_edit(arm)
    if s["_audit"](arm)["status"] != "COMPLETE":
        raise RuntimeError("AE leg IK incomplete; Foot Follow refused")
    if enabled and _record(arm, s) is None:
        install(context, arm, s)
    record = _record(arm, s)
    # Explicit OFF before first IK: store a valid guide anyway, so the
    # preference persists across future snaps and UI redraws.
    if record is None:
        install(context, arm, s)
        record = _record(arm, s)
    mode = _manifest(arm)[s["KEY"]]["mode"]
    if (record["preferred"] == bool(enabled) and
            record["enabled"] == (mode == "IK" and bool(enabled))):
        return {"preferred": bool(enabled), "already": True}
    solver = _ik_constraint(arm, s)
    previous_solver = solver.pole_subtarget
    before_manifest = str(arm[ae_biped.MANIFEST_KEY])
    guide = arm.pose.bones[guide_name(s)]
    manual = arm.pose.bones[s["IK_POLE"]]
    saved = {pb.name: pb.matrix_basis.copy() for pb in (guide, manual)}
    before_native = {n: arm.pose.bones[n].matrix.copy() for n in s["POSE_JOINTS"]}
    try:
        mode = _manifest(arm)[s["KEY"]]["mode"]
        if mode == "IK":
            # The currently driving pole remains the reference. Reposition
            # the *inactive* pole to it BEFORE changing the solver subtarget.
            live = arm.pose.bones[previous_solver].matrix.copy()
            inactive = guide if enabled else manual
            s["_set_pose"](context, inactive, live)
        data = _manifest(arm)
        data[s["KEY"]]["foot_follow"]["preferred"] = bool(enabled)
        apply_mode(arm, data, s, mode)
        arm[ae_biped.MANIFEST_KEY] = json.dumps(data, sort_keys=True)
        context.view_layer.update()
        s["_verify_snap"](arm, before_native, "Changing Knee Follow moved the character")
        if audit_faults(arm, s) or ae_biped.audit(arm)["status"] != "COMPLETE":
            raise RuntimeError("Knee Follow switching invalidated AE audit")
        return {"preferred": bool(enabled), "mode": mode}
    except Exception:
        solver.pole_subtarget = previous_solver
        arm[ae_biped.MANIFEST_KEY] = before_manifest
        for n, basis in saved.items():
            arm.pose.bones[n].matrix_basis = basis
        context.view_layer.update()
        raise


class SR_OT_AE_LegFootFollow(Operator):
    bl_idname = "saberrig.ae_leg_foot_follow"
    bl_label = "AE Leg Knee Foot Follow"
    bl_description = "Enable translation-only Knee Pole following the IK Foot target; OFF uses the independent Pole Target"
    bl_options = {"REGISTER", "UNDO"}
    side: bpy.props.EnumProperty(items=(("L", "Left", "Left leg"), ("R", "Right", "Right leg")))
    enabled: bpy.props.BoolProperty(default=True)

    def execute(self, context):
        # Local import avoids a cyclic dependency with the individual IK modules.
        from . import ae_ik_leg_l, ae_ik_leg_r
        s = (ae_ik_leg_l if self.side == "L" else ae_ik_leg_r).__dict__
        arm = engine._resolve_armature(context, context.scene.saberrig_state)
        try:
            result = set_preference(context, arm, s, self.enabled)
        except Exception as exc:
            self.report({"ERROR"}, "AE Knee Foot Follow rejected: " + str(exc)[:450])
            return {"CANCELLED"}
        self.report({"INFO"}, "AE " + self.side + " knee pole: " +
                    ("Foot Follow" if result["preferred"] else "Original Pole"))
        return {"FINISHED"}
