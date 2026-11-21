# SPDX-License-Identifier: GPL-2.0-or-later
# -*- coding: utf-8 -*-
"""Blender registration and lifecycle management for SaberRig.

Responsibilities:
    * Publish the add-on metadata and register the existing operators/panels.
    * Own the ``Scene.saberrig_state`` RNA pointer and optional header icon.
    * Register frame/load/undo/redo handlers and roll them back in reverse order.

Rig creation lives in :mod:`engine` and the Endfield modules; sidebar drawing
lives in :mod:`ui`. Registration order and operator identifiers are compatibility
contracts, because previously saved Blender scenes may depend on them.
"""

bl_info = {
    "name": "SaberRig",
    "author": "SaberRig Project",
    "version": (1, 0, 5),
    "blender": (5, 1, 1),
    "location": "View3D > Sidebar > SaberRig",
    "description": "Multi-profile rigging, IK/FK animation and FGO Arcade preparation",
    "category": "Rigging",
}

import bpy
from . import engine, ae_biped, ae_ik, ae_ik_arm_r, ae_ik_leg_l, ae_ik_leg_r, ae_leg_foot_follow, ae_fx_attachments, ae_auto_attachments, ui

# Registration order is intentional: property groups/operators must exist before
# Blender registers panels that reference them. Do not rename existing classes or
# operator identifiers solely for presentation purposes.
CLASSES = (
    engine.SR_MappingItem,
    engine.SR_CountItem,
    engine.SR_MessageItem,
    engine.SR_AnalysisState,
    engine.SR_OT_AnalyzeRig,
    engine.SR_OT_AnalyzeFGOArcade,
    engine.SR_OT_ExportFGOAudit,
    engine.SR_OT_FGO35Calibrate,
    engine.SR_OT_FGO35StartPreview,
    engine.SR_OT_FGO35RemovePreview,
    engine.SR_OT_FGO35TestPose,
    engine.SR_OT_FGO35RestorePose,
    engine.SR_OT_FGO35ExportCalibration,
    engine.SR_OT_FGO36BuildControls,
    engine.SR_OT_FGO36Switch,
    engine.SR_OT_FGO36Reset,
    engine.SR_OT_FGO36Remove,
    engine.SR_OT_FGO363SetVisibility,
    engine.SR_OT_FGO363RestoreVisibility,
    engine.SR_OT_FGO364FindIKControl,
    engine.SR_OT_FGO365MotionProbe,
    engine.SR_OT_FGO367AuditCurrentIK,
    engine.SR_OT_FGO365ExportMotionReport,
    engine.SR_OT_FGO368AuditLayers,
    engine.SR_OT_FGO368ReachSweep,
    engine.SR_OT_FGO368ExportReport,
    engine.SR_OT_FGO369AuxiliaryAudit,
    engine.SR_OT_FGO369TransferSweep,
    engine.SR_OT_FGO369ExportReport,
    engine.SR_OT_FGO3610StartPreview,
    engine.SR_OT_FGO3610SetInfluence,
    engine.SR_OT_FGO3610Sweep,
    engine.SR_OT_FGO3610RemovePreview,
    engine.SR_OT_FGO3610ExportReport,
    engine.SR_OT_FGO3611EnableBoth,
    engine.SR_OT_FGO3611BuildRight,
    engine.SR_OT_FGO3611SetRight,
    engine.SR_OT_FGO3611RemoveRight,
    engine.SR_OT_FGO3611BilateralSweep,
    engine.SR_OT_FGO3611SwitchQA,
    engine.SR_OT_FGO3611Export,
    engine.SR_OT_FGO3612StructureAudit,
    engine.SR_OT_FGO3612LegSweep,
    engine.SR_OT_FGO3612Export,
    engine.SR_OT_FGO3613Build,
    engine.SR_OT_FGO3613Influence,
    engine.SR_OT_FGO3613Sweep,
    engine.SR_OT_FGO3613PelvisPreview,
    engine.SR_OT_FGO3613LegSwitch,
    engine.SR_OT_FGO3613Remove,
    engine.SR_OT_FGO3613Export,
    engine.SR_OT_FGO36131Build,
    engine.SR_OT_FGO36131Set,
    engine.SR_OT_FGO36131Sweep,
    engine.SR_OT_FGO36131Remove,
    engine.SR_OT_FGO36131Export,
    engine.SR_OT_FGO361321GuardInspect,
    engine.SR_OT_FGO361321GuardRecover,
    engine.SR_OT_FGO36132Build,
    engine.SR_OT_FGO36132Mode,
    engine.SR_OT_FGO36132Sweep,
    engine.SR_OT_FGO36132Remove,
    engine.SR_OT_FGO36132Export,
    engine.SR_OT_FGO36143Repair,
    engine.SR_OT_FGO36143Diagnose,
    engine.SR_OT_FGO36143Export,
    engine.SR_OT_FGO36142Build,
    engine.SR_OT_FGO36142QA,
    engine.SR_OT_FGO36142Remove,
    engine.SR_OT_FGO36142Export,
    engine.SR_OT_FGO3614Build,
    engine.SR_OT_FGO3614Select,
    engine.SR_OT_FGO3614QA,
    engine.SR_OT_FGO3614Remove,
    engine.SR_OT_FGO3614Export,
    engine.SR_OT_ClearAnalysis,
    engine.SR_OT_ExportAnalysis,
    engine.SR_OT_RunRigQA,
    engine.SR_OT_ExportRigQA,
    engine.SR_OT_BakeToSource,
    engine.SR_OT_BakeCleanExport,
    engine.SR_OT_SelectMappedBone,
    engine.SR_OT_FGO3615Prepare,
    engine.SR_OT_PrepareFoundation,
    engine.SR_OT_BuildBodyControls,
    engine.SR_OT_BuildFingerControls,
    engine.SR_OT_ResetFingerControls,
    engine.SR_OT_BuildArmsFK,
    engine.SR_OT_BuildArmsIK,
    engine.SR_OT_SetArmMode,
    engine.SR_OT_SetArmJointLimits,
    engine.SR_OT_SetShoulderAssist,
    engine.SR_OT_BuildAnimationSpaces,
    engine.SR_OT_SetAnimationSpace,
    engine.SR_OT_BuildLegsIK,
    engine.SR_OT_SetLegMode,
    engine.SR_OT_SetLegJointLimits,
    engine.SR_OT_SetLegBendMode,
    engine.SR_OT_SetKneePoleSpace,
    engine.SR_OT_ResetFootRoll,
    engine.SR_OT_BuildSecondaryMotion,
    engine.SR_OT_BuildUmaWiggle,
    engine.SR_OT_ToggleWiggle,
    engine.SR_OT_ToggleWiggleFamily,
    engine.SR_OT_ResetWiggle,
    engine.SR_OT_RemoveWiggle,
    engine.SR_OT_SetRigView,
    engine.SR_OT_RefreshControlVisuals,
    engine.SR_OT_RemoveSetup,
    ae_biped.SR_OT_AE_Biped_Audit,
    ae_biped.SR_OT_AE_Biped_Build,
    ae_biped.SR_OT_AE_Biped_RestoreBaseline,
    ae_biped.SR_OT_AE_Biped_Remove,
    ae_biped.SR_OT_AE_Biped_OrganizeCollections,
    ae_biped.SR_OT_AE_Biped_ShowControls,
    ae_biped.SR_OT_AE_Biped_ToggleAuxiliary,
    ae_biped.SR_OT_AE_Biped_ToggleSource,
    ae_ik.SR_OT_AE_IK_L_Build,
    ae_ik.SR_OT_AE_IK_L_Switch,
    ae_ik.SR_OT_AE_IK_L_ResetPole,
    ae_ik.SR_OT_AE_IK_L_Remove,
    ae_ik_arm_r.SR_OT_AE_IK_R_Build,
    ae_ik_arm_r.SR_OT_AE_IK_R_Switch,
    ae_ik_arm_r.SR_OT_AE_IK_R_ResetPole,
    ae_ik_arm_r.SR_OT_AE_IK_R_Remove,
    ae_ik_leg_l.SR_OT_AE_IK_LL_Build,
    ae_ik_leg_l.SR_OT_AE_IK_LL_Switch,
    ae_ik_leg_l.SR_OT_AE_IK_LL_ResetPole,
    ae_ik_leg_l.SR_OT_AE_IK_LL_Remove,
    ae_ik_leg_r.SR_OT_AE_IK_LR_Build,
    ae_ik_leg_r.SR_OT_AE_IK_LR_Switch,
    ae_ik_leg_r.SR_OT_AE_IK_LR_ResetPole,
    ae_ik_leg_r.SR_OT_AE_IK_LR_Remove,
    ae_leg_foot_follow.SR_OT_AE_LegFootFollow,
    # Older Si attachment operators remain registered so existing files can be
    # inspected or restored. The primary Endfield UI uses the automatic workflow;
    # these operators are not promoted as a second, competing setup path.
    ae_fx_attachments.SR_OT_AE_SiAttachmentAudit,
    ae_fx_attachments.SR_OT_AE_SiAttachmentApply,
    ae_fx_attachments.SR_OT_AE_SiAttachmentRestore,
    ae_auto_attachments.SR_OT_AE_AutoAttachmentsSync,
    ui.SR_PT_MainPanel,
    ui.SR_PT_GeneralMMDPanel,
    ui.SR_PT_UmaMusumePanel,
    ui.SR_PT_FGOArcadePanel,
    ui.SR_PT_RigControlsPanel,
    ui.SR_PT_EndfieldPanel,
    ui.SR_PT_FGOPosePanel,
    ui.SR_PT_FGOQualityPanel,
)


# Transaction log for this registration attempt only. On failure or disable,
# undo owned resources in reverse order; never unregister another add-on's
# handler, class, or icon merely because it has a similar name.
_REGISTERED_STEPS = []


def _undo_registered_steps():
    failures = []
    for kind, value in reversed(tuple(_REGISTERED_STEPS)):
        try:
            if kind == "class":
                bpy.utils.unregister_class(value)
            elif kind == "scene_property":
                delattr(bpy.types.Scene, value)
            elif kind == "icon":
                ui._unregister_brand_icon()
            elif kind == "handler":
                # The original frame handler was stored as a raw function;
                # newly tracked lifecycle handlers store (collection, fn).
                # Accept both for safe rollback and injected E1 test fixtures.
                collection, callback = value if isinstance(value, tuple) else ("frame_change_post", value)
                handlers = getattr(bpy.app.handlers, collection)
                # Blender or another extension may already have removed it.
                if callback in handlers:
                    handlers.remove(callback)
            else:
                raise RuntimeError(f"Unknown registration step: {kind}")
        except Exception as exc:
            failures.append(f"{kind}: {exc}")
        else:
            _REGISTERED_STEPS.remove((kind, value))
    return failures


def register():
    if _REGISTERED_STEPS:
        raise RuntimeError("SaberRig is already registered or cleanup is incomplete")
    if hasattr(bpy.types.Scene, "saberrig_state"):
        raise RuntimeError("Scene.saberrig_state already exists; refusing to replace it")
    try:
        for cls in CLASSES:
            bpy.utils.register_class(cls)
            _REGISTERED_STEPS.append(("class", cls))
        bpy.types.Scene.saberrig_state = bpy.props.PointerProperty(type=engine.SR_AnalysisState)
        _REGISTERED_STEPS.append(("scene_property", "saberrig_state"))

        # The bundled preview image is optional. Claim it only when this call
        # actually created it, so cleanup never touches an existing preview.
        had_icon = ui._BRAND_PREVIEWS is not None
        ui._register_brand_icon()
        if not had_icon and ui._BRAND_PREVIEWS is not None:
            _REGISTERED_STEPS.append(("icon", None))

        handler = engine._saberrig_wiggle_frame_change
        handlers = bpy.app.handlers.frame_change_post
        if handler not in handlers:
            handlers.append(handler)
            _REGISTERED_STEPS.append(("handler", handler))

        # Wiggle caches contain runtime RNA references that can become stale
        # after loading a file or using Undo/Redo. Invalidation handlers share
        # the same transaction log as class and icon registration.
        for collection in ("load_post", "undo_post", "redo_post"):
            callbacks = getattr(bpy.app.handlers, collection, None)
            if callbacks is None:
                continue  # Supports reduced test environments and older hosts.
            reset = engine._saberrig_wiggle_invalidate_runtime
            if reset not in callbacks:
                callbacks.append(reset)
                _REGISTERED_STEPS.append(("handler", (collection, reset)))
    except Exception as exc:
        failures = _undo_registered_steps()
        if failures:
            raise RuntimeError(
                "SaberRig registration failed and rollback is incomplete: "
                + "; ".join(failures)
            ) from exc
        raise


def unregister():
    failures = _undo_registered_steps()
    if failures:
        raise RuntimeError("SaberRig cleanup is incomplete: " + "; ".join(failures))
    engine._WIGGLE_STATE.clear()
    engine._WIGGLE_CACHE.clear()
    getattr(engine, "_WIGGLE_COLLIDER_CACHE", {}).clear()
    engine._ANALYSIS_CACHE.clear()
    engine._FGO35_CALIBRATION_CACHE.clear()


if __name__ == '__main__':
    register()
