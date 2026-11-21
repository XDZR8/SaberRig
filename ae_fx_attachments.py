# SPDX-License-Identifier: GPL-2.0-or-later
"""Si-specific Endfield rigid attachments: auditable legacy migration.

This compatibility module handles the exact, source-audited group of rigid
attachments used by the Si pilot. It does not generalize to arbitrary AE
characters, nor does it rewrite mesh weights, materials or FK/IK constraints.
The newer automatic dispatcher can adopt the saved legacy ownership record.
"""

import bpy
import json
import math
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from mathutils import Matrix
from . import engine

PILOT_KEY = "saberrig_ae_si_rigid_attachment_pilot_v1"

# Source evidence: original Si import with eight objects rigidly attached
# to lod0. Destination assignments are character-specific and require visual
# verification; never reuse this table as a generic AE attachment dictionary.
TARGETS = {
    "S_actor_jsspsi_iris_01_lod0": "face_Head",
    "S_actor_jsspsi_cloth_06_lod0": "Bip001",
    "S_actor_jsspsi_cloth_09_lod0": "Bip001",
    "S_actor_jsspsi_vfxpart_01_lod0": "Bip001",
    "S_actor_jsspsi_vfxpart_02_lod0": "Bip001",
    "S_actor_jsspsi_vfxpart_05_lod0": "Bip001",
    "S_actor_jsspsi_vfxpart_06_lod0": "Bip001",
    "S_actor_jsspsi_vfxpart_07_lod0": "Bip001",
}


def _flat(m):
    return [float(m[r][c]) for r in range(4) for c in range(4)]


def _mat(numbers):
    return Matrix((numbers[0:4], numbers[4:8], numbers[8:12], numbers[12:16]))


def _delta(a, b):
    return max(abs(float(a[r][c]) - float(b[r][c])) for r in range(4) for c in range(4))


def _armature(context):
    # Never silently select another armature from a scene with multiple models.
    state = getattr(context.scene, 'saberrig_state', None)
    arm = engine._resolve_armature(context, state) if state is not None else context.active_object
    if arm is None or arm.type != 'ARMATURE':
        raise RuntimeError('Select the Si armature before using Si Rigid Attachments')
    needed = ('face_Head', 'Bip001', 'lod0', 'Mesh_all')
    if any(arm.data.bones.get(name) is None for name in needed):
        raise RuntimeError('Selected armature does not match Si rigid attachment topology')
    return arm


def _objects(context, arm):
    found = []
    for obj_name, dst_bone in TARGETS.items():
        obj = bpy.data.objects.get(obj_name)
        if obj is None or obj.name not in context.scene.objects:
            raise RuntimeError('Missing expected Si mesh: ' + obj_name)
        if obj.type != 'MESH' or obj.parent != arm:
            raise RuntimeError('Unexpected object/parent: ' + obj_name)
        if dst_bone not in arm.pose.bones:
            raise RuntimeError('Missing target bone: ' + dst_bone)
        found.append((obj, dst_bone))
    return found


def _before_state(obj):
    return {
        'name': obj.name,
        'parent_name': obj.parent.name if obj.parent else None,
        'parent_type': obj.parent_type,
        'parent_bone': obj.parent_bone,
        'matrix_parent_inverse': _flat(obj.matrix_parent_inverse),
        'matrix_basis': _flat(obj.matrix_basis),
        'matrix_world': _flat(obj.matrix_world),
    }


def _restore_state(context, state):
    obj = bpy.data.objects.get(state['name'])
    if obj is None:
        raise RuntimeError('Missing mesh during restore: ' + state['name'])
    parent = bpy.data.objects.get(state['parent_name']) if state['parent_name'] else None
    if state['parent_name'] and parent is None:
        raise RuntimeError('Missing parent during restore: ' + state['parent_name'])
    obj.parent = parent
    obj.parent_type = state['parent_type']
    obj.parent_bone = state['parent_bone']
    obj.matrix_parent_inverse = _mat(state['matrix_parent_inverse'])
    obj.matrix_basis = _mat(state['matrix_basis'])
    context.view_layer.update()


def _write_audit(context):
    arm = _armature(context)
    context.view_layer.update()
    entries = []
    for obj, dst_bone in _objects(context, arm):
        deps = [c.name for c in bpy.data.objects if c.parent == obj]
        arm_mods = [m.name for m in obj.modifiers if m.type == 'ARMATURE']
        entries.append({
            'name': obj.name, 'current_parent': obj.parent.name if obj.parent else None,
            'current_parent_type': obj.parent_type, 'current_parent_bone': obj.parent_bone,
            'proposed_bone': dst_bone, 'armature_modifiers': arm_mods,
            'vertex_group_count': len(obj.vertex_groups), 'constraints': [c.name for c in obj.constraints],
            'dependent_objects': deps, 'world_matrix': _flat(obj.matrix_world),
        })
    output = {
        'title': 'SaberRig Si E2.3 Rigid Attachment Read-Only Audit',
        'utc': datetime.now(timezone.utc).isoformat(), 'file_path': bpy.data.filepath,
        'blender_version': bpy.app.version_string,
        'armature': arm.name, 'saberRig_ae_manifest': bool(arm.get('saberrig_ae_biped_foundation')),
        'pilot_applied': bool(arm.get(PILOT_KEY)),
        'integration': 'SaberRig E2.3 integrated opt-in',
        'bones': {n: {'parent': arm.data.bones[n].parent.name if arm.data.bones[n].parent else None,
                      'pose_matrix': _flat(arm.pose.bones[n].matrix)}
                  for n in ('Bip001','Bip001_Head','face_Head','Mesh_all','lod0') if n in arm.pose.bones},
        'entries': entries,
    }
    path = Path(tempfile.gettempdir()) / ('SaberRig_AE_Si_Attachments_E2_3_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ') + '.json')
    path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding='utf-8')
    print('SABERRIG AE SI E2.3 READ ONLY:', str(path))
    print('Rigid objects:', len(entries), 'Pilot already applied:', bool(arm.get(PILOT_KEY)))
    return path


def _preflight(context, arm):
    if not arm.get('saberrig_ae_biped_foundation'):
        raise RuntimeError('Build AE Biped controls first; no AE foundation manifest found')
    if arm.library is not None or arm.override_library is not None or arm.data.library is not None:
        raise RuntimeError('Linked or library-overridden armature is not supported')
    ad = arm.animation_data
    if ad and (ad.action is not None or len(ad.nla_tracks) or len(ad.drivers)):
        raise RuntimeError('Disable Action/NLA/drivers before pilot')
    objs = _objects(context, arm)
    for obj, _ in objs:
        if obj.library is not None or obj.override_library is not None:
            raise RuntimeError('Linked/overridden mesh: ' + obj.name)
        if obj.constraints or any(m.type == 'ARMATURE' for m in obj.modifiers) or len(obj.vertex_groups):
            raise RuntimeError('Non-rigid / constrained mesh; refusing edit: ' + obj.name)
        ad2 = obj.animation_data
        if ad2 and (ad2.action is not None or len(ad2.nla_tracks) or len(ad2.drivers)):
            raise RuntimeError('Animated mesh; refusing edit: ' + obj.name)
    return objs


def _apply(context):
    arm = _armature(context)
    if arm.get(PILOT_KEY):
        raise RuntimeError('Si attachments already applied; use Restore before applying again')
    context.view_layer.update()
    objs = _preflight(context, arm)
    for obj, _ in objs:
        if obj.parent_type != 'BONE' or obj.parent_bone != 'lod0':
            raise RuntimeError('Unexpected original bone binding for ' + obj.name + ': ' + obj.parent_bone)
    original = [_before_state(obj) for obj, _ in objs]
    original_pose = {name: _flat(arm.pose.bones[name].matrix) for name in ('Bip001', 'face_Head', 'lod0')}
    armature_world = _flat(arm.matrix_world)
    written = []
    try:
        for obj, dst_bone in objs:
            before = obj.matrix_world.copy()
            # Change the existing bone parent only; preserve the evaluated world matrix.
            obj.parent = arm
            obj.parent_type = 'BONE'
            obj.parent_bone = dst_bone
            obj.matrix_parent_inverse = Matrix.Identity(4)
            obj.matrix_world = before
            context.view_layer.update()
            error = _delta(obj.matrix_world, before)
            if error > 3e-4:
                raise RuntimeError('World-space pose changed at rebind: %s, delta %.7g' % (obj.name, error))
            written.append((obj.name, dst_bone))
        manifest = {
            'version': 1, 'armature': arm.name, 'created_utc': datetime.now(timezone.utc).isoformat(),
            'source_bindings': original, 'pose_at_apply': original_pose,
            'armature_world_at_apply': armature_world,
            'applied_bindings': [{'name': name, 'bone': bone} for name,bone in written],
        }
        arm[PILOT_KEY] = json.dumps(manifest, ensure_ascii=False)
    except Exception as err:
        failed = []
        for old in reversed(original):
            try: _restore_state(context, old)
            except Exception as ex: failed.append('%s (%s)' % (old['name'], ex))
        if failed:
            raise RuntimeError('APPLY FAILED AND ROLLBACK INCOMPLETE: ' + '; '.join(failed)) from err
        raise
    return len(written)


def _restore(context):
    arm = _armature(context)
    raw = arm.get(PILOT_KEY)
    if not raw:
        raise RuntimeError('No E2.3 pilot manifest on selected armature')
    manifest = json.loads(raw)
    if manifest.get('version') != 1 or manifest.get('armature') != arm.name:
        raise RuntimeError('Unknown E2.3 pilot manifest; refusing restore')
    original = manifest['source_bindings']
    if (len(original) != len(TARGETS)
            or {x['name'] for x in original} != set(TARGETS)
            or any(x.get('parent_name') != arm.name or x.get('parent_type') != 'BONE'
                   or x.get('parent_bone') != 'lod0' for x in original)):
        raise RuntimeError('Attachment manifest has unexpected original bindings')
    # Check the rig and attachments before performing any changes. Old standalone
    # E2.3 manifests without these additional fields remain restorable.
    context.view_layer.update()
    if manifest.get('armature_world_at_apply') is not None:
        if _delta(arm.matrix_world, _mat(manifest['armature_world_at_apply'])) > 3e-4:
            raise RuntimeError('Armature moved since apply; return to the recorded transform')
    # Restore original source binding at the same underlying pose as APPLY.
    for name, matrix in manifest['pose_at_apply'].items():
        delta = _delta(arm.pose.bones[name].matrix, _mat(matrix))
        if delta > 3e-4:
            raise RuntimeError('Restore calibrated AE pose before pilot removal: %s delta %.7g' % (name, delta))
    # All eight objects must still be in the current scene and parented to the
    # same armature. Reuse the normal inventory checks instead of global lookup.
    found = dict((obj.name, obj) for obj, _bone in _objects(context, arm))
    current = [_before_state(found[x['name']]) for x in original]
    # Re-check external animation/constraints before restore to avoid undoing
    # modifications that have been made after the original migration.
    _preflight(context, arm)
    for obj_state in current:
        name = obj_state['name']
        if (obj_state['parent_bone'] != TARGETS[name] or obj_state['parent_type'] != 'BONE'
                or obj_state['parent_name'] != arm.name):
            raise RuntimeError('Unexpected rebind change; refusing restore: ' + name)
    try:
        for obj_state in original:
            _restore_state(context, obj_state)
            after = bpy.data.objects[obj_state['name']].matrix_world
            if _delta(after, _mat(obj_state['matrix_world'])) > 3e-4:
                raise RuntimeError('World-space pose changed on attachment restore: ' + obj_state['name'])
        del arm[PILOT_KEY]
    except Exception as err:
        failures = []
        for obj_state in reversed(current):
            try: _restore_state(context, obj_state)
            except Exception as ex: failures.append('%s (%s)' % (obj_state['name'], ex))
        if failures:
            raise RuntimeError('RESTORE FAILED; attempted rollback also failed: '+'; '.join(failures)) from err
        raise
    return len(original)


class SR_OT_AE_SiAttachmentAudit(bpy.types.Operator):
    bl_idname = 'saberrig.ae_si_attachment_audit'
    bl_label = 'Audit Si Rigid Attachments'
    bl_description = 'Write a read-only JSON of rigid attachments and parent bones'
    bl_options = {'REGISTER'}

    def execute(self, context):
        try: path = _write_audit(context)
        except Exception as exc: self.report({'ERROR'}, str(exc)); return {'CANCELLED'}
        self.report({'INFO'}, 'Read-only audit JSON: ' + str(path))
        return {'FINISHED'}


class SR_OT_AE_SiAttachmentApply(bpy.types.Operator):
    bl_idname = 'saberrig.ae_si_attachment_apply'
    bl_label = 'Bind Si Eyes and Rigid Parts'
    bl_description = 'Reversible Si-specific rigid attachment pilot. Save a copy before use.'
    bl_options = {'REGISTER','UNDO'}

    def execute(self, context):
        # Hidden legacy F3 action must not bypass the shared AE ownership
        # manifest commit. It uses the same safe path as Build and Sync.
        try:
            from . import ae_auto_attachments
            arm = _armature(context)
            outcome = ae_auto_attachments.apply(context, arm)
            count = outcome.get('count', 0)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, 'AE rigid bindings verified (%d); test Head, Root and Master' % count)
        return {'FINISHED'}


class SR_OT_AE_SiAttachmentRestore(bpy.types.Operator):
    bl_idname = 'saberrig.ae_si_attachment_restore'
    bl_label = 'Restore Original Si Bindings'
    bl_description = 'Restore original lod0 bone parenting. Return to build pose first.'
    bl_options = {'REGISTER','UNDO'}

    def execute(self, context):
        # Legacy F3 operator may still exist, but Build-owned E2.5 bindings
        # must be restored with Remove AE Controls, not independently.
        from . import ae_biped
        try:
            arm = _armature(context)
            foundation = json.loads(str(arm.get(ae_biped.MANIFEST_KEY, '{}')))
        except (RuntimeError, TypeError, ValueError) as exc:
            self.report({'ERROR'}, 'Cannot restore AE Si bindings: ' + str(exc))
            return {'CANCELLED'}
        if not isinstance(foundation, dict):
            self.report({'ERROR'}, 'Invalid AE foundation manifest')
            return {'CANCELLED'}
        if foundation.get('auto_rigid_attachments'):
            self.report({'ERROR'}, 'Automatic AE bindings are Build-owned; use Remove AE Controls')
            return {'CANCELLED'}
        try: count = _restore(context)
        except Exception as exc: self.report({'ERROR'}, str(exc)); return {'CANCELLED'}
        self.report({'INFO'}, 'Restored %d original lod0 bindings' % count)
        return {'FINISHED'}



# Class registration is owned by saberrig.__init__; do not register at import.
