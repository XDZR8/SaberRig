# SPDX-License-Identifier: GPL-2.0-or-later
# -*- coding: utf-8 -*-
"""SaberRig sidebar: profile navigation, status and operator controls.

Every supported game profile remains visible for discoverability. For an
incompatible selected armature, the corresponding section draws a read-only
feature preview rather than exposing a callable rigging operator.

This module owns interface presentation only. Geometry, constraint, FK/IK,
manifest and restoration changes belong to the underlying rigging modules.
Keep user-visible names descriptive; retain registered ``bl_idname`` values.
"""

import os
import json
import bpy
import bpy.utils.previews
from bpy.types import Panel
from . import ae_biped, ae_ik, ae_ik_arm_r, ae_ik_leg_l, ae_ik_leg_r, ae_leg_foot_follow, ae_fx_attachments, ae_auto_attachments
from .engine import (
    SR_SUPPORTED_PROFILE_IDS,
    _FGO3615_REPORT_KEY,
    _detect_profile,
    _fgo36132_manifest,
    _fgo3615_state,
    _fgo36_manifest,
    _fgo_is_candidate,
    _foundation_is_prepared,
    _resolve_armature,
    _secondary_preset_for_profile,
)


# The optional native Blender preview icon is not a rig-preparation dependency.
# The image is stored in the installed add-on package.
_BRAND_PREVIEWS = None
_BRAND_ICON_ID = 0


def _register_brand_icon():
    global _BRAND_PREVIEWS, _BRAND_ICON_ID
    if _BRAND_PREVIEWS is not None:
        return
    previews = None
    try:
        asset_path = os.path.join(os.path.dirname(__file__), 'assets', 'SaberRig_icon.png')
        if not os.path.isfile(asset_path):
            return
        previews = bpy.utils.previews.new()
        previews.load('saberrig_logo', asset_path, 'IMAGE')
        _BRAND_ICON_ID = int(previews['saberrig_logo'].icon_id)
        _BRAND_PREVIEWS = previews
    except Exception as exc:
        if previews is not None:
            try:
                bpy.utils.previews.remove(previews)
            except Exception:
                pass
        _BRAND_PREVIEWS = None
        _BRAND_ICON_ID = 0
        print('SaberRig: optional header icon unavailable:', exc)


def _unregister_brand_icon():
    global _BRAND_PREVIEWS, _BRAND_ICON_ID
    previews = _BRAND_PREVIEWS
    _BRAND_PREVIEWS = None
    _BRAND_ICON_ID = 0
    if previews is not None:
        try:
            bpy.utils.previews.remove(previews)
        except Exception:
            pass


def _current_profile(context, arm):
    """Read profile without assuming that another armature's analysis is current."""
    if arm is None:
        return None
    if _fgo_is_candidate(arm):
        return 'FGO_ARCADE'
    state = context.scene.saberrig_state
    registered = str(arm.get('saberrig_profile_id', '')).strip()
    if registered in SR_SUPPORTED_PROFILE_IDS or registered == 'GENERIC_HUMANOID':
        return registered
    if bool(state.analyzed) and state.armature_name == arm.name:
        return state.profile_id or 'GENERIC_HUMANOID'
    # Profile identification uses bone names until a full analysis is requested; the
    # active profile must remain visible before pressing Analyze/Prepare.
    try:
        profile, _score, _evidence, _scores = _detect_profile([b.name for b in arm.data.bones])
        return str(profile['id'])
    except Exception:
        return 'GENERIC_HUMANOID'


def _profile_label(profile_id):
    return {
        'FGO_ARCADE': 'FGO Arcade',
        'UMA_MUSUME_MMD_HYBRID': 'Uma Musume',
        'MMD_STANDARD': 'General / MMD',
        'GENERIC_HUMANOID': 'General / MMD',
        'ENDFIELD_SEGMENTED': 'Endfield (Segmented)',
        'ENDFIELD_BIPED': 'Endfield (Biped)',
    }.get(profile_id, 'General / MMD')


def _profile_preview(layout, profile_id, required_profile, features):
    """Describe another game's tools without exposing incompatible operators.

    Profile panels remain discoverable regardless of the selected armature.
    The preview is informational only: it deliberately does not instantiate
    rigging operators, run audits, or read profile-specific rig manifests.
    """
    notice = layout.box()
    notice.label(text='Available for: ' + required_profile, icon='INFO')
    if profile_id is None:
        notice.label(text='Select a compatible armature to enable these tools')
    else:
        notice.label(text='Current rig: ' + _profile_label(profile_id))
        notice.label(text='Select a compatible armature to enable these tools')
    preview = layout.column(align=True)
    preview.enabled = False
    for title, icon in features:
        preview.label(text=title, icon=icon)


# Public feature previews for incompatible profiles. This is a UI-only index:
# entries must match real tools, and must never become clickable shortcuts that
# bypass per-profile operator guards.
_GENERAL_FEATURES = (
    ('Body / COG controls', 'CON_TRACKTO'),
    ('Arms FK / IK and Legs IK', 'CON_KINEMATIC'),
    ('Finger controls and animation spaces', 'HAND'),
    ('Secondary motion', 'PHYSICS'),
    ('IK / FK switching and rig views', 'BONE_DATA'),
)
_UMA_FEATURES = (
    ('Uma / MMD hybrid rig controls', 'BONE_DATA'),
    ('Arms and legs FK / IK', 'CON_KINEMATIC'),
    ('Finger and thumb controls', 'HAND'),
    ('Secondary motion', 'PHYSICS'),
    ('Animation spaces and rig views', 'ARMATURE_DATA'),
)
_ENDFIELD_FEATURES = (
    ('AE Biped FK foundation / preflight', 'ARMATURE_DATA'),
    ('Left and right arm FK / IK', 'CONSTRAINT_BONE'),
    ('Left and right leg FK / IK', 'CON_KINEMATIC'),
    ('Foot Follow and knee poles', 'EMPTY_ARROWS'),
    ('Rigid attachments and bone collections', 'GROUP_BONE'),
    ('Restore / remove AE controls', 'LOOP_BACK'),
    ('Segmented rig: generic builders', 'BONE_DATA'),
)
_FGO_FEATURES = (
    ('Skeleton and IK / FK preparation', 'ARMATURE_DATA'),
    ('Arms, thighs and auxiliary bindings', 'BONE_DATA'),
    ('Knee Follow and pole guides', 'CON_KINEMATIC'),
    ('Squat and torso lean', 'CONSTRAINT_BONE'),
)
_FGO_ANIMATION_FEATURES = (
    ('Left / right arm FK / IK', 'HAND'),
    ('Left / right leg FK / IK', 'CON_KINEMATIC'),
    ('Find hand and foot IK controls', 'VIEWZOOM'),
    ('Controls in front', 'HIDE_OFF'),
)
_FGO_POSE_FEATURES = (
    ('Select pelvis / squat and lean', 'BONE_DATA'),
    ('Knee pole: Foot Follow / Original Pole', 'CON_KINEMATIC'),
    ('Verify torso lean', 'CHECKMARK'),
)
_FGO_QA_FEATURES = (
    ('Analyze FGO / bridge report', 'VIEWZOOM'),
    ('Knee and squat QA', 'CON_KINEMATIC'),
    ('Lean verification and JSON reports', 'EXPORT'),
)

def _generic_builders(layout, arm, profile_name, experimental=False):
    """Draw shared rigging controls only for a supported, prepared armature.

    Operator execution still enforces its own preconditions; hiding or disabling
    a button is not a substitute for validation inside the engine.
    """
    prepared = _foundation_is_prepared(arm)
    status = layout.box()
    status.label(text=profile_name + ' / Shared Rigging Tools', icon='ARMATURE_DATA')
    status.label(text='Foundation ready' if prepared else 'Run Prepare SaberRig first',
                 icon='CHECKMARK' if prepared else 'INFO')
    if experimental:
        status.label(text='Generic mapping: inspect deformation after each build', icon='ERROR')
    tools = status.column(align=True)
    tools.enabled = bool(prepared)
    for flag, op_id, title, icon in (
        ('saberrig_body_built', 'saberrig.build_body_controls', 'Body / COG', 'CON_TRACKTO'),
        ('saberrig_arms_fk_built', 'saberrig.build_arms_fk', 'Arms FK', 'BONE_DATA'),
        ('saberrig_arms_ik_built', 'saberrig.build_arms_ik', 'Arms IK', 'CON_KINEMATIC'),
        ('saberrig_legs_ik_built', 'saberrig.build_legs_ik', 'Legs IK', 'CON_KINEMATIC'),
        ('saberrig_fingers_built', 'saberrig.build_finger_controls', 'Finger Controls', 'HAND'),
        ('saberrig_animation_spaces_built', 'saberrig.build_animation_spaces', 'Animation Spaces', 'CON_CHILDOF'),
    ):
        row = tools.row(align=True)
        installed = bool(arm.get(flag, False))
        row.operator(op_id, text=('Refresh ' if installed else 'Build ') + title,
                     icon='CHECKMARK' if installed else icon)

    if arm.get('saberrig_arms_ik_built', False):
        sub = status.box()
        sub.label(text='Arm IK / FK', icon='HAND')
        for side in ('L', 'R'):
            mode = str(arm.get('saberrig_arm_mode_' + side, 'FK'))
            row = sub.row(align=True)
            row.label(text='Arm ' + side)
            for m in ('FK', 'IK'):
                op = row.operator('saberrig.set_arm_mode', text=m, depress=mode==m)
                op.side, op.mode = side, m

    if arm.get('saberrig_legs_ik_built', False):
        sub = status.box()
        sub.label(text='Leg IK / FK', icon='CON_KINEMATIC')
        for side in ('L', 'R'):
            mode = str(arm.get('saberrig_leg_mode_' + side, 'FK'))
            row = sub.row(align=True)
            row.label(text='Leg ' + side)
            for m in ('FK', 'IK'):
                op = row.operator('saberrig.set_leg_mode', text=m, depress=mode==m)
                op.side, op.mode = side, m

    row = status.row(align=True)
    row.enabled = bool(prepared)
    op = row.operator('saberrig.set_rig_view', text='Controls Only', icon='HIDE_OFF')
    op.mode = 'CONTROLS'
    op = row.operator('saberrig.set_rig_view', text='Source View', icon='HIDE_ON')
    op.mode = 'SOURCE'


def _secondary(layout, arm, profile_name):
    box = layout.box()
    box.label(text='Secondary Motion', icon='PHYSICS')
    preset = _secondary_preset_for_profile(_current_profile_from_arm(arm))
    box.label(text='Preset: ' + str(preset.get('name', 'Semantic')))
    row = box.row(align=True)
    row.enabled = _foundation_is_prepared(arm)
    built = bool(arm.get('saberrig_wiggle_built', False))
    row.operator('saberrig.build_secondary_motion',
                 text='Rebuild Secondary' if built else 'Build Secondary', icon='PHYSICS')
    if built:
        on = bool(arm.get('saberrig_wiggle_enabled', True))
        row = box.row(align=True)
        for flag, label in ((True, 'On'), (False, 'Off')):
            op = row.operator('saberrig.toggle_wiggle', text=label, depress=on==flag)
            op.enabled = flag
        row.operator('saberrig.reset_wiggle', text='Reset', icon='RECOVER_LAST')


def _current_profile_from_arm(arm):
    return str(arm.get('saberrig_profile_id', 'GENERIC_HUMANOID'))

def _confidence_icon(value):
    if value >= 0.85:
        return 'CHECKMARK'
    if value >= 0.60:
        return 'INFO'
    return 'ERROR'

def _find_mapping(state, role):
    for item in state.mappings:
        if item.role == role:
            return item
    return None


class SR_PT_MainPanel(Panel):
    bl_label = 'SaberRig'
    bl_order = 0
    bl_idname = 'SR_PT_main'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'SaberRig'

    def draw(self, context):
        layout = self.layout
        state = context.scene.saberrig_state
        arm = _resolve_armature(context, state)
        profile = _current_profile(context, arm)
        fgo = bool(arm and profile == 'FGO_ARCADE')
        # Keep one complete AE audit per MainPanel draw; no stale cross-draw cache.
        ae_state = (ae_biped.audit(arm).get('status')
                    if arm is not None and profile == 'ENDFIELD_BIPED' else None)

        box = layout.box()
        head = box.row(align=True)
        if _BRAND_ICON_ID:
            head.template_icon(icon_value=_BRAND_ICON_ID, scale=5.0)
        else:
            head.label(text='', icon='ARMATURE_DATA')
        title = head.column(align=True)
        title.label(text='SaberRig', icon='ARMATURE_DATA')
        title.label(text='Version 1.0.5')
        title.label(text='Rigging Workspace')

        if not arm:
            box.label(text='Select an armature to begin', icon='INFO')
        else:
            info = box.column(align=True)
            info.label(text='Target: ' + arm.name[:48], icon='OUTLINER_OB_ARMATURE')
            info.label(text='Profile: ' + _profile_label(profile), icon='BONE_DATA')
            if fgo:
                status = _fgo3615_state(arm)
                info.label(text='FGO setup: %d/%d ready' %
                           (status.get('count', 0), status.get('total', 8)),
                           icon='CHECKMARK' if status.get('ready') else 'INFO')
            elif profile == 'ENDFIELD_BIPED':
                ae_status = ae_state
                info.label(text='AE Biped: ' + str(ae_status),
                           icon='CHECKMARK' if ae_status == 'COMPLETE' else 'INFO')
            else:
                ready = _foundation_is_prepared(arm)
                info.label(text='Foundation: ' + ('Prepared' if ready else 'Not prepared'),
                           icon='CHECKMARK' if ready else 'INFO')

        row = box.row()
        row.scale_y = 1.4
        row.enabled = bool(arm)
        if fgo:
            ready = _fgo3615_state(arm).get('ready', False)
            row.operator('saberrig.fgo3615_prepare',
                         text='Recheck / Resume FGO' if ready else 'Prepare SaberRig',
                         icon='CHECKMARK' if ready else 'ARMATURE_DATA')
        elif profile == 'ENDFIELD_BIPED':
            status = ae_state
            if status == 'NOT_INSTALLED':
                row.operator('saberrig.ae_biped_build', text='Build AE Biped Controls', icon='BONE_DATA')
            else:
                row.operator('saberrig.ae_biped_audit', text='Audit AE Biped Controls', icon='VIEWZOOM')
        else:
            ready = bool(arm and _foundation_is_prepared(arm))
            row.operator('saberrig.prepare_foundation',
                         text='Recheck Foundation' if ready else 'Prepare SaberRig',
                         icon='CHECKMARK' if ready else 'ARMATURE_DATA')
        row = layout.row(align=True)
        row.operator('saberrig.analyze_rig', text='Analyze', icon='VIEWZOOM')
        row.operator('saberrig.run_rig_qa', text='Validate', icon='CHECKMARK')
        if fgo:
            status = _fgo3615_state(arm)
            if status.get('error'):
                col = layout.column(align=True)
                col.alert = True
                col.label(text='FGO preparation needs attention', icon='ERROR')
            try:
                result = json.loads(str(arm.get(_FGO3615_REPORT_KEY, '{}')))
                if result.get('status') == 'INCOMPLETE':
                    row = layout.row()
                    row.alert = True
                    row.label(text='Stopped: ' + str(result.get('failed_stage', '?'))[:40], icon='ERROR')
            except Exception:
                pass
        # Profile panels are organized below the main preparation controls.


class SR_PT_GeneralMMDPanel(Panel):
    bl_label = 'General / MMD'
    bl_order = 10
    bl_idname = 'SR_PT_general_mmd'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'SaberRig'
    bl_parent_id = 'SR_PT_main'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        arm = _resolve_armature(context, context.scene.saberrig_state)
        profile = _current_profile(context, arm)
        if profile not in ('GENERIC_HUMANOID', 'MMD_STANDARD'):
            _profile_preview(layout, profile, 'General / MMD', _GENERAL_FEATURES)
            return
        _generic_builders(layout, arm, 'General / MMD')
        if _foundation_is_prepared(arm):
            _secondary(layout, arm, 'General / MMD')
        layout.label(text='Source weights and original hierarchy are preserved', icon='LOCKED')


class SR_PT_UmaMusumePanel(Panel):
    bl_label = 'Uma Musume'
    bl_order = 20
    bl_idname = 'SR_PT_uma_musume'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'SaberRig'
    bl_parent_id = 'SR_PT_main'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        arm = _resolve_armature(context, context.scene.saberrig_state)
        profile = _current_profile(context, arm)
        if profile != 'UMA_MUSUME_MMD_HYBRID':
            _profile_preview(layout, profile, 'Uma Musume', _UMA_FEATURES)
            return
        layout.label(text='Uma / MMD Hybrid | semantic controllers', icon='BONE_DATA')
        _generic_builders(layout, arm, 'Uma Musume', experimental=True)
        if _foundation_is_prepared(arm):
            _secondary(layout, arm, 'Uma Musume')
            layout.label(text='Finger curl and thumb calibration are UMA-aware', icon='INFO')


class SR_PT_EndfieldPanel(Panel):
    bl_label = 'Arknights: Endfield'
    bl_order = 40
    bl_idname = 'SR_PT_endfield'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'SaberRig'
    bl_parent_id = 'SR_PT_main'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        arm = _resolve_armature(context, context.scene.saberrig_state)
        profile = _current_profile(context, arm)
        if profile not in ('ENDFIELD_SEGMENTED', 'ENDFIELD_BIPED'):
            _profile_preview(layout, profile, 'Arknights: Endfield', _ENDFIELD_FEATURES)
            return
        layout.label(text='Detected: ' + _profile_label(profile), icon='CHECKMARK')
        if profile == 'ENDFIELD_BIPED':
            result = ae_biped.audit(arm)
            status = result.get('status', 'UNKNOWN')
            box = layout.box()
            box.label(text='AE Biped Control Foundation', icon='ARMATURE_DATA')
            box.label(text='State: ' + status,
                      icon='CHECKMARK' if status == 'COMPLETE' else 'INFO')
            if status == 'NOT_INSTALLED':
                box.label(text='25 FK controls / 23 independent mechanisms')
                box.label(text='Build auto-fixes validated rigid attachments', icon='LINKED')
                box.label(text='Source anatomy, twists and weights unchanged')
                row = box.row(align=True)
                row.operator('saberrig.ae_biped_audit', text='Preflight', icon='VIEWZOOM')
                row.operator('saberrig.ae_biped_build', text='Build AE Controls', icon='BONE_DATA')
            elif status == 'COMPLETE':
                # Existing Endfield rigs can carry attachment records created by
                # earlier releases. Expose recovery/sync without adding a second
                # character-specific control panel.
                bound = bool(arm.get(ae_fx_attachments.PILOT_KEY))
                if bound and ae_auto_attachments.is_owned(arm):
                    box.label(text='Rigid attachments: managed by AE Build', icon='CHECKMARK')
                elif bound:
                    box.label(text='Rigid attachments: legacy - adopt ownership', icon='INFO')
                    box.operator('saberrig.ae_auto_attachments_sync',
                        text='Sync Existing Rigid Attachments', icon='LINKED')
                elif ae_auto_attachments._present(context, arm):
                    box.operator('saberrig.ae_auto_attachments_sync',
                        text='Sync AE Rigid Attachments (Existing Rig)', icon='LINKED')
                else:
                    box.label(text='Rigid attachments: no verified migration', icon='INFO')
                pilot = ae_ik._audit(arm)
                ik_box = box.box()
                ik_box.label(text='Left Arm FK/IK (Experimental)', icon='CONSTRAINT_BONE')
                if pilot['status'] == 'NOT_INSTALLED':
                    ik_box.label(text='Working FK is preserved; IK starts disabled')
                    ik_box.operator('saberrig.ae_ik_l_build', text='Add Left Arm IK', icon='CONSTRAINT_BONE')
                elif pilot['status'] == 'COMPLETE':
                    ik_box.label(text='Current left-arm mode: ' + pilot['mode'])
                    row = ik_box.row(align=True)
                    op = row.operator('saberrig.ae_ik_l_switch',
                        text='Snap FK', icon='BONE_DATA')
                    op.target_mode = 'FK'
                    op = row.operator('saberrig.ae_ik_l_switch',
                        text='Snap IK', icon='CONSTRAINT_BONE')
                    op.target_mode = 'IK'
                    ik_box.operator('saberrig.ae_ik_l_reset_pole',
                        text='Reset Elbow Pole (Current Pose)', icon='LOOP_BACK')
                    ik_box.operator('saberrig.ae_ik_l_remove',
                        text='Remove Left Arm IK (in FK)', icon='TRASH')
                else:
                    ik_box.alert = True
                    ik_box.label(text='IK incomplete - recover from saved copy', icon='ERROR')
                # Each limb IK extension owns its own controls and manifest.
                # Building another limb must not reinstall or retune left-arm IK.
                for limb_title, module, stem, pole_title in (
                    ('Right Arm', ae_ik_arm_r, 'ae_ik_r', 'Elbow'),
                    ('Left Leg', ae_ik_leg_l, 'ae_ik_ll', 'Knee'),
                    ('Right Leg', ae_ik_leg_r, 'ae_ik_lr', 'Knee')):
                    ext = module._audit(arm)
                    limb_box = box.box()
                    limb_box.label(text=limb_title + ' FK/IK (Experimental)', icon='CONSTRAINT_BONE')
                    if ext['status'] == 'NOT_INSTALLED':
                        limb_box.operator('saberrig.' + stem + '_build',
                                          text='Add ' + limb_title + ' IK', icon='CONSTRAINT_BONE')
                    elif ext['status'] == 'COMPLETE':
                        limb_box.label(text='Mode: ' + ext['mode'])
                        row = limb_box.row(align=True)
                        op = row.operator('saberrig.' + stem + '_switch',
                                          text='Snap FK', icon='BONE_DATA')
                        op.target_mode = 'FK'
                        op = row.operator('saberrig.' + stem + '_switch',
                                          text='Snap IK', icon='CONSTRAINT_BONE')
                        op.target_mode = 'IK'
                        if pole_title == 'Knee':
                            # Foot Follow is a per-leg user preference. Keep its
                            # controls next to that leg's FK/IK mode switches.
                            prefer = ae_leg_foot_follow.preference(arm, module.__dict__)
                            opts = limb_box.row(align=True)
                            op = opts.operator('saberrig.ae_leg_foot_follow',
                                text='Foot Follow ON', icon='CON_KINEMATIC', depress=prefer)
                            op.side = 'L' if stem.endswith('_ll') else 'R'
                            op.enabled = True
                            op = opts.operator('saberrig.ae_leg_foot_follow',
                                text='Static Pole', icon='EMPTY_ARROWS', depress=not prefer)
                            op.side = 'L' if stem.endswith('_ll') else 'R'
                            op.enabled = False
                            if prefer:
                                limb_box.label(text='Activates automatically on Snap IK', icon='INFO')
                                limb_box.label(text='Manual Knee Pole inactive in Follow IK', icon='INFO')
                        limb_box.operator('saberrig.' + stem + '_reset_pole',
                                           text='Reset ' + pole_title + ' Pole', icon='LOOP_BACK')
                        limb_box.operator('saberrig.' + stem + '_remove',
                                           text='Remove ' + limb_title + ' IK (in FK)', icon='TRASH')
                    else:
                        limb_box.alert = True
                        limb_box.label(text='Incomplete IK; restore saved copy', icon='ERROR')
                row = box.row(align=True)
                row.operator('saberrig.ae_biped_audit', text='Audit', icon='CHECKMARK')
                row.operator('saberrig.ae_biped_restore_baseline', text='Restore Calibrated Pose', icon='LOOP_BACK')
                row = box.row(align=True)
                manifest = None
                try:
                    import json as _ae_json
                    manifest = _ae_json.loads(str(arm.get(ae_biped.MANIFEST_KEY, '{}')))
                except (TypeError, ValueError):
                    pass
                if manifest and 'collection_organization' in manifest:
                    vis_box = box.box()
                    vis_box.label(text='AE Bone Collections', icon='GROUP_BONE')
                    a = ae_biped.engine._find_bone_collection(arm.data, 'SaberRig AE Source Auxiliary')
                    d = ae_biped.engine._find_bone_collection(arm.data, 'SaberRig AE Source Deform')
                    if a is not None and d is not None:
                        vis_box.label(text='Auxiliary: ' + ('Visible' if a.is_visible else 'Hidden'))
                        vis_box.label(text='Original main: ' + ('Visible' if d.is_visible else 'Hidden'))
                    vis_box.operator('saberrig.ae_biped_show_controls', text='Show Controls Only', icon='HIDE_OFF')
                    vis_box.operator('saberrig.ae_biped_toggle_auxiliary', text='Show / Hide Auxiliary Bones', icon='BONE_DATA')
                    vis_box.operator('saberrig.ae_biped_toggle_source', text='Show / Hide Original Skeleton', icon='ARMATURE_DATA')
                else:
                    box.operator('saberrig.ae_biped_organize_collections',
                        text='Organize AE Bone Collections', icon='GROUP_BONE')
                row = box.row(align=True)
                row.operator('saberrig.ae_biped_remove', text='Remove AE Controls', icon='TRASH')
            else:
                box.alert = True
                box.label(text='Unexpected AE state: inspect before editing', icon='ERROR')
                box.operator('saberrig.ae_biped_audit', text='Inspect AE State', icon='VIEWZOOM')
            layout.label(text='Experimental AE controls: keep a backup .blend', icon='INFO')
            layout.label(text='Generic builders are locked while AE layer exists', icon='LOCKED')
        else:
            _generic_builders(layout, arm, 'Endfield Segmented', experimental=True)
            if _foundation_is_prepared(arm):
                _secondary(layout, arm, 'Endfield')
            layout.label(text='Segmented rig: generic builder only', icon='INFO')


class SR_PT_RigControlsPanel(Panel):
    bl_label = "Animation Controls"
    bl_order = 0
    bl_idname = "SR_PT_animation_controls"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "SaberRig"
    bl_parent_id = "SR_PT_fgo_arcade"

    def draw(self, context):
        layout = self.layout
        state = context.scene.saberrig_state
        arm = _resolve_armature(context, state)
        if not arm or not _fgo_is_candidate(arm):
            _profile_preview(layout, _current_profile(context, arm), 'FGO Arcade',
                             _FGO_ANIMATION_FEATURES)
            return
        manifest = _fgo36_manifest(arm)
        if not manifest:
            layout.label(text='Run Prepare SaberRig first', icon='INFO')
            preview = layout.column(align=True)
            preview.enabled = False
            for title, icon in _FGO_ANIMATION_FEATURES:
                preview.label(text=title, icon=icon)
            return
        chains = manifest.get('chains', {})
        for key, label, icon in (
            ('ARM_L','Left Arm','HAND'), ('ARM_R','Right Arm','HAND'),
            ('LEG_L','Left Leg','CON_KINEMATIC'), ('LEG_R','Right Leg','CON_KINEMATIC')):
            spec = chains.get(key)
            if not spec:
                continue
            mode = spec.get('mode', 'FK')
            row = layout.row(align=True)
            row.label(text=label, icon=icon)
            for target in ('FK','IK'):
                op = row.operator('saberrig.fgo36_switch', text=target, depress=mode==target)
                op.chain = key
                op.mode = target
        row = layout.row(align=True)
        for chain, name in (('ARM_L','Hand L'),('ARM_R','Hand R')):
            op = row.operator('saberrig.fgo364_find_ik_control', text=name, icon='VIEWZOOM')
            op.chain = chain
        row = layout.row(align=True)
        for chain, name in (('LEG_L','Foot L'),('LEG_R','Foot R')):
            op = row.operator('saberrig.fgo364_find_ik_control', text=name, icon='VIEWZOOM')
            op.chain = chain
        layout.prop(arm,'show_in_front',text='Controls In Front')


class SR_PT_FGOArcadePanel(Panel):
    bl_label = "FGO Arcade"
    bl_order = 30
    bl_idname = "SR_PT_fgo_arcade"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "SaberRig"
    bl_parent_id = "SR_PT_main"

    def draw(self, context):
        layout = self.layout
        arm = _resolve_armature(context, context.scene.saberrig_state)
        if not arm or not _fgo_is_candidate(arm):
            _profile_preview(layout, _current_profile(context, arm), 'FGO Arcade',
                             _FGO_FEATURES)
            return
        status = _fgo3615_state(arm)
        box = layout.box()
        box.label(text='Use Prepare SaberRig in the main header', icon='INFO')
        flags = status.get('flags', {})
        for title, keys in (
            ('Skeleton & IK/FK',('bridge','ikfk','leg_ik')),
            ('Arms & Thighs',('arms','thighs')),
            ('Knee Follow',('poles',)),
            ('Squat & Torso Lean',('squat','lean'))):
            passed = all(flags.get(key, False) for key in keys)
            row = box.row(align=True)
            row.label(text=title, icon='CHECKMARK' if passed else 'RADIOBUT_OFF')
            row.label(text='Ready' if passed else 'Pending')
        if not status.get('ready'):
            box.label(text='Prepare runs the verified setup in order', icon='INFO')
        box.label(text='Extension guard not part of the verified FGO preset', icon='LOCKED')


class SR_PT_FGOPosePanel(Panel):
    bl_label = "Pelvis, Knees & Pose"
    bl_order = 10
    bl_idname = "SR_PT_fgo_pose"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "SaberRig"
    bl_parent_id = "SR_PT_fgo_arcade"

    def draw(self, context):
        layout = self.layout
        arm = _resolve_armature(context, context.scene.saberrig_state)
        if not arm or not _fgo_is_candidate(arm):
            _profile_preview(layout, _current_profile(context, arm), 'FGO Arcade',
                             _FGO_POSE_FEATURES)
            return
        status = _fgo3615_state(arm)
        flags = status.get('flags', {})
        row = layout.row()
        row.enabled = bool(flags.get('squat'))
        row.scale_y = 1.25
        row.operator('saberrig.fgo3614_select', text='Select Pelvis  (lowerbody_jo)', icon='BONE_DATA')
        layout.label(text='Move pelvis down to squat; forward to lean', icon='INFO')
        guide = _fgo36132_manifest(arm)
        if guide:
            box = layout.box()
            box.label(text='Knee Plane', icon='CON_KINEMATIC')
            row = box.row(align=True)
            for target, label in (('FOLLOW','Foot Follow'),('STATIC','Original Pole')):
                op = row.operator('saberrig.fgo36132_mode', text=label,
                                  depress=(target=='FOLLOW' and bool(flags.get('poles'))))
                op.mode = target
            box.label(text='Foot Follow is the verified FGO preset', icon='INFO')
        row = layout.row(align=True)
        row.enabled = bool(flags.get('lean'))
        row.operator('saberrig.fgo36143_diagnose', text='Verify Torso Lean', icon='CHECKMARK')


class SR_PT_FGOQualityPanel(Panel):
    bl_label = "QA & Reports"
    bl_order = 20
    bl_idname = "SR_PT_fgo_quality"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "SaberRig"
    bl_parent_id = "SR_PT_fgo_arcade"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        arm = _resolve_armature(context, context.scene.saberrig_state)
        if not arm or not _fgo_is_candidate(arm):
            _profile_preview(layout, _current_profile(context, arm), 'FGO Arcade',
                             _FGO_QA_FEATURES)
            return
        status = _fgo3615_state(arm)
        flags = status.get('flags', {})
        row = layout.row(align=True)
        row.operator('saberrig.analyze_fgo_arcade', text='Analyze FGO', icon='VIEWZOOM')
        row.operator('saberrig.fgo35_export_calibration', text='Bridge Report', icon='EXPORT')
        row = layout.row()
        row.enabled = bool(flags.get('poles'))
        row.operator('saberrig.fgo36132_sweep', text='3-Path Knee QA (restore)', icon='CON_KINEMATIC')
        row = layout.row()
        row.enabled = bool(flags.get('squat'))
        row.operator('saberrig.fgo3614_qa', text='5-Step Squat QA (restore)', icon='CON_KINEMATIC')
        row = layout.row()
        row.enabled = bool(flags.get('lean'))
        row.operator('saberrig.fgo36143_diagnose', text='Live Lean QA (restore)', icon='CHECKMARK')
        row = layout.row(align=True)
        row.operator('saberrig.fgo36132_export', text='Knee JSON', icon='EXPORT')
        row.operator('saberrig.fgo36143_export', text='Lean JSON', icon='EXPORT')
        layout.label(text='Tests restore their original pose', icon='INFO')
