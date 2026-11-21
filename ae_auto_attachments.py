# SPDX-License-Identifier: GPL-2.0-or-later
"""Guarded Endfield rigid-attachment migration during biped Build.

The dispatcher is character-neutral but only the exact Si attachment mapping
has source evidence here. Unsupported or partial character matches must fail
closed: never guess a target bone for an unfamiliar mesh.

Existing Si attachment manifests remain readable and removable without
rebuilding the underlying FK/IK rig. Keep ownership records stable.
"""

import json

import bpy

from . import ae_fx_attachments as si

ATTACHMENT_VERSION = 1
PROFILE = 'SI_RIGID_LOD0_V1'
FOUNDATION_KEY = 'saberrig_ae_biped_foundation'
AUTO_KEY = 'auto_rigid_attachments'


def _present(context, arm):
    """Only objects truly parented to the selected armature can identify it."""
    return [name for name in si.TARGETS
            if (obj := bpy.data.objects.get(name)) is not None
            and obj.parent == arm
            and obj.name in context.scene.objects]


def preflight(context, arm):
    """Read-only gate, run BEFORE AE Build mutates the armature.

    Returns SKIP for unknown structures (including Wulfa). If part of the
    exact, already-audited Si attachment group exists, fail closed instead
    of leaving an incomplete eye repair silently.
    """
    names = _present(context, arm)
    if not names:
        return {'status': 'SKIP', 'profile': None, 'count': 0}
    if set(names) != set(si.TARGETS):
        missing = sorted(set(si.TARGETS).difference(names))
        raise RuntimeError('Incomplete AE verified rigid attachment profile; '
                           'missing for selected armature: ' + ', '.join(missing))
    if arm.get(si.PILOT_KEY):
        return {'status': 'APPLIED', 'profile': PROFILE, 'count': len(names)}
    if any(getattr(item, 'library', None) is not None or getattr(item, 'override_library', None) is not None
           for item in (arm, arm.data)):
        raise RuntimeError('Cannot repair linked or library-overridden AE armature')
    for bone in ('face_Head', 'Bip001', 'lod0', 'Mesh_all'):
        if arm.data.bones.get(bone) is None:
            raise RuntimeError('Missing verified AE attachment bone: ' + bone)
    for obj, _target in si._objects(context, arm):
        if obj.parent_type != 'BONE' or obj.parent_bone != 'lod0':
            raise RuntimeError('Unexpected original AE rigid binding on ' + obj.name)
        if obj.library is not None or obj.override_library is not None:
            raise RuntimeError('Linked AE mesh cannot be rebound: ' + obj.name)
        if obj.constraints or len(obj.vertex_groups) or any(m.type == 'ARMATURE' for m in obj.modifiers):
            raise RuntimeError('Non-rigid AE mesh cannot be rebound: ' + obj.name)
        ad = obj.animation_data
        if ad and (ad.action is not None or len(ad.nla_tracks) or len(ad.drivers)):
            raise RuntimeError('Animated AE rigid mesh cannot be rebound: ' + obj.name)
    return {'status': 'READY', 'profile': PROFILE, 'count': len(names)}


def _read_foundation(arm):
    """Validate the existing AE foundation without attempting to repair it."""
    raw = arm.get(FOUNDATION_KEY)
    if not raw:
        raise RuntimeError('AE foundation manifest missing; cannot own rigid attachments')
    try:
        foundation = json.loads(str(raw))
    except (ValueError, TypeError) as exc:
        raise RuntimeError('Invalid AE foundation manifest') from exc
    if not isinstance(foundation, dict) or foundation.get('component') != 'AE_BIPED_FOUNDATION':
        raise RuntimeError('Unexpected AE foundation component; refusing attachment ownership')
    return foundation


def _write_foundation(arm, foundation):
    """One controlled write point; injectible by QA to verify recovery."""
    arm[FOUNDATION_KEY] = json.dumps(foundation, sort_keys=True)


def _ownership_record(count):
    return {'version': ATTACHMENT_VERSION, 'profile': PROFILE, 'count': count}


def is_owned(arm):
    """Read-only UI discriminator: a pilot binding can be legacy but not Build-owned."""
    try:
        foundation = _read_foundation(arm)
    except RuntimeError:
        return False
    return (foundation.get(AUTO_KEY) == _ownership_record(len(si.TARGETS))
            and bool(arm.get(si.PILOT_KEY)))


def _adopt_manifest(arm, count):
    """Build and Sync share the exact same ownership commit."""
    foundation = _read_foundation(arm)
    record = _ownership_record(count)
    previous = foundation.get(AUTO_KEY)
    if previous is not None and previous != record:
        raise RuntimeError('Conflicting AE auto rigid attachment manifest; refusing overwrite')
    if previous is None:
        foundation[AUTO_KEY] = record
        _write_foundation(arm, foundation)


def apply(context, arm):
    """Install or adopt attachments and their AE ownership record together.

    Legacy E2.3 data is never rebound when already applied. A newly applied
    migration is rolled back if its AE ownership commit fails. Any rollback
    failure is reported explicitly instead of claiming a clean build.
    """
    result = preflight(context, arm)
    if result['status'] == 'SKIP':
        if _read_foundation(arm).get(AUTO_KEY):
            raise RuntimeError('AE ownership metadata exists without matching attachment structure')
        return result
    # Refuse to change anything if the AE metadata is already inconsistent.
    foundation = _read_foundation(arm)
    original_foundation_raw = arm[FOUNDATION_KEY]
    existing = foundation.get(AUTO_KEY)
    if existing is not None and existing != _ownership_record(len(si.TARGETS)):
        raise RuntimeError('Unknown AE rigid attachment ownership metadata')
    if result['status'] == 'APPLIED':
        if audit_faults(arm):
            raise RuntimeError('Existing AE rigid attachment manifest is inconsistent')
        try:
            _adopt_manifest(arm, len(si.TARGETS))
        except Exception:
            # Existing E2.3 bindings are not ours to restore/rebind.
            arm[FOUNDATION_KEY] = original_foundation_raw
            raise
        return {**result, 'created': False}

    selected = si._armature(context)
    if selected != arm:
        raise RuntimeError('AE attachment armature selection changed during Build')
    count = si._apply(context)
    try:
        _adopt_manifest(arm, count)
        if audit_faults(arm):
            raise RuntimeError('AE rigid attachment post-apply audit failed')
    except Exception as exc:
        rollback_errors = []
        try:
            si._restore(context)
        except Exception as rollback_error:
            rollback_errors.append('bindings: ' + str(rollback_error))
        try:
            arm[FOUNDATION_KEY] = original_foundation_raw
        except Exception as rollback_error:
            rollback_errors.append('AE manifest: ' + str(rollback_error))
        if rollback_errors:
            raise RuntimeError('AE ATTACHMENT COMMIT FAILED AND ROLLBACK INCOMPLETE: '
                               + '; '.join(rollback_errors)) from exc
        raise RuntimeError('AE attachment ownership commit failed; new bindings restored: '
                           + str(exc)) from exc
    return {'status': 'APPLIED', 'profile': PROFILE, 'count': count, 'created': True}


def audit_faults(arm):
    """Audit the original E2.3 saved metadata without modifying the scene."""
    raw = arm.get(si.PILOT_KEY)
    if not raw:
        return []
    try:
        payload = json.loads(str(raw))
        if (payload.get('version') != 1 or payload.get('armature') != arm.name
                or len(payload.get('applied_bindings', [])) != len(si.TARGETS)):
            return ['rigid:manifest']
        names = {entry['name']: entry['bone'] for entry in payload['applied_bindings']}
        if names != si.TARGETS:
            return ['rigid:binding_map']
        faults = []
        for name, target in si.TARGETS.items():
            obj = bpy.data.objects.get(name)
            if (obj is None or obj.parent != arm or obj.parent_type != 'BONE'
                    or obj.parent_bone != target):
                faults.append('rigid:' + name)
        return faults
    except (ValueError, TypeError, KeyError, AttributeError):
        return ['rigid:manifest']


def restore(context, arm):
    """Restore original lod0 bindings and immediately release AE ownership.

    After success, a failure in the rest of Remove no longer leaves a stale
    ``auto_rigid_attachments`` key. A failure while committing the release
    attempts to recover the *previous* binding state and ownership record.
    This is NOT a transaction for the later constraint/bone deletion steps.
    """
    foundation = _read_foundation(arm)
    original_foundation_raw = arm[FOUNDATION_KEY]
    owned = foundation.get(AUTO_KEY)
    raw = arm.get(si.PILOT_KEY)
    if not raw:
        if owned is not None:
            raise RuntimeError('AE ownership exists without attachment manifest; refusing Remove')
        return 0
    if owned is not None and owned != _ownership_record(len(si.TARGETS)):
        raise RuntimeError('Invalid AE rigid attachment ownership record')
    if audit_faults(arm):
        raise RuntimeError('AE attachment integrity failure: cannot restore safely')
    selected = si._armature(context)
    if selected != arm:
        raise RuntimeError('Selected AE armature changed during attachment restore')

    # Snapshot only for rollback if the ownership metadata cannot be released.
    # The underlying E2.3 restore already performs its own guarded rollback.
    snapshots = ([si._before_state(obj) for obj, _ in si._objects(context, arm)]
                 if owned is not None else [])
    count = si._restore(context)
    if owned is None:
        return count  # Genuine E2.3 legacy binding: no AE ownership to clear.
    next_foundation = dict(foundation)
    next_foundation.pop(AUTO_KEY)
    try:
        _write_foundation(arm, next_foundation)
    except Exception as exc:
        failures = []
        for state in reversed(snapshots):
            try:
                si._restore_state(context, state)
            except Exception as failure:
                failures.append(state['name'] + ': ' + str(failure))
        try:
            arm[si.PILOT_KEY] = raw
            arm[FOUNDATION_KEY] = original_foundation_raw
        except Exception as failure:
            failures.append('manifest: ' + str(failure))
        if failures:
            raise RuntimeError('AE ATTACHMENT OWNERSHIP RELEASE FAILED; recovery INCOMPLETE: '
                               + '; '.join(failures)) from exc
        raise RuntimeError('AE attachment ownership release failed; bindings restored to '
                           'their prior applied state: ' + str(exc)) from exc
    return count


class SR_OT_AE_AutoAttachmentsSync(bpy.types.Operator):
    """Upgrade rigs already built with E2.3/E2.4 without rebuilding FK/IK."""
    bl_idname = 'saberrig.ae_auto_attachments_sync'
    bl_label = 'Sync AE Rigid Attachments'
    bl_description = 'Apply known verified rigid eye/accessory bindings to an existing AE rig'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from . import ae_biped, engine
        state = getattr(context.scene, 'saberrig_state', None)
        arm = engine._resolve_armature(context, state) if state is not None else context.active_object
        if arm is None or getattr(arm, 'type', None) != 'ARMATURE':
            self.report({'ERROR'}, 'Select an AE Biped armature')
            return {'CANCELLED'}
        if ae_biped.audit(arm).get('status') != 'COMPLETE':
            self.report({'ERROR'}, 'AE Biped must pass Audit before syncing attachments')
            return {'CANCELLED'}
        try:
            outcome = apply(context, arm)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        if outcome['status'] == 'SKIP':
            self.report({'INFO'}, 'No validated AE rigid attachment pattern; nothing changed')
        elif outcome['status'] == 'APPLIED' and not outcome.get('created'):
            self.report({'INFO'}, 'AE rigid bindings adopted / ownership verified in AE manifest')
        else:
            self.report({'INFO'}, 'AE rigid attachment migration applied and owned by AE Build')
        return {'FINISHED'}
