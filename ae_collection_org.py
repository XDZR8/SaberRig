# SPDX-License-Identifier: GPL-2.0-or-later
# -*- coding: utf-8 -*-
"""Reversible organization of Endfield biped Bone Collections.

Only collection membership and visibility are within scope; the source rig's
rest geometry, animation, constraints and deform weights must remain untouched.
The foundation manifest stores original memberships so Remove can restore them.
Do not delete or repurpose pre-existing user collections during cleanup.
"""

AUX_COLLECTION = "SaberRig AE Source Auxiliary"
DEFORM_COLLECTION = "SaberRig AE Source Deform"
ORG_KEY = "collection_organization"
ORG_VERSION = 1


def _all_collections(data):
    collections_all = getattr(data, "collections_all", None)
    return list(collections_all if collections_all is not None else data.collections)


def _find(data, name):
    return next((c for c in _all_collections(data) if c.name == name), None)


def _memberships(bone):
    return sorted(c.name for c in bone.collections)


def _original_names(arm, generated_names):
    return sorted(b.name for b in arm.data.bones if b.name not in generated_names)


def _write_manifest(arm, key, data):
    import json
    old = json.loads(str(arm[key]))
    old[ORG_KEY] = data
    arm[key] = json.dumps(old, sort_keys=True)


def validate(arm, org, source_names, generated_names):
    """Return collection integrity faults without writing anything."""
    if not isinstance(org, dict) or org.get("version") != ORG_VERSION:
        return ["organization:invalid_version"]
    expected_originals = set(_original_names(arm, generated_names))
    original = org.get("original_memberships")
    if not isinstance(original, dict) or set(original) != expected_originals:
        return ["organization:original_inventory"]
    if set(source_names) - expected_originals:
        return ["organization:source_inventory"]
    for name, names in original.items():
        if not isinstance(names, list) or any(not isinstance(n, str) for n in names):
            return ["organization:invalid_membership:" + name]
    aux = _find(arm.data, AUX_COLLECTION)
    deform = _find(arm.data, DEFORM_COLLECTION)
    if aux is None or deform is None:
        return ["organization:missing_collections"]
    errors = []
    for name in sorted(expected_originals):
        assigned = _memberships(arm.data.bones[name])
        target = DEFORM_COLLECTION if name in source_names else AUX_COLLECTION
        if assigned != [target]:
            errors.append("organization:membership:" + name)
            if len(errors) >= 8:
                break
    return errors


def _restore_assignments(arm, saved):
    """Best-effort rollback for a partially completed collection assignment."""
    data = arm.data
    errors = []
    for name, old_names in saved.items():
        bone = data.bones.get(name)
        if bone is None:
            errors.append("original bone missing: " + name)
            continue
        for cname in _memberships(bone):
            if cname in (AUX_COLLECTION, DEFORM_COLLECTION):
                coll = _find(data, cname)
                if coll is not None:
                    try:
                        coll.unassign(bone)
                    except Exception as exc:
                        errors.append("unassign " + name + ": " + str(exc))
        for cname in old_names:
            coll = _find(data, cname)
            if coll is None:
                errors.append("original collection missing: " + cname)
            elif cname not in _memberships(bone):
                try:
                    coll.assign(bone)
                except Exception as exc:
                    errors.append("assign " + name + ": " + str(exc))
    return errors


def _remove_empty_new_collections(data):
    errors = []
    for cname in (AUX_COLLECTION, DEFORM_COLLECTION):
        coll = _find(data, cname)
        if coll is not None:
            if len(coll.bones):
                errors.append("collection still contains bones: " + cname)
            else:
                try:
                    data.collections.remove(coll)
                except Exception as exc:
                    errors.append("remove collection " + cname + ": " + str(exc))
    return errors


def organize(arm, manifest_key, source_names, generated_names,
             controls_collection, mechanism_collection):
    """Organize a previously built or newly built AE rig, atomically if possible.

    Existing source collection membership is snapshotted into the AE manifest.
    This is essential because merely assigning a hidden collection would not
    hide a bone also present in its visible legacy collection.
    """
    import json
    data = arm.data
    manifest = json.loads(str(arm[manifest_key]))
    if ORG_KEY in manifest:
        errors = validate(arm, manifest[ORG_KEY], source_names, generated_names)
        if errors:
            raise RuntimeError("AE collections already organized but inconsistent: " + str(errors))
        return {"already_organized": True, "original": len(manifest[ORG_KEY]["original_memberships"])}
    if getattr(arm, "mode", "OBJECT") == "EDIT":
        raise RuntimeError("Leave Edit Mode before organizing AE collections")
    for name in (AUX_COLLECTION, DEFORM_COLLECTION):
        if _find(data, name) is not None:
            raise RuntimeError("AE source bone collection name occupied: " + name)
    names = _original_names(arm, generated_names)
    if not set(source_names).issubset(names):
        raise RuntimeError("Native AE source inventory incomplete")
    saved = {name: _memberships(data.bones[name]) for name in names}
    # Fail closed if two original collections have ambiguous duplicate names.
    existing_names = [c.name for c in _all_collections(data)]
    if len(existing_names) != len(set(existing_names)):
        raise RuntimeError("Duplicate original Bone Collection names prevent safe restoration")
    control_col = _find(data, controls_collection)
    mechanism_col = _find(data, mechanism_collection)
    old_visibility = ((control_col.is_visible if control_col else None),
                      (mechanism_col.is_visible if mechanism_col else None))
    try:
        aux = data.collections.new(AUX_COLLECTION)
        deform = data.collections.new(DEFORM_COLLECTION)
        for name in names:
            bone = data.bones[name]
            # Keep original collection objects/visibility untouched; remove only
            # native bone memberships, recording them all for exact reversal.
            for old_name in saved[name]:
                coll = _find(data, old_name)
                if coll is None:
                    raise RuntimeError("Original collection disappeared: " + old_name)
                coll.unassign(bone)
            (deform if name in source_names else aux).assign(bone)
        # Visual state is deliberately not part of structural audit: buttons
        # and users may toggle visibility without corrupting the rig.
        aux.is_visible = False
        deform.is_visible = False
        ctrl = _find(data, controls_collection)
        mech = _find(data, mechanism_collection)
        if ctrl is None or mech is None:
            raise RuntimeError("AE generated control collections missing")
        ctrl.is_visible = True
        mech.is_visible = False
        payload = {"version": ORG_VERSION, "original_memberships": saved}
        issues = validate(arm, payload, set(source_names), generated_names)
        if issues:
            raise RuntimeError("AE collection assignment audit failed: " + str(issues))
        _write_manifest(arm, manifest_key, payload)
    except Exception as exc:
        errors = _restore_assignments(arm, saved)
        errors.extend(_remove_empty_new_collections(data))
        for col, value in zip((control_col, mechanism_col), old_visibility):
            if col is not None:
                try:
                    col.is_visible = value
                except Exception as e:
                    errors.append("restore visibility: " + str(e))
        if errors:
            raise RuntimeError("AE organization FAILED; collection rollback INCOMPLETE: "
                               + str(exc) + "; " + "; ".join(errors)) from exc
        raise
    return {"already_organized": False, "original": len(names),
            "auxiliary": len(names) - len(source_names), "source": len(source_names)}


def restore(arm, manifest_key, source_names, generated_names):
    """Reinstate all original source memberships, delete only two owned groups.

    Manifest metadata is deliberately retained for caller to delete when its
    whole removal succeeds. Throws before mutation on detected user edits.
    """
    import json
    manifest = json.loads(str(arm[manifest_key]))
    org = manifest.get(ORG_KEY)
    if not org:
        return {"restored": False}
    issues = validate(arm, org, set(source_names), generated_names)
    if issues:
        raise RuntimeError("AE source organization altered: " + str(issues))
    saved = org["original_memberships"]
    # All targets must still exist before starting any writes.
    for collection_names in saved.values():
        for cname in collection_names:
            if _find(arm.data, cname) is None:
                raise RuntimeError("Original Bone Collection missing: " + cname)
    errors = []
    for name, old_names in saved.items():
        bone = arm.data.bones[name]
        target = DEFORM_COLLECTION if name in source_names else AUX_COLLECTION
        active_coll = _find(arm.data, target)
        try:
            active_coll.unassign(bone)
            for cname in old_names:
                _find(arm.data, cname).assign(bone)
        except Exception as exc:
            errors.append("restore " + name + ": " + str(exc))
    errors.extend(_remove_empty_new_collections(arm.data))
    if errors:
        raise RuntimeError("AE collection restoration INCOMPLETE: " + "; ".join(errors))
    return {"restored": True, "original": len(saved)}


def set_view(arm, controls_collection, mechanism_collection, mode):
    if mode not in {"CONTROLS", "AUX", "SOURCE"}:
        raise RuntimeError("Unsupported AE bone visibility mode")
    data = arm.data
    ctrl = _find(data, controls_collection)
    mech = _find(data, mechanism_collection)
    aux = _find(data, AUX_COLLECTION)
    deform = _find(data, DEFORM_COLLECTION)
    if any(coll is None for coll in (ctrl, mech, aux, deform)):
        raise RuntimeError("AE source organization not installed")
    if mode == "CONTROLS":
        ctrl.is_visible = True
        mech.is_visible = False
        aux.is_visible = False
        deform.is_visible = False
    elif mode == "AUX":
        aux.is_visible = not aux.is_visible
    else:
        deform.is_visible = not deform.is_visible
    return {"controls": ctrl.is_visible, "auxiliary": aux.is_visible,
            "source": deform.is_visible}
