# SPDX-License-Identifier: GPL-2.0-or-later
# -*- coding: utf-8 -*-

bl_info = {
    "name": "SaberRig",
    "author": "XDZR8",
    "version": (0, 1, 0),
    "blender": (5, 1, 1),
    "location": "View3D > Sidebar > SaberRig",
    "description": "Production release candidate with procedural custom control shapes, semantic IK/FK, secondary motion, bake/export and QA",
    "category": "Rigging",
}

import bpy
import json
import math
import os
import re
import unicodedata
from mathutils import Vector, Matrix
from collections import Counter, defaultdict
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Operator, Panel, PropertyGroup
from bpy_extras.io_utils import ExportHelper
from bpy.app.handlers import persistent

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------

ADDON_ID = "saberrig"
ADDON_VERSION = "0.4 RC3.3"
TARGET_BLENDER = "5.1.x"
SR_RELEASE_CHANNEL = "Release Candidate"
SR_RELEASE_CANDIDATE = "RC3.3"
SR_SUPPORTED_PROFILE_IDS = (
    "UMA_MUSUME_MMD_HYBRID",
    "MMD_STANDARD",
    "ENDFIELD_SEGMENTED",
    "ENDFIELD_BIPED",
)

# SaberRig-generated data always uses explicit metadata in addition to names.
SR_FOUNDATION_ID = "SaberRig V0.4 RC3.3 — Anatomical Elbow Hinge & Pole Plane Guard"
SR_COLLECTION_ROOT = "SaberRig"
SR_COLLECTION_CONTROLS = "SaberRig Controls"
SR_COLLECTION_MECHANISM = "SaberRig Mechanism"
SR_COLLECTION_SOURCE = "SaberRig Source"
SR_WIDGET_COLLECTION = "SaberRig Widgets"
SR_WIDGET_FK = "SR_WGT_FK"
SR_WIDGET_HAND_IK = "SR_WGT_HandIK"
SR_WIDGET_FOOT_IK = "SR_WGT_FootIK"
SR_WIDGET_FOOT_MASTER = "SR_WGT_FootMaster"
SR_WIDGET_HEEL_ROLL = "SR_WGT_HeelRoll"
SR_WIDGET_BALL_ROLL = "SR_WGT_BallRoll"
SR_WIDGET_TOE_ROLL = "SR_WGT_ToeRoll"
SR_WIDGET_POLE = "SR_WGT_Pole"
SR_WIDGET_MASTER = "SR_WGT_Master"
SR_WIDGET_ROOT = "SR_WGT_Root"
SR_WIDGET_COG = "SR_WGT_COG"
SR_WIDGET_HIPS = "SR_WGT_Hips"
SR_WIDGET_SPINE = "SR_WGT_Spine"
SR_WIDGET_CHEST = "SR_WGT_Chest"
SR_WIDGET_HEAD = "SR_WGT_Head"
SR_WIDGET_FINGERS = "SR_WGT_Fingers"
SR_WIDGET_GENERIC = "SR_WGT_Generic"
SR_WIDGET_LIBRARY_VERSION = "0.4-RC3-1"
SR_CONSTRAINT_PREFIX = "SR_"
SR_ARM_FK_COMPONENT = "ARMS_FK"
SR_ARM_IK_COMPONENT = "ARMS_IK"
SR_LEG_FK_COMPONENT = "LEGS_FK"
SR_LEG_IK_COMPONENT = "LEGS_IK"
SR_WIGGLE_COMPONENT = "SECONDARY_WIGGLE"
SR_BODY_COMPONENT = "BODY_CONTROLS"
SR_FINGER_COMPONENT = "FINGER_CONTROLS"
SR_ANIMATION_SPACE_COMPONENT = "ANIMATION_SPACES"

# Animator-facing IK space indices. WORLD means the SaberRig global
# frame: when Body Controls exist this is the Master control; otherwise it is
# armature/world space. Non-world spaces are driven by hidden rest-offset
# anchors so switching does not require Child-Of inverse-matrix bookkeeping.
SR_ANIMATION_SPACE_INDEX = {
    "WORLD": 0,
    "ROOT": 1,
    "COG": 2,
    "CHEST": 3,
    "SHOULDER": 4,
}
SR_ANIMATION_SPACE_LABELS = {
    "WORLD": "World",
    "ROOT": "Root",
    "COG": "COG",
    "CHEST": "Chest",
    "SHOULDER": "Shoulder",
}
# Straight-arm IK stabilization. The visible HandIK stays
# completely free; only the hidden effective target is capped to source reach.
# A tiny MCH-only elbow pre-bend keeps the solver below its own 180° extension.
SR_DEFAULT_ARM_REACH_FACTOR = 1.0
SR_DEFAULT_ARM_PREFERRED_REACH_FACTOR = 0.995
SR_ARM_PREBEND_ANGLE = math.radians(4.0)
SR_ARM_PREBEND_TRIGGER_DOT = 0.985
SR_ARM_PREBEND_MAX_OFFSET_FACTOR = 0.025
SR_DEFAULT_LEG_REACH_FACTOR = 1.0
SR_POLE_SINGULAR_DOT = 0.995
SR_POLE_CALIBRATION_WARN_RADIANS = math.radians(8.0)

# Semantic arm-safety defaults. These limits live only on SaberRig
# mechanism bones; source/game bones remain untouched. SAFE is intentionally
# permissive: the goal is to prevent solver flips/hyperextension, not to impose
# a medical/anatomical range of motion on stylized characters.
SR_ELBOW_SAFE_FLEXION = math.radians(158.0)
SR_ELBOW_SAFE_HYPEREXTENSION = math.radians(4.0)

# Once a straight-rest arm has been deliberately pre-bent toward
# the Elbow Pole, that MCH geometry becomes a reliable anatomical reference.
# Allow only a tiny reverse rotation from the pre-bent rest, so the elbow can
# never cross through straight and fold into the opposite hemisphere.
SR_ELBOW_PREBEND_REVERSE_TOLERANCE = math.radians(2.0)
SR_ELBOW_CANONICAL_SIGN_MIN_QUALITY = 0.35
SR_ELBOW_POLE_GUARD_MARGIN_FACTOR = 0.035
SR_ELBOW_POLE_GUARD_MIN_FRACTION = 0.08

SR_ELBOW_HARD_HINGE_CONFIDENCE = 0.90
SR_ELBOW_SOFT_HINGE_CONFIDENCE = 0.70
SR_ELBOW_NONHINGE_STIFFNESS = 0.82
SR_ELBOW_TWIST_STIFFNESS = 0.95
SR_SHOULDER_SWING_LIMIT = math.radians(170.0)
SR_SHOULDER_TWIST_LIMIT = math.radians(145.0)
SR_SHOULDER_TWIST_STIFFNESS = 0.22
SR_WRIST_SAFE_SWING = math.radians(100.0)
SR_WRIST_SAFE_TWIST = math.radians(125.0)

# Shoulder-assist defaults. The assist is intentionally conservative:
# it follows the IK hand direction through a separate MCH clavicle, so source
# bones remain untouched and manual FK clavicle control remains the base pose.
SR_SHOULDER_ASSIST_DEFAULT = 0.28
SR_SHOULDER_ASSIST_MIN = 0.0
SR_SHOULDER_ASSIST_MAX = 0.55

# Leg-IK safety defaults. SAFE defines a semantic hip workspace,
# a hinge-safe knee, and both maximum and minimum ankle reach. These limits live
# only on SaberRig mechanism bones; source/game bones remain untouched.
SR_DEFAULT_LEG_MIN_REACH_FACTOR = 0.18
# Smart Knee defaults. Imported game legs are often perfectly straight
# in rest pose; a two-bone IK chain built from that exact geometry is singular.
# SaberRig therefore gives only the hidden MCH solver chain a tiny real pre-bend
# toward the Knee Pole. Source/game bones are never edited. SMOOTH adds a small
# reach reserve on top, while RIGID still benefits from the stable pre-bent MCH.
SR_DEFAULT_LEG_PREFERRED_REACH_FACTOR = 0.995
SR_LEG_PREBEND_ANGLE = math.radians(4.0)
SR_LEG_PREBEND_TRIGGER_DOT = 0.985
SR_LEG_PREBEND_MAX_OFFSET_FACTOR = 0.025
SR_SMART_KNEE_HEMISPHERE_DOT = 0.985
SR_KNEE_POLE_GUIDED_STIFFNESS = 0.92
SR_KNEE_POLE_GUIDED_SWING = math.radians(14.0)
SR_KNEE_SAFE_FLEXION = math.radians(150.0)
SR_KNEE_SAFE_HYPEREXTENSION = math.radians(2.0)
SR_KNEE_HARD_HINGE_CONFIDENCE = 0.90
SR_KNEE_SOFT_HINGE_CONFIDENCE = 0.70
SR_KNEE_NONHINGE_STIFFNESS = 0.88
SR_KNEE_TWIST_STIFFNESS = 0.98
# Smart Knee Pole Space. The hidden pole-space bone tracks the
# effective ankle direction around the hip, so the animator pole keeps a stable
# hemisphere as the Foot Master travels through wide arcs. The local flip guard
# prevents the pole from crossing the hip→ankle plane accidentally.
SR_KNEE_POLE_SPACE_DEFAULT = "SMART"
SR_KNEE_POLE_GUARD_MARGIN_FACTOR = 0.035
SR_KNEE_POLE_GUARD_MIN_FRACTION = 0.08
SR_HIP_SAFE_FLEXION = math.radians(135.0)
SR_HIP_SAFE_EXTENSION = math.radians(45.0)
SR_HIP_SAFE_ABDUCTION = math.radians(85.0)
SR_HIP_SAFE_ADDUCTION = math.radians(40.0)
SR_HIP_TWIST_LIMIT = math.radians(65.0)
SR_HIP_TWIST_STIFFNESS = 0.28
# The foot output copies target position and orientation separately
# transform. The ankle target drives position only; foot orientation is solved
# separately and clamped relative to the shin. These are animation-safe rather
# than clinical anatomical limits.
SR_ANKLE_SAFE_FLEX = math.radians(60.0)
SR_ANKLE_SAFE_BANK = math.radians(35.0)
SR_ANKLE_SAFE_TWIST = math.radians(45.0)

# Reverse-foot defaults. These limits are intentionally animation-safe
# rather than anatomical absolutes. The generated pivot controls use a semantic
# foot frame (Y forward, Z up, X lateral), so X is pitch/roll and Y is bank.
SR_FOOT_HEEL_ROLL_BACK = math.radians(42.0)
SR_FOOT_HEEL_ROLL_FORWARD = math.radians(48.0)
SR_FOOT_HEEL_BANK = math.radians(28.0)
SR_FOOT_BALL_ROLL_BACK = math.radians(18.0)
SR_FOOT_BALL_ROLL_FORWARD = math.radians(78.0)
SR_FOOT_TOE_ROLL_BACK = math.radians(52.0)
SR_FOOT_TOE_ROLL_FORWARD = math.radians(72.0)

# Finger-control defaults. Anatomical chain curl is preserved
# intact. The closed-fist thumb is now a dedicated wrap target: its CMC/root
# flexion is reduced, while an opposition angle is solved geometrically toward
# the index/middle knuckle zone. This makes Fist cross the thumb over the folded
# fingers instead of treating it as a fifth ordinary curling finger.
SR_FINGER_ORDER = ("thumb", "index", "middle", "ring", "pinky")
SR_FINGER_LABELS = {"thumb": "Thumb", "index": "Index", "middle": "Middle", "ring": "Ring", "pinky": "Pinky"}

# Full manual curl target: (MCP/root, PIP/middle, DIP/tip). The DIP is
# intentionally strong so the fingertip tucks naturally with the chain.
SR_FINGER_CURL_ANGLES = {
    "thumb": tuple(math.radians(v) for v in (34.0, 54.0, 62.0)),
    "index": tuple(math.radians(v) for v in (62.0, 94.0, 88.0)),
    "middle": tuple(math.radians(v) for v in (64.0, 98.0, 90.0)),
    "ring": tuple(math.radians(v) for v in (66.0, 100.0, 92.0)),
    "pinky": tuple(math.radians(v) for v in (68.0, 102.0, 94.0)),
}

# Dedicated closed-fist targets. These are not merely Curl weights: when Fist
# approaches 1.0 the hand blends toward these per-joint target rotations and
# fades out manual Curl/Individual Curl, yielding a coherent pose.
SR_FINGER_FIST_ANGLES = {
    # Thumb uses moderate flexion; most of its fist motion comes from the
    # dedicated anatomical opposition/wrap target applied at the root.
    "thumb": tuple(math.radians(v) for v in (16.0, 38.0, 32.0)),
    "index": tuple(math.radians(v) for v in (56.0, 96.0, 84.0)),
    "middle": tuple(math.radians(v) for v in (60.0, 100.0, 86.0)),
    "ring": tuple(math.radians(v) for v in (62.0, 102.0, 88.0)),
    "pinky": tuple(math.radians(v) for v in (64.0, 104.0, 90.0)),
}
SR_FINGER_GLOBAL_CURL_WEIGHT = 0.84
SR_FINGER_MANUAL_BACKCURL = 0.35
SR_FINGER_SPREAD_ANGLES = {
    "thumb": math.radians(16.0), "index": math.radians(10.0),
    "middle": math.radians(3.0), "ring": math.radians(7.0),
    "pinky": math.radians(13.0),
}
# Inward fan amount at Fist=1, expressed as a fraction of each finger's normal
# outward Spread range. Fist owns this pose while blending in.
SR_FINGER_FIST_FAN = {"index": 0.34, "middle": 0.08, "ring": 0.14, "pinky": 0.36}
SR_FINGER_FIST_THUMB_OPPOSITION_MIN = math.radians(24.0)
SR_FINGER_FIST_THUMB_OPPOSITION_MAX = math.radians(42.0)
SR_FINGER_FIST_THUMB_TARGET_FORWARD = 0.22
SR_FINGER_FIST_THUMB_TARGET_INWARD = 0.38
SR_FINGER_FIST_THUMB_CURL_MIN = math.radians(-10.0)
SR_FINGER_FIST_THUMB_CURL_MAX = math.radians(18.0)

# 3D thumb contact-target fist solver. Closed-fist references
# show the thumb wrapping OUTSIDE the curled fingers, crossing primarily over
# index/middle via opposition + adduction, while the root itself is not curled
# downward like a fifth finger. These are SaberRig animation targets, not
# clinical joint limits: root slightly extends/lifts, then MCP/IP flex to lay
# the thumb across the folded fingers. Manual Thumb Curl/Spread stay unchanged.
SR_UMA_FIST_THUMB_CURL_ANGLES = tuple(math.radians(v) for v in (-14.0, 50.0, 34.0))
SR_UMA_FIST_THUMB_OPPOSITION = math.radians(42.0)
# The thumb fist pose uses a contact-target solve rather than preset Euler values.
# The root/thumb-metacarpal aims toward the index/middle knuckle band; MCP/IP
# flex are derived from downstream contact points and blended with a stable
# reference wrap. This keeps the thumb outside the curled fingers.
SR_THUMB_FIST_ROOT_REFERENCE = math.radians(-12.0)
SR_THUMB_FIST_MCP_REFERENCE = math.radians(54.0)
SR_THUMB_FIST_IP_REFERENCE = math.radians(30.0)
SR_THUMB_FIST_ROOT_CURL_MINMAG = math.radians(4.0)
SR_THUMB_FIST_ROOT_CURL_MAXMAG = math.radians(20.0)
SR_THUMB_FIST_MCP_FLEX_MIN = math.radians(40.0)
SR_THUMB_FIST_MCP_FLEX_MAX = math.radians(68.0)
SR_THUMB_FIST_IP_FLEX_MIN = math.radians(18.0)
SR_THUMB_FIST_IP_FLEX_MAX = math.radians(44.0)
SR_THUMB_FIST_ROOT_BLEND = 0.58
SR_THUMB_FIST_MCP_BLEND = 0.72
SR_THUMB_FIST_IP_BLEND = 0.64
SR_THUMB_FIST_TARGET_ROOT_BLEND = 0.72
SR_THUMB_FIST_TARGET_PALM_BLEND = 0.28
SR_THUMB_FIST_TARGET_FORWARD_OFFSET = 0.10
SR_THUMB_FIST_TARGET_INWARD_OFFSET = 0.12
SR_THUMB_FIST_CONTACT_MCP_BLEND = 0.58
SR_THUMB_FIST_CONTACT_TIP_BLEND = 0.72

# Fist-space quaternion/transform target architecture. The final
# thumb Fist pose is no longer injected as Euler angles on the animator MCH.
# Instead SaberRig builds a hidden target chain in the closed-fist contact
# space, gives each target a full 3D orientation (aim + roll/pronation), then
# blends the animator thumb toward it with a driven Copy Rotation constraint.
#
# Contact-depth tuning preserves the solved bend axis while keeping only a small
# CMC clearance. MCP/IP progressively tuck closer to the closed Index/Middle
# surface without changing the solved rotations.
SR_THUMB_FIST_OUTSIDE_ROOT = 0.080
SR_THUMB_FIST_OUTSIDE_MCP = 0.060
SR_THUMB_FIST_OUTSIDE_IP = 0.035
SR_THUMB_FIST_ROOT_NORMAL_BLEND = 0.16
SR_THUMB_FIST_MCP_NORMAL_BLEND = 0.24
SR_THUMB_FIST_IP_NORMAL_BLEND = 0.22
SR_THUMB_FIST_ROOT_PRONATION = math.radians(34.0)
SR_THUMB_FIST_MCP_PRONATION = math.radians(18.0)
SR_THUMB_FIST_IP_PRONATION = math.radians(24.0)

# Parent-relative quaternion thumb chain. Hidden fist targets share the source
# thumb REST hierarchy and store the fist pose as LOCAL quaternion deltas.
# Copy Rotation therefore transfers each joint rotation relative to its parent,
# preserving the intended MCP/IP bend through the full thumb chain.
SR_THUMB_FIST_IP_MIN_BEND = math.radians(28.0)
SR_THUMB_FIST_IP_MAX_BEND = math.radians(54.0)
SR_THUMB_FIST_IP_INFLUENCE_LEAD = 0.0

# Profile-level driver sign calibration. The UMA imported/MMD-hybrid convention
# is opposite the generic inferred curl direction. Apply this only at final
# amplitude so Spread remains untouched.
SR_FINGER_CURL_DRIVER_MULTIPLIER = {
    "UMA_MUSUME_MMD_HYBRID": -1.0,
}
# The thumb is not roll-compatible with the four main fingers on
# UMA/MMD-hybrid imports. Positive Thumb Curl must flex INTO the palm, while the
# main fingers keep the already validated UMA -1 multiplier.
SR_THUMB_CURL_DRIVER_MULTIPLIER = {
    "UMA_MUSUME_MMD_HYBRID": 1.0,
}
# UMA/MMD-hybrid thumb bend is defined in ARMATURE space rather than by a fixed
# local Euler channel. Distal thumb bones can have strongly rotated local axes,
# so SaberRig projects Armature +X through each authored rest matrix and uses
# the resulting per-bone local axis. This preserves bone roll while making
# "bend around X" spatially consistent across Thumb0/Thumb1/Thumb2.
SR_THUMB_BEND_ARMATURE_AXIS = {
    "UMA_MUSUME_MMD_HYBRID": (1.0, 0.0, 0.0),
}
SR_THUMB_FIST_MCP_HEMISPHERE_MIN = math.radians(18.0)
SR_THUMB_FIST_IP_HEMISPHERE_MIN = SR_THUMB_FIST_IP_MIN_BEND

# UMA thumb calibration uses a manually validated closed-fist reference.
# The stored LOCAL pose quaternion targets 親指１.L at Fist=1:
# W=0.565, X=0.289, Y=-0.056, Z=0.771. The quaternion is normalized at runtime.
# Only the middle thumb joint is calibrated; Thumb0 opposition, Thumb2 distal
# closure, contact geometry and Armature-X bend logic remain unchanged.
SR_UMA_THUMB_FIST_CALIBRATED_JOINT_INDEX = 1
SR_UMA_THUMB_FIST_CALIBRATED_SOURCE_TOKEN = "親指１"
SR_UMA_THUMB_FIST_CALIBRATED_LEFT_QUATERNION = (0.565, 0.289, -0.056, 0.771)

# UMA game-like secondary-motion profile.
# This remains SaberRig's own Blender solver: it does not copy the game's code.
# The solver uses per-family response, root-to-tip gradients, angular caps,
# and semantic collision groups while remaining fully implemented in Blender.
# Values are deliberately conservative and tuned as a practical starting point
# for UMA rigs.
SR_WIGGLE_PROFILE_ID = "UMA_GAME_LIKE_SECONDARY"
SR_WIGGLE_PRESET_NAME = "UMA Game-like"
SR_WIGGLE_FPS_REFERENCE = 30.0
SR_WIGGLE_MAX_FRAME_STEP = 4
SR_WIGGLE_GRAVITY = 9.81
SR_WIGGLE_DEFAULT_INTENSITY = 0.65
SR_WIGGLE_COLLISION_STRENGTH = 0.72

# Source-aware secondary-motion presets. These are SaberRig tuning
# profiles, not extracted game parameters. UMA uses the validated game-like
# response; MMD/Game-FBX/unknown rigs use slightly more conservative defaults.
SR_SECONDARY_PRESETS = {
    "UMA_MUSUME_MMD_HYBRID": {
        "id": "UMA_GAME_LIKE_SECONDARY", "name": "UMA Game-like",
        "intensity": 0.65, "stiffness_mul": 1.00, "damping_power": 1.00,
        "gravity_mul": 1.00, "angle_mul": 1.00, "collisions": True,
    },
    "MMD_STANDARD": {
        "id": "MMD_SEMANTIC_SECONDARY", "name": "MMD Semantic",
        "intensity": 0.55, "stiffness_mul": 0.94, "damping_power": 1.06,
        "gravity_mul": 1.02, "angle_mul": 0.92, "collisions": True,
    },
    "ENDFIELD_SEGMENTED": {
        "id": "GAME_FBX_SEMANTIC_SECONDARY", "name": "Game FBX Semantic",
        "intensity": 0.50, "stiffness_mul": 1.12, "damping_power": 1.08,
        "gravity_mul": 0.88, "angle_mul": 0.82, "collisions": True,
    },
    "ENDFIELD_BIPED": {
        "id": "GAME_FBX_SEMANTIC_SECONDARY", "name": "Game FBX Semantic",
        "intensity": 0.50, "stiffness_mul": 1.12, "damping_power": 1.08,
        "gravity_mul": 0.88, "angle_mul": 0.82, "collisions": True,
    },
    "DEFAULT": {
        "id": "GENERIC_SEMANTIC_SECONDARY", "name": "Generic Semantic",
        "intensity": 0.50, "stiffness_mul": 1.05, "damping_power": 1.08,
        "gravity_mul": 0.92, "angle_mul": 0.82, "collisions": True,
    },
}

# Ordered multilingual family signatures. Exact UMA signatures still take
# priority; these broaden discovery to MMD and game-FBX naming conventions.
SR_SECONDARY_FAMILY_PATTERNS = (
    ("VEIL", re.compile(r"(?:veil|ベール)", re.I)),
    ("RIBBON", re.compile(r"(?:ribbon|リボン|(?:^|[_\.\-\s])bow(?:$|[_\.\-\s\d]))", re.I)),
    ("CAPE", re.compile(r"(?:cape|cloak|mantle|マント)", re.I)),
    ("SKIRT", re.compile(r"(?:skirt|スカート|裙|(?:^|[_\.\-\s])hem(?:$|[_\.\-\s\d]))", re.I)),
    ("TAIL", re.compile(r"(?:tail|尻尾|しっぽ)", re.I)),
    ("EAR", re.compile(r"(?:耳|(?:^|[_\.\-\s])(?:ear|ears|mimi)(?:$|[_\.\-\s\d]))", re.I)),
    ("HAIR", re.compile(r"(?:hair|髪|ヘア|ponytail|pony_tail|braid|bangs?|fringe|ahoge|sidehair|backhair)", re.I)),
    ("CLOTH", re.compile(r"(?:cloth|clothes|fabric|dress|coat|sleeve|scarf|shawl|apron|robe|garment|flap|裾|袖)", re.I)),
    ("ACCESSORY", re.compile(r"(?:accessory|(?:^|[_\.\-\s])acc(?:$|[_\.\-\s\d])|ornament|pendant|strap|charm|decor|hat|帽子)", re.I)),
)

SR_WIGGLE_PROFILES = {
    "HAIR": {
        "label": "Hair", "pattern": r"^Sp_He_Hair", "stiffness": 50.0,
        "root_stiffness_mul": 1.40, "tip_stiffness_mul": 0.82,
        "root_damping": 0.60, "tip_damping": 0.73,
        "gravity": 0.012, "root_gravity_mul": 0.55, "tip_gravity_mul": 1.0,
        "max_angle": math.radians(18.0), "root_angle_mul": 0.42, "tip_angle_mul": 1.0,
        "colliders": ("HEAD", "CHEST", "SHOULDER_L", "SHOULDER_R"), "default": True,
    },
    "SKIRT": {
        "label": "Skirt", "pattern": r"^Sp_Hi_MSkirt0_", "stiffness": 78.0,
        "root_stiffness_mul": 1.50, "tip_stiffness_mul": 0.92,
        "root_damping": 0.54, "tip_damping": 0.68,
        "gravity": 0.018, "root_gravity_mul": 0.65, "tip_gravity_mul": 1.0,
        "max_angle": math.radians(11.0), "root_angle_mul": 0.40, "tip_angle_mul": 1.0,
        "colliders": ("PELVIS", "THIGH_L", "THIGH_R"), "default": True,
    },
    "TAIL": {
        "label": "Tail", "pattern": r"^Sp_Hi_Tail0_", "stiffness": 32.0,
        "root_stiffness_mul": 1.28, "tip_stiffness_mul": 0.72,
        "root_damping": 0.70, "tip_damping": 0.82,
        "gravity": 0.010, "root_gravity_mul": 0.50, "tip_gravity_mul": 1.0,
        "max_angle": math.radians(26.0), "root_angle_mul": 0.38, "tip_angle_mul": 1.0,
        "colliders": ("PELVIS", "CHEST"), "default": True,
    },
    "EAR": {
        "label": "Ears", "pattern": r"^Sp_He_Ear", "stiffness": 88.0,
        "root_stiffness_mul": 1.15, "tip_stiffness_mul": 1.0,
        "root_damping": 0.48, "tip_damping": 0.58,
        "gravity": 0.0, "root_gravity_mul": 0.0, "tip_gravity_mul": 0.0,
        "max_angle": math.radians(7.0), "root_angle_mul": 0.55, "tip_angle_mul": 1.0,
        "colliders": ("HEAD",), "default": True,
    },
    "RIBBON": {
        "label": "Ribbon", "pattern": r"^Sp_Hi_Ribbon0_", "stiffness": 44.0,
        "root_stiffness_mul": 1.35, "tip_stiffness_mul": 0.75,
        "root_damping": 0.62, "tip_damping": 0.76,
        "gravity": 0.010, "root_gravity_mul": 0.55, "tip_gravity_mul": 1.0,
        "max_angle": math.radians(19.0), "root_angle_mul": 0.40, "tip_angle_mul": 1.0,
        "colliders": ("HEAD", "CHEST"), "default": True,
    },
    "VEIL": {
        "label": "Veil", "pattern": r"^Sp_He_Veil", "stiffness": 52.0,
        "root_stiffness_mul": 1.38, "tip_stiffness_mul": 0.80,
        "root_damping": 0.61, "tip_damping": 0.74,
        "gravity": 0.013, "root_gravity_mul": 0.55, "tip_gravity_mul": 1.0,
        "max_angle": math.radians(16.0), "root_angle_mul": 0.40, "tip_angle_mul": 1.0,
        "colliders": ("HEAD", "CHEST", "SHOULDER_L", "SHOULDER_R"), "default": True,
    },
    "CLOTH": {
        "label": "Cloth", "pattern": r"(?:cloth|clothes|dress|coat|sleeve|scarf|裾|袖)", "stiffness": 66.0,
        "root_stiffness_mul": 1.45, "tip_stiffness_mul": 0.86,
        "root_damping": 0.56, "tip_damping": 0.71,
        "gravity": 0.016, "root_gravity_mul": 0.62, "tip_gravity_mul": 1.0,
        "max_angle": math.radians(14.0), "root_angle_mul": 0.40, "tip_angle_mul": 1.0,
        "colliders": ("CHEST", "PELVIS", "THIGH_L", "THIGH_R"), "default": True,
    },
    "CAPE": {
        "label": "Cape", "pattern": r"(?:cape|cloak|mantle|マント)", "stiffness": 58.0,
        "root_stiffness_mul": 1.42, "tip_stiffness_mul": 0.80,
        "root_damping": 0.60, "tip_damping": 0.75,
        "gravity": 0.015, "root_gravity_mul": 0.58, "tip_gravity_mul": 1.0,
        "max_angle": math.radians(17.0), "root_angle_mul": 0.38, "tip_angle_mul": 1.0,
        "colliders": ("CHEST", "PELVIS", "SHOULDER_L", "SHOULDER_R"), "default": True,
    },
    "ACCESSORY": {
        "label": "Accessories", "pattern": r"^Sp_(?:He|Ao)_Acc", "stiffness": 92.0,
        "root_stiffness_mul": 1.20, "tip_stiffness_mul": 1.0,
        "root_damping": 0.50, "tip_damping": 0.60,
        "gravity": 0.004, "root_gravity_mul": 0.60, "tip_gravity_mul": 1.0,
        "max_angle": math.radians(8.0), "root_angle_mul": 0.55, "tip_angle_mul": 1.0,
        "colliders": (), "default": False,
    },
}
_WIGGLE_STATE = {}
_WIGGLE_CACHE = {}
_WIGGLE_COLLIDER_CACHE = {}

_ANALYSIS_CACHE = {}

ROLE_LABELS = {
    "root": "Root",
    "global_offset": "Global Offset",
    "root_motion": "Root Motion",
    "torso_control": "Torso Control",
    "waist": "Waist",
    "pelvis": "Pelvis / Hips",
    "spine_01": "Spine 01",
    "spine_02": "Spine 02",
    "chest": "Chest",
    "neck": "Neck",
    "head": "Head",
    "jaw": "Jaw",
    "eye.L": "Eye L",
    "eye.R": "Eye R",
    "clavicle.L": "Clavicle L",
    "shoulder.L": "Shoulder L",
    "upper_arm.L": "Upper Arm L",
    "forearm.L": "Forearm L",
    "hand.L": "Hand L",
    "clavicle.R": "Clavicle R",
    "shoulder.R": "Shoulder R",
    "upper_arm.R": "Upper Arm R",
    "forearm.R": "Forearm R",
    "hand.R": "Hand R",
    "thigh.L": "Thigh L",
    "shin.L": "Shin L",
    "foot.L": "Foot L",
    "toe.L": "Toe L",
    "thigh.R": "Thigh R",
    "shin.R": "Shin R",
    "foot.R": "Foot R",
    "toe.R": "Toe R",
}

DISPLAY_GROUPS = [
    ("BODY", ["root", "global_offset", "root_motion", "waist", "pelvis", "spine_01", "spine_02", "chest", "neck", "head"]),
    ("LEFT ARM", ["clavicle.L", "shoulder.L", "upper_arm.L", "forearm.L", "hand.L"]),
    ("RIGHT ARM", ["clavicle.R", "shoulder.R", "upper_arm.R", "forearm.R", "hand.R"]),
    ("LEFT LEG", ["thigh.L", "shin.L", "foot.L", "toe.L"]),
    ("RIGHT LEG", ["thigh.R", "shin.R", "foot.R", "toe.R"]),
]

# Expected semantic ancestry. The resolver allows intermediate deform/twist bones.
EXPECTED_CHAINS = [
    ["pelvis", "spine_01", "spine_02", "chest", "neck", "head"],
    ["chest", "clavicle.L", "upper_arm.L", "forearm.L", "hand.L"],
    ["chest", "shoulder.L", "upper_arm.L", "forearm.L", "hand.L"],
    ["chest", "clavicle.R", "upper_arm.R", "forearm.R", "hand.R"],
    ["chest", "shoulder.R", "upper_arm.R", "forearm.R", "hand.R"],
    ["pelvis", "thigh.L", "shin.L", "foot.L", "toe.L"],
    ["pelvis", "thigh.R", "shin.R", "foot.R", "toe.R"],
]

GENERIC_ALIASES = {
    "root": [
        "Root", "root", "ROOT", "全ての親", "操作中心", "Bip001",
        "Armature", "master", "Master", "global", "Global",
    ],
    "global_offset": ["グルーブ", "Groove", "groove"],
    "root_motion": ["センター", "Center", "center", "Root_M"],
    "waist": ["腰", "Waist", "waist", "腰キャンセル"],
    "pelvis": [
        "下半身", "Pelvis", "pelvis", "Hips", "hips", "Hip", "hip",
        "Bip001_Pelvis", "Root_M", "J_Bip_C_Hips", "mixamorig:Hips",
    ],
    "spine_01": [
        "上半身", "Spine", "spine", "Spine1_M", "Bip001_Spine",
        "J_Bip_C_Spine", "mixamorig:Spine", "spine_01", "spine01",
    ],
    "spine_02": [
        "上半身2", "上半身２", "Spine2_M", "Bip001_Spine1", "Spine1",
        "spine_02", "spine02", "J_Bip_C_Chest", "mixamorig:Spine1",
    ],
    "chest": [
        "Chest", "chest", "Chest_M", "Bip001_Spine2", "UpperChest",
        "upper_chest", "J_Bip_C_UpperChest", "mixamorig:Spine2",
    ],
    "neck": ["首", "Neck", "neck", "Neck_M", "Bip001_Neck", "J_Bip_C_Neck", "mixamorig:Neck"],
    "head": ["頭", "Head", "head", "Head_M", "Bip001_Head", "J_Bip_C_Head", "mixamorig:Head"],
    "jaw": ["Chin", "Jaw", "jaw", "あご", "顎", "jawJoint", "faceMdJawDnJoint"],
    "eye.L": ["目.L", "左目", "Eye_L", "eye_L", "LeftEye", "eyeLfJoint", "faceLfIrisJoint"],
    "eye.R": ["目.R", "右目", "Eye_R", "eye_R", "RightEye", "eyeRtJoint", "faceRtIrisJoint"],
    "clavicle.L": [
        "肩.L", "左肩", "Clavicle_L", "clavicle_l", "Scapula_L",
        "Bip001_L_Clavicle", "J_Bip_L_Shoulder", "mixamorig:LeftShoulder",
    ],
    "shoulder.L": ["肩.L", "左肩", "Shoulder_L", "Scapula_L", "Bip001_L_Clavicle"],
    "upper_arm.L": [
        "腕.L", "左腕", "UpperArm_L", "upperarm_l", "Shoulder_L",
        "Bip001_L_UpperArm", "J_Bip_L_UpperArm", "mixamorig:LeftArm", "LeftArm",
    ],
    "forearm.L": [
        "ひじ.L", "左ひじ", "左肘", "Forearm_L", "forearm_l", "Elbow_L",
        "Bip001_L_Forearm", "J_Bip_L_LowerArm", "mixamorig:LeftForeArm", "LeftForeArm",
    ],
    "hand.L": [
        "手首.L", "左手首", "Hand_L", "hand_l", "Wrist_L", "Bip001_L_Hand",
        "J_Bip_L_Hand", "mixamorig:LeftHand", "LeftHand",
    ],
    "clavicle.R": [
        "肩.R", "右肩", "Clavicle_R", "clavicle_r", "Scapula_R",
        "Bip001_R_Clavicle", "J_Bip_R_Shoulder", "mixamorig:RightShoulder",
    ],
    "shoulder.R": ["肩.R", "右肩", "Shoulder_R", "Scapula_R", "Bip001_R_Clavicle"],
    "upper_arm.R": [
        "腕.R", "右腕", "UpperArm_R", "upperarm_r", "Shoulder_R",
        "Bip001_R_UpperArm", "J_Bip_R_UpperArm", "mixamorig:RightArm", "RightArm",
    ],
    "forearm.R": [
        "ひじ.R", "右ひじ", "右肘", "Forearm_R", "forearm_r", "Elbow_R",
        "Bip001_R_Forearm", "J_Bip_R_LowerArm", "mixamorig:RightForeArm", "RightForeArm",
    ],
    "hand.R": [
        "手首.R", "右手首", "Hand_R", "hand_r", "Wrist_R", "Bip001_R_Hand",
        "J_Bip_R_Hand", "mixamorig:RightHand", "RightHand",
    ],
    "thigh.L": [
        "足.L", "左足", "Thigh_L", "thigh_l", "Hip_L", "Bip001_L_Thigh",
        "J_Bip_L_UpperLeg", "mixamorig:LeftUpLeg", "LeftUpLeg",
    ],
    "shin.L": [
        "ひざ.L", "左ひざ", "左膝", "Shin_L", "shin_l", "Knee_L", "Calf_L",
        "Bip001_L_Calf", "J_Bip_L_LowerLeg", "mixamorig:LeftLeg", "LeftLeg",
    ],
    "foot.L": [
        "足首.L", "左足首", "Foot_L", "foot_l", "Ankle_L", "Bip001_L_Foot",
        "J_Bip_L_Foot", "mixamorig:LeftFoot", "LeftFoot",
    ],
    "toe.L": [
        "足先EX.L", "左つま先", "左足先EX", "Toe_L", "toe_l", "Toes_L",
        "Bip001_L_Toe0", "J_Bip_L_ToeBase", "mixamorig:LeftToeBase",
    ],
    "thigh.R": [
        "足.R", "右足", "Thigh_R", "thigh_r", "Hip_R", "Bip001_R_Thigh",
        "J_Bip_R_UpperLeg", "mixamorig:RightUpLeg", "RightUpLeg",
    ],
    "shin.R": [
        "ひざ.R", "右ひざ", "右膝", "Shin_R", "shin_r", "Knee_R", "Calf_R",
        "Bip001_R_Calf", "J_Bip_R_LowerLeg", "mixamorig:RightLeg", "RightLeg",
    ],
    "foot.R": [
        "足首.R", "右足首", "Foot_R", "foot_r", "Ankle_R", "Bip001_R_Foot",
        "J_Bip_R_Foot", "mixamorig:RightFoot", "RightFoot",
    ],
    "toe.R": [
        "足先EX.R", "右つま先", "右足先EX", "Toe_R", "toe_r", "Toes_R",
        "Bip001_R_Toe0", "J_Bip_R_ToeBase", "mixamorig:RightToeBase",
    ],
}

MMD_FINGERS = {
    "thumb": ["親指０", "親指１", "親指２"],
    "index": ["人指１", "人指２", "人指３"],
    "middle": ["中指１", "中指２", "中指３"],
    "ring": ["薬指１", "薬指２", "薬指３"],
    "pinky": ["小指１", "小指２", "小指３"],
}

MMD_PREFIX_FINGERS = {
    "thumb": ["親指０", "親指１", "親指２"],
    "index": ["人指１", "人指２", "人指３"],
    "middle": ["中指１", "中指２", "中指３"],
    "ring": ["薬指１", "薬指２", "薬指３"],
    "pinky": ["小指１", "小指２", "小指３"],
}

ENDFIELD_FACE_SIGNATURE = [
    "eyeLf01Joint", "eyeRt01Joint", "faceLfIrisJoint", "faceRtIrisJoint",
    "browLf01Joint", "browRt01Joint", "faceMdJawDnJoint", "NoseMd01Joint",
]

PROFILES = [
    {
        "id": "UMA_MUSUME_MMD_HYBRID",
        "game_family": "Uma Musume",
        "dialect": "MMD Hybrid",
        "source_style": "MMD / Blender hybrid",
        "signals": [
            ("prefix", "Sp_Hi_MSkirt0_", 4.0),
            ("prefix", "Sp_He_Hair", 4.0),
            ("prefix", "Sp_Hi_Tail0_", 4.0),
            ("exact", "上半身", 1.0),
            ("exact", "上半身2", 1.0),
            ("exact", "下半身", 1.0),
            ("exact", "腕.L", 2.0),
            ("exact", "ひじ.L", 2.0),
            ("exact", "手首.L", 2.0),
        ],
        "canonical": {
            "root": "センター",
            "root_motion": "センター",
            "global_offset": "グルーブ",
            "torso_control": "UpBody_Ctrl",
            "waist": "Waist",
            "pelvis": "下半身",
            "spine_01": "上半身",
            "spine_02": "上半身2",
            "neck": "首",
            "head": "頭",
            "jaw": "Chin",
            "eye.L": "目.L",
            "eye.R": "目.R",
            "shoulder.L": "肩.L",
            "upper_arm.L": "腕.L",
            "forearm.L": "ひじ.L",
            "hand.L": "手首.L",
            "shoulder.R": "肩.R",
            "upper_arm.R": "腕.R",
            "forearm.R": "ひじ.R",
            "hand.R": "手首.R",
            "thigh.L": "足.L",
            "shin.L": "ひざ.L",
            "foot.L": "足首.L",
            "toe.L": "足先EX.L",
            "thigh.R": "足.R",
            "shin.R": "ひざ.R",
            "foot.R": "足首.R",
            "toe.R": "足先EX.R",
        },
    },
    {
        "id": "ENDFIELD_SEGMENTED",
        "game_family": "Arknights: Endfield",
        "dialect": "Endfield Segmented",
        "source_style": "Game FBX-derived",
        "signals": [
            ("exact", "Root_M", 3.0),
            ("exact", "Hip_L", 3.0),
            ("exact", "Knee_L", 3.0),
            ("exact", "Ankle_L", 3.0),
            ("exact", "Spine1_M", 3.0),
            ("exact", "Chest_M", 3.0),
            ("exact", "Scapula_L", 3.0),
            ("exact", "Shoulder_L", 3.0),
            ("exact", "Elbow_L", 3.0),
            ("exact", "Wrist_L", 3.0),
        ] + [("exact", n, 0.5) for n in ENDFIELD_FACE_SIGNATURE],
        "canonical": {
            "root": "Root",
            "root_motion": "Root_M",
            "pelvis": "Root_M",
            "spine_01": "Spine1_M",
            "spine_02": "Spine2_M",
            "chest": "Chest_M",
            "neck": "Neck_M",
            "head": "Head_M",
            "clavicle.L": "Scapula_L",
            "upper_arm.L": "Shoulder_L",
            "forearm.L": "Elbow_L",
            "hand.L": "Wrist_L",
            "clavicle.R": "Scapula_R",
            "upper_arm.R": "Shoulder_R",
            "forearm.R": "Elbow_R",
            "hand.R": "Wrist_R",
            "thigh.L": "Hip_L",
            "shin.L": "Knee_L",
            "foot.L": "Ankle_L",
            "toe.L": "Toes_L",
            "thigh.R": "Hip_R",
            "shin.R": "Knee_R",
            "foot.R": "Ankle_R",
            "toe.R": "Toes_R",
        },
    },
    {
        "id": "ENDFIELD_BIPED",
        "game_family": "Arknights: Endfield",
        "dialect": "Endfield Biped",
        "source_style": "Game FBX-derived",
        "signals": [
            ("exact", "Bip001_Pelvis", 3.0),
            ("exact", "Bip001_Spine", 3.0),
            ("exact", "Bip001_Spine2", 3.0),
            ("exact", "Bip001_L_UpperArm", 3.0),
            ("exact", "Bip001_L_Forearm", 3.0),
            ("exact", "Bip001_L_Hand", 3.0),
            ("exact", "Bip001_L_Thigh", 3.0),
            ("exact", "Bip001_L_Calf", 3.0),
            ("exact", "Bip001_L_Foot", 3.0),
        ] + [("exact", n, 0.5) for n in ENDFIELD_FACE_SIGNATURE],
        "canonical": {
            "root": "Bip001",
            "pelvis": "Bip001_Pelvis",
            "spine_01": "Bip001_Spine",
            "spine_02": "Bip001_Spine1",
            "chest": "Bip001_Spine2",
            "neck": "Bip001_Neck",
            "head": "Bip001_Head",
            "clavicle.L": "Bip001_L_Clavicle",
            "upper_arm.L": "Bip001_L_UpperArm",
            "forearm.L": "Bip001_L_Forearm",
            "hand.L": "Bip001_L_Hand",
            "clavicle.R": "Bip001_R_Clavicle",
            "upper_arm.R": "Bip001_R_UpperArm",
            "forearm.R": "Bip001_R_Forearm",
            "hand.R": "Bip001_R_Hand",
            "thigh.L": "Bip001_L_Thigh",
            "shin.L": "Bip001_L_Calf",
            "foot.L": "Bip001_L_Foot",
            "toe.L": "Bip001_L_Toe0",
            "thigh.R": "Bip001_R_Thigh",
            "shin.R": "Bip001_R_Calf",
            "foot.R": "Bip001_R_Foot",
            "toe.R": "Bip001_R_Toe0",
        },
    },
    {
        "id": "MMD_STANDARD",
        "game_family": "Generic MMD / PMX",
        "dialect": "MMD Standard",
        "source_style": "PMX/MMD-derived",
        "signals": [
            ("exact", "全ての親", 2.0),
            ("exact", "センター", 2.0),
            ("exact", "下半身", 2.0),
            ("exact", "上半身", 2.0),
            ("exact", "上半身2", 1.0),
            ("exact", "首", 1.0),
            ("exact", "頭", 1.0),
            ("exact", "左腕", 2.0),
            ("exact", "左ひじ", 2.0),
            ("exact", "左手首", 2.0),
            ("exact", "左足", 2.0),
            ("exact", "左ひざ", 2.0),
            ("exact", "左足首", 2.0),
        ],
        "canonical": {
            "root": "全ての親",
            "root_motion": "センター",
            "global_offset": "グルーブ",
            "waist": "腰",
            "pelvis": "下半身",
            "spine_01": "上半身",
            "spine_02": "上半身2",
            "neck": "首",
            "head": "頭",
            "eye.L": "左目",
            "eye.R": "右目",
            "shoulder.L": "左肩",
            "upper_arm.L": "左腕",
            "forearm.L": "左ひじ",
            "hand.L": "左手首",
            "shoulder.R": "右肩",
            "upper_arm.R": "右腕",
            "forearm.R": "右ひじ",
            "hand.R": "右手首",
            "thigh.L": "左足",
            "shin.L": "左ひざ",
            "foot.L": "左足首",
            "toe.L": "左つま先",
            "thigh.R": "右足",
            "shin.R": "右ひざ",
            "foot.R": "右足首",
            "toe.R": "右つま先",
        },
    },
]

# Classification remains name/topology-oriented. It never
# changes deformation flags or the armature hierarchy.
CLASS_PATTERNS = {
    "CORRECTIVE": [
        re.compile(r"^corrective_", re.I),
        re.compile(r"(?:^|_)(?:t[xyz]|r[xyz])_(?:plus|minus)$", re.I),
        re.compile(r"(?:correct|corrective|補正|補助)", re.I),
    ],
    "TWIST": [
        re.compile(r"(?:twist|roll|捩|ねじ)", re.I),
    ],
    "INTERMEDIATE_DEFORM": [
        re.compile(r"(?:Hip|Knee|Shoulder|Elbow)Part\d+_[LR]$", re.I),
        re.compile(r"(?:part|segment)\d+", re.I),
    ],
    "MECHANISM": [
        re.compile(r"^SR_MCH_", re.I),
    ],
    "CONTROL": [
        re.compile(r"^SR_CTRL_", re.I),
        re.compile(r"(?:^|[_\.])(ctrl|control|handle)(?:$|[_\.])", re.I),
        re.compile(r"_Handle$", re.I),
    ],
    "FACIAL": [
        re.compile(r"(?:face|eye|brow|lip|jaw|tongue|nose|pupil|iris|eyelash|cheek)", re.I),
        re.compile(r"(?:目|眉|口|舌|鼻|顎|あご|歯)", re.I),
    ],
    "SECONDARY": [
        re.compile(r"(?:hair|skirt|tail|ribbon|veil|cloth|clothes|cape|ear|髪|スカート|尻尾|しっぽ)", re.I),
        re.compile(r"^Sp_(?:Hi|He)_", re.I),
    ],
    "ACCESSORY": [
        re.compile(r"(?:accessory|\bacc\b|weapon|prop|hat|maozi|帽子)", re.I),
        re.compile(r"^Sp_Ao_Acc", re.I),
    ],
}

IK_NAME_RE = re.compile(r"(?:\bIK\b|_IK_|IK_|_IK$|ＩＫ|IK親|pole|target)", re.I)

FACIAL_MARKERS = set(ENDFIELD_FACE_SIGNATURE)

# -----------------------------------------------------------------------------
# Utility helpers
# -----------------------------------------------------------------------------

def _norm(text):
    text = unicodedata.normalize("NFKC", str(text or ""))
    return re.sub(r"[^a-z0-9]+", "", text.casefold())

def _safe_filename(text):
    text = unicodedata.normalize("NFKC", text or "Armature")
    text = re.sub(r"[\\/:*?\"<>|]+", "_", text)
    text = re.sub(r"\s+", "_", text).strip("._")
    return text or "Armature"

def _vector(v):
    try:
        return [float(v[0]), float(v[1]), float(v[2])]
    except Exception:
        return [0.0, 0.0, 0.0]

def _object_poll_armature(_self, obj):
    return obj is not None and obj.type == 'ARMATURE'

def _resolve_armature(context, state):
    if state.target and state.target.type == 'ARMATURE':
        return state.target

    active = context.active_object
    if active:
        if active.type == 'ARMATURE':
            return active
        if active.type == 'MESH':
            try:
                arm = active.find_armature()
                if arm:
                    return arm
            except Exception:
                pass
            for mod in active.modifiers:
                if mod.type == 'ARMATURE' and mod.object:
                    return mod.object

    selected = [o for o in context.selected_objects if o.type == 'ARMATURE']
    if len(selected) == 1:
        return selected[0]

    visible = [o for o in context.view_layer.objects if o.type == 'ARMATURE' and not o.hide_get()]
    if len(visible) == 1:
        return visible[0]

    return None

def _is_ancestor(ancestor_bone, descendant_bone, max_depth=16):
    if not ancestor_bone or not descendant_bone or ancestor_bone == descendant_bone:
        return False
    cur = descendant_bone.parent
    depth = 0
    while cur and depth < max_depth:
        if cur == ancestor_bone:
            return True
        cur = cur.parent
        depth += 1
    return False

def _path_between(ancestor_bone, descendant_bone, max_depth=32):
    if not ancestor_bone or not descendant_bone or ancestor_bone == descendant_bone:
        return []
    path = []
    cur = descendant_bone.parent
    depth = 0
    while cur and depth < max_depth:
        if cur == ancestor_bone:
            return list(reversed(path))
        path.append(cur.name)
        cur = cur.parent
        depth += 1
    return []

def _profile_score(profile, names, name_set):
    total = 0.0
    hits = 0.0
    evidence = []
    for mode, token, weight in profile["signals"]:
        total += weight
        found = False
        if mode == "exact":
            found = token in name_set
        elif mode == "prefix":
            found = any(n.startswith(token) for n in names)
        elif mode == "regex":
            rx = re.compile(token)
            found = any(rx.search(n) for n in names)
        if found:
            hits += weight
            evidence.append(token)
    return (hits / total if total else 0.0), evidence

def _detect_profile(names):
    name_set = set(names)
    scored = []
    for profile in PROFILES:
        score, evidence = _profile_score(profile, names, name_set)
        scored.append((score, profile, evidence))
    scored.sort(key=lambda x: x[0], reverse=True)

    best_score, best, evidence = scored[0]
    if best_score < 0.42:
        best = {
            "id": "GENERIC_HUMANOID",
            "game_family": "Unknown / Generic",
            "dialect": "Generic Humanoid",
            "source_style": "Unknown",
            "canonical": {},
        }
        evidence = []

    return best, best_score, evidence, [
        {
            "profile": p[1]["id"],
            "score": round(p[0], 4),
        }
        for p in scored
    ]

def _candidate_matches(alias, bone_names, norm_map):
    out = []
    if alias in bone_names:
        out.append((alias, 0.86, "exact_alias"))
    na = _norm(alias)
    if na in norm_map:
        for bone_name in norm_map[na]:
            if bone_name != alias:
                out.append((bone_name, 0.74, "normalized_alias"))
    return out

def _resolve_semantics(arm_obj, profile):
    bones = arm_obj.data.bones
    bone_names = [b.name for b in bones]
    bone_set = set(bone_names)
    norm_map = defaultdict(list)
    for name in bone_names:
        norm_map[_norm(name)].append(name)

    roles = set(GENERIC_ALIASES.keys()) | set(profile.get("canonical", {}).keys())
    mapping = {}

    for role in sorted(roles):
        candidates = {}

        canonical = profile.get("canonical", {}).get(role)
        if canonical:
            if canonical in bone_set:
                candidates[canonical] = (0.96, "profile_exact")
            else:
                nc = _norm(canonical)
                for bone_name in norm_map.get(nc, []):
                    candidates[bone_name] = max(candidates.get(bone_name, (0.0, "")), (0.90, "profile_normalized"))

        for alias in GENERIC_ALIASES.get(role, []):
            for bone_name, score, source in _candidate_matches(alias, bone_set, norm_map):
                prev = candidates.get(bone_name)
                if not prev or score > prev[0]:
                    candidates[bone_name] = (score, source)

        if not candidates:
            mapping[role] = {
                "role": role,
                "bone": None,
                "confidence": 0.0,
                "evidence": [],
            }
            continue

        ranked = sorted(candidates.items(), key=lambda kv: (-kv[1][0], len(kv[0]), kv[0]))
        bone_name, (score, source) = ranked[0]
        mapping[role] = {
            "role": role,
            "bone": bone_name,
            "confidence": score,
            "evidence": [source],
        }

    # Topology validation. It increases confidence but never replaces a strong
    # name match with a speculative spatial guess.
    topology_pairs = set()
    for chain in EXPECTED_CHAINS:
        compact = [r for r in chain if mapping.get(r, {}).get("bone")]
        for a, b in zip(compact, compact[1:]):
            topology_pairs.add((a, b))

    for parent_role, child_role in topology_pairs:
        parent_name = mapping[parent_role]["bone"]
        child_name = mapping[child_role]["bone"]
        if not parent_name or not child_name:
            continue
        parent_bone = bones.get(parent_name)
        child_bone = bones.get(child_name)
        if _is_ancestor(parent_bone, child_bone):
            for role in (parent_role, child_role):
                mapping[role]["confidence"] = min(1.0, mapping[role]["confidence"] + 0.04)
                mapping[role]["evidence"].append("topology_consistent")
        else:
            # Some rigs have parallel control/deform chains. Keep the name match,
            # but mark the topology conflict in the evidence.
            mapping[child_role]["confidence"] = max(0.0, mapping[child_role]["confidence"] - 0.05)
            mapping[child_role]["evidence"].append("topology_unconfirmed")

    # Bilateral support: if both sides of a pair resolve, each gets a small bonus.
    bilateral_pairs = [
        ("clavicle.L", "clavicle.R"), ("shoulder.L", "shoulder.R"),
        ("upper_arm.L", "upper_arm.R"), ("forearm.L", "forearm.R"),
        ("hand.L", "hand.R"), ("thigh.L", "thigh.R"),
        ("shin.L", "shin.R"), ("foot.L", "foot.R"), ("toe.L", "toe.R"),
        ("eye.L", "eye.R"),
    ]
    for left_role, right_role in bilateral_pairs:
        if mapping.get(left_role, {}).get("bone") and mapping.get(right_role, {}).get("bone"):
            for role in (left_role, right_role):
                mapping[role]["confidence"] = min(1.0, mapping[role]["confidence"] + 0.02)
                mapping[role]["evidence"].append("bilateral_pair")

    return mapping

def _resolve_fingers(arm_obj, profile_id):
    names = set(b.name for b in arm_obj.data.bones)
    result = {}

    # Uma / suffix-MMD convention.
    for side in ("L", "R"):
        for finger, segments in MMD_FINGERS.items():
            key = f"{finger}.{side}"
            found = []
            for base in segments:
                n = f"{base}.{side}"
                if n in names:
                    found.append(n)
            if found:
                result[key] = found

    # Standard MMD prefix convention.
    jp_side = {"L": "左", "R": "右"}
    for side in ("L", "R"):
        for finger, segments in MMD_PREFIX_FINGERS.items():
            key = f"{finger}.{side}"
            if key in result and len(result[key]) >= 3:
                continue
            found = []
            for base in segments:
                n = f"{jp_side[side]}{base}"
                if n in names:
                    found.append(n)
            if found:
                result[key] = found

    # Endfield segmented convention.
    endfield_words = {
        "thumb": "Thumb", "index": "Index", "middle": "Middle",
        "ring": "Ring", "pinky": "Pinky",
    }
    for side in ("L", "R"):
        for finger, word in endfield_words.items():
            key = f"{finger}.{side}"
            found = [f"{word}Finger{i}_{side}" for i in (1, 2, 3) if f"{word}Finger{i}_{side}" in names]
            if len(found) > len(result.get(key, [])):
                result[key] = found

    # Endfield Biped / 3ds Max Biped-like fingers.
    finger_starts = {
        "thumb": ("0", "01", "02"),
        "index": ("1", "11", "12"),
        "middle": ("2", "21", "22"),
        "ring": ("3", "31", "32"),
        "pinky": ("4", "41", "42"),
    }
    for side in ("L", "R"):
        for finger, suffixes in finger_starts.items():
            key = f"{finger}.{side}"
            found = [f"Bip001_{side}_Finger{s}" for s in suffixes if f"Bip001_{side}_Finger{s}" in names]
            if len(found) > len(result.get(key, [])):
                result[key] = found

    return result

def _constraint_analysis(arm_obj):
    counts = Counter()
    ik_rows = []
    target_bones = set()
    pole_bones = set()

    if not arm_obj.pose:
        return {
            "total": 0,
            "by_type": {},
            "ik_count": 0,
            "ik": [],
            "target_bones": [],
            "pole_bones": [],
        }

    for pb in arm_obj.pose.bones:
        for c in pb.constraints:
            counts[c.type] += 1
            if c.type == 'IK':
                target = getattr(c, "target", None)
                pole_target = getattr(c, "pole_target", None)
                subtarget = getattr(c, "subtarget", "") or ""
                pole_subtarget = getattr(c, "pole_subtarget", "") or ""
                if target == arm_obj and subtarget:
                    target_bones.add(subtarget)
                if pole_target == arm_obj and pole_subtarget:
                    pole_bones.add(pole_subtarget)
                ik_rows.append({
                    "owner_bone": pb.name,
                    "target_object": target.name if target else None,
                    "target_bone": subtarget or None,
                    "pole_object": pole_target.name if pole_target else None,
                    "pole_bone": pole_subtarget or None,
                    "chain_count": int(getattr(c, "chain_count", 0)),
                    "influence": float(getattr(c, "influence", 1.0)),
                    "mute": bool(getattr(c, "mute", False)),
                })

    return {
        "total": sum(counts.values()),
        "by_type": dict(sorted(counts.items())),
        "ik_count": counts.get('IK', 0),
        "ik": ik_rows,
        "target_bones": sorted(target_bones),
        "pole_bones": sorted(pole_bones),
    }

def _matches_any(name, patterns):
    return any(rx.search(name) for rx in patterns)

def _classify_bones(arm_obj, mapping, constraint_info):
    primary_names = {v["bone"] for v in mapping.values() if v.get("bone")}
    ik_targets = set(constraint_info["target_bones"])
    ik_poles = set(constraint_info["pole_bones"])
    result = {}

    for bone in arm_obj.data.bones:
        name = bone.name
        if name in ik_poles or re.search(r"(?:pole|ポール)", name, re.I):
            cls = "IK_POLE"
        elif name in ik_targets or IK_NAME_RE.search(name):
            cls = "IK_TARGET"
        elif name in primary_names:
            cls = "PRIMARY"
        elif _matches_any(name, CLASS_PATTERNS["CORRECTIVE"]):
            cls = "CORRECTIVE"
        elif _matches_any(name, CLASS_PATTERNS["TWIST"]):
            cls = "TWIST"
        elif _matches_any(name, CLASS_PATTERNS["INTERMEDIATE_DEFORM"]):
            cls = "INTERMEDIATE_DEFORM"
        elif _matches_any(name, CLASS_PATTERNS["FACIAL"]):
            cls = "FACIAL"
        elif _matches_any(name, CLASS_PATTERNS["SECONDARY"]):
            cls = "SECONDARY"
        elif _matches_any(name, CLASS_PATTERNS["ACCESSORY"]):
            cls = "ACCESSORY"
        elif _matches_any(name, CLASS_PATTERNS["MECHANISM"]):
            cls = "MECHANISM"
        elif _matches_any(name, CLASS_PATTERNS["CONTROL"]):
            cls = "CONTROL"
        else:
            cls = "UNKNOWN"
        result[name] = cls

    return result

def _chain_analysis(arm_obj, mapping):
    bones = arm_obj.data.bones
    out = {}
    chain_specs = {
        "arm.L": ["upper_arm.L", "forearm.L", "hand.L"],
        "arm.R": ["upper_arm.R", "forearm.R", "hand.R"],
        "leg.L": ["thigh.L", "shin.L", "foot.L", "toe.L"],
        "leg.R": ["thigh.R", "shin.R", "foot.R", "toe.R"],
        "spine": ["pelvis", "spine_01", "spine_02", "chest", "neck", "head"],
    }

    for chain_name, roles in chain_specs.items():
        resolved = [(role, mapping.get(role, {}).get("bone")) for role in roles]
        resolved = [(r, n) for r, n in resolved if n]
        links = []
        valid_links = 0
        for (ra, a), (rb, b) in zip(resolved, resolved[1:]):
            ba = bones.get(a)
            bb = bones.get(b)
            valid = _is_ancestor(ba, bb)
            if valid:
                valid_links += 1
            links.append({
                "from_role": ra,
                "from_bone": a,
                "to_role": rb,
                "to_bone": b,
                "ancestor_valid": valid,
                "intermediate_bones": _path_between(ba, bb) if valid else [],
            })
        expected_links = max(0, len(resolved) - 1)
        out[chain_name] = {
            "resolved_roles": [r for r, _n in resolved],
            "resolved_bones": [n for _r, n in resolved],
            "links": links,
            "topology_score": (valid_links / expected_links) if expected_links else 0.0,
        }
    return out

def _semantic_summary(mapping, fingers):
    required = [
        "pelvis", "spine_01", "neck", "head",
        "upper_arm.L", "forearm.L", "hand.L",
        "upper_arm.R", "forearm.R", "hand.R",
        "thigh.L", "shin.L", "foot.L",
        "thigh.R", "shin.R", "foot.R",
    ]
    found = [r for r in required if mapping.get(r, {}).get("bone")]
    avg_conf = 0.0
    if found:
        avg_conf = sum(mapping[r]["confidence"] for r in found) / len(found)
    finger_segments = sum(len(v) for v in fingers.values())
    return {
        "required_found": len(found),
        "required_total": len(required),
        "coverage": len(found) / len(required),
        "average_confidence": avg_conf,
        "finger_segments_found": finger_segments,
    }

def _make_warnings(profile, profile_score, mapping, constraints, classifications, chains):
    warnings = []

    missing = [r for r in (
        "pelvis", "spine_01", "neck", "head",
        "upper_arm.L", "forearm.L", "hand.L",
        "upper_arm.R", "forearm.R", "hand.R",
        "thigh.L", "shin.L", "foot.L",
        "thigh.R", "shin.R", "foot.R",
    ) if not mapping.get(r, {}).get("bone")]
    if missing:
        warnings.append("Missing core semantic roles: " + ", ".join(missing))

    if profile["id"] == "GENERIC_HUMANOID":
        warnings.append("No known rig profile reached the detection threshold; generic aliases are being used.")
    elif profile_score < 0.70:
        warnings.append("Rig profile confidence is moderate; verify semantic mappings before building controls.")

    intermediate_count = sum(1 for c in classifications.values() if c == "INTERMEDIATE_DEFORM")
    twist_count = sum(1 for c in classifications.values() if c == "TWIST")
    corrective_count = sum(1 for c in classifications.values() if c == "CORRECTIVE")
    if intermediate_count:
        warnings.append(f"Detected {intermediate_count} intermediate deform bones; direct fixed chain-count IK may be unsafe.")
    if twist_count:
        warnings.append(f"Detected {twist_count} twist/roll bones; preserve them when constructing mechanism chains.")
    if corrective_count:
        warnings.append(f"Detected {corrective_count} corrective bones; they should not be treated as primary joints.")

    if constraints["ik_count"]:
        warnings.append(f"Detected {constraints['ik_count']} functional Blender IK constraint(s); future rig building should evaluate reuse before replacement.")
    else:
        ik_named = sum(1 for n, c in classifications.items() if c == "IK_TARGET")
        if ik_named:
            warnings.append(f"Found {ik_named} IK/target-style bone name(s), but no functional Blender IK constraints were detected.")

    bad_chains = [name for name, data in chains.items() if data["resolved_bones"] and data["topology_score"] < 0.5]
    if bad_chains:
        warnings.append("Topology is weak or parallel for: " + ", ".join(bad_chains) + ".")

    return warnings

def analyze_armature(arm_obj):
    bones = arm_obj.data.bones
    names = [b.name for b in bones]

    profile, profile_score, profile_evidence, profile_scores = _detect_profile(names)
    mapping = _resolve_semantics(arm_obj, profile)
    fingers = _resolve_fingers(arm_obj, profile["id"])
    constraints = _constraint_analysis(arm_obj)
    classifications = _classify_bones(arm_obj, mapping, constraints)
    chains = _chain_analysis(arm_obj, mapping)
    summary = _semantic_summary(mapping, fingers)
    warnings = _make_warnings(profile, profile_score, mapping, constraints, classifications, chains)

    class_counts = Counter(classifications.values())
    pose_bone_count = len(arm_obj.pose.bones) if arm_obj.pose else 0
    bone_collection_count = len(getattr(arm_obj.data, "collections", []))

    bone_rows = []
    for bone in bones:
        bone_rows.append({
            "name": bone.name,
            "parent": bone.parent.name if bone.parent else None,
            "children": [c.name for c in bone.children],
            "head_local": _vector(bone.head_local),
            "tail_local": _vector(bone.tail_local),
            "length": float(bone.length),
            "use_deform": bool(bone.use_deform),
            "classification": classifications.get(bone.name, "UNKNOWN"),
        })

    semantic_rows = {}
    for role, row in mapping.items():
        semantic_rows[role] = {
            "bone": row["bone"],
            "confidence": round(float(row["confidence"]), 4),
            "evidence": list(dict.fromkeys(row["evidence"])),
        }

    result = {
        "saberrig": {
            "version": ADDON_VERSION,
            "target_blender": TARGET_BLENDER,
            "analysis_type": "Semantic Armature Analyzer + Control Rig Foundation",
            "non_destructive": True,
        },
        "armature": {
            "object_name": arm_obj.name,
            "data_name": arm_obj.data.name,
            "bone_count": len(bones),
            "pose_bone_count": pose_bone_count,
            "bone_collection_count": bone_collection_count,
        },
        "detection": {
            "game_family": profile["game_family"],
            "rig_dialect": profile["dialect"],
            "profile_id": profile["id"],
            "source_style_hint": profile["source_style"],
            "profile_confidence": round(float(profile_score), 4),
            "profile_evidence": profile_evidence,
            "all_profile_scores": profile_scores,
        },
        "semantic_summary": {
            "required_found": summary["required_found"],
            "required_total": summary["required_total"],
            "coverage": round(summary["coverage"], 4),
            "average_confidence": round(summary["average_confidence"], 4),
            "finger_segments_found": summary["finger_segments_found"],
        },
        "semantic_map": semantic_rows,
        "fingers": fingers,
        "chains": chains,
        "constraints": constraints,
        "classifications": {
            "counts": dict(sorted(class_counts.items())),
            "by_bone": classifications,
        },
        "warnings": warnings,
        "bones": bone_rows,
    }
    return result

# -----------------------------------------------------------------------------
# SaberRig control-rig foundation
# -----------------------------------------------------------------------------

def _iter_bone_collections(arm_data):
    """Return every bone collection, including nested collections."""
    collections_all = getattr(arm_data, "collections_all", None)
    if collections_all is not None:
        return list(collections_all)
    return list(getattr(arm_data, "collections", []))

def _find_bone_collection(arm_data, name):
    for collection in _iter_bone_collections(arm_data):
        if collection.name == name:
            return collection
    return None

def _ensure_bone_collection(arm_data, name, parent=None):
    collection = _find_bone_collection(arm_data, name)
    if collection:
        return collection
    try:
        collection = arm_data.collections.new(name, parent=parent)
    except TypeError:
        collection = arm_data.collections.new(name)
        if parent is not None and hasattr(collection, "parent"):
            try:
                collection.parent = parent
            except Exception:
                pass
    return collection

def _ensure_widget_collection():
    """Create the hidden object collection that stores SaberRig custom-shape geometry."""
    collection = bpy.data.collections.get(SR_WIDGET_COLLECTION)
    if collection is None:
        collection = bpy.data.collections.new(SR_WIDGET_COLLECTION)
    scene = getattr(bpy.context, "scene", None)
    if scene and scene.collection.children.get(collection.name) is None:
        try:
            scene.collection.children.link(collection)
        except RuntimeError:
            pass
    collection.hide_render = True
    # Custom bone shapes are drawn from their object data even when the widget
    # collection itself is hidden from the normal scene viewport.
    try:
        collection.hide_viewport = True
    except Exception:
        pass
    collection["saberrig_widget_collection"] = True
    return collection

def _ensure_wire_widget(name, verts, edges):
    """Return a reusable procedural wire mesh for ``PoseBone.custom_shape``.

    Widget geometry is refreshed in place when the library stamp changes so
    existing .blend files keep their custom-shape object references.
    """
    obj = bpy.data.objects.get(name)
    collection = _ensure_widget_collection()

    if obj is not None and obj.type != 'MESH':
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:
            pass
        obj = None

    if obj is None:
        mesh = bpy.data.meshes.new(f"{name}_Mesh")
        obj = bpy.data.objects.new(name, mesh)
        try:
            collection.objects.link(obj)
        except RuntimeError:
            pass
    else:
        mesh = obj.data
        if collection.objects.get(obj.name) is None:
            try:
                collection.objects.link(obj)
            except RuntimeError:
                pass

    current_version = str(obj.get("saberrig_widget_version", ""))
    geometry_stamp = f"{SR_WIDGET_LIBRARY_VERSION}:{len(verts)}:{len(edges)}"
    if current_version != geometry_stamp:
        old_mesh = obj.data
        new_mesh = bpy.data.meshes.new(f"{name}_Mesh_RC3")
        new_mesh.from_pydata(verts, edges, [])
        new_mesh.update()
        obj.data = new_mesh
        if old_mesh is not None and old_mesh.users == 0:
            try:
                bpy.data.meshes.remove(old_mesh)
            except Exception:
                pass
        obj["saberrig_widget_version"] = geometry_stamp

    obj["saberrig_widget"] = True
    obj["saberrig_widget_library"] = SR_WIDGET_LIBRARY_VERSION
    obj.hide_render = True
    obj.hide_select = True
    try:
        obj.display_type = 'WIRE'
        obj.show_in_front = True
    except Exception:
        pass
    return obj

def _ensure_widgets():
    """Create the reusable animator widgets used by SaberRig controls."""
    # FK bone widget: tapered wire cage along local +Y. It reads like a
    # clean animator bone rather than a Blender octahedron and stays legible from
    # front/side/perspective views.
    fk_verts = [
        (-0.10, 0.02, -0.10), (0.10, 0.02, -0.10),
        (0.10, 0.02, 0.10), (-0.10, 0.02, 0.10),
        (-0.22, 0.22, -0.22), (0.22, 0.22, -0.22),
        (0.22, 0.22, 0.22), (-0.22, 0.22, 0.22),
        (-0.16, 0.80, -0.16), (0.16, 0.80, -0.16),
        (0.16, 0.80, 0.16), (-0.16, 0.80, 0.16),
        (-0.07, 0.98, -0.07), (0.07, 0.98, -0.07),
        (0.07, 0.98, 0.07), (-0.07, 0.98, 0.07),
    ]
    box_edges = [
        (0, 1), (1, 2), (2, 3), (3, 0),
        (4, 5), (5, 6), (6, 7), (7, 4),
        (0, 4), (1, 5), (2, 6), (3, 7),
    ]
    fk_edges = [
        (0,1),(1,2),(2,3),(3,0),
        (4,5),(5,6),(6,7),(7,4),
        (8,9),(9,10),(10,11),(11,8),
        (12,13),(13,14),(14,15),(15,12),
        (0,4),(1,5),(2,6),(3,7),
        (4,8),(5,9),(6,10),(7,11),
        (8,12),(9,13),(10,14),(11,15),
    ]

    # Hand IK: centered cube with diagonal corner cues; easy to grab in any view.
    hand_verts = [
        (-0.5, -0.5, -0.5), (0.5, -0.5, -0.5),
        (0.5, 0.5, -0.5), (-0.5, 0.5, -0.5),
        (-0.5, -0.5, 0.5), (0.5, -0.5, 0.5),
        (0.5, 0.5, 0.5), (-0.5, 0.5, 0.5),
    ]
    hand_edges = box_edges + [(0, 6), (1, 7), (2, 4), (3, 5)]

    # Foot IK: a low, elongated wire box around the foot. It is intentionally
    # flatter than the hand widget so it reads like a ground/foot controller.
    foot_verts = [
        (-0.55, -0.20, -0.12), (0.55, -0.20, -0.12),
        (0.55, 1.10, -0.12), (-0.55, 1.10, -0.12),
        (-0.55, -0.20, 0.12), (0.55, -0.20, 0.12),
        (0.55, 1.10, 0.12), (-0.55, 1.10, 0.12),
    ]
    foot_edges = box_edges + [(0, 6), (1, 7), (2, 4), (3, 5)]

    # Foot master: flat heel controller. Its origin sits behind the heel and
    # the arrow points toward the foot. This is the animator's primary leg IK
    # translation control; the ankle FootIK child remains an orientation pivot.
    foot_master_verts = [
        (-0.75, -0.20, 0.0), (0.75, -0.20, 0.0),
        (0.75, 0.55, 0.0), (-0.75, 0.55, 0.0),
        (-0.38, 0.55, 0.0), (0.0, 1.05, 0.0), (0.38, 0.55, 0.0),
    ]
    foot_master_edges = [
        (0, 1), (1, 2), (2, 6), (6, 5), (5, 4), (4, 3), (3, 0),
        (3, 2),
    ]

    # Reverse-foot widgets. Their bones are aligned to the semantic foot frame,
    # so the shapes remain readable even on imported skeletons with unusual roll.
    heel_roll_verts = [
        (-0.72, -0.18, 0.0), (0.72, -0.18, 0.0),
        (0.58, 0.30, 0.0), (-0.58, 0.30, 0.0),
        (-0.28, 0.46, 0.0), (0.28, 0.46, 0.0),
    ]
    heel_roll_edges = [(0, 1), (1, 2), (2, 5), (5, 4), (4, 3), (3, 0)]

    ball_roll_verts = [
        (0.0, -0.52, 0.0), (0.58, 0.0, 0.0),
        (0.0, 0.52, 0.0), (-0.58, 0.0, 0.0),
        (0.0, -0.22, 0.22), (0.0, 0.22, 0.22),
    ]
    ball_roll_edges = [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (0, 4), (2, 5)]

    toe_roll_verts = [
        (-0.46, -0.12, 0.0), (0.46, -0.12, 0.0),
        (0.32, 0.32, 0.0), (0.0, 0.70, 0.0), (-0.32, 0.32, 0.0),
        (0.0, 0.18, 0.24),
    ]
    toe_roll_edges = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 0), (0, 5), (1, 5), (3, 5)]

    # Body controls use deliberately broad silhouettes so a
    # novice animator can read the whole-body hierarchy immediately.
    def _ring(radius=1.0, y=0.0, segments=20):
        # XZ ring: body controls are normally aligned with local +Y along the
        # spine, so this displays as a horizontal ring around the character.
        verts = []
        edges = []
        for i in range(segments):
            a = (math.tau * i) / segments
            verts.append((math.cos(a) * radius, y, math.sin(a) * radius))
            edges.append((i, (i + 1) % segments))
        return verts, edges

    master_verts, master_edges = _ring(1.0, 0.0, 24)
    # Cardinal cues on the global Master ring.
    master_verts += [(0.0, 0.0, 1.00), (-0.16, 0.0, 0.75), (0.16, 0.0, 0.75),
                     (1.00, 0.0, 0.0), (0.75, 0.0, -0.16), (0.75, 0.0, 0.16)]
    master_edges += [(24, 25), (24, 26), (27, 28), (27, 29)]

    root_verts, root_edges = _ring(0.82, 0.0, 16)
    root_verts += [(-0.82, 0.0, 0.0), (0.82, 0.0, 0.0), (0.0, 0.0, -0.82), (0.0, 0.0, 0.82)]
    root_edges += [(16, 17), (18, 19)]

    cog_verts = [(0.0, -0.85, 0.0), (0.85, 0.0, 0.0), (0.0, 0.85, 0.0), (-0.85, 0.0, 0.0),
                 (0.0, 0.0, 0.35), (0.0, 0.0, -0.35)]
    cog_edges = [(0,1),(1,2),(2,3),(3,0),(0,4),(1,4),(2,4),(3,4),(0,5),(1,5),(2,5),(3,5)]

    hips_verts = [(-0.72,-0.32,-0.22),(0.72,-0.32,-0.22),(0.72,0.32,-0.22),(-0.72,0.32,-0.22),
                  (-0.72,-0.32,0.22),(0.72,-0.32,0.22),(0.72,0.32,0.22),(-0.72,0.32,0.22)]
    hips_edges = box_edges

    spine_verts, spine_edges = _ring(0.52, 0.0, 16)
    spine_verts += [(0.0,0.0,-0.45),(0.0,0.0,0.45)]
    spine_edges += [(16,17)]

    chest_verts, chest_edges = _ring(0.70, 0.0, 20)
    chest_verts += [(-0.70,0.0,0.0),(0.70,0.0,0.0),(0.0,0.0,-0.28),(0.0,0.0,0.28)]
    chest_edges += [(20,21),(22,23)]

    # Head: three orthogonal rings give a familiar animation-controller cage.
    head_verts = []
    head_edges = []
    _head_segments = 16
    for ring_axis in range(3):
        base = len(head_verts)
        for i in range(_head_segments):
            a = (math.tau * i) / _head_segments
            c, s = math.cos(a), math.sin(a)
            if ring_axis == 0:
                head_verts.append((0.0, c, s))
            elif ring_axis == 1:
                head_verts.append((c, 0.0, s))
            else:
                head_verts.append((c, s, 0.0))
            head_edges.append((base + i, base + ((i + 1) % _head_segments)))

    # Generic fallback: compact octahedral widget for any future SR_CTRL bone
    # that does not yet have a role-specific silhouette.
    generic_verts = [
        (0.0, 0.0, 0.72), (0.0, 0.0, -0.72),
        (0.72, 0.0, 0.0), (-0.72, 0.0, 0.0),
        (0.0, 0.72, 0.0), (0.0, -0.72, 0.0),
    ]
    generic_edges = [
        (0,2),(0,3),(0,4),(0,5),
        (1,2),(1,3),(1,4),(1,5),
        (2,4),(4,3),(3,5),(5,2),
    ]

    # Fingers: stylized open-hand fan. The control is property-driven rather
    # than transform-driven, so this broad shape is simply an easy selection
    # target near the palm.
    finger_verts = [
        (-0.46, 0.02, 0.0), (0.46, 0.02, 0.0),
        (0.40, 0.48, 0.0), (-0.40, 0.48, 0.0),
        (-0.34, 0.48, 0.0), (-0.42, 1.02, 0.0),
        (-0.16, 0.48, 0.0), (-0.18, 1.18, 0.0),
        (0.02, 0.48, 0.0), (0.02, 1.24, 0.0),
        (0.20, 0.48, 0.0), (0.24, 1.14, 0.0),
        (0.36, 0.43, 0.0), (0.48, 0.92, 0.0),
        (-0.40, 0.28, 0.0), (-0.72, 0.58, 0.0),
    ]
    finger_edges = [
        (0,1),(1,2),(2,3),(3,0),
        (4,5),(6,7),(8,9),(10,11),(12,13),(14,15),
    ]

    # Pole: octahedral diamond centered on the pole location.
    pole_verts = [
        (0.0, 0.0, 0.7), (0.0, 0.0, -0.7),
        (0.7, 0.0, 0.0), (-0.7, 0.0, 0.0),
        (0.0, 0.7, 0.0), (0.0, -0.7, 0.0),
    ]
    pole_edges = [
        (0, 2), (0, 3), (0, 4), (0, 5),
        (1, 2), (1, 3), (1, 4), (1, 5),
        (2, 4), (4, 3), (3, 5), (5, 2),
    ]

    return {
        "fk": _ensure_wire_widget(SR_WIDGET_FK, fk_verts, fk_edges),
        "hand_ik": _ensure_wire_widget(SR_WIDGET_HAND_IK, hand_verts, hand_edges),
        "foot_ik": _ensure_wire_widget(SR_WIDGET_FOOT_IK, foot_verts, foot_edges),
        "foot_master": _ensure_wire_widget(SR_WIDGET_FOOT_MASTER, foot_master_verts, foot_master_edges),
        "heel_roll": _ensure_wire_widget(SR_WIDGET_HEEL_ROLL, heel_roll_verts, heel_roll_edges),
        "ball_roll": _ensure_wire_widget(SR_WIDGET_BALL_ROLL, ball_roll_verts, ball_roll_edges),
        "toe_roll": _ensure_wire_widget(SR_WIDGET_TOE_ROLL, toe_roll_verts, toe_roll_edges),
        "pole": _ensure_wire_widget(SR_WIDGET_POLE, pole_verts, pole_edges),
        "master": _ensure_wire_widget(SR_WIDGET_MASTER, master_verts, master_edges),
        "root": _ensure_wire_widget(SR_WIDGET_ROOT, root_verts, root_edges),
        "cog": _ensure_wire_widget(SR_WIDGET_COG, cog_verts, cog_edges),
        "hips": _ensure_wire_widget(SR_WIDGET_HIPS, hips_verts, hips_edges),
        "spine": _ensure_wire_widget(SR_WIDGET_SPINE, spine_verts, spine_edges),
        "chest": _ensure_wire_widget(SR_WIDGET_CHEST, chest_verts, chest_edges),
        "head": _ensure_wire_widget(SR_WIDGET_HEAD, head_verts, head_edges),
        "fingers": _ensure_wire_widget(SR_WIDGET_FINGERS, finger_verts, finger_edges),
        "generic": _ensure_wire_widget(SR_WIDGET_GENERIC, generic_verts, generic_edges),
    }

def _assign_custom_shape(pose_bone, widget_obj, scale=(1.0, 1.0, 1.0), use_bone_size=True):
    if not pose_bone or not widget_obj:
        return
    pose_bone.custom_shape = widget_obj
    try:
        pose_bone.use_custom_shape_bone_size = bool(use_bone_size)
    except Exception:
        pass
    try:
        pose_bone.custom_shape_scale_xyz = scale
    except Exception:
        # Compatibility fallback for older/newer RNA variants.
        if hasattr(pose_bone, "custom_shape_scale"):
            try:
                pose_bone.custom_shape_scale = max(scale)
            except Exception:
                pass

def _setup_control_visuals(arm_obj):
    """Attach procedural custom shapes to every SaberRig animator control."""
    if not arm_obj or arm_obj.type != 'ARMATURE' or not arm_obj.pose:
        return 0

    widgets = _ensure_widgets()
    assigned_names = set()

    def bind(pb, widget_key, scale=(1.0, 1.0, 1.0), use_bone_size=True):
        if not pb:
            return
        _assign_custom_shape(
            pb, widgets[widget_key],
            scale=scale, use_bone_size=use_bone_size
        )
        assigned_names.add(pb.name)

    # Arms.
    for side in ("L", "R"):
        fk = _arm_fk_names(side)
        bind(arm_obj.pose.bones.get(fk["ctrl_clavicle"]), "fk", (0.90, 0.90, 0.90), True)
        bind(arm_obj.pose.bones.get(fk["ctrl_upper"]), "fk", (1.00, 1.00, 1.00), True)
        bind(arm_obj.pose.bones.get(fk["ctrl_forearm"]), "fk", (0.92, 0.92, 0.92), True)
        bind(arm_obj.pose.bones.get(fk["ctrl_hand"]), "fk", (1.10, 1.10, 1.10), True)

        ik = _arm_ik_names(side)
        bind(arm_obj.pose.bones.get(ik["ctrl_hand"]), "hand_ik", (1.35, 1.35, 1.35), True)
        bind(arm_obj.pose.bones.get(ik["ctrl_pole"]), "pole", (1.80, 1.80, 1.80), True)

    # Legs + reverse foot.
    for side in ("L", "R"):
        fk = _leg_fk_names(side)
        bind(arm_obj.pose.bones.get(fk["ctrl_thigh"]), "fk", (1.00, 1.00, 1.00), True)
        bind(arm_obj.pose.bones.get(fk["ctrl_shin"]), "fk", (0.95, 0.95, 0.95), True)
        bind(arm_obj.pose.bones.get(fk["ctrl_foot"]), "fk", (1.10, 1.10, 1.10), True)

        ik = _leg_ik_names(side)
        bind(arm_obj.pose.bones.get(ik.get("ctrl_master", "")), "foot_master", (1.35, 1.35, 1.35), True)
        bind(arm_obj.pose.bones.get(ik.get("ctrl_heel", "")), "heel_roll", (0.90, 0.90, 0.90), True)
        bind(arm_obj.pose.bones.get(ik.get("ctrl_ball", "")), "ball_roll", (0.78, 0.78, 0.78), True)
        bind(arm_obj.pose.bones.get(ik.get("ctrl_toe", "")), "toe_roll", (0.72, 0.72, 0.72), True)
        bind(arm_obj.pose.bones.get(ik["ctrl_foot"]), "foot_ik", (0.68, 0.68, 0.68), True)
        bind(arm_obj.pose.bones.get(ik["ctrl_pole"]), "pole", (1.80, 1.80, 1.80), True)

    # Body.
    body = _body_control_names()
    body_shapes = (
        ("ctrl_master", "master", (2.50, 2.50, 2.50), True),
        ("ctrl_root", "root", (1.75, 1.75, 1.75), True),
        ("ctrl_cog", "cog", (1.45, 1.45, 1.45), True),
        ("ctrl_hips", "hips", (1.05, 1.05, 1.05), True),
        ("ctrl_spine", "spine", (1.15, 1.15, 1.15), True),
        ("ctrl_chest", "chest", (1.20, 1.20, 1.20), True),
        ("ctrl_neck", "spine", (0.82, 0.82, 0.82), True),
        ("ctrl_head", "head", (0.92, 0.92, 0.92), True),
    )
    for key, widget_key, scale, use_bone_size in body_shapes:
        bind(arm_obj.pose.bones.get(body[key]), widget_key, scale, use_bone_size)

    # Finger property controls use the dedicated hand-shaped widget.
    for side in ("L", "R"):
        finger_ctrl = _finger_control_names(side)["ctrl_master"]
        bind(
            arm_obj.pose.bones.get(finger_ctrl),
            "fingers", (0.86, 0.86, 0.86), True
        )

    # Future-proof fallback: every generated animator CTRL should present a
    # selectable widget even when a new component has not yet gained a custom
    # silhouette.
    for pb in arm_obj.pose.bones:
        bone = pb.bone
        is_ctrl = (
            pb.name.startswith("SR_CTRL_")
            or (
                bool(bone.get("saberrig_generated", False))
                and str(bone.get("saberrig_kind", "")).upper() == "CTRL"
            )
        )
        if is_ctrl and pb.name not in assigned_names:
            bind(pb, "generic", (0.85, 0.85, 0.85), True)

    arm_obj["saberrig_widget_library_ready"] = True
    arm_obj["saberrig_widget_library_version"] = SR_WIDGET_LIBRARY_VERSION
    arm_obj["saberrig_widget_control_count"] = int(len(assigned_names))
    arm_obj["saberrig_widget_object_count"] = int(len(widgets))
    return len(assigned_names)

def _source_hide_snapshot_key():
    return "saberrig_source_hide_snapshot"

def _capture_source_hide_state(arm_obj):
    key = _source_hide_snapshot_key()
    if key in arm_obj.data:
        return
    snapshot = {}
    for bone in arm_obj.data.bones:
        if bool(bone.get("saberrig_generated", False)) or bone.name.startswith(("SR_CTRL_", "SR_MCH_")):
            continue
        snapshot[bone.name] = bool(bone.hide)
    arm_obj.data[key] = json.dumps(snapshot, ensure_ascii=False)

def _restore_source_hide_state(arm_obj):
    key = _source_hide_snapshot_key()
    raw = arm_obj.data.get(key, "")
    snapshot = {}
    if raw:
        try:
            snapshot = json.loads(raw)
        except Exception:
            snapshot = {}
    for bone in arm_obj.data.bones:
        if bool(bone.get("saberrig_generated", False)) or bone.name.startswith(("SR_CTRL_", "SR_MCH_")):
            continue
        bone.hide = bool(snapshot.get(bone.name, False))
    if key in arm_obj.data:
        try:
            del arm_obj.data[key]
        except Exception:
            pass

def _set_rig_view(arm_obj, controls_only):
    """Switch between a clean animator view and the original source-rig view."""
    if not arm_obj or arm_obj.type != 'ARMATURE':
        return False

    mechanism = _find_bone_collection(arm_obj.data, SR_COLLECTION_MECHANISM)
    controls = _find_bone_collection(arm_obj.data, SR_COLLECTION_CONTROLS)
    source = _find_bone_collection(arm_obj.data, SR_COLLECTION_SOURCE)
    try:
        if mechanism:
            mechanism.is_visible = False
        if controls:
            controls.is_visible = True
        if source:
            source.is_visible = True
    except Exception:
        pass

    if controls_only:
        _capture_source_hide_state(arm_obj)
        if "saberrig_prev_show_in_front" not in arm_obj:
            arm_obj["saberrig_prev_show_in_front"] = bool(getattr(arm_obj, "show_in_front", False))
        for bone in arm_obj.data.bones:
            generated = bool(bone.get("saberrig_generated", False)) or bone.name.startswith(("SR_CTRL_", "SR_MCH_"))
            if not generated:
                bone.hide = True
            else:
                kind = bone.get("saberrig_kind", "")
                bone.hide = (kind == "MCH" or bone.name.startswith("SR_MCH_"))
        arm_obj.show_in_front = True
        arm_obj["saberrig_view_mode"] = "CONTROLS"
    else:
        _restore_source_hide_state(arm_obj)
        for bone in arm_obj.data.bones:
            if bool(bone.get("saberrig_generated", False)) or bone.name.startswith(("SR_CTRL_", "SR_MCH_")):
                kind = bone.get("saberrig_kind", "")
                bone.hide = (kind == "MCH" or bone.name.startswith("SR_MCH_"))
        if "saberrig_prev_show_in_front" in arm_obj:
            try:
                arm_obj.show_in_front = bool(arm_obj["saberrig_prev_show_in_front"])
            except Exception:
                pass
            try:
                del arm_obj["saberrig_prev_show_in_front"]
            except Exception:
                pass
        arm_obj["saberrig_view_mode"] = "SOURCE"
    return True

def _widget_is_used(obj):
    if obj is None:
        return False
    for arm in (o for o in bpy.data.objects if o.type == 'ARMATURE' and o.pose):
        for pb in arm.pose.bones:
            if getattr(pb, "custom_shape", None) == obj:
                return True
    return False

def _cleanup_unused_widgets():
    for name in (
        SR_WIDGET_FK, SR_WIDGET_HAND_IK, SR_WIDGET_FOOT_IK, SR_WIDGET_FOOT_MASTER,
        SR_WIDGET_HEEL_ROLL, SR_WIDGET_BALL_ROLL, SR_WIDGET_TOE_ROLL, SR_WIDGET_POLE,
        SR_WIDGET_MASTER, SR_WIDGET_ROOT, SR_WIDGET_COG, SR_WIDGET_HIPS,
        SR_WIDGET_SPINE, SR_WIDGET_CHEST, SR_WIDGET_HEAD, SR_WIDGET_FINGERS,
        SR_WIDGET_GENERIC,
    ):
        obj = bpy.data.objects.get(name)
        if obj is not None and not _widget_is_used(obj):
            mesh = obj.data if obj.type == 'MESH' else None
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except Exception:
                continue
            if mesh is not None and mesh.users == 0:
                try:
                    bpy.data.meshes.remove(mesh)
                except Exception:
                    pass
    collection = bpy.data.collections.get(SR_WIDGET_COLLECTION)
    if collection is not None and len(collection.objects) == 0:
        try:
            bpy.data.collections.remove(collection)
        except Exception:
            pass

def _armature_is_editable(arm_obj):
    if not arm_obj or arm_obj.type != 'ARMATURE':
        return False
    if getattr(arm_obj, "library", None) is not None:
        return False
    if getattr(arm_obj.data, "library", None) is not None:
        return False
    return True

def _activate_armature(context, arm_obj, mode='OBJECT'):
    """Make arm_obj active and switch it to a predictable mode."""
    active = context.view_layer.objects.active
    if active and getattr(active, "mode", 'OBJECT') != 'OBJECT':
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            pass

    for obj in list(context.selected_objects):
        try:
            obj.select_set(False)
        except Exception:
            pass
    arm_obj.select_set(True)
    context.view_layer.objects.active = arm_obj
    if mode != 'OBJECT':
        bpy.ops.object.mode_set(mode=mode)

def _foundation_is_prepared(arm_obj):
    if not arm_obj or arm_obj.type != 'ARMATURE':
        return False
    if not bool(arm_obj.get("saberrig_prepared", False)):
        return False
    required = (SR_COLLECTION_ROOT, SR_COLLECTION_CONTROLS, SR_COLLECTION_MECHANISM, SR_COLLECTION_SOURCE)
    return all(_find_bone_collection(arm_obj.data, name) is not None for name in required)

def _generated_bones(arm_obj, component=None):
    rows = []
    if not arm_obj or arm_obj.type != 'ARMATURE':
        return rows
    for bone in arm_obj.data.bones:
        if not bool(bone.get("saberrig_generated", False)):
            continue
        if component is not None and bone.get("saberrig_component", "") != component:
            continue
        rows.append(bone.name)
    return rows

def _prepare_foundation(arm_obj, analysis=None):
    if not _armature_is_editable(arm_obj):
        raise RuntimeError("Target armature is linked/read-only or unavailable")

    arm_data = arm_obj.data
    root = _ensure_bone_collection(arm_data, SR_COLLECTION_ROOT)
    source = _ensure_bone_collection(arm_data, SR_COLLECTION_SOURCE, parent=root)
    mechanism = _ensure_bone_collection(arm_data, SR_COLLECTION_MECHANISM, parent=root)
    controls = _ensure_bone_collection(arm_data, SR_COLLECTION_CONTROLS, parent=root)

    # Source membership is additive; existing game/MMD collections are preserved.
    assigned = 0
    for bone in arm_data.bones:
        if bool(bone.get("saberrig_generated", False)) or bone.name.startswith(("SR_CTRL_", "SR_MCH_")):
            continue
        try:
            if source.assign(bone):
                assigned += 1
        except Exception:
            pass

    try:
        root.is_visible = True
        source.is_visible = True
        controls.is_visible = True
        mechanism.is_visible = False
        root.is_expanded = True
    except Exception:
        pass

    # Build the reusable procedural widget library during Prepare so the
    # rig is ready for custom-shape controls before individual components exist.
    widgets = _ensure_widgets()

    arm_obj["saberrig_prepared"] = True
    arm_obj["saberrig_version"] = ADDON_VERSION
    arm_obj["saberrig_widget_library_ready"] = True
    arm_obj["saberrig_widget_library_version"] = SR_WIDGET_LIBRARY_VERSION
    arm_obj["saberrig_widget_object_count"] = int(len(widgets))
    arm_obj["saberrig_foundation"] = SR_FOUNDATION_ID
    arm_obj["saberrig_release_channel"] = SR_RELEASE_CHANNEL
    arm_obj["saberrig_release_candidate"] = SR_RELEASE_CANDIDATE
    if "saberrig_view_mode" not in arm_obj:
        arm_obj["saberrig_view_mode"] = "SOURCE"
    arm_data["saberrig_prepared"] = True
    arm_data["saberrig_version"] = ADDON_VERSION
    if analysis:
        arm_obj["saberrig_profile_id"] = analysis["detection"]["profile_id"]
        arm_obj["saberrig_game_family"] = analysis["detection"]["game_family"]
        arm_obj["saberrig_rig_dialect"] = analysis["detection"]["rig_dialect"]

    # Existing controls in an upgraded .blend are refreshed non-destructively.
    try:
        _setup_control_visuals(arm_obj)
    except Exception:
        pass

    return {
        "assigned_source_bones": assigned,
        "collections": [root.name, source.name, mechanism.name, controls.name],
        "widgets": len(widgets),
    }

def _remove_constraints(arm_obj, name_prefix=SR_CONSTRAINT_PREFIX):
    removed = 0
    if not arm_obj.pose:
        return removed
    for pose_bone in arm_obj.pose.bones:
        for constraint in list(pose_bone.constraints):
            if constraint.name.startswith(name_prefix):
                pose_bone.constraints.remove(constraint)
                removed += 1
    return removed

def _remove_constraints_for_side(arm_obj, side):
    removed = 0
    token = f"_{side.upper()}_"
    if not arm_obj.pose:
        return removed
    for pose_bone in arm_obj.pose.bones:
        for constraint in list(pose_bone.constraints):
            if constraint.name.startswith("SR_ARMS_FK_") and token in constraint.name:
                pose_bone.constraints.remove(constraint)
                removed += 1
    return removed

def _remove_bones_by_names(context, arm_obj, names):
    names = [name for name in names if name]
    if not names:
        return 0
    _activate_armature(context, arm_obj, mode='EDIT')
    edit_bones = arm_obj.data.edit_bones
    removed = 0
    for name in names:
        bone = edit_bones.get(name)
        if bone:
            edit_bones.remove(bone)
            removed += 1
    bpy.ops.object.mode_set(mode='OBJECT')
    return removed

def _remove_generated_bones(context, arm_obj, component=None):
    names = _generated_bones(arm_obj, component=component)
    if not names:
        return 0
    _activate_armature(context, arm_obj, mode='EDIT')
    edit_bones = arm_obj.data.edit_bones
    removed = 0
    for name in names:
        edit_bone = edit_bones.get(name)
        if edit_bone:
            edit_bones.remove(edit_bone)
            removed += 1
    bpy.ops.object.mode_set(mode='OBJECT')
    return removed

def _remove_arm_fk(context, arm_obj):
    removed_constraints = _remove_constraints(arm_obj, "SR_ARMS_FK_")
    removed_bones = _remove_generated_bones(context, arm_obj, component=SR_ARM_FK_COMPONENT)
    if "saberrig_arms_fk_built" in arm_obj:
        del arm_obj["saberrig_arms_fk_built"]
    return removed_bones, removed_constraints

def _remove_saberrig_setup(context, arm_obj):
    if not arm_obj or arm_obj.type != 'ARMATURE':
        return 0, 0
    _wiggle_reset_runtime(arm_obj)
    _WIGGLE_CACHE.pop(arm_obj.name, None)
    # Finger controls install scripted drivers on generated MCH pose bones.
    # Remove those FCurves explicitly before deleting the bones so a full
    # SaberRig teardown leaves no broken driver paths behind.
    try:
        _finger_remove_driver_curves(arm_obj)
    except Exception:
        pass
    _activate_armature(context, arm_obj, mode='OBJECT')
    try:
        _set_rig_view(arm_obj, False)
    except Exception:
        pass
    removed_constraints = _remove_constraints(arm_obj, SR_CONSTRAINT_PREFIX)
    removed_bones = _remove_generated_bones(context, arm_obj, component=None)

    # Remove only collections owned by the prepared SaberRig foundation.
    for name in (SR_COLLECTION_CONTROLS, SR_COLLECTION_MECHANISM, SR_COLLECTION_SOURCE, SR_COLLECTION_ROOT):
        collection = _find_bone_collection(arm_obj.data, name)
        if collection:
            try:
                arm_obj.data.collections.remove(collection)
            except Exception:
                pass

    for datablock in (arm_obj, arm_obj.data):
        for key in list(datablock.keys()):
            if str(key).startswith("saberrig_"):
                try:
                    del datablock[key]
                except Exception:
                    pass

    _cleanup_unused_widgets()
    return removed_bones, removed_constraints

def _semantic_bone(analysis, role):
    row = analysis.get("semantic_map", {}).get(role, {})
    return row.get("bone") or None

def _arm_sources_for_side(analysis, side):
    suffix = side.upper()
    clavicle = _semantic_bone(analysis, f"clavicle.{suffix}") or _semantic_bone(analysis, f"shoulder.{suffix}")
    upper = _semantic_bone(analysis, f"upper_arm.{suffix}")
    forearm = _semantic_bone(analysis, f"forearm.{suffix}")
    hand = _semantic_bone(analysis, f"hand.{suffix}")
    if clavicle == upper:
        clavicle = None
    return {
        "clavicle": clavicle,
        "upper_arm": upper,
        "forearm": forearm,
        "hand": hand,
    }

def _leg_sources_for_side(analysis, side):
    suffix = side.upper()
    return {
        "pelvis": _semantic_bone(analysis, "pelvis"),
        "thigh": _semantic_bone(analysis, f"thigh.{suffix}"),
        "shin": _semantic_bone(analysis, f"shin.{suffix}"),
        "foot": _semantic_bone(analysis, f"foot.{suffix}"),
        "toe": _semantic_bone(analysis, f"toe.{suffix}"),
    }

def _body_control_names():
    return {
        "ctrl_master": "SR_CTRL_Master",
        "ctrl_root": "SR_CTRL_Root",
        "ctrl_cog": "SR_CTRL_COG",
        "ctrl_hips": "SR_CTRL_Hips",
        "ctrl_spine": "SR_CTRL_Spine",
        "ctrl_chest": "SR_CTRL_Chest",
        "ctrl_neck": "SR_CTRL_Neck",
        "ctrl_head": "SR_CTRL_Head",
        "mch_root": "SR_MCH_Body_Root",
        "mch_hips": "SR_MCH_Body_Hips",
        "mch_spine": "SR_MCH_Body_Spine",
        "mch_chest": "SR_MCH_Body_Chest",
        "mch_neck": "SR_MCH_Body_Neck",
        "mch_head": "SR_MCH_Body_Head",
    }

def _body_sources(analysis):
    """Resolve a compact production body chain without assuming one game dialect.

    `chest` is allowed to fall back to spine_02, which is the authored upper
    torso on UMA/MMD hybrids. Duplicate semantics are removed so SaberRig never
    installs two output constraints on the same source bone.
    """
    root = (_semantic_bone(analysis, "root_motion") or
            _semantic_bone(analysis, "root") or
            _semantic_bone(analysis, "global_offset"))
    pelvis = _semantic_bone(analysis, "pelvis")
    spine = _semantic_bone(analysis, "spine_01")
    chest = _semantic_bone(analysis, "chest") or _semantic_bone(analysis, "spine_02")
    neck = _semantic_bone(analysis, "neck")
    head = _semantic_bone(analysis, "head")

    # Pelvis is the indispensable body anchor. Some game dialects alias Root
    # and Pelvis to the same bone; in that case keep Hips and make Root a
    # synthetic hierarchy layer instead of sacrificing the pelvis mapping.
    if root == pelvis:
        root = None
    if chest == spine:
        chest = None
    if neck in {spine, chest}:
        neck = None
    if head in {spine, chest, neck}:
        head = None

    seen = set()
    out = {}
    for role, bone_name in (
        ("hips", pelvis), ("root", root), ("spine", spine),
        ("chest", chest), ("neck", neck), ("head", head),
    ):
        if bone_name and bone_name not in seen:
            out[role] = bone_name
            seen.add(bone_name)
        else:
            out[role] = None
    return out

def _source_constraint_conflicts(arm_obj, bone_names):
    risky = {
        'IK', 'COPY_TRANSFORMS', 'COPY_ROTATION', 'COPY_LOCATION', 'CHILD_OF',
        'ARMATURE', 'TRANSFORM', 'TRANSFORM_CACHE', 'ACTION', 'SPLINE_IK',
    }
    conflicts = []
    if not arm_obj.pose:
        return conflicts
    for name in bone_names:
        pb = arm_obj.pose.bones.get(name) if name else None
        if not pb:
            continue
        for c in pb.constraints:
            if c.name.startswith(SR_CONSTRAINT_PREFIX):
                continue
            if c.type in risky and not getattr(c, "mute", False) and getattr(c, "influence", 1.0) > 0.0001:
                conflicts.append(f"{name}: {c.type} ({c.name})")
    return conflicts

def _clone_edit_bone(edit_bones, name, source_name, parent_name=None):
    source = edit_bones.get(source_name)
    if source is None:
        raise RuntimeError(f"Source edit bone not found: {source_name}")
    existing = edit_bones.get(name)
    if existing:
        edit_bones.remove(existing)
    bone = edit_bones.new(name)
    bone.head = source.head.copy()
    bone.tail = source.tail.copy()
    bone.roll = source.roll
    bone.use_connect = False
    bone.use_deform = False
    if parent_name:
        bone.parent = edit_bones.get(parent_name)
    return bone

def _mark_generated_bone(arm_obj, bone_name, kind, semantic_role, source_name, component=SR_ARM_FK_COMPONENT):
    bone = arm_obj.data.bones.get(bone_name)
    if not bone:
        return
    bone["saberrig_generated"] = True
    bone["saberrig_component"] = component
    bone["saberrig_kind"] = kind
    bone["saberrig_semantic_role"] = semantic_role
    bone["saberrig_source_bone"] = source_name
    bone.use_deform = False

    target_collection = _find_bone_collection(
        arm_obj.data, SR_COLLECTION_MECHANISM if kind == "MCH" else SR_COLLECTION_CONTROLS
    )
    if target_collection:
        for collection in list(bone.collections):
            if collection != target_collection:
                try:
                    collection.unassign(bone)
                except Exception:
                    pass
        try:
            target_collection.assign(bone)
        except Exception:
            pass

    if kind == "MCH":
        bone.hide_select = True
        try:
            bone.display_type = 'STICK'
        except Exception:
            pass
    else:
        try:
            bone.display_type = 'BBONE'
        except Exception:
            pass

def _add_copy_transforms(owner_pb, target_obj, target_bone, name):
    c = owner_pb.constraints.new('COPY_TRANSFORMS')
    c.name = name
    c.target = target_obj
    c.subtarget = target_bone
    if hasattr(c, "target_space"):
        c.target_space = 'POSE'
    if hasattr(c, "owner_space"):
        c.owner_space = 'POSE'
    if hasattr(c, "mix_mode"):
        try:
            c.mix_mode = 'REPLACE'
        except Exception:
            pass
    return c

def _add_copy_location(owner_pb, target_obj, target_bone, name):
    """Copy only target position in armature pose space.

    Used by IK effective targets so foot orientation cannot feed back into the
    two-bone solver. The visible animator control remains unconstrained.
    """
    c = owner_pb.constraints.new('COPY_LOCATION')
    c.name = name
    c.target = target_obj
    c.subtarget = target_bone
    if hasattr(c, "target_space"):
        c.target_space = 'POSE'
    if hasattr(c, "owner_space"):
        c.owner_space = 'POSE'
    if hasattr(c, "head_tail"):
        c.head_tail = 0.0
    return c

def _add_copy_rotation(owner_pb, target_obj, target_bone, name):
    """Copy animator foot orientation without changing the ankle position."""
    c = owner_pb.constraints.new('COPY_ROTATION')
    c.name = name
    c.target = target_obj
    c.subtarget = target_bone
    if hasattr(c, "target_space"):
        c.target_space = 'POSE'
    if hasattr(c, "owner_space"):
        c.owner_space = 'POSE'
    if hasattr(c, "mix_mode"):
        try:
            c.mix_mode = 'REPLACE'
        except Exception:
            pass
    return c

# -----------------------------------------------------------------------------
# UMA secondary motion / Wiggle Bones
# -----------------------------------------------------------------------------

def _secondary_preset_for_profile(profile_id):
    return SR_SECONDARY_PRESETS.get(str(profile_id or ""), SR_SECONDARY_PRESETS["DEFAULT"])

def _secondary_preset_for_analysis(analysis):
    profile_id = str((analysis or {}).get("detection", {}).get("profile_id", ""))
    return profile_id, _secondary_preset_for_profile(profile_id)

def _wiggle_profile_family(name, profile_id=""):
    """Resolve a semantic secondary-motion family from a source bone name."""
    if not name or name.startswith(("SR_",)):
        return None
    lowered = str(name).lower()
    if (
        "handle" in lowered or "_ik" in lowered or "pole" in lowered
        or "target" in lowered or "control" in lowered or "ctrl" in lowered
    ):
        return None

    if str(profile_id) == "UMA_MUSUME_MMD_HYBRID":
        for family, cfg in SR_WIGGLE_PROFILES.items():
            pattern = cfg.get("pattern")
            if pattern and re.search(pattern, name, re.I):
                return family

    for family, rx in SR_SECONDARY_FAMILY_PATTERNS:
        if rx.search(name):
            return family
    return None

def _wiggle_candidate_class_allowed(class_name):
    return str(class_name or "UNKNOWN") in {"SECONDARY", "ACCESSORY", "UNKNOWN"}

def _wiggle_can_inherit_family(bone, classifications, protected):
    """Conservatively propagate an already-known family down unnamed children."""
    if not bone or bone.name in protected:
        return False
    cls = str(classifications.get(bone.name, "UNKNOWN"))
    if not _wiggle_candidate_class_allowed(cls):
        return False
    if bone.length <= 1.0e-6 or len(bone.children) > 3:
        return False
    lowered = bone.name.lower()
    if IK_NAME_RE.search(bone.name) or any(t in lowered for t in ("ctrl", "control", "handle", "target", "pole")):
        return False
    return True

def _bone_depth(bone):
    depth = 0
    p = bone.parent
    while p is not None and depth < 512:
        depth += 1
        p = p.parent
    return depth

def _wiggle_lerp(a, b, t):
    t = max(0.0, min(1.0, float(t)))
    return float(a) + (float(b) - float(a)) * t

def _wiggle_chain_metadata(arm_obj, candidates):
    """Resolve root→tip depth inside each semantic secondary chain.

    Global skeleton depth is not useful for spring tuning: a hair root can sit
    very deep below the character root yet still be the root of its own spring
    chain. Secondary-motion tuning is expressed along each local
    chain, so SaberRig stores a normalized 0=root .. 1=tip position per MCH.
    """
    candidate_family = {name: family for name, family, _ in candidates}
    local_depth = {}
    root_name = {}
    for name, family, _global_depth in sorted(candidates, key=lambda row: (row[2], row[0])):
        bone = arm_obj.data.bones.get(name)
        parent = bone.parent if bone else None
        if parent and parent.name in candidate_family and candidate_family[parent.name] == family and parent.name in local_depth:
            local_depth[name] = local_depth[parent.name] + 1
            root_name[name] = root_name[parent.name]
        else:
            local_depth[name] = 0
            root_name[name] = name
    max_depth = {}
    for name, depth in local_depth.items():
        root = root_name[name]
        max_depth[root] = max(max_depth.get(root, 0), depth)
    meta = {}
    for name, depth in local_depth.items():
        root = root_name[name]
        mx = max_depth.get(root, 0)
        meta[name] = {
            "root": root,
            "depth": int(depth),
            "max_depth": int(mx),
            "t": float(depth / mx) if mx > 0 else 0.0,
        }
    return meta

def _wiggle_effective_profile(cfg, chain_t, intensity, preset=None):
    """Return calmer root→tip parameters for the current spring element."""
    t = max(0.0, min(1.0, float(chain_t)))
    motion = max(0.0, min(2.0, float(intensity)))
    stiffness = float(cfg["stiffness"]) * _wiggle_lerp(cfg.get("root_stiffness_mul", 1.0), cfg.get("tip_stiffness_mul", 1.0), t)
    # Lower intensity should be *more* controlled, not a weaker spring. Increase
    # return force slightly and bleed velocity faster below the 1.0 reference.
    stiffness *= 1.0 + max(0.0, 1.0 - motion) * 0.45
    damping = _wiggle_lerp(cfg.get("root_damping", cfg.get("damping", 0.75)), cfg.get("tip_damping", cfg.get("damping", 0.75)), t)
    damping *= _wiggle_lerp(0.88, 1.0, min(motion, 1.0))
    damping = max(0.05, min(0.97, damping))
    gravity = float(cfg.get("gravity", 0.0)) * _wiggle_lerp(cfg.get("root_gravity_mul", 1.0), cfg.get("tip_gravity_mul", 1.0), t) * motion
    angle = float(cfg["max_angle"]) * _wiggle_lerp(cfg.get("root_angle_mul", 1.0), cfg.get("tip_angle_mul", 1.0), t) * motion

    preset = preset or SR_SECONDARY_PRESETS["DEFAULT"]
    stiffness *= float(preset.get("stiffness_mul", 1.0))
    damping = max(0.05, min(0.97, damping ** float(preset.get("damping_power", 1.0))))
    gravity *= float(preset.get("gravity_mul", 1.0))
    angle *= float(preset.get("angle_mul", 1.0))
    return stiffness, damping, gravity, max(0.0, angle)

def _wiggle_semantic_names(analysis):
    semantic = analysis.get("semantic_map", {}) if analysis else {}
    roles = ("head", "spine_02", "chest", "pelvis", "thigh.L", "thigh.R", "upper_arm.L", "upper_arm.R", "clavicle.L", "clavicle.R")
    return {role: semantic.get(role, {}).get("bone") for role in roles if semantic.get(role, {}).get("bone")}

def _wiggle_store_collider_semantics(arm_obj, analysis):
    names = _wiggle_semantic_names(analysis)
    arm_obj["saberrig_wiggle_collider_bones"] = json.dumps(names, ensure_ascii=False)
    arm_obj["saberrig_wiggle_collisions"] = True
    try:
        arm_obj.id_properties_ui("saberrig_wiggle_collisions").update(description="Semantic body collision guards for secondary motion")
    except Exception:
        pass
    _WIGGLE_COLLIDER_CACHE.pop(arm_obj.name, None)
    return names

def _wiggle_collider_bone_names(arm_obj):
    cached = _WIGGLE_COLLIDER_CACHE.get(arm_obj.name)
    raw = str(arm_obj.get("saberrig_wiggle_collider_bones", "{}"))
    if cached and cached.get("raw") == raw:
        return cached.get("names", {})
    try:
        names = json.loads(raw) if raw else {}
    except Exception:
        names = {}
    _WIGGLE_COLLIDER_CACHE[arm_obj.name] = {"raw": raw, "names": names}
    return names

def _wiggle_pose_bone(arm_obj, names, role):
    name = names.get(role)
    return arm_obj.pose.bones.get(name) if name else None

def _wiggle_body_colliders(arm_obj):
    """Build lightweight pose-space sphere/capsule guards from semantic body bones."""
    names = _wiggle_collider_bone_names(arm_obj)
    out = {}

    def capsule(key, pb, radius_factor=0.2, min_radius=1.0e-4):
        if not pb:
            return
        a, b = pb.head.copy(), pb.tail.copy()
        length = max((b - a).length, 1.0e-4)
        out[key] = ("CAPSULE", a, b, max(length * radius_factor, min_radius))

    def sphere(key, center, radius):
        if center is not None and radius > 1.0e-6:
            out[key] = ("SPHERE", center.copy(), center.copy(), float(radius))

    head = _wiggle_pose_bone(arm_obj, names, "head")
    chest = _wiggle_pose_bone(arm_obj, names, "chest") or _wiggle_pose_bone(arm_obj, names, "spine_02")
    pelvis = _wiggle_pose_bone(arm_obj, names, "pelvis")
    thigh_l = _wiggle_pose_bone(arm_obj, names, "thigh.L")
    thigh_r = _wiggle_pose_bone(arm_obj, names, "thigh.R")
    upper_l = _wiggle_pose_bone(arm_obj, names, "upper_arm.L")
    upper_r = _wiggle_pose_bone(arm_obj, names, "upper_arm.R")

    if head:
        center = (head.head + head.tail) * 0.5
        sphere("HEAD", center, max(head.length * 0.58, 1.0e-4))
    if chest:
        capsule("CHEST", chest, 0.32)
    if pelvis:
        center = (pelvis.head + pelvis.tail) * 0.5
        hip_width = 0.0
        if thigh_l and thigh_r:
            hip_width = (thigh_l.head - thigh_r.head).length
        radius = max(pelvis.length * 0.42, hip_width * 0.52, 1.0e-4)
        sphere("PELVIS", center, radius)
    capsule("THIGH_L", thigh_l, 0.17)
    capsule("THIGH_R", thigh_r, 0.17)
    if upper_l:
        sphere("SHOULDER_L", upper_l.head, max(upper_l.length * 0.13, 1.0e-4))
    if upper_r:
        sphere("SHOULDER_R", upper_r.head, max(upper_r.length * 0.13, 1.0e-4))
    return out

def _wiggle_segment_closest(point, a, b):
    ab = b - a
    denom = ab.length_squared
    if denom < 1.0e-12:
        return a.copy()
    t = max(0.0, min(1.0, (point - a).dot(ab) / denom))
    return a + ab * t

def _wiggle_collider_signed_distance(point, collider):
    kind, a, b, radius = collider
    center = a if kind == "SPHERE" else _wiggle_segment_closest(point, a, b)
    delta = point - center
    return delta.length - float(radius), center

def _wiggle_collision_push(point, rest_point, collider, strength=SR_WIGGLE_COLLISION_STRENGTH):
    """Prevent deeper penetration while preserving any authored rest overlap."""
    signed, center = _wiggle_collider_signed_distance(point, collider)
    rest_signed, _ = _wiggle_collider_signed_distance(rest_point, collider)
    allowed = min(0.0, float(rest_signed))
    if signed >= allowed:
        return point
    delta = point - center
    if delta.length < 1.0e-8:
        delta = rest_point - center
    if delta.length < 1.0e-8:
        delta = Vector((0.0, 0.0, 1.0))
    normal = delta.normalized()
    correction = (allowed - signed) * max(0.0, min(1.0, float(strength)))
    return point + normal * correction

def _wiggle_apply_family_collisions(arm_obj, family, point, rest_point, colliders):
    if not bool(arm_obj.get("saberrig_wiggle_collisions", True)):
        return point
    cfg = SR_WIGGLE_PROFILES.get(family, {})
    result = point.copy()
    for key in cfg.get("colliders", ()):
        col = colliders.get(key)
        if col:
            result = _wiggle_collision_push(result, rest_point, col)
    return result

def _wiggle_has_authored_animation(arm_obj, bone_name):
    ad = getattr(arm_obj, "animation_data", None)
    action = getattr(ad, "action", None) if ad else None
    if not action:
        return False
    token = f'pose.bones["{bone_name}"]'
    try:
        for fc in action.fcurves:
            if token in fc.data_path:
                return True
    except Exception:
        pass
    return False

def _wiggle_candidates(arm_obj, analysis=None):
    profile_id = str((analysis or {}).get("detection", {}).get("profile_id", ""))
    classifications = (analysis or {}).get("classifications", {}).get("by_bone", {})
    semantic_map = (analysis or {}).get("semantic_map", {})
    protected = {
        row.get("bone") for row in semantic_map.values()
        if isinstance(row, dict) and row.get("bone")
    }

    families = {}
    seed_count = propagated_count = 0
    skipped_animation = skipped_constraints = skipped_protected = 0

    for bone in arm_obj.data.bones:
        if bool(bone.get("saberrig_generated", False)):
            continue
        family = _wiggle_profile_family(bone.name, profile_id)
        if not family:
            continue
        if bone.name in protected or not _wiggle_candidate_class_allowed(classifications.get(bone.name, "UNKNOWN")):
            skipped_protected += 1
            continue
        if bone.length <= 1.0e-6:
            continue
        if _wiggle_has_authored_animation(arm_obj, bone.name):
            skipped_animation += 1
            continue
        if _source_constraint_conflicts(arm_obj, [bone.name]):
            skipped_constraints += 1
            continue
        families[bone.name] = family
        seed_count += 1

    changed = True
    while changed:
        changed = False
        for bone in arm_obj.data.bones:
            if bone.name in families or not bone.parent:
                continue
            parent_family = families.get(bone.parent.name)
            if not parent_family:
                continue
            if not _wiggle_can_inherit_family(bone, classifications, protected):
                continue
            if _wiggle_has_authored_animation(arm_obj, bone.name):
                skipped_animation += 1
                continue
            if _source_constraint_conflicts(arm_obj, [bone.name]):
                skipped_constraints += 1
                continue
            families[bone.name] = parent_family
            propagated_count += 1
            changed = True

    result = [
        (name, family, _bone_depth(arm_obj.data.bones.get(name)))
        for name, family in families.items() if arm_obj.data.bones.get(name)
    ]
    result.sort(key=lambda x: (x[2], x[0]))
    stats = {
        "seeds": int(seed_count), "propagated": int(propagated_count),
        "skipped_animation": int(skipped_animation),
        "skipped_constraints": int(skipped_constraints),
        "skipped_protected": int(skipped_protected),
    }
    return result, stats

def _wiggle_mch_name(index):
    return f"SR_MCH_Wiggle_{index:03d}"

def _wiggle_source_constraint(source_pb, arm_obj, target_bone, family, index):
    c = source_pb.constraints.new('COPY_ROTATION')
    c.name = f"SR_WIGGLE_{family}_{index:03d}"
    c.target = arm_obj
    c.subtarget = target_bone
    if hasattr(c, "target_space"):
        c.target_space = 'LOCAL'
    if hasattr(c, "owner_space"):
        c.owner_space = 'LOCAL'
    if hasattr(c, "mix_mode"):
        try: c.mix_mode = 'REPLACE'
        except Exception: pass
    c.influence = 1.0
    return c

def _wiggle_reset_runtime(arm_obj, families=None):
    name = arm_obj.name if arm_obj else ""
    for key in list(_WIGGLE_STATE.keys()):
        if key[0] != name:
            continue
        if families:
            bone = arm_obj.data.bones.get(key[1]) if arm_obj else None
            fam = bone.get("saberrig_wiggle_family", "") if bone else ""
            if fam not in families:
                continue
        _WIGGLE_STATE.pop(key, None)
    if not arm_obj or not arm_obj.pose:
        return
    for pb in arm_obj.pose.bones:
        b = pb.bone
        if b.get("saberrig_component", "") != SR_WIGGLE_COMPONENT:
            continue
        if families and b.get("saberrig_wiggle_family", "") not in families:
            continue
        try:
            pb.matrix_basis = Matrix.Identity(4)
        except Exception:
            pass

def _remove_wiggle_setup(context, arm_obj):
    if not arm_obj or arm_obj.type != 'ARMATURE':
        return 0, 0
    _wiggle_reset_runtime(arm_obj)
    _WIGGLE_CACHE.pop(arm_obj.name, None)
    _WIGGLE_COLLIDER_CACHE.pop(arm_obj.name, None)
    removed_constraints = _remove_constraints(arm_obj, "SR_WIGGLE_")
    removed_bones = _remove_generated_bones(context, arm_obj, component=SR_WIGGLE_COMPONENT)
    for key in list(arm_obj.keys()):
        if str(key).startswith("saberrig_wiggle_"):
            try: del arm_obj[key]
            except Exception: pass
    return removed_bones, removed_constraints

def _build_wiggle_setup(context, arm_obj, analysis):
    source_profile_id, secondary_preset = _secondary_preset_for_analysis(analysis)
    if not _foundation_is_prepared(arm_obj):
        _prepare_foundation(arm_obj, analysis)
    _remove_wiggle_setup(context, arm_obj)
    candidates, candidate_stats = _wiggle_candidates(arm_obj, analysis)
    if not candidates:
        raise RuntimeError("No safe semantic secondary chains were found (hair/skirt/cloth/cape/tail/ear/ribbon/veil/accessory).")

    mechanism_collection = _find_bone_collection(arm_obj.data, SR_COLLECTION_MECHANISM)
    if not mechanism_collection:
        raise RuntimeError("SaberRig mechanism collection is missing")

    source_to_mch = {}
    source_family = {}
    chain_meta = _wiggle_chain_metadata(arm_obj, candidates)
    _activate_armature(context, arm_obj, mode='EDIT')
    ebones = arm_obj.data.edit_bones
    candidate_names = {row[0] for row in candidates}
    for index, (source_name, family, _depth) in enumerate(candidates):
        source = ebones.get(source_name)
        if not source:
            continue
        parent_name = None
        if source.parent:
            if source.parent.name in candidate_names and source.parent.name in source_to_mch:
                parent_name = source_to_mch[source.parent.name]
            else:
                parent_name = source.parent.name
        mch_name = _wiggle_mch_name(index)
        mch = _clone_edit_bone(ebones, mch_name, source_name, parent_name)
        mechanism_collection.assign(mch)
        source_to_mch[source_name] = mch_name
        source_family[source_name] = family
    bpy.ops.object.mode_set(mode='OBJECT')

    family_counts = Counter()
    built = 0
    for index, (source_name, family, _depth) in enumerate(candidates):
        mch_name = source_to_mch.get(source_name)
        if not mch_name:
            continue
        _mark_generated_bone(arm_obj, mch_name, "MCH", f"wiggle.{family.lower()}", source_name, component=SR_WIGGLE_COMPONENT)
        mch_bone = arm_obj.data.bones.get(mch_name)
        if mch_bone:
            mch_bone["saberrig_wiggle_family"] = family
            mch_bone["saberrig_wiggle_source"] = source_name
            mch_bone["saberrig_wiggle_index"] = int(index)
            meta = chain_meta.get(source_name, {})
            mch_bone["saberrig_wiggle_chain_root"] = str(meta.get("root", source_name))
            mch_bone["saberrig_wiggle_chain_depth"] = int(meta.get("depth", 0))
            mch_bone["saberrig_wiggle_chain_max_depth"] = int(meta.get("max_depth", 0))
            mch_bone["saberrig_wiggle_chain_t"] = float(meta.get("t", 0.0))
        source_pb = arm_obj.pose.bones.get(source_name)
        if not source_pb:
            continue
        c = _wiggle_source_constraint(source_pb, arm_obj, mch_name, family, index)
        enabled = bool(arm_obj.get(f"saberrig_wiggle_family_{family}", SR_WIGGLE_PROFILES[family]["default"]))
        c.influence = 1.0 if enabled else 0.0
        family_counts[family] += 1
        built += 1

    for family, cfg in SR_WIGGLE_PROFILES.items():
        key = f"saberrig_wiggle_family_{family}"
        if key not in arm_obj:
            arm_obj[key] = bool(cfg["default"])
        arm_obj[f"saberrig_wiggle_count_{family}"] = int(family_counts.get(family, 0))
    arm_obj["saberrig_wiggle_built"] = True
    arm_obj["saberrig_wiggle_enabled"] = True
    arm_obj["saberrig_wiggle_profile"] = str(secondary_preset.get("id", "GENERIC_SEMANTIC_SECONDARY"))
    arm_obj["saberrig_wiggle_preset"] = str(secondary_preset.get("name", "Generic Semantic"))
    arm_obj["saberrig_wiggle_source_profile"] = str(source_profile_id or "UNKNOWN")
    arm_obj["saberrig_wiggle_detection_mode"] = "SEMANTIC_NAME_PLUS_CHAIN_PROPAGATION"
    arm_obj["saberrig_wiggle_strength"] = float(secondary_preset.get("intensity", SR_WIGGLE_DEFAULT_INTENSITY))
    arm_obj["saberrig_wiggle_seed_count"] = int(candidate_stats.get("seeds", 0))
    arm_obj["saberrig_wiggle_propagated_count"] = int(candidate_stats.get("propagated", 0))
    arm_obj["saberrig_wiggle_skipped_animation"] = int(candidate_stats.get("skipped_animation", 0))
    arm_obj["saberrig_wiggle_skipped_constraints"] = int(candidate_stats.get("skipped_constraints", 0))
    arm_obj["saberrig_wiggle_skipped_protected"] = int(candidate_stats.get("skipped_protected", 0))
    _wiggle_store_collider_semantics(arm_obj, analysis)
    arm_obj["saberrig_wiggle_collisions"] = bool(secondary_preset.get("collisions", True))
    try:
        arm_obj.id_properties_ui("saberrig_wiggle_strength").update(min=0.0, max=1.5, soft_min=0.0, soft_max=1.0, description="SaberRig semantic secondary-motion intensity")
    except Exception:
        pass
    arm_obj["saberrig_wiggle_bone_count"] = int(built)
    _WIGGLE_CACHE.pop(arm_obj.name, None)
    _wiggle_reset_runtime(arm_obj)
    _activate_armature(context, arm_obj, mode='POSE')
    return built, dict(family_counts)

def _wiggle_runtime_bones(arm_obj):
    cached = _WIGGLE_CACHE.get(arm_obj.name)
    current_count = int(arm_obj.get("saberrig_wiggle_bone_count", 0))
    if cached and len(cached) == current_count:
        return cached
    rows = []
    for b in arm_obj.data.bones:
        if b.get("saberrig_component", "") != SR_WIGGLE_COMPONENT:
            continue
        fam = str(b.get("saberrig_wiggle_family", ""))
        if fam not in SR_WIGGLE_PROFILES:
            continue
        chain_t = float(b.get("saberrig_wiggle_chain_t", 0.0))
        chain_depth = int(b.get("saberrig_wiggle_chain_depth", 0))
        rows.append((b.name, fam, chain_t, chain_depth))
    rows.sort(key=lambda x: (x[3], x[0]))
    _WIGGLE_CACHE[arm_obj.name] = rows
    return rows

def _wiggle_family_enabled(arm_obj, family):
    return bool(arm_obj.get(f"saberrig_wiggle_family_{family}", SR_WIGGLE_PROFILES[family]["default"]))

def _wiggle_apply_constraint_influences(arm_obj):
    master = bool(arm_obj.get("saberrig_wiggle_enabled", True))
    for pb in arm_obj.pose.bones:
        for c in pb.constraints:
            if not c.name.startswith("SR_WIGGLE_"):
                continue
            mch = arm_obj.data.bones.get(getattr(c, "subtarget", ""))
            family = str(mch.get("saberrig_wiggle_family", "")) if mch else ""
            c.influence = 1.0 if master and family in SR_WIGGLE_PROFILES and _wiggle_family_enabled(arm_obj, family) else 0.0

def _wiggle_base_matrix(pb):
    b = pb.bone
    if pb.parent and b.parent:
        try:
            rel = b.parent.matrix_local.inverted_safe() @ b.matrix_local
            return pb.parent.matrix @ rel
        except Exception:
            pass
    return b.matrix_local.copy()

def _wiggle_clamp_direction(rest_dir, candidate_dir, max_angle):
    if rest_dir.length < 1.0e-9 or candidate_dir.length < 1.0e-9:
        return rest_dir.normalized() if rest_dir.length else Vector((0.0, 1.0, 0.0))
    r = rest_dir.normalized(); c = candidate_dir.normalized()
    dot = max(-1.0, min(1.0, r.dot(c)))
    angle = math.acos(dot)
    if angle <= max_angle or angle < 1.0e-7:
        return c
    axis = r.cross(c)
    if axis.length < 1.0e-9:
        return r
    axis.normalize()
    q = axis.rotation_difference(axis)  # identity quaternion with stable type
    # Build from axis/angle without importing Quaternion explicitly.
    from mathutils import Quaternion
    q = Quaternion(axis, max_angle)
    return (q @ r).normalized()

def _wiggle_step_armature(scene, arm_obj, frame_delta):
    if not bool(arm_obj.get("saberrig_wiggle_built", False)):
        return
    if not bool(arm_obj.get("saberrig_wiggle_enabled", True)):
        return
    fps = max(float(scene.render.fps) / max(float(scene.render.fps_base), 1.0e-6), 1.0)
    dt = min(max(abs(float(frame_delta)) / fps, 1.0 / fps), SR_WIGGLE_MAX_FRAME_STEP / fps)
    intensity = max(0.0, min(1.5, float(arm_obj.get("saberrig_wiggle_strength", SR_WIGGLE_DEFAULT_INTENSITY))))
    gravity_local = arm_obj.matrix_world.inverted_safe().to_3x3() @ Vector((0.0, 0.0, -SR_WIGGLE_GRAVITY))
    colliders = _wiggle_body_colliders(arm_obj) if bool(arm_obj.get("saberrig_wiggle_collisions", True)) else {}
    source_profile_id = str(arm_obj.get("saberrig_wiggle_source_profile", ""))
    secondary_preset = _secondary_preset_for_profile(source_profile_id)

    for mch_name, family, chain_t, _chain_depth in _wiggle_runtime_bones(arm_obj):
        pb = arm_obj.pose.bones.get(mch_name)
        if not pb:
            continue
        if not _wiggle_family_enabled(arm_obj, family):
            try: pb.matrix_basis = Matrix.Identity(4)
            except Exception: pass
            continue
        cfg = SR_WIGGLE_PROFILES[family]
        stiffness, damping, gravity_factor, max_angle = _wiggle_effective_profile(cfg, chain_t, intensity, secondary_preset)
        base = _wiggle_base_matrix(pb)
        head = base.translation.copy()
        rest_vec = base.to_3x3() @ Vector((0.0, max(pb.bone.length, 1.0e-5), 0.0))
        length = max(rest_vec.length, 1.0e-5)
        rest_dir = rest_vec.normalized()
        rest_tail = head + rest_dir * length
        key = (arm_obj.name, mch_name)
        state = _WIGGLE_STATE.get(key)
        if state is None:
            state = {"tail": rest_tail.copy(), "vel": Vector((0.0, 0.0, 0.0))}
            _WIGGLE_STATE[key] = state
        tail = state["tail"]
        vel = state["vel"]

        spring = (rest_tail - tail) * stiffness
        gravity = gravity_local * (gravity_factor * length)
        vel = vel + (spring + gravity) * dt
        vel *= damping ** max(dt * SR_WIGGLE_FPS_REFERENCE, 0.1)
        predicted = tail + vel * dt
        candidate = predicted - head
        if candidate.length < 1.0e-8:
            candidate = rest_dir.copy()
        new_dir = _wiggle_clamp_direction(rest_dir, candidate, max_angle)
        new_tail = head + new_dir * length

        # Semantic secondary collision layer: preserve the authored rest overlap,
        # but do not allow the simulated tip to penetrate deeper into the body.
        new_tail = _wiggle_apply_family_collisions(arm_obj, family, new_tail, rest_tail, colliders)
        post = new_tail - head
        if post.length > 1.0e-8:
            new_dir = _wiggle_clamp_direction(rest_dir, post, max_angle)
            new_tail = head + new_dir * length

        vel = (new_tail - tail) / max(dt, 1.0e-6)
        try:
            delta_q = rest_dir.rotation_difference(new_dir)
            desired_q = delta_q @ base.to_quaternion()
            desired = desired_q.to_matrix().to_4x4()
            desired.translation = head
            pb.matrix = desired
        except Exception:
            continue
        state["tail"] = new_tail
        state["vel"] = vel

@persistent
def _saberrig_wiggle_frame_change(scene, depsgraph=None):
    frame = int(scene.frame_current)
    for arm_obj in [o for o in bpy.data.objects if o.type == 'ARMATURE' and bool(o.get("saberrig_wiggle_built", False))]:
        key = (arm_obj.name, "__frame__")
        state = _WIGGLE_STATE.get(key)
        last = int(state.get("frame", frame)) if state else frame
        delta = frame - last
        if delta <= 0 or abs(delta) > SR_WIGGLE_MAX_FRAME_STEP:
            _wiggle_reset_runtime(arm_obj)
            _WIGGLE_STATE[key] = {"frame": frame}
            continue
        _wiggle_step_armature(scene, arm_obj, delta)
        _WIGGLE_STATE[key] = {"frame": frame}
    # MCH matrices are changed after dependency-graph evaluation; refresh once
    # so source Copy Rotation constraints display the same frame, not one later.
    try:
        bpy.context.view_layer.update()
    except Exception:
        pass

def _ensure_wiggle_handler():
    handlers = bpy.app.handlers.frame_change_post
    if _saberrig_wiggle_frame_change not in handlers:
        handlers.append(_saberrig_wiggle_frame_change)

def _remove_wiggle_handler():
    handlers = bpy.app.handlers.frame_change_post
    while _saberrig_wiggle_frame_change in handlers:
        handlers.remove(_saberrig_wiggle_frame_change)

# -----------------------------------------------------------------------------
# Root / COG / Spine control foundation
# -----------------------------------------------------------------------------

def _body_character_scale(arm_obj, analysis):
    src = _body_sources(analysis)
    pelvis = arm_obj.data.bones.get(src.get("hips") or "")
    head = arm_obj.data.bones.get(src.get("head") or "")
    if pelvis and head:
        h = (head.head_local - pelvis.head_local).length
        if h > 1.0e-5:
            return max(h * 0.22, pelvis.length * 1.5, 0.02)
    if pelvis:
        return max(pelvis.length * 2.5, 0.02)
    lengths = [b.length for b in arm_obj.data.bones if b.length > 1.0e-6]
    return max((sum(lengths) / len(lengths)) * 2.0 if lengths else 0.1, 0.02)

def _body_ground_point(arm_obj, analysis):
    src = _body_sources(analysis)
    root_b = arm_obj.data.bones.get(src.get("root") or "")
    pelvis_b = arm_obj.data.bones.get(src.get("hips") or "")
    anchor = (root_b.head_local.copy() if root_b else
              pelvis_b.head_local.copy() if pelvis_b else Vector((0.0, 0.0, 0.0)))
    foot_points = []
    for role in ("foot.L", "foot.R", "toe.L", "toe.R"):
        name = _semantic_bone(analysis, role)
        b = arm_obj.data.bones.get(name or "")
        if b:
            foot_points.extend((b.head_local.copy(), b.tail_local.copy()))
    if foot_points:
        anchor.z = min(p.z for p in foot_points)
    return anchor

def _new_body_control_edit_bone(edit_bones, name, source_name=None, head=None, tail=None, parent_name=None):
    existing = edit_bones.get(name)
    if existing:
        edit_bones.remove(existing)
    if source_name:
        b = _clone_edit_bone(edit_bones, name, source_name, parent_name)
    else:
        b = edit_bones.new(name)
        b.head = (head or Vector((0.0, 0.0, 0.0))).copy()
        b.tail = (tail or (b.head + Vector((0.0, 0.1, 0.0)))).copy()
        if (b.tail - b.head).length < 1.0e-6:
            b.tail = b.head + Vector((0.0, 0.1, 0.0))
        b.roll = 0.0
        b.use_connect = False
        b.use_deform = False
        b.parent = edit_bones.get(parent_name) if parent_name else None
    b.use_connect = False
    b.use_deform = False
    return b

def _body_global_control_names(arm_obj):
    """Animator controls that should follow Master, but remain independent of Root/COG.

    This is what gives the classic production-rig distinction:
    Master moves the *entire* rig, while Root/COG can move the character body
    against planted hand/foot IK controls.
    """
    names = []
    for side in ("L", "R"):
        a = _arm_ik_names(side)
        for key in ("ctrl_hand", "ctrl_pole"):
            ctrl_name = a.get(key, "")
            if not arm_obj.data.bones.get(ctrl_name):
                continue
            if key == "ctrl_pole":
                # Elbow Pole is parented to a hidden shoulder→wrist smart
                # space. Master owns the space so the guard hierarchy survives
                # body-control attachment.
                pole_space = a.get("mch_pole_space", "")
                if pole_space and arm_obj.data.bones.get(pole_space):
                    names.append(pole_space)
                    continue
            # Hand IK may sit under an animation-space switch MCH.
            switch_name = _animation_space_switch_for_control(arm_obj, ctrl_name)
            names.append(switch_name or ctrl_name)
        l = _leg_ik_names(side)
        # Foot Master is world-space relative to body posing; knee pole keeps its
        # Smart Pole Space parent so it follows the hip/ankle solve correctly.
        ctrl_name = l.get("ctrl_master", "")
        if arm_obj.data.bones.get(ctrl_name):
            switch_name = _animation_space_switch_for_control(arm_obj, ctrl_name)
            names.append(switch_name or ctrl_name)
    return names

def _attach_global_controls_to_master(context, arm_obj):
    names = _body_control_names()
    master_name = names["ctrl_master"]
    if not arm_obj.data.bones.get(master_name):
        return 0
    targets = [n for n in _body_global_control_names(arm_obj) if n != master_name]
    if not targets:
        return 0

    # Preserve evaluated matrices so adding the parent never pops an existing IK pose.
    pose_snapshot = {}
    if arm_obj.pose:
        for n in targets:
            pb = arm_obj.pose.bones.get(n)
            if pb:
                pose_snapshot[n] = pb.matrix.copy()

    _activate_armature(context, arm_obj, mode='EDIT')
    eb = arm_obj.data.edit_bones
    master = eb.get(master_name)
    changed = 0
    if master:
        for n in targets:
            b = eb.get(n)
            if b and (b.parent is None or b.parent.name != master_name):
                b.parent = master
                b.use_connect = False
                changed += 1
    bpy.ops.object.mode_set(mode='POSE')
    _view_layer_update(context)
    for n, matrix in pose_snapshot.items():
        pb = arm_obj.pose.bones.get(n)
        if pb:
            try:
                _set_pose_bone_matrix(pb, matrix)
            except Exception:
                pass
    _view_layer_update(context)
    return changed

def _detach_global_controls_from_master(context, arm_obj):
    names = _body_control_names()
    master_name = names["ctrl_master"]
    if not arm_obj.data.bones.get(master_name):
        return 0
    targets = _body_global_control_names(arm_obj)
    pose_snapshot = {}
    for n in targets:
        pb = arm_obj.pose.bones.get(n) if arm_obj.pose else None
        if pb:
            pose_snapshot[n] = pb.matrix.copy()
    _activate_armature(context, arm_obj, mode='EDIT')
    eb = arm_obj.data.edit_bones
    changed = 0
    for n in targets:
        b = eb.get(n)
        if b and b.parent and b.parent.name == master_name:
            b.parent = None
            b.use_connect = False
            changed += 1
    bpy.ops.object.mode_set(mode='POSE')
    _view_layer_update(context)
    for n, matrix in pose_snapshot.items():
        pb = arm_obj.pose.bones.get(n)
        if pb:
            try:
                _set_pose_bone_matrix(pb, matrix)
            except Exception:
                pass
    _view_layer_update(context)
    return changed

def _remove_body_controls(context, arm_obj):
    try:
        _detach_global_controls_from_master(context, arm_obj)
    except Exception:
        pass
    removed_constraints = _remove_constraints(arm_obj, "SR_BODY_")
    removed_bones = _remove_generated_bones(context, arm_obj, component=SR_BODY_COMPONENT)
    for key in list(arm_obj.keys()):
        if str(key).startswith("saberrig_body_"):
            try:
                del arm_obj[key]
            except Exception:
                pass
    return removed_bones, removed_constraints

def _build_body_controls(context, arm_obj, analysis):
    if not _foundation_is_prepared(arm_obj):
        _prepare_foundation(arm_obj, analysis)

    src = _body_sources(analysis)
    if not src.get("hips") or not src.get("spine") or not src.get("head"):
        missing = [r for r in ("hips", "spine", "head") if not src.get(r)]
        raise RuntimeError("Body controls need semantic pelvis/spine/head; missing: " + ", ".join(missing))

    source_targets = [n for n in src.values() if n]
    conflicts = _source_constraint_conflicts(arm_obj, source_targets)
    # Existing SaberRig constraints are filtered by _source_constraint_conflicts.
    if conflicts:
        raise RuntimeError("Body source has an existing transform constraint: " + conflicts[0])

    # Preserve current source pose before rebuilding so controls can be generated
    # on an already posed character without snapping the body to rest.
    source_pose = {}
    if arm_obj.pose:
        for role, source_name in src.items():
            pb = arm_obj.pose.bones.get(source_name or "")
            if pb:
                source_pose[role] = pb.matrix.copy()

    _remove_body_controls(context, arm_obj)
    controls_collection = _find_bone_collection(arm_obj.data, SR_COLLECTION_CONTROLS)
    mechanism_collection = _find_bone_collection(arm_obj.data, SR_COLLECTION_MECHANISM)
    if not controls_collection or not mechanism_collection:
        raise RuntimeError("SaberRig foundation collections are missing")

    names = _body_control_names()
    scale = _body_character_scale(arm_obj, analysis)
    ground = _body_ground_point(arm_obj, analysis)
    pelvis_data = arm_obj.data.bones.get(src["hips"])
    pelvis_head = pelvis_data.head_local.copy()
    up = Vector((0.0, 0.0, 1.0))

    _activate_armature(context, arm_obj, mode='EDIT')
    eb = arm_obj.data.edit_bones

    master = _new_body_control_edit_bone(
        eb, names["ctrl_master"], head=ground,
        tail=ground + up * max(scale * 0.18, 0.015), parent_name=None
    )
    root_source = src.get("root")
    if root_source:
        root = _new_body_control_edit_bone(eb, names["ctrl_root"], source_name=root_source, parent_name=names["ctrl_master"])
    else:
        root = _new_body_control_edit_bone(
            eb, names["ctrl_root"], head=ground + up * (scale * 0.08),
            tail=ground + up * max(scale * 0.28, 0.02), parent_name=names["ctrl_master"]
        )
    cog = _new_body_control_edit_bone(
        eb, names["ctrl_cog"], head=pelvis_head,
        tail=pelvis_head + up * max(scale * 0.30, 0.02), parent_name=names["ctrl_root"]
    )
    hips = _new_body_control_edit_bone(eb, names["ctrl_hips"], source_name=src["hips"], parent_name=names["ctrl_cog"])

    parent = names["ctrl_hips"]
    spine = _new_body_control_edit_bone(eb, names["ctrl_spine"], source_name=src["spine"], parent_name=parent)
    parent = names["ctrl_spine"]
    chest = None
    if src.get("chest"):
        chest = _new_body_control_edit_bone(eb, names["ctrl_chest"], source_name=src["chest"], parent_name=parent)
        parent = names["ctrl_chest"]
    neck = None
    if src.get("neck"):
        neck = _new_body_control_edit_bone(eb, names["ctrl_neck"], source_name=src["neck"], parent_name=parent)
        parent = names["ctrl_neck"]
    head = _new_body_control_edit_bone(eb, names["ctrl_head"], source_name=src["head"], parent_name=parent)

    controls = [master, root, cog, hips, spine, head] + ([chest] if chest else []) + ([neck] if neck else [])
    for b in controls:
        controls_collection.assign(b)

    # MCH output bones stay parentless. Copy Transforms writes their global pose
    # from the hierarchical controls, then a second Copy Transforms drives source.
    role_to_mch = {
        "root": "mch_root", "hips": "mch_hips", "spine": "mch_spine",
        "chest": "mch_chest", "neck": "mch_neck", "head": "mch_head",
    }
    for role, mch_key in role_to_mch.items():
        source_name = src.get(role)
        if not source_name:
            continue
        mch = _clone_edit_bone(eb, names[mch_key], source_name, None)
        mechanism_collection.assign(mch)

    bpy.ops.object.mode_set(mode='OBJECT')

    # Metadata.
    generated = []
    ctrl_meta = (
        ("ctrl_master", "master", src.get("root") or src["hips"]),
        ("ctrl_root", "root", src.get("root") or src["hips"]),
        ("ctrl_cog", "cog", src["hips"]),
        ("ctrl_hips", "hips", src["hips"]),
        ("ctrl_spine", "spine", src["spine"]),
        ("ctrl_chest", "chest", src.get("chest")),
        ("ctrl_neck", "neck", src.get("neck")),
        ("ctrl_head", "head", src["head"]),
    )
    for key, role, source_name in ctrl_meta:
        if arm_obj.data.bones.get(names[key]):
            _mark_generated_bone(arm_obj, names[key], "CTRL", f"body.{role}", source_name or "", component=SR_BODY_COMPONENT)
            generated.append(names[key])
    for role, mch_key in role_to_mch.items():
        source_name = src.get(role)
        if source_name and arm_obj.data.bones.get(names[mch_key]):
            _mark_generated_bone(arm_obj, names[mch_key], "MCH", f"body.{role}", source_name, component=SR_BODY_COMPONENT)
            generated.append(names[mch_key])

    _activate_armature(context, arm_obj, mode='POSE')

    # Match controls to the currently evaluated source pose before constraints
    # begin driving the source bones. COG is intentionally a neutral extra layer.
    for role, ctrl_key in (("root","ctrl_root"), ("hips","ctrl_hips"), ("spine","ctrl_spine"),
                           ("chest","ctrl_chest"), ("neck","ctrl_neck"), ("head","ctrl_head")):
        pb = arm_obj.pose.bones.get(names[ctrl_key])
        if pb and role in source_pose:
            try:
                _set_pose_bone_matrix(pb, source_pose[role])
                _view_layer_update(context)
            except Exception:
                pass

    # Animator locks: Master/Root/COG/Hips can translate + rotate; torso/head are
    # clean rotation controls. Scale is locked everywhere to protect game rigs.
    for key in ("ctrl_master", "ctrl_root", "ctrl_cog", "ctrl_hips"):
        pb = arm_obj.pose.bones.get(names[key])
        if pb:
            pb.lock_location = (False, False, False)
            pb.lock_rotation = (False, False, False)
            pb.lock_scale = (True, True, True)
            pb.rotation_mode = 'QUATERNION'
    for key in ("ctrl_spine", "ctrl_chest", "ctrl_neck", "ctrl_head"):
        pb = arm_obj.pose.bones.get(names[key])
        if pb:
            pb.lock_location = (True, True, True)
            pb.lock_rotation = (False, False, False)
            pb.lock_scale = (True, True, True)
            try:
                source_role = {"ctrl_spine":"spine", "ctrl_chest":"chest", "ctrl_neck":"neck", "ctrl_head":"head"}[key]
                source_pb = arm_obj.pose.bones.get(src.get(source_role) or "")
                if source_pb:
                    pb.rotation_mode = source_pb.rotation_mode
            except Exception:
                pass

    # CTRL -> MCH -> source, all in pose space. COG is an intentional hierarchy
    # layer rather than a source-bound control.
    ctrl_for_role = {
        "root": "ctrl_root", "hips": "ctrl_hips", "spine": "ctrl_spine",
        "chest": "ctrl_chest", "neck": "ctrl_neck", "head": "ctrl_head",
    }
    for role, mch_key in role_to_mch.items():
        source_name = src.get(role)
        ctrl_name = names[ctrl_for_role[role]]
        mch_name = names[mch_key]
        if not source_name or not arm_obj.pose.bones.get(ctrl_name) or not arm_obj.pose.bones.get(mch_name):
            continue
        mch_pb = arm_obj.pose.bones[mch_name]
        source_pb = arm_obj.pose.bones.get(source_name)
        _add_copy_transforms(mch_pb, arm_obj, ctrl_name, f"SR_BODY_CTRL_{role.upper()}")
        _add_copy_transforms(source_pb, arm_obj, mch_name, f"SR_BODY_DEF_{role.upper()}")

    arm_obj["saberrig_body_built"] = True
    arm_obj["saberrig_body_version"] = ADDON_VERSION
    arm_obj["saberrig_body_root_source"] = src.get("root") or "SYNTHETIC"
    arm_obj["saberrig_body_chest_source"] = src.get("chest") or "NONE"
    arm_obj["saberrig_body_has_neck"] = bool(src.get("neck"))

    # The global Master owns free hand/foot IK controls. Root/COG do not, which
    # keeps planted contacts in world space while posing the body.
    _attach_global_controls_to_master(context, arm_obj)
    if bool(arm_obj.get("saberrig_animation_spaces_built", False)):
        try:
            _build_animation_spaces(context, arm_obj, analyze_armature(arm_obj))
        except Exception:
            pass
    _setup_control_visuals(arm_obj)
    _set_rig_view(arm_obj, True)
    return generated

def _arm_fk_names(side):
    suffix = side.upper()
    return {
        "ctrl_clavicle": f"SR_CTRL_Clavicle.{suffix}",
        "ctrl_upper": f"SR_CTRL_UpperArmFK.{suffix}",
        "ctrl_forearm": f"SR_CTRL_ForearmFK.{suffix}",
        "ctrl_hand": f"SR_CTRL_HandFK.{suffix}",
        "mch_clavicle": f"SR_MCH_Clavicle.{suffix}",
        "mch_upper": f"SR_MCH_UpperArm.{suffix}",
        "mch_forearm": f"SR_MCH_Forearm.{suffix}",
        "mch_hand": f"SR_MCH_Hand.{suffix}",
    }

def _build_arm_fk_side(context, arm_obj, analysis, side):
    src = _arm_sources_for_side(analysis, side)
    if not all((src["upper_arm"], src["forearm"], src["hand"])):
        missing = [name for name in ("upper_arm", "forearm", "hand") if not src[name]]
        raise RuntimeError(f"{side}: missing semantic bones: {', '.join(missing)}")

    conflict_names = [src["clavicle"], src["upper_arm"], src["forearm"], src["hand"]]
    conflicts = _source_constraint_conflicts(arm_obj, [n for n in conflict_names if n])
    if conflicts:
        raise RuntimeError(f"{side}: existing transform constraints require compatibility handling: {conflicts[0]}")

    controls_collection = _find_bone_collection(arm_obj.data, SR_COLLECTION_CONTROLS)
    mechanism_collection = _find_bone_collection(arm_obj.data, SR_COLLECTION_MECHANISM)
    if not controls_collection or not mechanism_collection:
        raise RuntimeError("SaberRig foundation collections are missing")

    suffix = side.upper()
    names = _arm_fk_names(side)

    # Cache source parents before entering Edit Mode.
    data_bones = arm_obj.data.bones
    clav_parent = data_bones.get(src["clavicle"]).parent.name if src["clavicle"] and data_bones.get(src["clavicle"]).parent else None
    upper_parent = data_bones.get(src["upper_arm"]).parent.name if data_bones.get(src["upper_arm"]).parent else None

    _activate_armature(context, arm_obj, mode='EDIT')
    ebones = arm_obj.data.edit_bones

    if src["clavicle"]:
        ctrl_clav = _clone_edit_bone(ebones, names["ctrl_clavicle"], src["clavicle"], clav_parent)
        mch_clav = _clone_edit_bone(ebones, names["mch_clavicle"], src["clavicle"], clav_parent)
        controls_collection.assign(ctrl_clav)
        mechanism_collection.assign(mch_clav)
        ctrl_upper_parent = names["ctrl_clavicle"]
        mch_upper_parent = names["mch_clavicle"]
    else:
        ctrl_upper_parent = upper_parent
        mch_upper_parent = upper_parent

    ctrl_upper = _clone_edit_bone(ebones, names["ctrl_upper"], src["upper_arm"], ctrl_upper_parent)
    ctrl_forearm = _clone_edit_bone(ebones, names["ctrl_forearm"], src["forearm"], names["ctrl_upper"])
    ctrl_hand = _clone_edit_bone(ebones, names["ctrl_hand"], src["hand"], names["ctrl_forearm"])
    mch_upper = _clone_edit_bone(ebones, names["mch_upper"], src["upper_arm"], mch_upper_parent)
    mch_forearm = _clone_edit_bone(ebones, names["mch_forearm"], src["forearm"], names["mch_upper"])
    mch_hand = _clone_edit_bone(ebones, names["mch_hand"], src["hand"], names["mch_forearm"])

    for bone in (ctrl_upper, ctrl_forearm, ctrl_hand):
        controls_collection.assign(bone)
    for bone in (mch_upper, mch_forearm, mch_hand):
        mechanism_collection.assign(bone)

    bpy.ops.object.mode_set(mode='OBJECT')

    # Metadata is applied after leaving Edit Mode, when Bone data is synchronized.
    generated = []
    if src["clavicle"]:
        _mark_generated_bone(arm_obj, names["ctrl_clavicle"], "CTRL", f"clavicle.{suffix}", src["clavicle"])
        _mark_generated_bone(arm_obj, names["mch_clavicle"], "MCH", f"clavicle.{suffix}", src["clavicle"])
        generated.extend((names["ctrl_clavicle"], names["mch_clavicle"]))
    for role, ctrl_key, mch_key in (
        ("upper_arm", "ctrl_upper", "mch_upper"),
        ("forearm", "ctrl_forearm", "mch_forearm"),
        ("hand", "ctrl_hand", "mch_hand"),
    ):
        _mark_generated_bone(arm_obj, names[ctrl_key], "CTRL", f"{role}.{suffix}", src[role])
        _mark_generated_bone(arm_obj, names[mch_key], "MCH", f"{role}.{suffix}", src[role])
        generated.extend((names[ctrl_key], names[mch_key]))

    # Make controls rotation-only FK manipulators. MCH bones remain non-selectable.
    for key in ("ctrl_clavicle", "ctrl_upper", "ctrl_forearm", "ctrl_hand"):
        pb = arm_obj.pose.bones.get(names[key])
        if not pb:
            continue
        source_role = {
            "ctrl_clavicle": "clavicle",
            "ctrl_upper": "upper_arm",
            "ctrl_forearm": "forearm",
            "ctrl_hand": "hand",
        }[key]
        source_pb = arm_obj.pose.bones.get(src.get(source_role)) if src.get(source_role) else None
        if source_pb:
            try:
                pb.rotation_mode = source_pb.rotation_mode
            except Exception:
                pass
        pb.lock_location = (True, True, True)
        pb.lock_scale = (True, True, True)

    # CTRL -> MCH in armature/pose space, then MCH -> original source bones.
    pairs = []
    if src["clavicle"]:
        pairs.append(("clavicle", src["clavicle"], names["ctrl_clavicle"], names["mch_clavicle"]))
    pairs.extend([
        ("upper_arm", src["upper_arm"], names["ctrl_upper"], names["mch_upper"]),
        ("forearm", src["forearm"], names["ctrl_forearm"], names["mch_forearm"]),
        ("hand", src["hand"], names["ctrl_hand"], names["mch_hand"]),
    ])

    for role, source_name, ctrl_name, mch_name in pairs:
        mch_pb = arm_obj.pose.bones.get(mch_name)
        source_pb = arm_obj.pose.bones.get(source_name)
        if not mch_pb or not source_pb:
            raise RuntimeError(f"{side}: failed to initialize generated pose bones for {role}")
        _add_copy_transforms(mch_pb, arm_obj, ctrl_name, f"SR_ARMS_FK_CTRL_{suffix}_{role}")
        _add_copy_transforms(source_pb, arm_obj, mch_name, f"SR_ARMS_FK_DEF_{suffix}_{role}")

    return generated

def _build_arms_fk(context, arm_obj, analysis):
    if not _foundation_is_prepared(arm_obj):
        _prepare_foundation(arm_obj, analysis)

    # Idempotent rebuild: remove only the generated arm-FK component.
    _activate_armature(context, arm_obj, mode='OBJECT')
    _remove_arm_fk(context, arm_obj)

    generated = []
    errors = []
    for side in ("L", "R"):
        try:
            generated.extend(_build_arm_fk_side(context, arm_obj, analysis, side))
        except Exception as exc:
            try:
                if arm_obj.mode != 'OBJECT':
                    bpy.ops.object.mode_set(mode='OBJECT')
            except Exception:
                pass
            _remove_constraints_for_side(arm_obj, side)
            try:
                _remove_bones_by_names(context, arm_obj, _arm_fk_names(side).values())
            except Exception:
                pass
            errors.append(str(exc))

    if not generated:
        raise RuntimeError(errors[0] if errors else "No arm FK controls could be generated")

    arm_obj["saberrig_arms_fk_built"] = True
    arm_obj["saberrig_arms_fk_version"] = ADDON_VERSION
    _activate_armature(context, arm_obj, mode='POSE')
    _setup_control_visuals(arm_obj)
    return generated, errors

# -----------------------------------------------------------------------------
# Arm IK
# -----------------------------------------------------------------------------

def _arm_ik_names(side):
    suffix = side.upper()
    return {
        "mch_shoulder": f"SR_MCH_IK_ShoulderAssist.{suffix}",
        "mch_upper": f"SR_MCH_IK_UpperArm.{suffix}",
        "mch_forearm": f"SR_MCH_IK_Forearm.{suffix}",
        "mch_hand": f"SR_MCH_IK_Hand.{suffix}",
        "mch_reach": f"SR_MCH_IK_ReachAnchor.{suffix}",
        "mch_target": f"SR_MCH_IK_EffectiveTarget.{suffix}",
        "mch_pole_space": f"SR_MCH_IK_ElbowPoleSpace.{suffix}",
        "ctrl_hand": f"SR_CTRL_HandIK.{suffix}",
        "ctrl_pole": f"SR_CTRL_ElbowPole.{suffix}",
    }

def _find_named_constraint(pose_bone, name):
    if not pose_bone:
        return None
    return pose_bone.constraints.get(name)

def _set_arm_mode_raw(arm_obj, side, use_ik):
    """Switch source-bone ownership without moving controls."""
    suffix = side.upper()
    analysis = analyze_armature(arm_obj)
    src = _arm_sources_for_side(analysis, suffix)
    value_ik = 1.0 if use_ik else 0.0
    value_fk = 0.0 if use_ik else 1.0

    changed = 0
    # Generated shoulder-assist can also switch clavicle ownership when
    # mechanism exists. This keeps FK clavicle control intact in FK mode and
    # lets the assisted clavicle become the IK-mode output without touching the
    # imported source hierarchy.
    for role in ("clavicle", "upper_arm", "forearm", "hand"):
        source_name = src.get(role)
        pb = arm_obj.pose.bones.get(source_name) if source_name else None
        if not pb:
            continue
        fk = _find_named_constraint(pb, f"SR_ARMS_FK_DEF_{suffix}_{role}")
        ik = _find_named_constraint(pb, f"SR_ARMS_IK_DEF_{suffix}_{role}")
        if fk:
            fk.influence = value_fk
            changed += 1
        if ik:
            ik.influence = value_ik
            changed += 1

    arm_obj[f"saberrig_arm_mode_{suffix}"] = "IK" if use_ik else "FK"
    return changed

def _set_arm_mode(arm_obj, side, use_ik):
    # Backward-compatible internal raw switch. Operators use the snap-aware path.
    return _set_arm_mode_raw(arm_obj, side, use_ik)

def _remove_ik_constraints_for_side(arm_obj, side):
    suffix = side.upper()
    removed = 0
    if not arm_obj.pose:
        return removed
    tokens = (f"_{suffix}_", f"_{suffix}")
    for pose_bone in arm_obj.pose.bones:
        for constraint in list(pose_bone.constraints):
            if not constraint.name.startswith("SR_ARMS_IK_"):
                continue
            if any(token in constraint.name for token in tokens):
                pose_bone.constraints.remove(constraint)
                removed += 1
    return removed

def _remove_arm_ik(context, arm_obj):
    # Put source bones back under FK before deleting the IK mechanism.
    for side in ("L", "R"):
        try:
            _set_arm_mode(arm_obj, side, False)
        except Exception:
            pass
    removed_constraints = _remove_constraints(arm_obj, "SR_ARMS_IK_")
    removed_bones = _remove_generated_bones(context, arm_obj, component=SR_ARM_IK_COMPONENT)
    fixed_keys = ("saberrig_arms_ik_built", "saberrig_arms_ik_version", "saberrig_arm_mode_L", "saberrig_arm_mode_R")
    for key in list(arm_obj.keys()):
        if key in fixed_keys or str(key).startswith((
            "saberrig_pole_angle_", "saberrig_pole_error_", "saberrig_pole_singular_",
            "saberrig_arm_straightness_", "saberrig_arm_reach_", "saberrig_arm_reach_factor_",
            "saberrig_arm_prebend_", "saberrig_arm_mch_reach_",
            "saberrig_arm_reach_guard_factor_", "saberrig_arm_solver_state_",
            "saberrig_arm_preferred_reach_", "saberrig_elbow_signed_lock_",
            "saberrig_elbow_canonical_", "saberrig_elbow_pole_guard_",
            "saberrig_elbow_pole_space_",
            "saberrig_elbow_hinge_axis_", "saberrig_elbow_hinge_sign_",
            "saberrig_elbow_hinge_confidence_", "saberrig_elbow_hinge_mode_",
            "saberrig_elbow_flexion_deg_", "saberrig_elbow_hyper_deg_",
            "saberrig_shoulder_assist_", "saberrig_twist_preserve_",
        )):
            try:
                del arm_obj[key]
            except Exception:
                pass
    return removed_bones, removed_constraints

def _view_layer_update(context=None):
    try:
        if context and context.view_layer:
            context.view_layer.update()
        else:
            bpy.context.view_layer.update()
    except Exception:
        pass

def _wrap_pi(angle):
    while angle > math.pi:
        angle -= math.tau
    while angle < -math.pi:
        angle += math.tau
    return angle

def _pose_rotation_error(reference_matrix, candidate_matrix):
    try:
        q_ref = reference_matrix.to_quaternion().normalized()
        q_candidate = candidate_matrix.to_quaternion().normalized()
        return q_ref.rotation_difference(q_candidate).angle
    except Exception:
        return math.pi

def _arm_rest_joint_points(arm_obj, src):
    upper = arm_obj.data.bones.get(src.get("upper_arm"))
    fore = arm_obj.data.bones.get(src.get("forearm"))
    hand = arm_obj.data.bones.get(src.get("hand"))
    if not upper or not fore or not hand:
        raise RuntimeError("Cannot resolve arm rest joint points")
    shoulder = upper.head_local.copy()
    elbow = fore.head_local.copy()
    wrist = hand.head_local.copy()
    if (wrist - elbow).length < 1.0e-8:
        wrist = fore.tail_local.copy()
    return shoulder, elbow, wrist

def _arm_pose_joint_points(arm_obj, src):
    upper = arm_obj.pose.bones.get(src.get("upper_arm"))
    fore = arm_obj.pose.bones.get(src.get("forearm"))
    hand = arm_obj.pose.bones.get(src.get("hand"))
    if not upper or not fore or not hand:
        raise RuntimeError("Cannot resolve arm pose joint points")
    shoulder = upper.head.copy()
    elbow = fore.head.copy()
    wrist = hand.head.copy()
    if (wrist - elbow).length < 1.0e-8:
        wrist = fore.tail.copy()
    return shoulder, elbow, wrist

def _arm_reach_distance(arm_obj, src):
    shoulder, elbow, wrist = _arm_rest_joint_points(arm_obj, src)
    reach = (elbow - shoulder).length + (wrist - elbow).length
    if reach < 1.0e-6:
        upper = arm_obj.data.bones.get(src.get("upper_arm"))
        fore = arm_obj.data.bones.get(src.get("forearm"))
        reach = max((upper.length if upper else 0.0) + (fore.length if fore else 0.0), 0.05)
    return reach

def _straightness_from_points(shoulder, elbow, wrist):
    a = elbow - shoulder
    b = wrist - elbow
    if a.length < 1.0e-8 or b.length < 1.0e-8:
        return 1.0
    return max(-1.0, min(1.0, a.normalized().dot(b.normalized())))

def _pole_position_from_pose(arm_obj, src, existing_pole=None):
    """Place the pole from the evaluated pose, with a stable fallback for straight arms."""
    shoulder, elbow, wrist = _arm_pose_joint_points(arm_obj, src)
    line = wrist - shoulder
    if line.length < 1.0e-8:
        line = elbow - shoulder
    line_n = line.normalized() if line.length else Vector((1.0, 0.0, 0.0))
    projection = shoulder + line_n * (elbow - shoulder).dot(line_n)
    bend = elbow - projection

    reach = max((elbow - shoulder).length + (wrist - elbow).length, 0.05)
    if bend.length < reach * 1.0e-4:
        # Preserve the current pole side when possible. This makes repeated FK→IK
        # matching stable even when the arm passes through a straight singularity.
        if existing_pole is not None:
            previous = existing_pole - elbow
            previous = previous - line_n * previous.dot(line_n)
            if previous.length > 1.0e-8:
                bend = previous

    if bend.length < 1.0e-8:
        upper = arm_obj.data.bones.get(src.get("upper_arm"))
        fallback = upper.x_axis.copy() if upper else Vector((0.0, 0.0, 1.0))
        fallback = fallback - line_n * fallback.dot(line_n)
        if fallback.length < 1.0e-8 and upper:
            fallback = upper.z_axis.copy() - line_n * upper.z_axis.dot(line_n)
        bend = fallback

    if bend.length < 1.0e-8:
        bend = Vector((0.0, 0.0, 1.0))
        bend = bend - line_n * bend.dot(line_n)
    if bend.length < 1.0e-8:
        bend = Vector((0.0, 1.0, 0.0))

    return elbow + bend.normalized() * reach * 0.75, _straightness_from_points(shoulder, elbow, wrist)

def _set_pose_bone_matrix(pose_bone, matrix):
    if not pose_bone:
        return
    pose_bone.matrix = matrix.copy()

def _set_pose_bone_location(pose_bone, location):
    if not pose_bone:
        return
    matrix = pose_bone.matrix.copy()
    matrix.translation = location
    pose_bone.matrix = matrix

def _arm_ik_constraint(arm_obj, side):
    names = _arm_ik_names(side)
    fore_pb = arm_obj.pose.bones.get(names["mch_forearm"])
    return _find_named_constraint(fore_pb, f"SR_ARMS_IK_SOLVER_{side.upper()}") if fore_pb else None

def _calibrate_arm_pole_angle(context, arm_obj, side, reference_upper=None, reference_forearm=None):
    """Numerically find the pole angle that best reproduces the FK orientation.

    The calibration deliberately treats imported bone roll as opaque. We let the
    Blender IK solver evaluate candidates and choose the orientation with the
    smallest angular error against the already-correct FK/output pose.
    """
    suffix = side.upper()
    names = _arm_ik_names(suffix)
    ik = _arm_ik_constraint(arm_obj, suffix)
    ik_upper = arm_obj.pose.bones.get(names["mch_upper"])
    ik_fore = arm_obj.pose.bones.get(names["mch_forearm"])
    if not ik or not ik_upper or not ik_fore or not hasattr(ik, "pole_angle"):
        return 0.0, math.pi

    analysis = analyze_armature(arm_obj)
    src = _arm_sources_for_side(analysis, suffix)
    if reference_upper is None:
        ref_pb = arm_obj.pose.bones.get(src.get("upper_arm"))
        reference_upper = ref_pb.matrix.copy() if ref_pb else None
    if reference_forearm is None:
        ref_pb = arm_obj.pose.bones.get(src.get("forearm"))
        reference_forearm = ref_pb.matrix.copy() if ref_pb else None
    if reference_upper is None or reference_forearm is None:
        return float(ik.pole_angle), math.pi

    def evaluate(angle):
        ik.pole_angle = _wrap_pi(angle)
        _view_layer_update(context)
        return (
            _pose_rotation_error(reference_upper, ik_upper.matrix) +
            _pose_rotation_error(reference_forearm, ik_fore.matrix)
        ) * 0.5

    # Coarse full-circle search, then two local refinement passes.
    best_angle = float(getattr(ik, "pole_angle", 0.0))
    best_error = math.pi
    coarse_step = math.radians(15.0)
    for i in range(25):
        angle = -math.pi + i * coarse_step
        error = evaluate(angle)
        if error < best_error:
            best_angle, best_error = angle, error

    for step_deg, radius_steps in ((3.0, 5), (0.5, 6), (0.1, 5)):
        step = math.radians(step_deg)
        center = best_angle
        for offset in range(-radius_steps, radius_steps + 1):
            angle = center + offset * step
            error = evaluate(angle)
            if error < best_error:
                best_angle, best_error = angle, error

    ik.pole_angle = _wrap_pi(best_angle)
    _view_layer_update(context)
    arm_obj[f"saberrig_pole_angle_{suffix}"] = float(ik.pole_angle)
    arm_obj[f"saberrig_pole_error_{suffix}"] = float(best_error)
    return float(ik.pole_angle), float(best_error)

def _snap_fk_to_ik(context, arm_obj, side, calibrate=False):
    suffix = side.upper()
    analysis = analyze_armature(arm_obj)
    src = _arm_sources_for_side(analysis, suffix)
    names = _arm_ik_names(suffix)
    hand_ctrl = arm_obj.pose.bones.get(names["ctrl_hand"])
    pole_ctrl = arm_obj.pose.bones.get(names["ctrl_pole"])
    source_upper = arm_obj.pose.bones.get(src.get("upper_arm"))
    source_fore = arm_obj.pose.bones.get(src.get("forearm"))
    source_hand = arm_obj.pose.bones.get(src.get("hand"))
    if not all((hand_ctrl, pole_ctrl, source_upper, source_fore, source_hand)):
        raise RuntimeError(f"{suffix}: FK→IK matching controls are incomplete")

    _view_layer_update(context)
    ref_upper = source_upper.matrix.copy()
    ref_fore = source_fore.matrix.copy()
    ref_hand = source_hand.matrix.copy()
    existing_pole = pole_ctrl.matrix.translation.copy()

    # Match hand position AND orientation before enabling IK ownership.
    _set_pose_bone_matrix(hand_ctrl, ref_hand)
    _view_layer_update(context)

    pole_pos, straightness = _pole_position_from_pose(arm_obj, src, existing_pole=existing_pole)
    _set_pose_bone_location(pole_ctrl, pole_pos)
    _view_layer_update(context)
    arm_obj[f"saberrig_arm_straightness_{suffix}"] = float(straightness)

    if calibrate or f"saberrig_pole_angle_{suffix}" not in arm_obj:
        angle, error = _calibrate_arm_pole_angle(
            context, arm_obj, suffix,
            reference_upper=ref_upper,
            reference_forearm=ref_fore,
        )
    else:
        ik = _arm_ik_constraint(arm_obj, suffix)
        angle = float(arm_obj.get(f"saberrig_pole_angle_{suffix}", 0.0))
        if ik and hasattr(ik, "pole_angle"):
            ik.pole_angle = angle
            _view_layer_update(context)
        ik_upper = arm_obj.pose.bones.get(names["mch_upper"])
        ik_fore = arm_obj.pose.bones.get(names["mch_forearm"])
        error = 0.5 * (
            _pose_rotation_error(ref_upper, ik_upper.matrix) +
            _pose_rotation_error(ref_fore, ik_fore.matrix)
        ) if ik_upper and ik_fore else math.pi
        arm_obj[f"saberrig_pole_error_{suffix}"] = float(error)

    return {
        "pole_angle": angle,
        "error": error,
        "straightness": straightness,
        "singular": straightness >= SR_POLE_SINGULAR_DOT,
    }

def _snap_ik_to_fk(context, arm_obj, side):
    suffix = side.upper()
    analysis = analyze_armature(arm_obj)
    src = _arm_sources_for_side(analysis, suffix)
    names = _arm_fk_names(suffix)
    controls = {
        "upper_arm": arm_obj.pose.bones.get(names["ctrl_upper"]),
        "forearm": arm_obj.pose.bones.get(names["ctrl_forearm"]),
        "hand": arm_obj.pose.bones.get(names["ctrl_hand"]),
    }
    source = {
        role: arm_obj.pose.bones.get(src.get(role))
        for role in ("upper_arm", "forearm", "hand")
    }

    # If IK shoulder assist is driving the semantic clavicle, capture
    # that evaluated clavicle pose into the FK clavicle control before ownership
    # switches back. This prevents the assisted shoulder from snapping downward
    # when the animator returns from IK to FK.
    if src.get("clavicle") and names.get("ctrl_clavicle"):
        clav_ctrl = arm_obj.pose.bones.get(names["ctrl_clavicle"])
        clav_src = arm_obj.pose.bones.get(src["clavicle"])
        if clav_ctrl and clav_src:
            controls["clavicle"] = clav_ctrl
            source["clavicle"] = clav_src

    required = ("upper_arm", "forearm", "hand")
    if not all(controls.get(role) for role in required) or not all(source.get(role) for role in required):
        raise RuntimeError(f"{suffix}: IK→FK matching controls are incomplete")

    _view_layer_update(context)
    targets = {role: source[role].matrix.copy() for role in source if source.get(role)}
    # Parent-space dependencies make sequential updates safer than a bulk write.
    order = (["clavicle"] if "clavicle" in targets else []) + ["upper_arm", "forearm", "hand"]
    for role in order:
        _set_pose_bone_matrix(controls[role], targets[role])
        _view_layer_update(context)
    return True

def _create_reach_anchor_edit_bone(edit_bones, name, shoulder, reference_bone, parent_name=None):
    existing = edit_bones.get(name)
    if existing:
        edit_bones.remove(existing)
    b = edit_bones.new(name)
    b.head = shoulder.copy()
    axis = reference_bone.vector.copy()
    if axis.length < 1.0e-8:
        axis = Vector((0.0, 0.0, 1.0))
    axis.normalize()
    b.tail = shoulder + axis * max(reference_bone.length * 0.12, 0.01)
    b.roll = reference_bone.roll
    b.use_connect = False
    b.use_deform = False
    if parent_name:
        b.parent = edit_bones.get(parent_name)
    return b

def _add_hand_reach_limit(
    arm_obj, side, target_pb, anchor_name, reach_distance,
    factor=SR_DEFAULT_ARM_REACH_FACTOR,
    preferred_factor=SR_DEFAULT_ARM_PREFERRED_REACH_FACTOR,
):
    """Clamp only the hidden HandIK target, never the animator control.

    A hard anatomical maximum protects reach while a preferred working radius
    is enabled only when the mechanism chain needs pre-bend, preserving a
    visible and numerically stable elbow angle near full extension.
    """
    suffix = side.upper()

    cmax = target_pb.constraints.new('LIMIT_DISTANCE')
    cmax.name = f"SR_ARMS_IK_REACH_{suffix}"
    cmax.target = arm_obj
    if hasattr(cmax, "subtarget"):
        cmax.subtarget = anchor_name
    if hasattr(cmax, "head_tail"):
        cmax.head_tail = 0.0
    cmax.distance = max(float(reach_distance) * float(factor), 1.0e-5)
    if hasattr(cmax, "limit_mode"):
        cmax.limit_mode = 'LIMITDIST_INSIDE'
    if hasattr(cmax, "owner_space"):
        cmax.owner_space = 'POSE'
    if hasattr(cmax, "target_space"):
        cmax.target_space = 'POSE'
    if hasattr(cmax, "use_transform_limit"):
        cmax.use_transform_limit = True

    cpref = target_pb.constraints.new('LIMIT_DISTANCE')
    cpref.name = f"SR_ARMS_IK_PREFERRED_BEND_{suffix}"
    cpref.target = arm_obj
    if hasattr(cpref, "subtarget"):
        cpref.subtarget = anchor_name
    if hasattr(cpref, "head_tail"):
        cpref.head_tail = 0.0
    safe_pref = min(max(float(preferred_factor), 0.50), float(factor))
    cpref.distance = max(float(reach_distance) * safe_pref, 1.0e-5)
    if hasattr(cpref, "limit_mode"):
        cpref.limit_mode = 'LIMITDIST_INSIDE'
    if hasattr(cpref, "owner_space"):
        cpref.owner_space = 'POSE'
    if hasattr(cpref, "target_space"):
        cpref.target_space = 'POSE'
    if hasattr(cpref, "use_transform_limit"):
        cpref.use_transform_limit = True

    # Normal bent-rest arms retain the full source reach. Only straight-rest
    # chains use the preferred reserve.
    prebend_active = bool(
        arm_obj.get(f"saberrig_arm_prebend_active_{suffix}", False)
    )
    cpref.influence = 1.0 if prebend_active else 0.0

    arm_obj[f"saberrig_arm_reach_{suffix}"] = float(cmax.distance)
    arm_obj[f"saberrig_arm_reach_factor_{suffix}"] = float(factor)
    arm_obj[f"saberrig_arm_preferred_reach_{suffix}"] = float(cpref.distance)
    arm_obj[f"saberrig_arm_preferred_reach_factor_{suffix}"] = float(safe_pref)
    arm_obj[f"saberrig_arm_preferred_reach_active_{suffix}"] = bool(prebend_active)
    return cmax, cpref

def _pb_set_if(pose_bone, attr, value):
    if pose_bone is not None and hasattr(pose_bone, attr):
        try:
            setattr(pose_bone, attr, value)
            return True
        except Exception:
            pass
    return False

def _clear_ik_axis_safety(pose_bone):
    """Return a SaberRig MCH bone to unconstrained Blender-IK axis settings."""
    if not pose_bone:
        return
    for axis in "xyz":
        _pb_set_if(pose_bone, f"lock_ik_{axis}", False)
        _pb_set_if(pose_bone, f"use_ik_limit_{axis}", False)
        _pb_set_if(pose_bone, f"ik_stiffness_{axis}", 0.0)
        _pb_set_if(pose_bone, f"ik_min_{axis}", -math.pi)
        _pb_set_if(pose_bone, f"ik_max_{axis}", math.pi)

def _set_ik_axis_range(pose_bone, axis, minimum, maximum, enabled=True):
    axis = str(axis).lower()
    _pb_set_if(pose_bone, f"use_ik_limit_{axis}", bool(enabled))
    if enabled:
        _pb_set_if(pose_bone, f"ik_min_{axis}", float(minimum))
        _pb_set_if(pose_bone, f"ik_max_{axis}", float(maximum))

def _set_ik_axis_stiffness(pose_bone, axis, value):
    axis = str(axis).lower()
    value = max(0.0, min(0.99, float(value)))
    _pb_set_if(pose_bone, f"ik_stiffness_{axis}", value)

def _bone_local_axes(data_bone):
    """Armature-space vectors representing a Blender bone's local XYZ axes."""
    if not data_bone:
        return {}
    result = {}
    for key, attr in (("X", "x_axis"), ("Y", "y_axis"), ("Z", "z_axis")):
        try:
            v = getattr(data_bone, attr).copy()
            if v.length > 1.0e-8:
                result[key] = v.normalized()
        except Exception:
            pass
    return result

def _elbow_hinge_diagnostics(arm_obj, side):
    """Infer the best local elbow hinge axis from the semantic chain + pole.

    Imported rigs may have arbitrary roll. We therefore do not blindly assume
    local X or Z. The expected hinge normal is the normal of the plane formed by
    the arm direction and the current pole direction. We compare that normal to
    the generated forearm's local axes and record a confidence score.
    """
    suffix = side.upper()
    analysis = analyze_armature(arm_obj)
    src = _arm_sources_for_side(analysis, suffix)
    names = _arm_ik_names(suffix)
    fore_data = arm_obj.data.bones.get(names["mch_forearm"])
    pole_pb = arm_obj.pose.bones.get(names["ctrl_pole"])
    if not fore_data or not pole_pb:
        raise RuntimeError(f"{suffix}: elbow safety cannot resolve IK forearm/pole")

    shoulder, elbow, wrist = _arm_rest_joint_points(arm_obj, src)
    chain = wrist - elbow
    if chain.length < 1.0e-8:
        chain = fore_data.vector.copy()
    if chain.length < 1.0e-8:
        raise RuntimeError(f"{suffix}: elbow safety found a zero-length forearm")
    chain.normalize()

    pole_vector = pole_pb.matrix.translation - elbow
    pole_vector = pole_vector - chain * pole_vector.dot(chain)
    if pole_vector.length < 1.0e-8:
        fallback, _ = _pole_position_from_pose(arm_obj, src, existing_pole=None)
        pole_vector = fallback - elbow
        pole_vector = pole_vector - chain * pole_vector.dot(chain)
    if pole_vector.length < 1.0e-8:
        pole_vector = Vector((0.0, 0.0, 1.0))
        pole_vector = pole_vector - chain * pole_vector.dot(chain)
    if pole_vector.length < 1.0e-8:
        pole_vector = Vector((0.0, 1.0, 0.0))
    pole_vector.normalize()

    plane_normal = chain.cross(pole_vector)
    if plane_normal.length < 1.0e-8:
        plane_normal = pole_vector.cross(chain)
    plane_normal.normalize()

    axes = _bone_local_axes(fore_data)
    # Local Y is longitudinal for Blender bones, so a true elbow hinge should
    # normally resolve to X/Z. Keep Y as a diagnostic fallback but penalize it.
    scores = {axis: abs(vec.dot(plane_normal)) for axis, vec in axes.items()}
    if "Y" in scores:
        scores["Y"] *= 0.25
    if not scores:
        raise RuntimeError(f"{suffix}: elbow safety cannot read local bone axes")
    hinge_axis = max(scores, key=scores.get)
    axis_vector = axes[hinge_axis]
    confidence = abs(axis_vector.dot(plane_normal))

    # Positive local rotation moves the forearm instantaneously along axis×dir.
    # Compare that motion with the pole side to learn whether flexion is + or -.
    positive_motion = axis_vector.cross(chain)
    if positive_motion.length < 1.0e-8:
        flex_sign = 1.0
    else:
        flex_sign = 1.0 if positive_motion.normalized().dot(pole_vector) >= 0.0 else -1.0

    if confidence >= SR_ELBOW_HARD_HINGE_CONFIDENCE:
        mode = "HARD"
    elif confidence >= SR_ELBOW_SOFT_HINGE_CONFIDENCE:
        mode = "SOFT"
    else:
        mode = "FALLBACK"

    return {
        "axis": hinge_axis,
        "confidence": float(confidence),
        "flex_sign": float(flex_sign),
        "mode": mode,
        "plane_normal": plane_normal,
    }

def _ensure_wrist_limit_constraint(arm_obj, side, enabled=True):
    suffix = side.upper()
    names = _arm_ik_names(suffix)
    hand_pb = arm_obj.pose.bones.get(names["mch_hand"])
    if not hand_pb:
        return None
    cname = f"SR_ARMS_IK_SAFE_WRIST_{suffix}"
    c = hand_pb.constraints.get(cname)
    if c is None:
        c = hand_pb.constraints.new('LIMIT_ROTATION')
        c.name = cname
    c.influence = 1.0 if enabled else 0.0
    if hasattr(c, "owner_space"):
        c.owner_space = 'LOCAL'
    if hasattr(c, "use_transform_limit"):
        c.use_transform_limit = False
    for axis in "xyz":
        if hasattr(c, f"use_limit_{axis}"):
            setattr(c, f"use_limit_{axis}", True)
    # Blender bone local Y runs along the bone and is therefore treated as the
    # broad twist channel. X/Z are intentionally generous swing safety limits.
    c.min_x = -SR_WRIST_SAFE_SWING
    c.max_x = SR_WRIST_SAFE_SWING
    c.min_y = -SR_WRIST_SAFE_TWIST
    c.max_y = SR_WRIST_SAFE_TWIST
    c.min_z = -SR_WRIST_SAFE_SWING
    c.max_z = SR_WRIST_SAFE_SWING
    return c

def _apply_arm_joint_limits(arm_obj, side, enabled=True):
    """Apply non-destructive SAFE constraints to one generated IK arm."""
    suffix = side.upper()
    names = _arm_ik_names(suffix)
    upper_pb = arm_obj.pose.bones.get(names["mch_upper"])
    fore_pb = arm_obj.pose.bones.get(names["mch_forearm"])
    if not upper_pb or not fore_pb:
        return None

    _clear_ik_axis_safety(upper_pb)
    _clear_ik_axis_safety(fore_pb)
    _ensure_wrist_limit_constraint(arm_obj, suffix, enabled=enabled)

    if not enabled:
        arm_obj[f"saberrig_elbow_hinge_mode_{suffix}"] = "OFF"
        return {"mode": "OFF"}

    # Shoulder: broad ball-joint safety only. No axis is locked; we merely keep
    # the solver out of multi-turn/flipped solutions and add light twist damping.
    _set_ik_axis_range(upper_pb, "x", -SR_SHOULDER_SWING_LIMIT, SR_SHOULDER_SWING_LIMIT)
    _set_ik_axis_range(upper_pb, "z", -SR_SHOULDER_SWING_LIMIT, SR_SHOULDER_SWING_LIMIT)
    _set_ik_axis_range(upper_pb, "y", -SR_SHOULDER_TWIST_LIMIT, SR_SHOULDER_TWIST_LIMIT)
    _set_ik_axis_stiffness(upper_pb, "y", SR_SHOULDER_TWIST_STIFFNESS)

    diag = _elbow_hinge_diagnostics(arm_obj, suffix)
    hinge = diag["axis"].lower()
    sign = diag["flex_sign"]
    confidence = diag["confidence"]
    mode = diag["mode"]

    # The forearm's longitudinal Y twist is never a useful elbow degree of
    # freedom. Removing it prevents the classic corkscrew solution.
    _pb_set_if(fore_pb, "lock_ik_y", True)
    _set_ik_axis_stiffness(fore_pb, "y", SR_ELBOW_TWIST_STIFFNESS)

    flex = SR_ELBOW_SAFE_FLEXION
    hyper = SR_ELBOW_SAFE_HYPEREXTENSION
    prebend_active = bool(
        arm_obj.get(f"saberrig_arm_prebend_active_{suffix}", False)
    )

    # The source arm may be exactly straight, but the hidden MCH chain
    # is not: SaberRig deliberately pre-bent it toward the Elbow Pole. Use that
    # authored MCH geometry to recover a canonical flexion sign, then permit
    # only 2° back toward straight. This prevents the elbow crossing through
    # 180° and folding into the opposite hemisphere.
    canonical = {
        "sign": float(sign), "quality": 0.0, "metric": 0.0, "angle": 0.0
    }
    signed_lock = True
    if prebend_active:
        canonical = _elbow_prebend_canonical_sign(
            arm_obj, suffix, hinge_axis=diag["axis"]
        )
        canonical_sign = float(canonical.get("sign", 0.0))
        canonical_quality = float(canonical.get("quality", 0.0))
        if canonical_sign != 0.0 and canonical_quality >= SR_ELBOW_CANONICAL_SIGN_MIN_QUALITY:
            reverse = SR_ELBOW_PREBEND_REVERSE_TOLERANCE
            if canonical_sign > 0.0:
                minimum, maximum = -reverse, flex
            else:
                minimum, maximum = -flex, reverse
            effective_mode = "PREBEND_LOCK"
        else:
            # Safety fallback: keep a conservative bidirectional range rather than
            # risk locking a low-quality imported basis to the wrong side.
            minimum, maximum = -flex, flex
            effective_mode = "PREBEND_FALLBACK"
            signed_lock = False
    else:
        canonical_sign = float(sign)
        canonical_quality = float(confidence)
        if sign >= 0.0:
            minimum, maximum = -hyper, flex
        else:
            minimum, maximum = -flex, hyper
        effective_mode = mode
    _set_ik_axis_range(fore_pb, hinge, minimum, maximum, enabled=True)

    # Arbitrary imported roll can place the true hinge between local X and Z.
    # Confidence still controls lateral freedom. PREBEND changes only the hinge
    # sign policy, not which local axis was diagnosed.
    lateral_axes = [a for a in ("x", "z") if a != hinge]
    if mode == "HARD":
        for axis in lateral_axes:
            _pb_set_if(fore_pb, f"lock_ik_{axis}", True)
    elif mode == "SOFT":
        for axis in lateral_axes:
            _set_ik_axis_stiffness(fore_pb, axis, SR_ELBOW_NONHINGE_STIFFNESS)
            _set_ik_axis_range(fore_pb, axis, math.radians(-35.0), math.radians(35.0), enabled=True)
    else:
        for axis in ("x", "z"):
            if axis != hinge:
                _set_ik_axis_stiffness(fore_pb, axis, 0.55)
                _set_ik_axis_range(fore_pb, axis, math.radians(-55.0), math.radians(55.0), enabled=True)

    arm_obj[f"saberrig_elbow_hinge_axis_{suffix}"] = diag["axis"]
    arm_obj[f"saberrig_elbow_hinge_sign_{suffix}"] = float(sign)
    arm_obj[f"saberrig_elbow_hinge_confidence_{suffix}"] = float(confidence)
    arm_obj[f"saberrig_elbow_hinge_mode_{suffix}"] = effective_mode
    arm_obj[f"saberrig_elbow_signed_lock_{suffix}"] = bool(signed_lock)
    arm_obj[f"saberrig_elbow_canonical_sign_{suffix}"] = float(canonical_sign)
    arm_obj[f"saberrig_elbow_canonical_quality_{suffix}"] = float(canonical_quality)
    arm_obj[f"saberrig_elbow_flexion_deg_{suffix}"] = math.degrees(flex)
    arm_obj[f"saberrig_elbow_hyper_deg_{suffix}"] = math.degrees(
        SR_ELBOW_PREBEND_REVERSE_TOLERANCE if prebend_active and signed_lock else hyper
    )
    return diag

def _apply_all_arm_joint_limits(arm_obj, enabled=True):
    diagnostics = {}
    for side in ("L", "R"):
        try:
            diagnostics[side] = _apply_arm_joint_limits(arm_obj, side, enabled=enabled)
        except Exception as exc:
            diagnostics[side] = {"mode": "ERROR", "error": str(exc)}
    arm_obj["saberrig_joint_limits_mode"] = "SAFE" if enabled else "OFF"
    return diagnostics

def _shoulder_assist_constraint_name(side):
    return f"SR_ARMS_IK_SHOULDER_ASSIST_{str(side).upper()}"

def _twist_helpers_for_arm(arm_obj, analysis, side):
    """Return source twist/roll helpers under the semantic upper arm.

    SaberRig deliberately leaves these source helpers unconstrained. They keep
    inheriting the original game-rig hierarchy, which preserves authored roll
    distribution instead of replacing it with a generic twist system.
    """
    suffix = str(side).upper()
    src = _arm_sources_for_side(analysis, suffix)
    upper_name = src.get("upper_arm")
    upper = arm_obj.data.bones.get(upper_name) if upper_name else None
    if not upper:
        return []
    classes = analysis.get("classifications", {}).get("by_bone", {})
    result = []
    for bone in arm_obj.data.bones:
        if classes.get(bone.name) != "TWIST":
            continue
        try:
            if bone.name == upper.name or _is_ancestor(upper, bone):
                result.append(bone.name)
        except Exception:
            continue
    return sorted(set(result))

def _store_twist_preservation_metadata(arm_obj, analysis, side):
    suffix = str(side).upper()
    helpers = _twist_helpers_for_arm(arm_obj, analysis, suffix)
    arm_obj[f"saberrig_twist_preserve_count_{suffix}"] = len(helpers)
    arm_obj[f"saberrig_twist_preserve_names_{suffix}"] = json.dumps(helpers, ensure_ascii=False)
    return helpers

def _ensure_shoulder_assist_constraint(arm_obj, side, enabled=True, strength=None):
    """Create/update the non-cyclic IK clavicle follow constraint.

    The generated IK shoulder MCH first copies the FK clavicle MCH, then uses a
    low-influence Damped Track toward the animator-facing Hand IK. Because the
    hand control is unparented, the constraint does not depend on the IK chain
    and therefore avoids the shoulder↔IK dependency cycle.
    """
    suffix = str(side).upper()
    names = _arm_ik_names(suffix)
    fk_names = _arm_fk_names(suffix)
    shoulder_pb = arm_obj.pose.bones.get(names.get("mch_shoulder"))
    if not shoulder_pb:
        return None

    base_name = f"SR_ARMS_IK_SHOULDER_BASE_{suffix}"
    base = shoulder_pb.constraints.get(base_name)
    if base is None:
        base = _add_copy_transforms(shoulder_pb, arm_obj, fk_names["mch_clavicle"], base_name)

    cname = _shoulder_assist_constraint_name(suffix)
    c = shoulder_pb.constraints.get(cname)
    if c is None:
        c = shoulder_pb.constraints.new('DAMPED_TRACK')
        c.name = cname
        c.target = arm_obj
        c.subtarget = names["ctrl_hand"]
        if hasattr(c, "track_axis"):
            c.track_axis = 'TRACK_Y'
        if hasattr(c, "head_tail"):
            c.head_tail = 0.0

    if strength is None:
        strength = float(arm_obj.get("saberrig_shoulder_assist_strength", SR_SHOULDER_ASSIST_DEFAULT))
    strength = max(SR_SHOULDER_ASSIST_MIN, min(SR_SHOULDER_ASSIST_MAX, float(strength)))
    arm_obj["saberrig_shoulder_assist_strength"] = strength
    c.influence = strength if enabled else 0.0
    return c

def _apply_shoulder_assist_mode(arm_obj, enabled=True):
    strength = float(arm_obj.get("saberrig_shoulder_assist_strength", SR_SHOULDER_ASSIST_DEFAULT))
    applied = 0
    for side in ("L", "R"):
        c = _ensure_shoulder_assist_constraint(arm_obj, side, enabled=enabled, strength=strength)
        if c:
            applied += 1
    arm_obj["saberrig_shoulder_assist_mode"] = "AUTO" if enabled else "OFF"
    return applied

def _rest_point(bone, attr):
    value = getattr(bone, attr, None)
    return value.copy() if value is not None else Vector((0.0, 0.0, 0.0))

def _arm_pole_position(arm_obj, src):
    """Return a stable armature-space elbow pole position from the rest chain."""
    upper = arm_obj.data.bones.get(src["upper_arm"])
    fore = arm_obj.data.bones.get(src["forearm"])
    if not upper or not fore:
        raise RuntimeError("Cannot compute pole position without upper-arm and forearm bones")

    shoulder = _rest_point(upper, "head_local")
    elbow = _rest_point(fore, "head_local")
    wrist = _rest_point(fore, "tail_local")

    line = wrist - shoulder
    if line.length < 1.0e-8:
        line = upper.vector.copy()
    line_n = line.normalized() if line.length else Vector((0.0, 1.0, 0.0))
    projection = shoulder + line_n * (elbow - shoulder).dot(line_n)
    bend = elbow - projection

    # Straight rest poses are common in game rigs. Use the upper-arm local X axis
    # as a deterministic fallback, projected perpendicular to the chain.
    if bend.length < max(upper.length, fore.length, 0.001) * 1.0e-4:
        fallback = upper.x_axis.copy()
        fallback = fallback - line_n * fallback.dot(line_n)
        if fallback.length < 1.0e-8:
            fallback = upper.z_axis.copy()
            fallback = fallback - line_n * fallback.dot(line_n)
        bend = fallback

    if bend.length < 1.0e-8:
        bend = Vector((0.0, 0.0, 1.0))

    distance = max(upper.length + fore.length, 0.05) * 0.75
    return elbow + bend.normalized() * distance

def _create_pole_edit_bone(edit_bones, name, pole_pos, reference_bone):
    existing = edit_bones.get(name)
    if existing:
        edit_bones.remove(existing)
    b = edit_bones.new(name)
    size = max(reference_bone.length * 0.18, 0.015)
    b.head = pole_pos
    # A short vertical-ish display bone; its location, not orientation, is the pole.
    axis = Vector((0.0, 0.0, 1.0))
    if abs(reference_bone.vector.normalized().dot(axis)) > 0.95:
        axis = Vector((0.0, 1.0, 0.0))
    b.tail = pole_pos + axis * size
    b.roll = 0.0
    b.use_connect = False
    b.use_deform = False
    b.parent = None
    return b

def _pole_angle_for_rest_chain(upper_bone, forearm_bone, pole_bone):
    """Approximate Blender IK pole angle so the rest pose does not roll abruptly."""
    try:
        chain_axis = forearm_bone.tail_local - upper_bone.head_local
        pole_axis = pole_bone.head_local - upper_bone.head_local
        if chain_axis.length < 1.0e-8 or pole_axis.length < 1.0e-8:
            return 0.0
        pole_normal = chain_axis.cross(pole_axis)
        if pole_normal.length < 1.0e-8:
            return 0.0
        projected = pole_normal.normalized().cross(upper_bone.vector.normalized())
        if projected.length < 1.0e-8:
            return 0.0
        projected.normalize()
        x_axis = upper_bone.x_axis.normalized()
        angle = x_axis.angle(projected)
        if x_axis.cross(projected).dot(upper_bone.vector) < 0.0:
            angle = -angle
        return angle
    except Exception:
        return 0.0

def _apply_arm_prebend_edit_geometry(arm_obj, src, mch_upper, mch_fore, pole_pos):
    """Give a rest-straight hidden arm solver chain a small pole-facing bend.

    Only SaberRig MCH geometry changes. Source bones, skinning and animator
    controls remain untouched. Shoulder and wrist endpoints stay fixed.
    """
    shoulder = mch_upper.head.copy()
    source_elbow = mch_upper.tail.copy()
    wrist = mch_fore.tail.copy()
    straightness = _straightness_from_points(shoulder, source_elbow, wrist)

    result = {
        "active": False,
        "straightness": float(straightness),
        "offset": 0.0,
        "angle": 0.0,
        "direction": "SOURCE",
        "mch_reach": float(
            (source_elbow - shoulder).length + (wrist - source_elbow).length
        ),
    }
    if straightness < SR_ARM_PREBEND_TRIGGER_DOT:
        try:
            result["angle"] = float(
                (source_elbow - shoulder).angle(wrist - source_elbow)
            )
        except Exception:
            pass
        return result

    line = wrist - shoulder
    if line.length < 1.0e-8:
        return result
    line_n = line.normalized()
    projection = shoulder + line_n * (source_elbow - shoulder).dot(line_n)

    bend_dir = pole_pos - projection
    bend_dir = bend_dir - line_n * bend_dir.dot(line_n)
    if bend_dir.length < 1.0e-8:
        bend_dir = source_elbow - projection

    src_upper = arm_obj.data.bones.get(src.get("upper_arm"))
    if bend_dir.length < 1.0e-8 and src_upper:
        bend_dir = src_upper.x_axis.copy()
        bend_dir = bend_dir - line_n * bend_dir.dot(line_n)
    if bend_dir.length < 1.0e-8 and src_upper:
        bend_dir = src_upper.z_axis.copy()
        bend_dir = bend_dir - line_n * bend_dir.dot(line_n)
    if bend_dir.length < 1.0e-8:
        trial = Vector((0.0, 0.0, 1.0))
        bend_dir = trial - line_n * trial.dot(line_n)
    if bend_dir.length < 1.0e-8:
        trial = Vector((0.0, 1.0, 0.0))
        bend_dir = trial - line_n * trial.dot(line_n)
    if bend_dir.length < 1.0e-8:
        return result
    bend_dir.normalize()

    l1 = max((source_elbow - shoulder).length, 1.0e-6)
    l2 = max((wrist - source_elbow).length, 1.0e-6)
    source_reach = l1 + l2
    desired_offset = min(l1, l2) * math.tan(SR_ARM_PREBEND_ANGLE * 0.5)
    desired_offset = min(
        desired_offset,
        source_reach * SR_ARM_PREBEND_MAX_OFFSET_FACTOR,
    )

    existing = source_elbow - projection
    existing_along_pole = existing.dot(bend_dir)
    offset = max(desired_offset, existing_along_pole)
    elbow = projection + bend_dir * offset

    mch_upper.tail = elbow
    mch_fore.head = elbow
    mch_fore.tail = wrist
    try:
        mch_fore.use_connect = True
    except Exception:
        pass

    try:
        bend_angle = (elbow - shoulder).angle(wrist - elbow)
    except Exception:
        bend_angle = SR_ARM_PREBEND_ANGLE

    result.update({
        "active": True,
        "offset": float(offset),
        "angle": float(bend_angle),
        "direction": "ELBOW_POLE",
        "mch_reach": float(
            (elbow - shoulder).length + (wrist - elbow).length
        ),
    })
    return result

def _create_elbow_pole_space_edit_bone(
    edit_bones, name, shoulder, wrist, pole_pos, parent_name=None
):
    """Create a hidden smart space whose local Y follows shoulder→wrist."""
    existing = edit_bones.get(name)
    if existing:
        edit_bones.remove(existing)
    b = edit_bones.new(name)
    line = wrist - shoulder
    if line.length < 1.0e-8:
        line = Vector((1.0, 0.0, 0.0))
    line_n = line.normalized()
    b.head = shoulder.copy()
    b.tail = shoulder + line_n * max(line.length * 0.28, 0.04)
    b.roll = 0.0

    # Bias one lateral local axis toward the initial pole hemisphere.
    lateral = pole_pos - shoulder
    lateral = lateral - line_n * lateral.dot(line_n)
    if lateral.length > 1.0e-8:
        try:
            b.align_roll(lateral.normalized())
        except Exception:
            pass

    b.use_connect = False
    b.use_deform = False
    if parent_name:
        parent = edit_bones.get(parent_name)
        if parent:
            b.parent = parent
    return b

def _elbow_prebend_canonical_sign(arm_obj, side, hinge_axis=None):
    """Infer the flexion sign from the *pre-bent MCH* chain, not straight source.

    For an increasing bend the dot product upper_dir·fore_dir must decrease.
    The derivative of fore_dir under a positive local hinge rotation is
    axis×fore_dir. Its sign therefore tells us whether + or - local rotation
    increases the elbow angle. This removes the ambiguity of a 180° source arm.
    """
    suffix = str(side).upper()
    names = _arm_ik_names(suffix)
    upper = arm_obj.data.bones.get(names["mch_upper"])
    fore = arm_obj.data.bones.get(names["mch_forearm"])
    if not upper or not fore:
        return {"sign": 0.0, "quality": 0.0, "metric": 0.0}

    upper_dir = upper.tail_local - upper.head_local
    fore_dir = fore.tail_local - fore.head_local
    if upper_dir.length < 1.0e-8 or fore_dir.length < 1.0e-8:
        return {"sign": 0.0, "quality": 0.0, "metric": 0.0}
    upper_dir.normalize()
    fore_dir.normalize()

    if not hinge_axis:
        try:
            hinge_axis = _elbow_hinge_diagnostics(arm_obj, suffix)["axis"]
        except Exception:
            hinge_axis = "Z"
    axes = _bone_local_axes(fore)
    axis_vec = axes.get(str(hinge_axis).upper())
    if axis_vec is None or axis_vec.length < 1.0e-8:
        return {"sign": 0.0, "quality": 0.0, "metric": 0.0}
    axis_vec = axis_vec.normalized()

    derivative = axis_vec.cross(fore_dir)
    metric = float(upper_dir.dot(derivative))
    # More flexion means a smaller upper·fore dot. Negative derivative means
    # positive local rotation is the flexion direction.
    sign = 1.0 if metric < 0.0 else -1.0

    try:
        bend_angle = upper_dir.angle(fore_dir)
    except Exception:
        bend_angle = 0.0
    expected = max(abs(math.sin(bend_angle)), 1.0e-5)
    quality = min(1.0, abs(metric) / expected)

    return {
        "sign": float(sign),
        "quality": float(quality),
        "metric": float(metric),
        "angle": float(bend_angle),
    }

def _elbow_pole_space_constraint(arm_obj, side):
    suffix = str(side).upper()
    names = _arm_ik_names(suffix)
    pb = arm_obj.pose.bones.get(names.get("mch_pole_space", ""))
    return (
        pb.constraints.get(f"SR_ARMS_IK_POLE_SPACE_SMART_{suffix}")
        if pb else None
    )

def _elbow_pole_guard_constraint(arm_obj, side):
    suffix = str(side).upper()
    names = _arm_ik_names(suffix)
    pb = arm_obj.pose.bones.get(names.get("ctrl_pole", ""))
    return (
        pb.constraints.get(f"SR_ARMS_IK_POLE_PLANE_GUARD_{suffix}")
        if pb else None
    )

def _install_elbow_pole_plane_guard(arm_obj, side, reach_distance):
    """Prevent the Elbow Pole from crossing the shoulder→wrist smart plane.

    The pole is a child of SR_MCH_IK_ElbowPoleSpace. That hidden space tracks
    the effective HandIK target, so its Y axis follows shoulder→wrist. In the
    pole's local coordinates, crossing the bend plane requires reversing its
    dominant lateral rest coordinate; Limit Location stops just before that.
    """
    suffix = str(side).upper()
    names = _arm_ik_names(suffix)
    pole_pb = arm_obj.pose.bones.get(names["ctrl_pole"])
    pole_data = arm_obj.data.bones.get(names["ctrl_pole"])
    space_data = arm_obj.data.bones.get(names["mch_pole_space"])
    if not pole_pb or not pole_data or not space_data:
        return None

    try:
        rest_local = space_data.matrix_local.inverted() @ pole_data.head_local
    except Exception:
        return None

    candidates = {"X": float(rest_local.x), "Z": float(rest_local.z)}
    axis = max(candidates, key=lambda a: abs(candidates[a]))
    rest_value = candidates[axis]
    if abs(rest_value) < 1.0e-6:
        return None

    margin = max(
        float(reach_distance) * SR_ELBOW_POLE_GUARD_MARGIN_FACTOR,
        abs(rest_value) * SR_ELBOW_POLE_GUARD_MIN_FRACTION,
        1.0e-4,
    )

    c = pole_pb.constraints.get(f"SR_ARMS_IK_POLE_PLANE_GUARD_{suffix}")
    if c is None:
        c = pole_pb.constraints.new('LIMIT_LOCATION')
        c.name = f"SR_ARMS_IK_POLE_PLANE_GUARD_{suffix}"
    if hasattr(c, "owner_space"):
        c.owner_space = 'LOCAL'
    if hasattr(c, "use_transform_limit"):
        c.use_transform_limit = True

    for a in "xyz":
        for prefix in ("use_min_", "use_max_"):
            attr = prefix + a
            if hasattr(c, attr):
                setattr(c, attr, False)

    al = axis.lower()
    if rest_value > 0.0:
        setattr(c, f"use_min_{al}", True)
        setattr(c, f"min_{al}", -rest_value + margin)
        sign = 1.0
    else:
        setattr(c, f"use_max_{al}", True)
        setattr(c, f"max_{al}", -rest_value - margin)
        sign = -1.0
    c.influence = 1.0

    quality = min(
        1.0,
        abs(rest_value) / max(float(reach_distance) * 0.35, 1.0e-6),
    )
    arm_obj[f"saberrig_elbow_pole_guard_axis_{suffix}"] = axis
    arm_obj[f"saberrig_elbow_pole_guard_sign_{suffix}"] = float(sign)
    arm_obj[f"saberrig_elbow_pole_guard_margin_{suffix}"] = float(margin)
    arm_obj[f"saberrig_elbow_pole_guard_quality_{suffix}"] = float(quality)
    arm_obj[f"saberrig_elbow_pole_guard_mode_{suffix}"] = "AUTO"
    return c

def _build_arm_ik_side(context, arm_obj, analysis, side):
    suffix = side.upper()
    src = _arm_sources_for_side(analysis, suffix)
    if not all((src["upper_arm"], src["forearm"], src["hand"])):
        missing = [name for name in ("upper_arm", "forearm", "hand") if not src[name]]
        raise RuntimeError(f"{suffix}: missing semantic bones: {', '.join(missing)}")

    fk_names = _arm_fk_names(suffix)
    if not all(arm_obj.data.bones.get(fk_names[k]) for k in ("mch_upper", "mch_forearm", "mch_hand")):
        raise RuntimeError(f"{suffix}: build SaberRig Arms FK before adding IK")

    controls_collection = _find_bone_collection(arm_obj.data, SR_COLLECTION_CONTROLS)
    mechanism_collection = _find_bone_collection(arm_obj.data, SR_COLLECTION_MECHANISM)
    if not controls_collection or not mechanism_collection:
        raise RuntimeError("SaberRig foundation collections are missing")

    names = _arm_ik_names(suffix)
    pole_pos = _arm_pole_position(arm_obj, src)
    rest_shoulder, _rest_elbow, _rest_wrist = _arm_rest_joint_points(arm_obj, src)
    reach_distance = _arm_reach_distance(arm_obj, src)

    data_bones = arm_obj.data.bones
    source_upper_parent = data_bones.get(src["upper_arm"]).parent.name if data_bones.get(src["upper_arm"]).parent else None
    upper_parent = source_upper_parent
    shoulder_parent = None
    has_clavicle_assist = bool(src.get("clavicle") and data_bones.get(fk_names.get("mch_clavicle", "")))
    if has_clavicle_assist:
        fk_clav = data_bones.get(fk_names["mch_clavicle"])
        shoulder_parent = fk_clav.parent.name if fk_clav and fk_clav.parent else None
        upper_parent = names["mch_shoulder"]

    _activate_armature(context, arm_obj, mode='EDIT')
    ebones = arm_obj.data.edit_bones

    mch_shoulder = None
    if has_clavicle_assist:
        mch_shoulder = _clone_edit_bone(ebones, names["mch_shoulder"], src["clavicle"], shoulder_parent)
        mechanism_collection.assign(mch_shoulder)

    mch_upper = _clone_edit_bone(ebones, names["mch_upper"], src["upper_arm"], upper_parent)
    mch_fore = _clone_edit_bone(ebones, names["mch_forearm"], src["forearm"], names["mch_upper"])
    mch_hand = _clone_edit_bone(ebones, names["mch_hand"], src["hand"], names["mch_forearm"])

    # Semantic two-bone MCH chain with straight-rest pre-bend.
    src_fore_edit = ebones.get(src["forearm"])
    src_hand_edit = ebones.get(src["hand"])
    prebend = {
        "active": False, "straightness": 0.0, "angle": 0.0,
        "offset": 0.0, "direction": "SOURCE",
        "mch_reach": float(reach_distance),
    }
    if src_fore_edit and src_hand_edit:
        mch_upper.tail = src_fore_edit.head.copy()
        mch_fore.head = src_fore_edit.head.copy()
        mch_fore.tail = src_hand_edit.head.copy()
        prebend = _apply_arm_prebend_edit_geometry(
            arm_obj, src, mch_upper, mch_fore, pole_pos
        )

    mch_reach = _create_reach_anchor_edit_bone(
        ebones, names["mch_reach"], rest_shoulder, mch_upper, parent_name=upper_parent
    )
    # Non-blocking effective IK target. The animator-facing HandIK remains free;
    # this hidden mechanism follows it, then gets clamped to anatomical reach.
    # This avoids Limit Distance fighting Blender's interactive transform operator.
    mch_target = _clone_edit_bone(ebones, names["mch_target"], src["hand"], None)
    ctrl_hand = _clone_edit_bone(ebones, names["ctrl_hand"], src["hand"], None)

    # Smart Elbow Pole space follows shoulder→effective-wrist and
    # gives the animator pole a stable local hemisphere for the plane guard.
    mch_pole_space = _create_elbow_pole_space_edit_bone(
        ebones, names["mch_pole_space"],
        rest_shoulder, _rest_wrist, pole_pos,
        parent_name=upper_parent,
    )
    ctrl_pole = _create_pole_edit_bone(
        ebones, names["ctrl_pole"], pole_pos, mch_fore
    )
    ctrl_pole.parent = mch_pole_space
    ctrl_pole.use_connect = False

    for b in (mch_upper, mch_fore, mch_hand, mch_reach, mch_target, mch_pole_space):
        mechanism_collection.assign(b)
    for b in (ctrl_hand, ctrl_pole):
        controls_collection.assign(b)

    bpy.ops.object.mode_set(mode='OBJECT')

    arm_obj[f"saberrig_arm_prebend_active_{suffix}"] = bool(prebend.get("active", False))
    arm_obj[f"saberrig_arm_prebend_angle_{suffix}"] = float(prebend.get("angle", 0.0))
    arm_obj[f"saberrig_arm_prebend_offset_{suffix}"] = float(prebend.get("offset", 0.0))
    arm_obj[f"saberrig_arm_prebend_direction_{suffix}"] = str(prebend.get("direction", "SOURCE"))
    arm_obj[f"saberrig_arm_mch_reach_{suffix}"] = float(prebend.get("mch_reach", reach_distance))
    arm_obj[f"saberrig_arm_reach_guard_factor_{suffix}"] = float(SR_DEFAULT_ARM_REACH_FACTOR)

    generated = []
    if has_clavicle_assist and arm_obj.data.bones.get(names["mch_shoulder"]):
        _mark_generated_bone(
            arm_obj, names["mch_shoulder"], "MCH", f"shoulder_assist.{suffix}", src["clavicle"], component=SR_ARM_IK_COMPONENT
        )
        generated.append(names["mch_shoulder"])
    for role, key in (("upper_arm", "mch_upper"), ("forearm", "mch_forearm"), ("hand", "mch_hand")):
        _mark_generated_bone(
            arm_obj, names[key], "MCH", f"{role}.{suffix}", src[role], component=SR_ARM_IK_COMPONENT
        )
        generated.append(names[key])
    _mark_generated_bone(
        arm_obj, names["mch_reach"], "MCH", f"reach_anchor.{suffix}", src["upper_arm"], component=SR_ARM_IK_COMPONENT
    )
    generated.append(names["mch_reach"])
    _mark_generated_bone(
        arm_obj, names["mch_target"], "MCH", f"effective_ik_target.{suffix}", src["hand"], component=SR_ARM_IK_COMPONENT
    )
    generated.append(names["mch_target"])
    _mark_generated_bone(
        arm_obj, names["mch_pole_space"], "MCH",
        f"elbow_pole_space.{suffix}", src["upper_arm"],
        component=SR_ARM_IK_COMPONENT
    )
    generated.append(names["mch_pole_space"])
    _mark_generated_bone(
        arm_obj, names["ctrl_hand"], "CTRL", f"hand_ik.{suffix}", src["hand"], component=SR_ARM_IK_COMPONENT
    )
    _mark_generated_bone(
        arm_obj, names["ctrl_pole"], "CTRL", f"elbow_pole.{suffix}", src["forearm"], component=SR_ARM_IK_COMPONENT
    )
    generated.extend((names["ctrl_hand"], names["ctrl_pole"]))

    # IK controls: hand can translate/rotate; pole translates only.
    hand_pb = arm_obj.pose.bones.get(names["ctrl_hand"])
    pole_pb = arm_obj.pose.bones.get(names["ctrl_pole"])
    if hand_pb:
        hand_pb.lock_location = (False, False, False)
        hand_pb.lock_rotation = (False, False, False)
        hand_pb.lock_scale = (True, True, True)
        try:
            hand_pb.rotation_mode = arm_obj.pose.bones[src["hand"]].rotation_mode
        except Exception:
            pass
    if pole_pb:
        pole_pb.lock_location = (False, False, False)
        pole_pb.lock_rotation = (True, True, True)
        pole_pb.lock_scale = (True, True, True)

    # IK shoulder assist is a separate mechanism clavicle. It copies
    # the animator's FK clavicle as a base and gently tracks the free Hand IK.
    # Source clavicle ownership is switched together with the arm, so the
    # imported hierarchy and any authored twist helpers stay intact.
    if has_clavicle_assist:
        assist_enabled = str(arm_obj.get("saberrig_shoulder_assist_mode", "AUTO")).upper() != "OFF"
        _ensure_shoulder_assist_constraint(arm_obj, suffix, enabled=assist_enabled)

    # Reach guard: only the hidden effective target is capped at the
    # original source-arm reach. With the MCH pre-bend this remains below the
    # solver chain's own full extension; the visible HandIK stays unrestricted.
    effective_target_pb = arm_obj.pose.bones.get(names["mch_target"])
    if not effective_target_pb:
        raise RuntimeError(f"{suffix}: failed to initialize effective IK target")
    _add_copy_transforms(
        effective_target_pb, arm_obj, names["ctrl_hand"], f"SR_ARMS_IK_TARGET_FOLLOW_{suffix}"
    )
    _add_hand_reach_limit(
        arm_obj, suffix, effective_target_pb, names["mch_reach"], reach_distance, factor=SR_DEFAULT_ARM_REACH_FACTOR
    )

    pole_space_pb = arm_obj.pose.bones.get(names["mch_pole_space"])
    if not pole_space_pb:
        raise RuntimeError(f"{suffix}: failed to initialize Elbow Pole smart space")
    smart_track = pole_space_pb.constraints.get(
        f"SR_ARMS_IK_POLE_SPACE_SMART_{suffix}"
    )
    if smart_track is None:
        smart_track = pole_space_pb.constraints.new('DAMPED_TRACK')
        smart_track.name = f"SR_ARMS_IK_POLE_SPACE_SMART_{suffix}"
    smart_track.target = arm_obj
    smart_track.subtarget = names["mch_target"]
    if hasattr(smart_track, "track_axis"):
        smart_track.track_axis = 'TRACK_Y'
    smart_track.influence = 1.0
    arm_obj[f"saberrig_elbow_pole_space_mode_{suffix}"] = "AUTO"
    _install_elbow_pole_plane_guard(
        arm_obj, suffix, reach_distance
    )

    # Two-bone IK is solved on the IK forearm mechanism using the clamped hidden
    # target; the hand mechanism also follows that target, preserving HandIK
    # rotation while preventing positional separation beyond arm reach.
    ik_fore_pb = arm_obj.pose.bones.get(names["mch_forearm"])
    ik_hand_pb = arm_obj.pose.bones.get(names["mch_hand"])
    if not ik_fore_pb or not ik_hand_pb:
        raise RuntimeError(f"{suffix}: failed to initialize IK mechanism pose bones")

    ik = ik_fore_pb.constraints.new('IK')
    ik.name = f"SR_ARMS_IK_SOLVER_{suffix}"
    ik.target = arm_obj
    ik.subtarget = names["mch_target"]
    ik.pole_target = arm_obj
    ik.pole_subtarget = names["ctrl_pole"]
    ik.chain_count = 2
    if hasattr(ik, "use_tail"):
        ik.use_tail = True
    if hasattr(ik, "use_stretch"):
        ik.use_stretch = False
    if hasattr(ik, "use_rotation"):
        ik.use_rotation = False

    # Start neutral. A numerical FK-referenced calibration is performed after
    # the controls are matched to the current pose. This avoids assuming any
    # particular imported bone-roll convention.
    if hasattr(ik, "pole_angle"):
        ik.pole_angle = 0.0

    _add_copy_transforms(
        ik_hand_pb, arm_obj, names["mch_target"], f"SR_ARMS_IK_HAND_CTRL_{suffix}"
    )

    # Add an IK alternative to the existing source-bone FK constraints.
    if has_clavicle_assist:
        source_clav_pb = arm_obj.pose.bones.get(src["clavicle"])
        if source_clav_pb:
            c = _add_copy_transforms(
                source_clav_pb, arm_obj, names["mch_shoulder"], f"SR_ARMS_IK_DEF_{suffix}_clavicle"
            )
            c.influence = 0.0

    for role, source_name, mch_name in (
        ("upper_arm", src["upper_arm"], names["mch_upper"]),
        ("forearm", src["forearm"], names["mch_forearm"]),
        ("hand", src["hand"], names["mch_hand"]),
    ):
        source_pb = arm_obj.pose.bones.get(source_name)
        if not source_pb:
            raise RuntimeError(f"{suffix}: source pose bone disappeared: {source_name}")
        c = _add_copy_transforms(
            source_pb, arm_obj, mch_name, f"SR_ARMS_IK_DEF_{suffix}_{role}"
        )
        c.influence = 0.0

    # Preserve FK ownership while matching/calibrating the hidden IK chain.
    _set_arm_mode_raw(arm_obj, suffix, False)
    calibration = _snap_fk_to_ik(context, arm_obj, suffix, calibrate=True)
    source_singular = bool(calibration.get("singular", False))
    arm_obj[f"saberrig_pole_singular_{suffix}"] = source_singular
    stabilized = bool(arm_obj.get(f"saberrig_arm_prebend_active_{suffix}", False))
    preferred_active = bool(
        arm_obj.get(f"saberrig_arm_preferred_reach_active_{suffix}", False)
    )
    arm_obj[f"saberrig_arm_solver_state_{suffix}"] = (
        "STABLE_PREBEND_PREFERRED" if source_singular and stabilized and preferred_active
        else ("STABLE_PREBEND" if source_singular and stabilized
              else ("SINGULAR_FALLBACK" if source_singular else "STABLE"))
    )

    # Constrain only the generated IK mechanism. The imported/source
    # skeleton remains untouched. SAFE is the default unless the user explicitly
    # disabled limits before rebuilding.
    safe_enabled = str(arm_obj.get("saberrig_joint_limits_mode", "SAFE")).upper() != "OFF"
    _apply_arm_joint_limits(arm_obj, suffix, enabled=safe_enabled)
    _store_twist_preservation_metadata(arm_obj, analysis, suffix)
    return generated

def _build_arms_ik(context, arm_obj, analysis):
    if not _foundation_is_prepared(arm_obj):
        _prepare_foundation(arm_obj, analysis)
    if not bool(arm_obj.get("saberrig_arms_fk_built", False)):
        _build_arms_fk(context, arm_obj, analysis)
        analysis = analyze_armature(arm_obj)

    _activate_armature(context, arm_obj, mode='OBJECT')
    requested_limits_mode = str(arm_obj.get("saberrig_joint_limits_mode", "SAFE")).upper()
    requested_shoulder_mode = str(arm_obj.get("saberrig_shoulder_assist_mode", "AUTO")).upper()
    requested_shoulder_strength = float(arm_obj.get("saberrig_shoulder_assist_strength", SR_SHOULDER_ASSIST_DEFAULT))
    _remove_arm_ik(context, arm_obj)
    arm_obj["saberrig_joint_limits_mode"] = "OFF" if requested_limits_mode == "OFF" else "SAFE"
    arm_obj["saberrig_shoulder_assist_mode"] = "OFF" if requested_shoulder_mode == "OFF" else "AUTO"
    arm_obj["saberrig_shoulder_assist_strength"] = max(SR_SHOULDER_ASSIST_MIN, min(SR_SHOULDER_ASSIST_MAX, requested_shoulder_strength))

    generated = []
    errors = []
    for side in ("L", "R"):
        try:
            generated.extend(_build_arm_ik_side(context, arm_obj, analysis, side))
        except Exception as exc:
            try:
                if arm_obj.mode != 'OBJECT':
                    bpy.ops.object.mode_set(mode='OBJECT')
            except Exception:
                pass
            _remove_ik_constraints_for_side(arm_obj, side)
            try:
                _remove_bones_by_names(context, arm_obj, _arm_ik_names(side).values())
            except Exception:
                pass
            errors.append(str(exc))

    if not generated:
        raise RuntimeError(errors[0] if errors else "No arm IK controls could be generated")

    arm_obj["saberrig_arms_ik_built"] = True
    arm_obj["saberrig_arms_ik_version"] = ADDON_VERSION
    arm_obj["saberrig_arm_mode_L"] = "FK"
    arm_obj["saberrig_arm_mode_R"] = "FK"
    _activate_armature(context, arm_obj, mode='POSE')
    if bool(arm_obj.get("saberrig_body_built", False)):
        _attach_global_controls_to_master(context, arm_obj)
    if bool(arm_obj.get("saberrig_animation_spaces_built", False)):
        try:
            _build_animation_spaces(context, arm_obj, analyze_armature(arm_obj))
        except Exception:
            pass
    _setup_control_visuals(arm_obj)
    _set_rig_view(arm_obj, True)
    return generated, errors

# -----------------------------------------------------------------------------
# Leg FK/IK
# -----------------------------------------------------------------------------

def _leg_fk_names(side):
    suffix = side.upper()
    return {
        "ctrl_thigh": f"SR_CTRL_ThighFK.{suffix}",
        "ctrl_shin": f"SR_CTRL_ShinFK.{suffix}",
        "ctrl_foot": f"SR_CTRL_FootFK.{suffix}",
        "mch_thigh": f"SR_MCH_Thigh.{suffix}",
        "mch_shin": f"SR_MCH_Shin.{suffix}",
        "mch_foot": f"SR_MCH_Foot.{suffix}",
    }

def _leg_ik_names(side):
    suffix = side.upper()
    return {
        "mch_thigh": f"SR_MCH_IK_Thigh.{suffix}",
        "mch_shin": f"SR_MCH_IK_Shin.{suffix}",
        "mch_foot": f"SR_MCH_IK_Foot.{suffix}",
        "mch_reach": f"SR_MCH_IK_LegReachAnchor.{suffix}",
        "mch_target": f"SR_MCH_IK_EffectiveFootTarget.{suffix}",
        "mch_pole_space": f"SR_MCH_IK_KneePoleSpace.{suffix}",
        "ctrl_master": f"SR_CTRL_FootMasterIK.{suffix}",
        "ctrl_heel": f"SR_CTRL_HeelRoll.{suffix}",
        "ctrl_ball": f"SR_CTRL_BallRoll.{suffix}",
        "ctrl_toe": f"SR_CTRL_ToeRoll.{suffix}",
        "ctrl_foot": f"SR_CTRL_FootIK.{suffix}",
        "ctrl_pole": f"SR_CTRL_KneePole.{suffix}",
    }

def _remove_leg_fk(context, arm_obj):
    removed_constraints = _remove_constraints(arm_obj, "SR_LEGS_FK_")
    removed_bones = _remove_generated_bones(context, arm_obj, component=SR_LEG_FK_COMPONENT)
    for key in ("saberrig_legs_fk_built", "saberrig_legs_fk_version"):
        if key in arm_obj:
            del arm_obj[key]
    return removed_bones, removed_constraints

def _remove_leg_ik(context, arm_obj):
    for side in ("L", "R"):
        try:
            _set_leg_mode_raw(arm_obj, side, False)
        except Exception:
            pass
    removed_constraints = _remove_constraints(arm_obj, "SR_LEGS_IK_")
    removed_bones = _remove_generated_bones(context, arm_obj, component=SR_LEG_IK_COMPONENT)
    fixed = ("saberrig_legs_ik_built", "saberrig_legs_ik_version", "saberrig_leg_mode_L", "saberrig_leg_mode_R")
    prefixes = (
        "saberrig_leg_pole_angle_", "saberrig_leg_pole_error_", "saberrig_leg_pole_singular_",
        "saberrig_leg_straightness_", "saberrig_leg_reach_", "saberrig_leg_reach_factor_",
        "saberrig_leg_min_reach_", "saberrig_leg_min_reach_factor_",
        "saberrig_leg_preferred_reach_", "saberrig_leg_preferred_reach_factor_",
        "saberrig_leg_prebend_active_", "saberrig_leg_prebend_angle_", "saberrig_leg_prebend_offset_",
        "saberrig_leg_prebend_direction_", "saberrig_leg_mch_reach_",
        "saberrig_knee_hinge_axis_", "saberrig_knee_hinge_sign_", "saberrig_knee_hinge_confidence_",
        "saberrig_knee_hinge_mode_", "saberrig_knee_flexion_deg_", "saberrig_knee_hyper_deg_",
        "saberrig_knee_prebend_sign_", "saberrig_knee_prebend_sign_confidence_",
        "saberrig_knee_pole_guard_axis_", "saberrig_knee_pole_guard_sign_",
        "saberrig_knee_pole_guard_margin_", "saberrig_knee_pole_guard_quality_",
        "saberrig_foot_ctrl_limit_", "saberrig_foot_roll_",
        "saberrig_hip_flex_axis_", "saberrig_hip_abd_axis_", "saberrig_hip_flex_sign_",
        "saberrig_hip_abd_sign_", "saberrig_hip_flex_confidence_", "saberrig_hip_abd_confidence_",
        "saberrig_ankle_flex_axis_", "saberrig_ankle_bank_axis_", "saberrig_ankle_flex_confidence_",
    )
    for key in list(arm_obj.keys()):
        if key in fixed or str(key).startswith(prefixes):
            try:
                del arm_obj[key]
            except Exception:
                pass
    return removed_bones, removed_constraints

def _build_leg_fk_side(context, arm_obj, analysis, side):
    suffix = side.upper()
    src = _leg_sources_for_side(analysis, suffix)
    if not all((src["thigh"], src["shin"], src["foot"])):
        missing = [r for r in ("thigh", "shin", "foot") if not src[r]]
        raise RuntimeError(f"{suffix}: missing semantic leg bones: {', '.join(missing)}")

    controls_collection = _find_bone_collection(arm_obj.data, SR_COLLECTION_CONTROLS)
    mechanism_collection = _find_bone_collection(arm_obj.data, SR_COLLECTION_MECHANISM)
    if not controls_collection or not mechanism_collection:
        raise RuntimeError("SaberRig foundation collections are missing")

    names = _leg_fk_names(suffix)
    data_bones = arm_obj.data.bones
    thigh_data = data_bones.get(src["thigh"])
    thigh_parent = thigh_data.parent.name if thigh_data and thigh_data.parent else None

    _activate_armature(context, arm_obj, mode='EDIT')
    ebones = arm_obj.data.edit_bones
    ctrl_thigh = _clone_edit_bone(ebones, names["ctrl_thigh"], src["thigh"], thigh_parent)
    ctrl_shin = _clone_edit_bone(ebones, names["ctrl_shin"], src["shin"], names["ctrl_thigh"])
    ctrl_foot = _clone_edit_bone(ebones, names["ctrl_foot"], src["foot"], names["ctrl_shin"])
    mch_thigh = _clone_edit_bone(ebones, names["mch_thigh"], src["thigh"], thigh_parent)
    mch_shin = _clone_edit_bone(ebones, names["mch_shin"], src["shin"], names["mch_thigh"])
    mch_foot = _clone_edit_bone(ebones, names["mch_foot"], src["foot"], names["mch_shin"])
    for b in (ctrl_thigh, ctrl_shin, ctrl_foot):
        controls_collection.assign(b)
    for b in (mch_thigh, mch_shin, mch_foot):
        mechanism_collection.assign(b)
    bpy.ops.object.mode_set(mode='OBJECT')

    generated = []
    for role, ctrl_key, mch_key in (
        ("thigh", "ctrl_thigh", "mch_thigh"),
        ("shin", "ctrl_shin", "mch_shin"),
        ("foot", "ctrl_foot", "mch_foot"),
    ):
        _mark_generated_bone(arm_obj, names[ctrl_key], "CTRL", f"{role}.{suffix}", src[role], component=SR_LEG_FK_COMPONENT)
        _mark_generated_bone(arm_obj, names[mch_key], "MCH", f"{role}.{suffix}", src[role], component=SR_LEG_FK_COMPONENT)
        generated.extend((names[ctrl_key], names[mch_key]))

    for role, key in (("thigh", "ctrl_thigh"), ("shin", "ctrl_shin"), ("foot", "ctrl_foot")):
        pb = arm_obj.pose.bones.get(names[key])
        src_pb = arm_obj.pose.bones.get(src[role])
        if pb:
            if src_pb:
                try:
                    pb.rotation_mode = src_pb.rotation_mode
                except Exception:
                    pass
            pb.lock_location = (True, True, True)
            pb.lock_scale = (True, True, True)

    for role, ctrl_key, mch_key in (
        ("thigh", "ctrl_thigh", "mch_thigh"),
        ("shin", "ctrl_shin", "mch_shin"),
        ("foot", "ctrl_foot", "mch_foot"),
    ):
        mch_pb = arm_obj.pose.bones.get(names[mch_key])
        source_pb = arm_obj.pose.bones.get(src[role])
        if not mch_pb or not source_pb:
            raise RuntimeError(f"{suffix}: failed to initialize generated leg pose bones for {role}")
        _add_copy_transforms(mch_pb, arm_obj, names[ctrl_key], f"SR_LEGS_FK_CTRL_{suffix}_{role}")
        fk = _add_copy_transforms(source_pb, arm_obj, names[mch_key], f"SR_LEGS_FK_DEF_{suffix}_{role}")
        fk.influence = 1.0
    return generated

def _build_legs_fk(context, arm_obj, analysis):
    if not _foundation_is_prepared(arm_obj):
        _prepare_foundation(arm_obj, analysis)
    _activate_armature(context, arm_obj, mode='OBJECT')
    _remove_leg_fk(context, arm_obj)
    generated, errors = [], []
    for side in ("L", "R"):
        try:
            generated.extend(_build_leg_fk_side(context, arm_obj, analysis, side))
        except Exception as exc:
            try:
                if arm_obj.mode != 'OBJECT':
                    bpy.ops.object.mode_set(mode='OBJECT')
            except Exception:
                pass
            _remove_constraints(arm_obj, f"SR_LEGS_FK_")
            try:
                _remove_bones_by_names(context, arm_obj, _leg_fk_names(side).values())
            except Exception:
                pass
            errors.append(str(exc))
    if not generated:
        raise RuntimeError(errors[0] if errors else "No leg FK controls could be generated")
    arm_obj["saberrig_legs_fk_built"] = True
    arm_obj["saberrig_legs_fk_version"] = ADDON_VERSION
    _activate_armature(context, arm_obj, mode='POSE')
    _setup_control_visuals(arm_obj)
    return generated, errors

def _leg_rest_joint_points(arm_obj, src):
    thigh = arm_obj.data.bones.get(src.get("thigh"))
    shin = arm_obj.data.bones.get(src.get("shin"))
    foot = arm_obj.data.bones.get(src.get("foot"))
    if not thigh or not shin or not foot:
        raise RuntimeError("Cannot resolve leg rest joint points")
    hip = thigh.head_local.copy()
    knee = shin.head_local.copy()
    ankle = foot.head_local.copy()
    if (ankle - knee).length < 1.0e-8:
        ankle = shin.tail_local.copy()
    return hip, knee, ankle

def _leg_pose_joint_points(arm_obj, src):
    thigh = arm_obj.pose.bones.get(src.get("thigh"))
    shin = arm_obj.pose.bones.get(src.get("shin"))
    foot = arm_obj.pose.bones.get(src.get("foot"))
    if not thigh or not shin or not foot:
        raise RuntimeError("Cannot resolve leg pose joint points")
    hip = thigh.head.copy()
    knee = shin.head.copy()
    ankle = foot.head.copy()
    if (ankle - knee).length < 1.0e-8:
        ankle = shin.tail.copy()
    return hip, knee, ankle

def _leg_reach_distance(arm_obj, src):
    hip, knee, ankle = _leg_rest_joint_points(arm_obj, src)
    reach = (knee - hip).length + (ankle - knee).length
    if reach < 1.0e-6:
        thigh = arm_obj.data.bones.get(src.get("thigh"))
        shin = arm_obj.data.bones.get(src.get("shin"))
        reach = max((thigh.length if thigh else 0.0) + (shin.length if shin else 0.0), 0.05)
    return reach

def _leg_forward_vector(arm_obj, src, pose=False):
    collection = arm_obj.pose.bones if pose else arm_obj.data.bones
    foot = collection.get(src.get("foot")) if src.get("foot") else None
    toe = collection.get(src.get("toe")) if src.get("toe") else None
    if foot and toe:
        foot_head = foot.head.copy() if pose else foot.head_local.copy()
        toe_head = toe.head.copy() if pose else toe.head_local.copy()
        v = toe_head - foot_head
        if v.length > 1.0e-8:
            return v
    if foot:
        return (foot.tail - foot.head) if pose else (foot.tail_local - foot.head_local)
    return Vector((0.0, -1.0, 0.0))

def _leg_master_geometry(arm_obj, src):
    """Return (heel_head, heel_tail) for a ground-level master control.

    The control is inferred semantically rather than assuming global Z is up:
    leg direction supplies an up vector, while foot/toe direction supplies
    forward. The heel is lowered to approximately sole/toe height and moved
    slightly behind the ankle.
    """
    hip, knee, ankle = _leg_rest_joint_points(arm_obj, src)
    up = hip - ankle
    if up.length < 1.0e-8:
        up = Vector((0.0, 0.0, 1.0))
    up.normalize()

    raw_forward = _leg_forward_vector(arm_obj, src, pose=False)
    forward = raw_forward - up * raw_forward.dot(up)
    foot = arm_obj.data.bones.get(src.get("foot")) if src.get("foot") else None
    toe = arm_obj.data.bones.get(src.get("toe")) if src.get("toe") else None
    if forward.length < 1.0e-8 and foot:
        forward = foot.vector.copy() - up * foot.vector.dot(up)
    if forward.length < 1.0e-8:
        # Any horizontal-ish axis is acceptable as a last-resort display axis.
        trial = Vector((0.0, 1.0, 0.0))
        forward = trial - up * trial.dot(up)
    if forward.length < 1.0e-8:
        trial = Vector((1.0, 0.0, 0.0))
        forward = trial - up * trial.dot(up)
    forward.normalize()

    candidates = []
    if foot:
        candidates.append(foot.tail_local.copy())
    if toe:
        candidates.append(toe.head_local.copy())
        candidates.append(toe.tail_local.copy())
    vertical = [((p - ankle).dot(up)) for p in candidates]
    sole_drop = min(vertical) if vertical else 0.0

    foot_len = max(raw_forward.length, foot.length if foot else 0.0, 0.04)
    # Some rigs have toe/foot points at ankle height. Give the master a small
    # downward offset so it still reads as a heel/ground control.
    if sole_drop > -foot_len * 0.05:
        sole_drop = -foot_len * 0.18
    sole = ankle + up * sole_drop
    heel = sole - forward * (foot_len * 0.28)
    tail = heel + forward * max(foot_len * 0.48, 0.025)
    return heel, tail

def _create_master_edit_bone(edit_bones, name, head, tail):
    existing = edit_bones.get(name)
    if existing:
        edit_bones.remove(existing)
    b = edit_bones.new(name)
    b.head = head.copy()
    b.tail = tail.copy()
    if (b.tail - b.head).length < 1.0e-6:
        b.tail = b.head + Vector((0.0, 0.03, 0.0))
    b.roll = 0.0
    b.use_connect = False
    b.use_deform = False
    b.parent = None
    return b

def _leg_foot_roll_geometry(arm_obj, src):
    """Resolve semantic heel, ball and toe-tip pivots in armature space.

    Imported game rigs frequently disagree about foot bone roll and whether a
    dedicated toe bone exists. SaberRig therefore derives a local foot frame
    from Hip→Ankle (up) and Foot→Toe (forward), then projects all reverse-foot
    pivots onto the sole plane. Missing toe semantics get a conservative
    synthetic ball/toe pair instead of disabling leg IK.
    """
    heel, _master_tail = _leg_master_geometry(arm_obj, src)
    hip, _knee, ankle = _leg_rest_joint_points(arm_obj, src)
    up = hip - ankle
    if up.length < 1.0e-8:
        up = Vector((0.0, 0.0, 1.0))
    up.normalize()

    raw_forward = _leg_forward_vector(arm_obj, src, pose=False)
    forward = raw_forward - up * raw_forward.dot(up)
    foot = arm_obj.data.bones.get(src.get("foot")) if src.get("foot") else None
    toe = arm_obj.data.bones.get(src.get("toe")) if src.get("toe") else None
    if forward.length < 1.0e-8 and foot:
        forward = foot.vector.copy() - up * foot.vector.dot(up)
    if forward.length < 1.0e-8:
        trial = Vector((0.0, 1.0, 0.0))
        forward = trial - up * trial.dot(up)
    if forward.length < 1.0e-8:
        trial = Vector((1.0, 0.0, 0.0))
        forward = trial - up * trial.dot(up)
    forward.normalize()

    foot_len = max(raw_forward.length, foot.length if foot else 0.0, 0.04)
    def sole_project(point):
        p = point.copy()
        return p - up * (p - heel).dot(up)

    source_mode = "SEMANTIC_TOE" if toe else "SYNTHETIC_TOE"
    if toe:
        ball = sole_project(toe.head_local)
        toe_tip = sole_project(toe.tail_local)
    else:
        forward_end = foot.tail_local.copy() if foot else (ankle + forward * foot_len)
        ball = sole_project(forward_end)
        toe_tip = ball + forward * max(foot_len * 0.34, 0.018)

    # Protect against rigs whose toe helper is nearly coincident with the ankle
    # or points backwards because it is an orientation helper rather than a
    # geometric toe chain.
    if (ball - heel).dot(forward) < foot_len * 0.30:
        ball = heel + forward * max(foot_len * 0.68, 0.032)
        source_mode = "SYNTHETIC_BALL"
    if (toe_tip - ball).dot(forward) < foot_len * 0.10:
        toe_tip = ball + forward * max(foot_len * 0.34, 0.018)
        source_mode = "SYNTHETIC_TIP" if toe else "SYNTHETIC_TOE"

    display_len = max(foot_len * 0.24, 0.016)
    return {
        "heel": heel.copy(), "ball": ball.copy(), "toe": toe_tip.copy(),
        "forward": forward.copy(), "up": up.copy(), "display_len": float(display_len),
        "foot_len": float(foot_len), "source_mode": source_mode,
    }

def _create_foot_roll_pivot_edit_bone(edit_bones, name, point, forward, up, length, parent_name):
    existing = edit_bones.get(name)
    if existing:
        edit_bones.remove(existing)
    b = edit_bones.new(name)
    b.head = point.copy()
    direction = forward.normalized() if forward.length > 1.0e-8 else Vector((0.0, 1.0, 0.0))
    b.tail = b.head + direction * max(float(length), 1.0e-4)
    b.roll = 0.0
    try:
        b.align_roll(up)
    except Exception:
        pass
    b.use_connect = False
    b.use_deform = False
    b.parent = edit_bones.get(parent_name) if parent_name else None
    return b

def _ensure_local_rotation_limit(pb, name, x_range=None, y_range=None, z_range=None):
    c = pb.constraints.get(name)
    if c is None:
        c = pb.constraints.new('LIMIT_ROTATION')
        c.name = name
    c.influence = 1.0
    if hasattr(c, "owner_space"):
        c.owner_space = 'LOCAL'
    if hasattr(c, "use_transform_limit"):
        c.use_transform_limit = True
    for axis, rng in (("x", x_range), ("y", y_range), ("z", z_range)):
        use = rng is not None
        if hasattr(c, f"use_limit_{axis}"):
            setattr(c, f"use_limit_{axis}", use)
        if use:
            lo, hi = rng
            setattr(c, f"min_{axis}", float(lo))
            setattr(c, f"max_{axis}", float(hi))
    return c

def _configure_foot_roll_pose_control(pb, kind, suffix):
    if not pb:
        return None
    try:
        pb.rotation_mode = 'XYZ'
    except Exception:
        pass
    pb.lock_location = (True, True, True)
    pb.lock_scale = (True, True, True)
    if kind == "HEEL":
        # X = heel/toe pitch, Y = foot bank, Z = yaw (owned by Foot Master).
        pb.lock_rotation = (False, False, True)
        return _ensure_local_rotation_limit(
            pb, f"SR_LEGS_IK_HEEL_ROLL_LIMIT_{suffix}",
            x_range=(-SR_FOOT_HEEL_ROLL_BACK, SR_FOOT_HEEL_ROLL_FORWARD),
            y_range=(-SR_FOOT_HEEL_BANK, SR_FOOT_HEEL_BANK),
            z_range=(0.0, 0.0),
        )
    if kind == "BALL":
        pb.lock_rotation = (False, True, True)
        return _ensure_local_rotation_limit(
            pb, f"SR_LEGS_IK_BALL_ROLL_LIMIT_{suffix}",
            x_range=(-SR_FOOT_BALL_ROLL_BACK, SR_FOOT_BALL_ROLL_FORWARD),
            y_range=(0.0, 0.0), z_range=(0.0, 0.0),
        )
    pb.lock_rotation = (False, True, True)
    return _ensure_local_rotation_limit(
        pb, f"SR_LEGS_IK_TOE_ROLL_LIMIT_{suffix}",
        x_range=(-SR_FOOT_TOE_ROLL_BACK, SR_FOOT_TOE_ROLL_FORWARD),
        y_range=(0.0, 0.0), z_range=(0.0, 0.0),
    )

def _reset_foot_roll_controls(arm_obj, side):
    suffix = side.upper()
    names = _leg_ik_names(suffix)
    reset = 0
    for key in ("ctrl_heel", "ctrl_ball", "ctrl_toe"):
        pb = arm_obj.pose.bones.get(names.get(key, ""))
        if not pb:
            continue
        try:
            pb.matrix_basis = Matrix.Identity(4)
            reset += 1
        except Exception:
            for attr in ("rotation_euler",):
                try:
                    setattr(pb, attr, (0.0, 0.0, 0.0))
                    reset += 1
                    break
                except Exception:
                    pass
    return reset

def _leg_pole_position_from_points(arm_obj, src, hip, knee, ankle, existing_pole=None, pose=False):
    """Build a stable knee-pole position from the current leg plane.

    Near full extension the geometric bend vector becomes numerically unstable.
    In that zone SaberRig preserves the previous pole hemisphere, then falls
    back to semantic foot-forward. This prevents a straight leg from choosing
    an arbitrary knee side on the next evaluation.
    """
    line = ankle - hip
    if line.length < 1.0e-8:
        line = knee - hip
    line_n = line.normalized() if line.length else Vector((0.0, 0.0, 1.0))
    projection = hip + line_n * (knee - hip).dot(line_n)
    bend = knee - projection
    reach = max((knee - hip).length + (ankle - knee).length, 0.05)
    straightness = _straightness_from_points(hip, knee, ankle)

    previous = None
    if existing_pole is not None:
        previous = existing_pole - knee
        previous = previous - line_n * previous.dot(line_n)
        if previous.length <= 1.0e-8:
            previous = None

    if previous is not None and straightness >= SR_SMART_KNEE_HEMISPHERE_DOT:
        if bend.length < reach * 1.0e-3:
            bend = previous.copy()
        elif bend.dot(previous) < 0.0:
            bend.negate()

    if bend.length < reach * 1.0e-4 and previous is not None:
        bend = previous.copy()

    if bend.length < reach * 1.0e-4:
        forward = _leg_forward_vector(arm_obj, src, pose=pose)
        forward = forward - line_n * forward.dot(line_n)
        if forward.length > 1.0e-8:
            if previous is not None and forward.dot(previous) < 0.0:
                forward.negate()
            bend = forward

    if bend.length < 1.0e-8:
        thigh = arm_obj.data.bones.get(src.get("thigh"))
        if thigh:
            fallback = thigh.x_axis.copy() - line_n * thigh.x_axis.dot(line_n)
            if fallback.length < 1.0e-8:
                fallback = thigh.z_axis.copy() - line_n * thigh.z_axis.dot(line_n)
            if previous is not None and fallback.length > 1.0e-8 and fallback.dot(previous) < 0.0:
                fallback.negate()
            bend = fallback
    if bend.length < 1.0e-8:
        bend = Vector((0.0, -1.0, 0.0))
        bend = bend - line_n * bend.dot(line_n)
    if bend.length < 1.0e-8:
        bend = Vector((1.0, 0.0, 0.0))

    return knee + bend.normalized() * reach * 0.72, straightness

def _leg_pole_position(arm_obj, src):
    hip, knee, ankle = _leg_rest_joint_points(arm_obj, src)
    pos, _ = _leg_pole_position_from_points(arm_obj, src, hip, knee, ankle, existing_pole=None, pose=False)
    return pos

def _create_knee_pole_space_edit_bone(edit_bones, name, hip, ankle, pole_pos, parent_name=None):
    """Create a hidden pole-space whose Y axis follows the semantic leg line.

    The animator-facing Knee Pole is parented to this space. At runtime a
    Damped Track rotates the space toward the effective ankle target; because
    the pole retains its local offset, it follows wide Foot-Master arcs without
    being left behind in world space. This is intentionally a mechanism bone,
    never a deform/source bone.
    """
    existing = edit_bones.get(name)
    if existing:
        edit_bones.remove(existing)
    b = edit_bones.new(name)
    line = ankle - hip
    if line.length < 1.0e-8:
        line = Vector((0.0, 0.0, -1.0))
    line_n = line.normalized()
    length = max(line.length * 0.28, 0.04)
    b.head = hip.copy()
    b.tail = hip + line_n * length
    b.roll = 0.0
    # Bias the roll so one lateral local axis points toward the initial pole.
    lateral = pole_pos - hip
    lateral = lateral - line_n * lateral.dot(line_n)
    if lateral.length > 1.0e-8:
        try:
            b.align_roll(lateral.normalized())
        except Exception:
            pass
    b.use_connect = False
    b.use_deform = False
    if parent_name:
        parent = edit_bones.get(parent_name)
        if parent:
            b.parent = parent
    return b

def _knee_pole_space_constraint(arm_obj, side):
    names = _leg_ik_names(side)
    pb = arm_obj.pose.bones.get(names.get("mch_pole_space", ""))
    return pb.constraints.get(f"SR_LEGS_IK_POLE_SPACE_SMART_{side.upper()}") if pb else None

def _knee_pole_guard_constraint(arm_obj, side):
    names = _leg_ik_names(side)
    pb = arm_obj.pose.bones.get(names.get("ctrl_pole", ""))
    return pb.constraints.get(f"SR_LEGS_IK_POLE_FLIP_GUARD_{side.upper()}") if pb else None

def _install_knee_pole_flip_guard(arm_obj, side, reach_distance):
    """Keep the Knee Pole on its authored side of the smart pole plane.

    The pole is a child of SR_MCH_IK_KneePoleSpace. In pose-local coordinates
    its rest offset from that space is known. Crossing the hip→ankle plane would
    require cancelling and reversing the dominant lateral rest coordinate, so
    a local Limit Location can stop just before that crossing without freezing
    normal pole edits.
    """
    suffix = side.upper(); names = _leg_ik_names(suffix)
    pole_pb = arm_obj.pose.bones.get(names["ctrl_pole"])
    pole_data = arm_obj.data.bones.get(names["ctrl_pole"])
    space_data = arm_obj.data.bones.get(names["mch_pole_space"])
    if not pole_pb or not pole_data or not space_data:
        return None
    try:
        rest_local = space_data.matrix_local.inverted() @ pole_data.head_local
    except Exception:
        return None
    candidates = {"X": float(rest_local.x), "Z": float(rest_local.z)}
    axis = max(candidates, key=lambda a: abs(candidates[a]))
    rest_value = candidates[axis]
    if abs(rest_value) < 1.0e-6:
        return None
    margin = max(float(reach_distance) * SR_KNEE_POLE_GUARD_MARGIN_FACTOR,
                 abs(rest_value) * SR_KNEE_POLE_GUARD_MIN_FRACTION,
                 1.0e-4)
    c = pole_pb.constraints.get(f"SR_LEGS_IK_POLE_FLIP_GUARD_{suffix}")
    if c is None:
        c = pole_pb.constraints.new('LIMIT_LOCATION')
        c.name = f"SR_LEGS_IK_POLE_FLIP_GUARD_{suffix}"
    if hasattr(c, "owner_space"):
        c.owner_space = 'LOCAL'
    if hasattr(c, "use_transform_limit"):
        c.use_transform_limit = True
    # Clear every axis first. Pose-bone local location is zero at rest, so the
    # crossing delta is approximately -rest_value on the dominant lateral axis.
    for a in "xyz":
        for prefix in ("use_min_", "use_max_"):
            if hasattr(c, prefix + a):
                setattr(c, prefix + a, False)
    al = axis.lower()
    if rest_value > 0.0:
        setattr(c, f"use_min_{al}", True)
        setattr(c, f"min_{al}", -rest_value + margin)
        sign = 1.0
    else:
        setattr(c, f"use_max_{al}", True)
        setattr(c, f"max_{al}", -rest_value - margin)
        sign = -1.0
    c.influence = 1.0
    quality = min(1.0, abs(rest_value) / max(float(reach_distance) * 0.35, 1.0e-6))
    arm_obj[f"saberrig_knee_pole_guard_axis_{suffix}"] = axis
    arm_obj[f"saberrig_knee_pole_guard_sign_{suffix}"] = float(sign)
    arm_obj[f"saberrig_knee_pole_guard_margin_{suffix}"] = float(margin)
    arm_obj[f"saberrig_knee_pole_guard_quality_{suffix}"] = float(quality)
    return c

def _apply_knee_pole_space_mode(arm_obj, side, mode="SMART", preserve_world=True):
    suffix = side.upper(); names = _leg_ik_names(suffix)
    pole_pb = arm_obj.pose.bones.get(names["ctrl_pole"])
    smart = _knee_pole_space_constraint(arm_obj, suffix)
    guard = _knee_pole_guard_constraint(arm_obj, suffix)
    if not pole_pb or not smart:
        return False
    world = pole_pb.matrix.copy() if preserve_world else None
    mode = "GLOBAL" if str(mode).upper() == "GLOBAL" else "SMART"
    smart.influence = 1.0 if mode == "SMART" else 0.0
    if guard:
        guard.influence = 1.0 if mode == "SMART" else 0.0
    try:
        bpy.context.view_layer.update()
    except Exception:
        pass
    if world is not None:
        try:
            pole_pb.matrix = world
            bpy.context.view_layer.update()
        except Exception:
            pass
    arm_obj["saberrig_knee_pole_space_mode"] = mode
    return True

def _apply_all_knee_pole_space_mode(arm_obj, mode="SMART", preserve_world=True):
    changed = 0
    for side in ("L", "R"):
        changed += 1 if _apply_knee_pole_space_mode(arm_obj, side, mode=mode, preserve_world=preserve_world) else 0
    arm_obj["saberrig_knee_pole_space_mode"] = "GLOBAL" if str(mode).upper() == "GLOBAL" else "SMART"
    return changed

def _apply_leg_prebend_edit_geometry(arm_obj, src, mch_thigh, mch_shin, pole_pos):
    """Give a rest-straight hidden IK chain a tiny *real* bend toward its pole.

    This mirrors the key requirement of production two-bone rigs: the solver
    chain itself must not be perfectly collinear. Only SaberRig MCH bones are
    edited; source/game bones and skinning remain untouched. The ankle endpoint
    stays at the semantic source ankle, while the hidden knee is offset by only
    a few percent of a segment length. The resulting tiny increase in MCH chain
    length means a target at the original anatomical reach still resolves with
    a stable bend instead of entering the 180-degree singularity.
    """
    hip = mch_thigh.head.copy()
    source_knee = mch_thigh.tail.copy()
    ankle = mch_shin.tail.copy()
    straightness = _straightness_from_points(hip, source_knee, ankle)

    result = {
        "active": False,
        "straightness": float(straightness),
        "offset": 0.0,
        "angle": 0.0,
        "direction": "SOURCE",
    }
    if straightness < SR_LEG_PREBEND_TRIGGER_DOT:
        try:
            result["angle"] = float((source_knee - hip).angle(ankle - source_knee))
        except Exception:
            pass
        return result

    line = ankle - hip
    if line.length < 1.0e-8:
        return result
    line_n = line.normalized()
    projection = hip + line_n * (source_knee - hip).dot(line_n)

    bend_dir = pole_pos - projection
    bend_dir = bend_dir - line_n * bend_dir.dot(line_n)
    if bend_dir.length < 1.0e-8:
        bend_dir = source_knee - projection
    if bend_dir.length < 1.0e-8:
        # The pole builder already contains semantic fallbacks, but retain one
        # final deterministic axis so a malformed rig never produces NaNs.
        trial = Vector((0.0, -1.0, 0.0))
        bend_dir = trial - line_n * trial.dot(line_n)
    if bend_dir.length < 1.0e-8:
        trial = Vector((1.0, 0.0, 0.0))
        bend_dir = trial - line_n * trial.dot(line_n)
    if bend_dir.length < 1.0e-8:
        return result
    bend_dir.normalize()

    l1 = max((source_knee - hip).length, 1.0e-6)
    l2 = max((ankle - source_knee).length, 1.0e-6)
    reach = l1 + l2
    desired_offset = min(l1, l2) * math.tan(SR_LEG_PREBEND_ANGLE * 0.5)
    desired_offset = min(desired_offset, reach * SR_LEG_PREBEND_MAX_OFFSET_FACTOR)

    existing = source_knee - projection
    existing_along_pole = existing.dot(bend_dir)
    offset = max(desired_offset, existing_along_pole)
    knee = projection + bend_dir * offset

    mch_thigh.tail = knee
    mch_shin.head = knee
    mch_shin.tail = ankle
    try:
        mch_shin.use_connect = True
    except Exception:
        pass

    try:
        bend_angle = (knee - hip).angle(ankle - knee)
    except Exception:
        bend_angle = SR_LEG_PREBEND_ANGLE
    result.update({
        "active": True,
        "offset": float(offset),
        "angle": float(bend_angle),
        "direction": "KNEE_POLE",
        "mch_reach": float((knee - hip).length + (ankle - knee).length),
    })
    return result

def _leg_pole_position_from_pose(arm_obj, src, existing_pole=None):
    hip, knee, ankle = _leg_pose_joint_points(arm_obj, src)
    return _leg_pole_position_from_points(arm_obj, src, hip, knee, ankle, existing_pole=existing_pole, pose=True)

def _set_leg_mode_raw(arm_obj, side, use_ik):
    suffix = side.upper()
    analysis = analyze_armature(arm_obj)
    src = _leg_sources_for_side(analysis, suffix)
    value_ik = 1.0 if use_ik else 0.0
    value_fk = 0.0 if use_ik else 1.0
    changed = 0
    for role in ("thigh", "shin", "foot"):
        pb = arm_obj.pose.bones.get(src.get(role)) if src.get(role) else None
        if not pb:
            continue
        fk = _find_named_constraint(pb, f"SR_LEGS_FK_DEF_{suffix}_{role}")
        ik = _find_named_constraint(pb, f"SR_LEGS_IK_DEF_{suffix}_{role}")
        if fk:
            fk.influence = value_fk
            changed += 1
        if ik:
            ik.influence = value_ik
            changed += 1
    arm_obj[f"saberrig_leg_mode_{suffix}"] = "IK" if use_ik else "FK"
    return changed

def _leg_ik_constraint(arm_obj, side):
    suffix = side.upper()
    names = _leg_ik_names(suffix)
    shin_pb = arm_obj.pose.bones.get(names["mch_shin"])
    return _find_named_constraint(shin_pb, f"SR_LEGS_IK_SOLVER_{suffix}") if shin_pb else None

def _calibrate_leg_pole_angle(context, arm_obj, side, reference_thigh=None, reference_shin=None):
    suffix = side.upper()
    names = _leg_ik_names(suffix)
    ik = _leg_ik_constraint(arm_obj, suffix)
    ik_thigh = arm_obj.pose.bones.get(names["mch_thigh"])
    ik_shin = arm_obj.pose.bones.get(names["mch_shin"])
    if not ik or not ik_thigh or not ik_shin or not hasattr(ik, "pole_angle"):
        return 0.0, math.pi
    analysis = analyze_armature(arm_obj)
    src = _leg_sources_for_side(analysis, suffix)
    if reference_thigh is None:
        pb = arm_obj.pose.bones.get(src.get("thigh")); reference_thigh = pb.matrix.copy() if pb else None
    if reference_shin is None:
        pb = arm_obj.pose.bones.get(src.get("shin")); reference_shin = pb.matrix.copy() if pb else None
    if reference_thigh is None or reference_shin is None:
        return float(ik.pole_angle), math.pi

    def evaluate(angle):
        ik.pole_angle = _wrap_pi(angle)
        _view_layer_update(context)
        return 0.5 * (
            _pose_rotation_error(reference_thigh, ik_thigh.matrix) +
            _pose_rotation_error(reference_shin, ik_shin.matrix)
        )

    best_angle, best_error = float(getattr(ik, "pole_angle", 0.0)), math.pi
    coarse = math.radians(15.0)
    for i in range(25):
        angle = -math.pi + i * coarse
        err = evaluate(angle)
        if err < best_error:
            best_angle, best_error = angle, err
    for step_deg, radius in ((3.0, 5), (0.5, 6), (0.1, 5)):
        step = math.radians(step_deg); center = best_angle
        for offset in range(-radius, radius + 1):
            angle = center + offset * step
            err = evaluate(angle)
            if err < best_error:
                best_angle, best_error = angle, err
    ik.pole_angle = _wrap_pi(best_angle)
    _view_layer_update(context)
    arm_obj[f"saberrig_leg_pole_angle_{suffix}"] = float(ik.pole_angle)
    arm_obj[f"saberrig_leg_pole_error_{suffix}"] = float(best_error)
    return float(ik.pole_angle), float(best_error)

def _snap_leg_fk_to_ik(context, arm_obj, side, calibrate=False):
    suffix = side.upper()
    analysis = analyze_armature(arm_obj)
    src = _leg_sources_for_side(analysis, suffix)
    names = _leg_ik_names(suffix)
    master_ctrl = arm_obj.pose.bones.get(names.get("ctrl_master", ""))
    foot_ctrl = arm_obj.pose.bones.get(names["ctrl_foot"])
    pole_ctrl = arm_obj.pose.bones.get(names["ctrl_pole"])
    source_thigh = arm_obj.pose.bones.get(src.get("thigh"))
    source_shin = arm_obj.pose.bones.get(src.get("shin"))
    source_foot = arm_obj.pose.bones.get(src.get("foot"))
    if not all((master_ctrl, foot_ctrl, pole_ctrl, source_thigh, source_shin, source_foot)):
        raise RuntimeError(f"{suffix}: FK→IK leg matching controls are incomplete")

    _view_layer_update(context)
    ref_thigh = source_thigh.matrix.copy(); ref_shin = source_shin.matrix.copy(); ref_foot = source_foot.matrix.copy()
    existing_pole = pole_ctrl.matrix.translation.copy()

    # A reverse-foot hierarchy can retain heel/ball/toe rotations from the last
    # IK pose. Neutralize those pivots before matching FK so the destination
    # ankle is solved from a clean rest hierarchy rather than compensating three
    # stale parent rotations in FootIK's local matrix.
    _reset_foot_roll_controls(arm_obj, suffix)
    _view_layer_update(context)

    # Move the heel master by the ankle positional delta. This keeps the child
    # FootIK control near its rest offset instead of baking large translations
    # into the ankle pivot. Then match the ankle orientation to the source foot.
    delta = ref_foot.translation - foot_ctrl.matrix.translation
    _set_pose_bone_location(master_ctrl, master_ctrl.matrix.translation + delta)
    _view_layer_update(context)
    # Foot Pivot is rotation-oriented. Preserve its inherited ankle position and
    # match only the evaluated source-foot orientation; otherwise a full matrix
    # assignment can bake a child translation offset into the heel-master rig.
    desired_foot = ref_foot.copy()
    desired_foot.translation = foot_ctrl.matrix.translation.copy()
    _set_pose_bone_matrix(foot_ctrl, desired_foot)
    _view_layer_update(context)
    pole_pos, straightness = _leg_pole_position_from_pose(arm_obj, src, existing_pole=existing_pole)
    _set_pose_bone_location(pole_ctrl, pole_pos)
    _view_layer_update(context)
    arm_obj[f"saberrig_leg_straightness_{suffix}"] = float(straightness)

    if calibrate or f"saberrig_leg_pole_angle_{suffix}" not in arm_obj:
        angle, error = _calibrate_leg_pole_angle(context, arm_obj, suffix, reference_thigh=ref_thigh, reference_shin=ref_shin)
    else:
        ik = _leg_ik_constraint(arm_obj, suffix)
        angle = float(arm_obj.get(f"saberrig_leg_pole_angle_{suffix}", 0.0))
        if ik and hasattr(ik, "pole_angle"):
            ik.pole_angle = angle; _view_layer_update(context)
        ik_thigh = arm_obj.pose.bones.get(names["mch_thigh"])
        ik_shin = arm_obj.pose.bones.get(names["mch_shin"])
        error = 0.5 * (_pose_rotation_error(ref_thigh, ik_thigh.matrix) + _pose_rotation_error(ref_shin, ik_shin.matrix)) if ik_thigh and ik_shin else math.pi
        arm_obj[f"saberrig_leg_pole_error_{suffix}"] = float(error)
    return {"pole_angle": angle, "error": error, "straightness": straightness, "singular": straightness >= SR_POLE_SINGULAR_DOT}

def _snap_leg_ik_to_fk(context, arm_obj, side):
    suffix = side.upper()
    analysis = analyze_armature(arm_obj)
    src = _leg_sources_for_side(analysis, suffix)
    names = _leg_fk_names(suffix)
    controls = {
        "thigh": arm_obj.pose.bones.get(names["ctrl_thigh"]),
        "shin": arm_obj.pose.bones.get(names["ctrl_shin"]),
        "foot": arm_obj.pose.bones.get(names["ctrl_foot"]),
    }
    source = {role: arm_obj.pose.bones.get(src.get(role)) for role in ("thigh", "shin", "foot")}
    if not all(controls.values()) or not all(source.values()):
        raise RuntimeError(f"{suffix}: IK→FK leg matching controls are incomplete")
    _view_layer_update(context)
    targets = {role: source[role].matrix.copy() for role in source}
    for role in ("thigh", "shin", "foot"):
        _set_pose_bone_matrix(controls[role], targets[role]); _view_layer_update(context)
    return True

def _add_leg_reach_limits(arm_obj, side, target_pb, anchor_name, reach_distance,
                          max_factor=SR_DEFAULT_LEG_REACH_FACTOR,
                          min_factor=SR_DEFAULT_LEG_MIN_REACH_FACTOR,
                          preferred_factor=SR_DEFAULT_LEG_PREFERRED_REACH_FACTOR):
    """Keep the *effective* ankle target inside a safe annular workspace.

    The animator-facing Foot Master remains completely free. Only this hidden
    mechanism target is clamped, so repeated G-moves never stick at either
    boundary. MAX prevents stretch; MIN prevents the ankle target from crossing
    through the hip and forcing a folded/inverted two-bone solution.
    """
    suffix = side.upper()
    cmax = target_pb.constraints.new('LIMIT_DISTANCE')
    cmax.name = f"SR_LEGS_IK_REACH_{suffix}"
    cmax.target = arm_obj
    if hasattr(cmax, "subtarget"): cmax.subtarget = anchor_name
    if hasattr(cmax, "head_tail"): cmax.head_tail = 0.0
    cmax.distance = max(float(reach_distance) * float(max_factor), 1.0e-5)
    if hasattr(cmax, "limit_mode"): cmax.limit_mode = 'LIMITDIST_INSIDE'
    if hasattr(cmax, "owner_space"): cmax.owner_space = 'POSE'
    if hasattr(cmax, "target_space"): cmax.target_space = 'POSE'
    if hasattr(cmax, "use_transform_limit"): cmax.use_transform_limit = False

    cmin = target_pb.constraints.new('LIMIT_DISTANCE')
    cmin.name = f"SR_LEGS_IK_MIN_REACH_{suffix}"
    cmin.target = arm_obj
    if hasattr(cmin, "subtarget"): cmin.subtarget = anchor_name
    if hasattr(cmin, "head_tail"): cmin.head_tail = 0.0
    # Never let an accidentally bad profile invert the annulus.
    safe_min_factor = min(max(float(min_factor), 0.0), max(float(max_factor) - 0.05, 0.0))
    cmin.distance = max(float(reach_distance) * safe_min_factor, 1.0e-5)
    if hasattr(cmin, "limit_mode"): cmin.limit_mode = 'LIMITDIST_OUTSIDE'
    if hasattr(cmin, "owner_space"): cmin.owner_space = 'POSE'
    if hasattr(cmin, "target_space"): cmin.target_space = 'POSE'
    if hasattr(cmin, "use_transform_limit"): cmin.use_transform_limit = False

    # Optional preferred bend: Smooth mode keeps the ankle a fraction inside
    # full reach, which gives the pole a stable knee plane near 180 degrees.
    cpref = target_pb.constraints.new('LIMIT_DISTANCE')
    cpref.name = f"SR_LEGS_IK_PREFERRED_BEND_{suffix}"
    cpref.target = arm_obj
    if hasattr(cpref, "subtarget"): cpref.subtarget = anchor_name
    if hasattr(cpref, "head_tail"): cpref.head_tail = 0.0
    safe_pref = min(max(float(preferred_factor), safe_min_factor + 0.05), float(max_factor))
    cpref.distance = max(float(reach_distance) * safe_pref, 1.0e-5)
    if hasattr(cpref, "limit_mode"): cpref.limit_mode = 'LIMITDIST_INSIDE'
    if hasattr(cpref, "owner_space"): cpref.owner_space = 'POSE'
    if hasattr(cpref, "target_space"): cpref.target_space = 'POSE'
    if hasattr(cpref, "use_transform_limit"): cpref.use_transform_limit = False
    bend_mode = str(arm_obj.get("saberrig_leg_bend_mode", "SMOOTH")).upper()
    cpref.influence = 1.0 if bend_mode != "RIGID" else 0.0

    arm_obj[f"saberrig_leg_reach_{suffix}"] = float(cmax.distance)
    arm_obj[f"saberrig_leg_reach_factor_{suffix}"] = float(max_factor)
    arm_obj[f"saberrig_leg_min_reach_{suffix}"] = float(cmin.distance)
    arm_obj[f"saberrig_leg_min_reach_factor_{suffix}"] = float(safe_min_factor)
    arm_obj[f"saberrig_leg_preferred_reach_{suffix}"] = float(cpref.distance)
    arm_obj[f"saberrig_leg_preferred_reach_factor_{suffix}"] = float(safe_pref)
    return cmax, cmin, cpref

def _apply_leg_preferred_bend(arm_obj, side, smooth=True):
    suffix = side.upper()
    names = _leg_ik_names(suffix)
    target_pb = arm_obj.pose.bones.get(names["mch_target"])
    if not target_pb:
        return False
    c = target_pb.constraints.get(f"SR_LEGS_IK_PREFERRED_BEND_{suffix}")
    if not c:
        return False
    c.influence = 1.0 if smooth else 0.0
    return True

def _apply_all_leg_preferred_bend(arm_obj, smooth=True):
    changed = 0
    for side in ("L", "R"):
        changed += 1 if _apply_leg_preferred_bend(arm_obj, side, smooth=smooth) else 0
    arm_obj["saberrig_leg_bend_mode"] = "SMOOTH" if smooth else "RIGID"
    return changed

def _hip_limit_diagnostics(arm_obj, side):
    """Map semantic flexion/abduction to the generated thigh's actual local axes.

    Blender edit bones always use local Y along the bone, but imported game rigs
    may have arbitrary X/Z roll. We therefore infer which lateral axis produces
    forward/back flexion and which produces ab/adduction from the character's
    foot direction and the rest leg direction.
    """
    suffix = side.upper()
    analysis = analyze_armature(arm_obj)
    src = _leg_sources_for_side(analysis, suffix)
    names = _leg_ik_names(suffix)
    thigh_data = arm_obj.data.bones.get(names["mch_thigh"])
    if not thigh_data:
        raise RuntimeError(f"{suffix}: hip safety cannot resolve IK thigh")

    hip, knee, ankle = _leg_rest_joint_points(arm_obj, src)
    leg_dir = knee - hip
    if leg_dir.length < 1.0e-8:
        leg_dir = ankle - hip
    if leg_dir.length < 1.0e-8:
        leg_dir = thigh_data.vector.copy()
    if leg_dir.length < 1.0e-8:
        raise RuntimeError(f"{suffix}: hip safety found a zero-length thigh")
    leg_dir.normalize()

    up = hip - ankle
    if up.length < 1.0e-8:
        up = -leg_dir
    up.normalize()

    forward = _leg_forward_vector(arm_obj, src, pose=False)
    forward = forward - up * forward.dot(up)
    if forward.length < 1.0e-8:
        # Knee pole is already semantically front/back aware and makes a robust
        # fallback for rigs whose foot/toe chain is degenerate.
        pole_pos = _leg_pole_position(arm_obj, src)
        forward = pole_pos - knee
        forward = forward - up * forward.dot(up)
    if forward.length < 1.0e-8:
        forward = Vector((0.0, -1.0, 0.0))
        forward = forward - up * forward.dot(up)
    if forward.length < 1.0e-8:
        forward = Vector((1.0, 0.0, 0.0))
    forward.normalize()

    # forward x up gives the character's leftward anatomical side in a
    # conventional right-handed frame. Flip it for the right leg to get outward.
    leftward = forward.cross(up)
    if leftward.length < 1.0e-8:
        leftward = thigh_data.x_axis.copy()
    leftward.normalize()
    outward = leftward if suffix == "L" else -leftward

    axes = _bone_local_axes(thigh_data)
    lateral = {axis: vec for axis, vec in axes.items() if axis in {"X", "Z"}}
    if not lateral:
        raise RuntimeError(f"{suffix}: hip safety cannot read thigh lateral axes")

    # Rotation around the anatomical side axis is flexion/extension.
    flex_axis = max(lateral, key=lambda a: abs(lateral[a].dot(leftward)))
    remaining = [a for a in lateral if a != flex_axis]
    abd_axis = remaining[0] if remaining else flex_axis
    flex_vec = lateral[flex_axis]
    abd_vec = lateral[abd_axis]
    flex_conf = abs(flex_vec.dot(leftward))
    abd_conf = abs(abd_vec.dot(forward))

    flex_motion = flex_vec.cross(leg_dir)
    flex_sign = 1.0 if flex_motion.length < 1.0e-8 or flex_motion.normalized().dot(forward) >= 0.0 else -1.0
    abd_motion = abd_vec.cross(leg_dir)
    abd_sign = 1.0 if abd_motion.length < 1.0e-8 or abd_motion.normalized().dot(outward) >= 0.0 else -1.0
    return {
        "flex_axis": flex_axis,
        "abd_axis": abd_axis,
        "flex_sign": float(flex_sign),
        "abd_sign": float(abd_sign),
        "flex_confidence": float(flex_conf),
        "abd_confidence": float(abd_conf),
    }

def _knee_hinge_diagnostics(arm_obj, side):
    suffix = side.upper()
    analysis = analyze_armature(arm_obj)
    src = _leg_sources_for_side(analysis, suffix)
    names = _leg_ik_names(suffix)
    shin_data = arm_obj.data.bones.get(names["mch_shin"])
    pole_pb = arm_obj.pose.bones.get(names["ctrl_pole"])
    if not shin_data or not pole_pb:
        raise RuntimeError(f"{suffix}: knee safety cannot resolve IK shin/pole")
    hip, knee, ankle = _leg_rest_joint_points(arm_obj, src)
    chain = ankle - knee
    if chain.length < 1.0e-8:
        chain = shin_data.vector.copy()
    if chain.length < 1.0e-8:
        raise RuntimeError(f"{suffix}: knee safety found a zero-length shin")
    chain.normalize()
    pole_vector = pole_pb.matrix.translation - knee
    pole_vector = pole_vector - chain * pole_vector.dot(chain)
    if pole_vector.length < 1.0e-8:
        fallback, _ = _leg_pole_position_from_pose(arm_obj, src, existing_pole=None)
        pole_vector = fallback - knee
        pole_vector = pole_vector - chain * pole_vector.dot(chain)
    if pole_vector.length < 1.0e-8:
        pole_vector = _leg_forward_vector(arm_obj, src, pose=False)
        pole_vector = pole_vector - chain * pole_vector.dot(chain)
    if pole_vector.length < 1.0e-8:
        pole_vector = Vector((0.0, -1.0, 0.0))
    pole_vector.normalize()
    plane_normal = chain.cross(pole_vector)
    if plane_normal.length < 1.0e-8: plane_normal = pole_vector.cross(chain)
    plane_normal.normalize()
    axes = _bone_local_axes(shin_data)
    scores = {axis: abs(vec.dot(plane_normal)) for axis, vec in axes.items()}
    if "Y" in scores: scores["Y"] *= 0.25
    if not scores: raise RuntimeError(f"{suffix}: knee safety cannot read local axes")
    lateral_scores = {axis: score for axis, score in scores.items() if axis in {"X", "Z"}}
    hinge_axis = max(lateral_scores or scores, key=(lateral_scores or scores).get)
    axis_vector = axes[hinge_axis]
    confidence = abs(axis_vector.dot(plane_normal))
    positive_motion = axis_vector.cross(chain)
    flex_sign = 1.0 if positive_motion.length < 1.0e-8 or positive_motion.normalized().dot(pole_vector) >= 0.0 else -1.0
    straightness = float(arm_obj.get(f"saberrig_leg_straightness_{suffix}", _straightness_from_points(hip, knee, ankle)))
    singular = bool(arm_obj.get(f"saberrig_leg_pole_singular_{suffix}", straightness >= SR_POLE_SINGULAR_DOT))
    if singular:
        mode = "POLE_GUIDED"
    else:
        mode = "HARD" if confidence >= SR_KNEE_HARD_HINGE_CONFIDENCE else ("SOFT" if confidence >= SR_KNEE_SOFT_HINGE_CONFIDENCE else "FALLBACK")
    return {"axis": hinge_axis, "confidence": float(confidence), "flex_sign": float(flex_sign), "mode": mode, "straightness": float(straightness), "singular": singular}

def _ankle_limit_diagnostics(arm_obj, side):
    """Resolve the ankle flex/bank axes from the actual imported bone roll."""
    suffix = side.upper()
    analysis = analyze_armature(arm_obj)
    src = _leg_sources_for_side(analysis, suffix)
    names = _leg_ik_names(suffix)
    foot_data = arm_obj.data.bones.get(names["mch_foot"])
    if not foot_data:
        raise RuntimeError(f"{suffix}: ankle safety cannot resolve IK foot")

    hip, knee, ankle = _leg_rest_joint_points(arm_obj, src)
    shin_dir = ankle - knee
    if shin_dir.length < 1.0e-8:
        shin_data = arm_obj.data.bones.get(names["mch_shin"])
        shin_dir = shin_data.vector.copy() if shin_data else Vector((0.0, 0.0, -1.0))
    shin_dir.normalize()

    foot_dir = _leg_forward_vector(arm_obj, src, pose=False)
    if foot_dir.length < 1.0e-8:
        foot_dir = foot_data.vector.copy()
    if foot_dir.length < 1.0e-8:
        foot_dir = Vector((0.0, -1.0, 0.0))
    foot_dir.normalize()

    # Sagittal ankle flexion rotates around the normal of the shin/foot plane.
    flex_normal = shin_dir.cross(foot_dir)
    if flex_normal.length < 1.0e-8:
        # Degenerate source: use the knee-pole direction to reconstruct the plane.
        pole_pb = arm_obj.pose.bones.get(names.get("ctrl_pole", ""))
        pole_vec = (pole_pb.matrix.translation - knee) if pole_pb else Vector((0.0, -1.0, 0.0))
        flex_normal = shin_dir.cross(pole_vec)
    if flex_normal.length < 1.0e-8:
        flex_normal = foot_data.x_axis.copy()
    flex_normal.normalize()

    axes = _bone_local_axes(foot_data)
    lateral = {axis: vec for axis, vec in axes.items() if axis in {"X", "Z"}}
    if not lateral:
        raise RuntimeError(f"{suffix}: ankle safety cannot read foot local axes")
    flex_axis = max(lateral, key=lambda a: abs(lateral[a].dot(flex_normal)))
    remaining = [a for a in lateral if a != flex_axis]
    bank_axis = remaining[0] if remaining else flex_axis
    confidence = abs(lateral[flex_axis].dot(flex_normal))
    return {"flex_axis": flex_axis, "bank_axis": bank_axis, "confidence": float(confidence)}

def _ensure_ankle_limit_constraint(arm_obj, side, enabled=True):
    suffix = side.upper(); names = _leg_ik_names(suffix)
    foot_pb = arm_obj.pose.bones.get(names["mch_foot"])
    if not foot_pb: return None
    cname = f"SR_LEGS_IK_SAFE_ANKLE_{suffix}"
    c = foot_pb.constraints.get(cname)
    if c is None:
        c = foot_pb.constraints.new('LIMIT_ROTATION'); c.name = cname
    c.influence = 1.0 if enabled else 0.0
    if hasattr(c, "owner_space"): c.owner_space = 'LOCAL'
    if hasattr(c, "use_transform_limit"): c.use_transform_limit = False

    # Reset every axis first; then assign semantic flex/bank/twist ranges.
    for axis in "xyz":
        if hasattr(c, f"use_limit_{axis}"): setattr(c, f"use_limit_{axis}", bool(enabled))
    if not enabled:
        return c

    diag = _ankle_limit_diagnostics(arm_obj, suffix)
    flex_axis = diag["flex_axis"].lower()
    bank_axis = diag["bank_axis"].lower()
    ranges = {
        "x": (-SR_ANKLE_SAFE_BANK, SR_ANKLE_SAFE_BANK),
        "y": (-SR_ANKLE_SAFE_TWIST, SR_ANKLE_SAFE_TWIST),
        "z": (-SR_ANKLE_SAFE_BANK, SR_ANKLE_SAFE_BANK),
    }
    ranges[flex_axis] = (-SR_ANKLE_SAFE_FLEX, SR_ANKLE_SAFE_FLEX)
    ranges[bank_axis] = (-SR_ANKLE_SAFE_BANK, SR_ANKLE_SAFE_BANK)
    for axis, (mn, mx) in ranges.items():
        setattr(c, f"min_{axis}", mn); setattr(c, f"max_{axis}", mx)

    arm_obj[f"saberrig_ankle_flex_axis_{suffix}"] = diag["flex_axis"]
    arm_obj[f"saberrig_ankle_bank_axis_{suffix}"] = diag["bank_axis"]
    arm_obj[f"saberrig_ankle_flex_confidence_{suffix}"] = float(diag["confidence"])
    return c

def _ensure_foot_control_limit_constraint(arm_obj, side, enabled=True):
    """Mirror ankle-safe rotation limits onto the animator-facing Foot Pivot.

    The MCH foot keeps its post-solve safety constraint, while this second
    constraint prevents the visible Foot Pivot from accumulating rotations the
    mechanism would immediately clamp. Translation remains owned by Foot Master.
    """
    suffix = side.upper(); names = _leg_ik_names(suffix)
    ctrl_pb = arm_obj.pose.bones.get(names["ctrl_foot"])
    if not ctrl_pb:
        return None
    cname = f"SR_LEGS_CTRL_SAFE_FOOT_{suffix}"
    c = ctrl_pb.constraints.get(cname)
    if c is None:
        c = ctrl_pb.constraints.new('LIMIT_ROTATION'); c.name = cname
    c.influence = 1.0 if enabled else 0.0
    if hasattr(c, "owner_space"): c.owner_space = 'LOCAL'
    # Rotation limits are useful directly on the animator control: unlike the
    # previous distance-clamp experiment, respecting them during R transforms
    # does not make positional manipulation sticky.
    if hasattr(c, "use_transform_limit"): c.use_transform_limit = bool(enabled)
    for axis in "xyz":
        if hasattr(c, f"use_limit_{axis}"): setattr(c, f"use_limit_{axis}", bool(enabled))
    if not enabled:
        arm_obj[f"saberrig_foot_ctrl_limit_{suffix}"] = False
        return c

    diag = _ankle_limit_diagnostics(arm_obj, suffix)
    flex_axis = diag["flex_axis"].lower(); bank_axis = diag["bank_axis"].lower()
    ranges = {
        "x": (-SR_ANKLE_SAFE_BANK, SR_ANKLE_SAFE_BANK),
        "y": (-SR_ANKLE_SAFE_TWIST, SR_ANKLE_SAFE_TWIST),
        "z": (-SR_ANKLE_SAFE_BANK, SR_ANKLE_SAFE_BANK),
    }
    ranges[flex_axis] = (-SR_ANKLE_SAFE_FLEX, SR_ANKLE_SAFE_FLEX)
    ranges[bank_axis] = (-SR_ANKLE_SAFE_BANK, SR_ANKLE_SAFE_BANK)
    for axis, (mn, mx) in ranges.items():
        setattr(c, f"min_{axis}", mn); setattr(c, f"max_{axis}", mx)
    arm_obj[f"saberrig_foot_ctrl_limit_{suffix}"] = True
    return c

def _prebent_knee_hinge_sign(arm_obj, side, hinge_axis):
    """Resolve the real bend sign from SaberRig's pre-bent mechanism chain.

    A non-collinear hidden solver makes the knee hinge sign measurable rather
    than guessed. The sign is chosen so bending follows the current Knee Pole.
    """
    suffix = side.upper(); names = _leg_ik_names(suffix)
    shin_data = arm_obj.data.bones.get(names["mch_shin"])
    pole_pb = arm_obj.pose.bones.get(names["ctrl_pole"])
    if not shin_data or not pole_pb:
        return 1.0, 0.0
    axes = _bone_local_axes(shin_data)
    axis_vec = axes.get(str(hinge_axis).upper())
    if axis_vec is None:
        return 1.0, 0.0
    knee = shin_data.head_local.copy(); ankle = shin_data.tail_local.copy()
    chain = ankle - knee
    if chain.length < 1.0e-8:
        return 1.0, 0.0
    chain.normalize()
    pole_vec = pole_pb.matrix.translation - knee
    pole_vec = pole_vec - chain * pole_vec.dot(chain)
    if pole_vec.length < 1.0e-8:
        return 1.0, 0.0
    pole_vec.normalize()
    motion = axis_vec.cross(chain)
    if motion.length < 1.0e-8:
        return 1.0, 0.0
    motion.normalize()
    d = max(-1.0, min(1.0, motion.dot(pole_vec)))
    return (1.0 if d >= 0.0 else -1.0), abs(float(d))

def _apply_leg_joint_limits(arm_obj, side, enabled=True):
    suffix = side.upper(); names = _leg_ik_names(suffix)
    thigh_pb = arm_obj.pose.bones.get(names["mch_thigh"]); shin_pb = arm_obj.pose.bones.get(names["mch_shin"])
    if not thigh_pb or not shin_pb: return None
    _clear_ik_axis_safety(thigh_pb); _clear_ik_axis_safety(shin_pb)
    _ensure_ankle_limit_constraint(arm_obj, suffix, enabled=enabled)
    _ensure_foot_control_limit_constraint(arm_obj, suffix, enabled=enabled)
    if not enabled:
        arm_obj[f"saberrig_knee_hinge_mode_{suffix}"] = "OFF"
        return {"mode": "OFF"}

    hip_diag = _hip_limit_diagnostics(arm_obj, suffix)
    flex_axis = hip_diag["flex_axis"].lower()
    abd_axis = hip_diag["abd_axis"].lower()
    flex_sign = hip_diag["flex_sign"]
    abd_sign = hip_diag["abd_sign"]

    # Asymmetric semantic hip cone: large forward flexion, modest extension;
    # generous outward abduction, smaller inward adduction. This prevents the
    # solver from folding the ankle through the pelvis while remaining useful
    # for stylized animation.
    flex_min, flex_max = ((-SR_HIP_SAFE_EXTENSION, SR_HIP_SAFE_FLEXION)
                          if flex_sign >= 0.0 else
                          (-SR_HIP_SAFE_FLEXION, SR_HIP_SAFE_EXTENSION))
    abd_min, abd_max = ((-SR_HIP_SAFE_ADDUCTION, SR_HIP_SAFE_ABDUCTION)
                        if abd_sign >= 0.0 else
                        (-SR_HIP_SAFE_ABDUCTION, SR_HIP_SAFE_ADDUCTION))
    _set_ik_axis_range(thigh_pb, flex_axis, flex_min, flex_max, enabled=True)
    _set_ik_axis_range(thigh_pb, abd_axis, abd_min, abd_max, enabled=True)
    _set_ik_axis_range(thigh_pb, "y", -SR_HIP_TWIST_LIMIT, SR_HIP_TWIST_LIMIT, enabled=True)
    _set_ik_axis_stiffness(thigh_pb, "y", SR_HIP_TWIST_STIFFNESS)

    arm_obj[f"saberrig_hip_flex_axis_{suffix}"] = hip_diag["flex_axis"]
    arm_obj[f"saberrig_hip_abd_axis_{suffix}"] = hip_diag["abd_axis"]
    arm_obj[f"saberrig_hip_flex_sign_{suffix}"] = float(flex_sign)
    arm_obj[f"saberrig_hip_abd_sign_{suffix}"] = float(abd_sign)
    arm_obj[f"saberrig_hip_flex_confidence_{suffix}"] = float(hip_diag["flex_confidence"])
    arm_obj[f"saberrig_hip_abd_confidence_{suffix}"] = float(hip_diag["abd_confidence"])

    diag = _knee_hinge_diagnostics(arm_obj, suffix)
    hinge = diag["axis"].lower(); sign = diag["flex_sign"]; mode = diag["mode"]
    if hinge != "y":
        _pb_set_if(shin_pb, "lock_ik_y", True)
        _set_ik_axis_stiffness(shin_pb, "y", SR_KNEE_TWIST_STIFFNESS)
    flex, hyper = SR_KNEE_SAFE_FLEXION, SR_KNEE_SAFE_HYPEREXTENSION
    lateral = [a for a in ("x", "z") if a != hinge]
    if mode == "POLE_GUIDED":
        # Source-straight legs remain pole-driven even after MCH pre-bend.
        # Do NOT force a one-way hinge here: imported bone-roll conventions can
        # make the inferred sign/axis disagree with Blender's live IK solution
        # and freeze the knee. The real pre-bent MCH chain + pole establishes
        # the bend hemisphere; SAFE limits only suppress twist/lateral wobble.
        _set_ik_axis_range(shin_pb, hinge, -flex, flex, enabled=True)
        for axis in lateral:
            _set_ik_axis_stiffness(shin_pb, axis, SR_KNEE_POLE_GUIDED_STIFFNESS)
            _set_ik_axis_range(shin_pb, axis, -SR_KNEE_POLE_GUIDED_SWING, SR_KNEE_POLE_GUIDED_SWING, enabled=True)
        mode = "POLE_GUIDED_SAFE"
        # Clear stale Smart Knee metadata when rebuilding over an existing rig.
        for key in (f"saberrig_knee_prebend_sign_{suffix}", f"saberrig_knee_prebend_sign_confidence_{suffix}"):
            if key in arm_obj:
                del arm_obj[key]
    elif mode == "HARD":
        minimum, maximum = ((-hyper, flex) if sign >= 0.0 else (-flex, hyper))
        _set_ik_axis_range(shin_pb, hinge, minimum, maximum, enabled=True)
        for axis in lateral:
            _pb_set_if(shin_pb, f"lock_ik_{axis}", True)
    elif mode == "SOFT":
        minimum, maximum = ((-hyper, flex) if sign >= 0.0 else (-flex, hyper))
        _set_ik_axis_range(shin_pb, hinge, minimum, maximum, enabled=True)
        for axis in lateral:
            _set_ik_axis_stiffness(shin_pb, axis, SR_KNEE_NONHINGE_STIFFNESS)
            _set_ik_axis_range(shin_pb, axis, math.radians(-25.0), math.radians(25.0), enabled=True)
    else:
        minimum, maximum = ((-hyper, flex) if sign >= 0.0 else (-flex, hyper))
        _set_ik_axis_range(shin_pb, hinge, minimum, maximum, enabled=True)
        for axis in lateral:
            _set_ik_axis_stiffness(shin_pb, axis, 0.65)
            _set_ik_axis_range(shin_pb, axis, math.radians(-40.0), math.radians(40.0), enabled=True)
    arm_obj[f"saberrig_knee_hinge_axis_{suffix}"] = diag["axis"]
    arm_obj[f"saberrig_knee_hinge_sign_{suffix}"] = float(sign)
    arm_obj[f"saberrig_knee_hinge_confidence_{suffix}"] = float(diag["confidence"])
    arm_obj[f"saberrig_knee_hinge_mode_{suffix}"] = mode
    arm_obj[f"saberrig_knee_flexion_deg_{suffix}"] = math.degrees(flex)
    arm_obj[f"saberrig_knee_hyper_deg_{suffix}"] = math.degrees(hyper)
    return diag

def _apply_all_leg_joint_limits(arm_obj, enabled=True):
    diagnostics = {}
    for side in ("L", "R"):
        try: diagnostics[side] = _apply_leg_joint_limits(arm_obj, side, enabled=enabled)
        except Exception as exc: diagnostics[side] = {"mode": "ERROR", "error": str(exc)}
    arm_obj["saberrig_leg_joint_limits_mode"] = "SAFE" if enabled else "OFF"
    return diagnostics

def _build_leg_ik_side(context, arm_obj, analysis, side):
    suffix = side.upper(); src = _leg_sources_for_side(analysis, suffix)
    if not all((src["thigh"], src["shin"], src["foot"])):
        missing = [r for r in ("thigh", "shin", "foot") if not src[r]]
        raise RuntimeError(f"{suffix}: missing semantic leg bones: {', '.join(missing)}")
    fk_names = _leg_fk_names(suffix)
    if not all(arm_obj.data.bones.get(fk_names[k]) for k in ("mch_thigh", "mch_shin", "mch_foot")):
        raise RuntimeError(f"{suffix}: build SaberRig Legs FK before adding IK")
    controls_collection = _find_bone_collection(arm_obj.data, SR_COLLECTION_CONTROLS)
    mechanism_collection = _find_bone_collection(arm_obj.data, SR_COLLECTION_MECHANISM)
    if not controls_collection or not mechanism_collection:
        raise RuntimeError("SaberRig foundation collections are missing")

    names = _leg_ik_names(suffix)
    pole_pos = _leg_pole_position(arm_obj, src)
    master_head, master_tail = _leg_master_geometry(arm_obj, src)
    foot_roll = _leg_foot_roll_geometry(arm_obj, src)
    rest_hip, _knee, _ankle = _leg_rest_joint_points(arm_obj, src)
    reach_distance = _leg_reach_distance(arm_obj, src)
    thigh_data = arm_obj.data.bones.get(src["thigh"])
    thigh_parent = thigh_data.parent.name if thigh_data and thigh_data.parent else None

    _activate_armature(context, arm_obj, mode='EDIT')
    ebones = arm_obj.data.edit_bones
    mch_thigh = _clone_edit_bone(ebones, names["mch_thigh"], src["thigh"], thigh_parent)
    mch_shin = _clone_edit_bone(ebones, names["mch_shin"], src["shin"], names["mch_thigh"])
    mch_foot = _clone_edit_bone(ebones, names["mch_foot"], src["foot"], names["mch_shin"])
    # Build a *semantic* two-bone solver chain. Game rigs such as Endfield may
    # insert several deform helpers between thigh→knee and knee→ankle; cloning
    # their original lengths would make Blender solve those offsets rather than
    # the anatomical joints. Use the semantic joint positions as MCH endpoints.
    src_shin_edit = ebones.get(src["shin"])
    src_foot_edit = ebones.get(src["foot"])
    prebend = {"active": False, "angle": 0.0, "offset": 0.0, "direction": "SOURCE"}
    if src_shin_edit and src_foot_edit:
        mch_thigh.tail = src_shin_edit.head.copy()
        mch_shin.head = src_shin_edit.head.copy()
        mch_shin.tail = src_foot_edit.head.copy()
        # Never feed Blender a perfectly collinear two-bone solver
        # chain when the imported source leg is straight. Give only the hidden
        # MCH knee a tiny pole-facing bias; source/game bones stay untouched.
        prebend = _apply_leg_prebend_edit_geometry(arm_obj, src, mch_thigh, mch_shin, pole_pos)
    mch_reach = _create_reach_anchor_edit_bone(ebones, names["mch_reach"], rest_hip, mch_thigh, parent_name=thigh_parent)
    mch_target = _clone_edit_bone(ebones, names["mch_target"], src["foot"], None)
    ctrl_master = _create_master_edit_bone(ebones, names["ctrl_master"], master_head, master_tail)
    # Reverse-foot hierarchy: master → heel → toe-tip → ball → ankle. Rotating
    # a parent physically moves the ankle target around that semantic pivot, so
    # Foot Roll works without drivers and remains editable/keyframe-friendly.
    ctrl_heel = _create_foot_roll_pivot_edit_bone(
        ebones, names["ctrl_heel"], foot_roll["heel"], foot_roll["forward"], foot_roll["up"],
        foot_roll["display_len"], names["ctrl_master"]
    )
    ctrl_toe = _create_foot_roll_pivot_edit_bone(
        ebones, names["ctrl_toe"], foot_roll["toe"], foot_roll["forward"], foot_roll["up"],
        foot_roll["display_len"], names["ctrl_heel"]
    )
    ctrl_ball = _create_foot_roll_pivot_edit_bone(
        ebones, names["ctrl_ball"], foot_roll["ball"], foot_roll["forward"], foot_roll["up"],
        foot_roll["display_len"], names["ctrl_toe"]
    )
    ctrl_foot = _clone_edit_bone(ebones, names["ctrl_foot"], src["foot"], names["ctrl_ball"])
    # Smart pole space rotates around the semantic hip toward the effective
    # ankle target. The Knee Pole stays an animator control, but now carries a
    # stable parent space instead of being abandoned in global space.
    mch_pole_space = _create_knee_pole_space_edit_bone(
        ebones, names["mch_pole_space"], rest_hip, _ankle, pole_pos, parent_name=thigh_parent
    )
    ctrl_pole = _create_pole_edit_bone(ebones, names["ctrl_pole"], pole_pos, mch_shin)
    ctrl_pole.parent = mch_pole_space
    ctrl_pole.use_connect = False
    for b in (mch_thigh, mch_shin, mch_foot, mch_reach, mch_target, mch_pole_space): mechanism_collection.assign(b)
    for b in (ctrl_master, ctrl_heel, ctrl_ball, ctrl_toe, ctrl_foot, ctrl_pole): controls_collection.assign(b)
    bpy.ops.object.mode_set(mode='OBJECT')

    arm_obj[f"saberrig_leg_prebend_active_{suffix}"] = bool(prebend.get("active", False))
    arm_obj[f"saberrig_leg_prebend_angle_{suffix}"] = float(prebend.get("angle", 0.0))
    arm_obj[f"saberrig_leg_prebend_offset_{suffix}"] = float(prebend.get("offset", 0.0))
    arm_obj[f"saberrig_leg_prebend_direction_{suffix}"] = str(prebend.get("direction", "SOURCE"))
    if "mch_reach" in prebend:
        arm_obj[f"saberrig_leg_mch_reach_{suffix}"] = float(prebend["mch_reach"])

    generated = []
    for role, key in (("thigh", "mch_thigh"), ("shin", "mch_shin"), ("foot", "mch_foot")):
        _mark_generated_bone(arm_obj, names[key], "MCH", f"{role}.{suffix}", src[role], component=SR_LEG_IK_COMPONENT); generated.append(names[key])
    _mark_generated_bone(arm_obj, names["mch_reach"], "MCH", f"leg_reach_anchor.{suffix}", src["thigh"], component=SR_LEG_IK_COMPONENT); generated.append(names["mch_reach"])
    _mark_generated_bone(arm_obj, names["mch_target"], "MCH", f"effective_foot_target.{suffix}", src["foot"], component=SR_LEG_IK_COMPONENT); generated.append(names["mch_target"])
    _mark_generated_bone(arm_obj, names["mch_pole_space"], "MCH", f"knee_pole_space.{suffix}", src["thigh"], component=SR_LEG_IK_COMPONENT); generated.append(names["mch_pole_space"])
    _mark_generated_bone(arm_obj, names["ctrl_master"], "CTRL", f"foot_master_ik.{suffix}", src["foot"], component=SR_LEG_IK_COMPONENT)
    _mark_generated_bone(arm_obj, names["ctrl_heel"], "CTRL", f"heel_roll.{suffix}", src["foot"], component=SR_LEG_IK_COMPONENT)
    _mark_generated_bone(arm_obj, names["ctrl_ball"], "CTRL", f"ball_roll.{suffix}", src.get("toe") or src["foot"], component=SR_LEG_IK_COMPONENT)
    _mark_generated_bone(arm_obj, names["ctrl_toe"], "CTRL", f"toe_roll.{suffix}", src.get("toe") or src["foot"], component=SR_LEG_IK_COMPONENT)
    _mark_generated_bone(arm_obj, names["ctrl_foot"], "CTRL", f"foot_ik_pivot.{suffix}", src["foot"], component=SR_LEG_IK_COMPONENT)
    _mark_generated_bone(arm_obj, names["ctrl_pole"], "CTRL", f"knee_pole.{suffix}", src["shin"], component=SR_LEG_IK_COMPONENT)
    generated.extend((names["ctrl_master"], names["ctrl_heel"], names["ctrl_ball"], names["ctrl_toe"], names["ctrl_foot"], names["ctrl_pole"]))
    arm_obj[f"saberrig_foot_roll_mode_{suffix}"] = str(foot_roll["source_mode"])
    arm_obj[f"saberrig_foot_roll_foot_len_{suffix}"] = float(foot_roll["foot_len"])

    master_pb = arm_obj.pose.bones.get(names["ctrl_master"])
    heel_pb = arm_obj.pose.bones.get(names["ctrl_heel"])
    ball_pb = arm_obj.pose.bones.get(names["ctrl_ball"])
    toe_pb = arm_obj.pose.bones.get(names["ctrl_toe"])
    foot_pb = arm_obj.pose.bones.get(names["ctrl_foot"])
    pole_pb = arm_obj.pose.bones.get(names["ctrl_pole"])
    if master_pb:
        master_pb.lock_location = (False, False, False); master_pb.lock_rotation = (False, False, False); master_pb.lock_scale = (True, True, True)
    _configure_foot_roll_pose_control(heel_pb, "HEEL", suffix)
    _configure_foot_roll_pose_control(ball_pb, "BALL", suffix)
    _configure_foot_roll_pose_control(toe_pb, "TOE", suffix)
    if foot_pb:
        # Translation belongs to the Foot Master plus reverse-foot parents. The
        # ankle child remains rotation-only; Heel/Ball/Toe pivots move its head
        # through hierarchy transforms while FootIK supplies final orientation.
        foot_pb.lock_location = (True, True, True); foot_pb.lock_rotation = (False, False, False); foot_pb.lock_scale = (True, True, True)
        try: foot_pb.rotation_mode = arm_obj.pose.bones[src["foot"]].rotation_mode
        except Exception: pass
    if pole_pb:
        pole_pb.lock_location = (False, False, False); pole_pb.lock_rotation = (True, True, True); pole_pb.lock_scale = (True, True, True)

    target_pb = arm_obj.pose.bones.get(names["mch_target"])
    if not target_pb: raise RuntimeError(f"{suffix}: failed to initialize effective Foot IK target")
    # Position and orientation are deliberately decoupled. The two-bone IK
    # target only needs the animator's ankle position. Foot orientation is
    # handled on MCH_Foot after the IK solve, where it can be limited relative
    # to the shin without moving the ankle target.
    _add_copy_location(target_pb, arm_obj, names["ctrl_foot"], f"SR_LEGS_IK_TARGET_FOLLOW_{suffix}")
    _add_leg_reach_limits(arm_obj, suffix, target_pb, names["mch_reach"], reach_distance, max_factor=SR_DEFAULT_LEG_REACH_FACTOR, min_factor=SR_DEFAULT_LEG_MIN_REACH_FACTOR, preferred_factor=SR_DEFAULT_LEG_PREFERRED_REACH_FACTOR)

    # Smart Knee Pole Space: rotate the hidden space's Y axis toward the
    # effective ankle target. The pole control inherits that stable space, while
    # the local flip guard prevents accidental hemisphere crossing.
    pole_space_pb = arm_obj.pose.bones.get(names["mch_pole_space"])
    if not pole_space_pb:
        raise RuntimeError(f"{suffix}: failed to initialize Knee Pole smart space")
    smart_track = pole_space_pb.constraints.new('DAMPED_TRACK')
    smart_track.name = f"SR_LEGS_IK_POLE_SPACE_SMART_{suffix}"
    smart_track.target = arm_obj
    smart_track.subtarget = names["mch_target"]
    if hasattr(smart_track, "track_axis"):
        smart_track.track_axis = 'TRACK_Y'
    smart_track.influence = 1.0
    _install_knee_pole_flip_guard(arm_obj, suffix, reach_distance)

    shin_pb = arm_obj.pose.bones.get(names["mch_shin"]); mch_foot_pb = arm_obj.pose.bones.get(names["mch_foot"])
    if not shin_pb or not mch_foot_pb: raise RuntimeError(f"{suffix}: failed to initialize leg IK mechanism")
    ik = shin_pb.constraints.new('IK'); ik.name = f"SR_LEGS_IK_SOLVER_{suffix}"
    ik.target = arm_obj; ik.subtarget = names["mch_target"]; ik.pole_target = arm_obj; ik.pole_subtarget = names["ctrl_pole"]; ik.chain_count = 2
    if hasattr(ik, "use_tail"): ik.use_tail = True
    if hasattr(ik, "use_stretch"): ik.use_stretch = False
    if hasattr(ik, "use_rotation"): ik.use_rotation = False
    if hasattr(ik, "pole_angle"): ik.pole_angle = 0.0
    # Never copy target location onto MCH_Foot: its head already follows the
    # solved shin/ankle. Only the animator-facing Foot Pivot supplies rotation.
    # The semantic LOCAL ankle limit, added afterwards, prevents the world-space
    # foot orientation from folding back through the shin at extreme leg raises.
    _add_copy_rotation(mch_foot_pb, arm_obj, names["ctrl_foot"], f"SR_LEGS_IK_FOOT_ORIENT_{suffix}")

    for role, source_name, mch_name in (
        ("thigh", src["thigh"], names["mch_thigh"]),
        ("shin", src["shin"], names["mch_shin"]),
        ("foot", src["foot"], names["mch_foot"]),
    ):
        source_pb = arm_obj.pose.bones.get(source_name)
        if not source_pb: raise RuntimeError(f"{suffix}: source pose bone disappeared: {source_name}")
        c = _add_copy_transforms(source_pb, arm_obj, mch_name, f"SR_LEGS_IK_DEF_{suffix}_{role}"); c.influence = 0.0

    _set_leg_mode_raw(arm_obj, suffix, False)
    calibration = _snap_leg_fk_to_ik(context, arm_obj, suffix, calibrate=True)
    arm_obj[f"saberrig_leg_pole_singular_{suffix}"] = bool(calibration.get("singular", False))
    safe = str(arm_obj.get("saberrig_leg_joint_limits_mode", "SAFE")).upper() != "OFF"
    _apply_leg_joint_limits(arm_obj, suffix, enabled=safe)
    pole_mode = str(arm_obj.get("saberrig_knee_pole_space_mode", SR_KNEE_POLE_SPACE_DEFAULT)).upper()
    _apply_knee_pole_space_mode(arm_obj, suffix, mode=pole_mode, preserve_world=False)
    return generated

def _build_legs_ik(context, arm_obj, analysis):
    if not _foundation_is_prepared(arm_obj): _prepare_foundation(arm_obj, analysis)
    if not bool(arm_obj.get("saberrig_legs_fk_built", False)):
        _build_legs_fk(context, arm_obj, analysis); analysis = analyze_armature(arm_obj)
    _activate_armature(context, arm_obj, mode='OBJECT')
    requested_limits = str(arm_obj.get("saberrig_leg_joint_limits_mode", "SAFE")).upper()
    requested_bend = str(arm_obj.get("saberrig_leg_bend_mode", "SMOOTH")).upper()
    requested_pole_space = str(arm_obj.get("saberrig_knee_pole_space_mode", SR_KNEE_POLE_SPACE_DEFAULT)).upper()
    _remove_leg_ik(context, arm_obj)
    arm_obj["saberrig_leg_joint_limits_mode"] = "OFF" if requested_limits == "OFF" else "SAFE"
    arm_obj["saberrig_leg_bend_mode"] = "RIGID" if requested_bend == "RIGID" else "SMOOTH"
    arm_obj["saberrig_knee_pole_space_mode"] = "GLOBAL" if requested_pole_space == "GLOBAL" else "SMART"
    generated, errors = [], []
    for side in ("L", "R"):
        try:
            generated.extend(_build_leg_ik_side(context, arm_obj, analysis, side))
        except Exception as exc:
            try:
                if arm_obj.mode != 'OBJECT': bpy.ops.object.mode_set(mode='OBJECT')
            except Exception: pass
            try: _remove_bones_by_names(context, arm_obj, _leg_ik_names(side).values())
            except Exception: pass
            errors.append(str(exc))
    if not generated:
        raise RuntimeError(errors[0] if errors else "No leg IK controls could be generated")
    arm_obj["saberrig_legs_ik_built"] = True; arm_obj["saberrig_legs_ik_version"] = ADDON_VERSION
    arm_obj["saberrig_leg_mode_L"] = "FK"; arm_obj["saberrig_leg_mode_R"] = "FK"
    _activate_armature(context, arm_obj, mode='POSE')
    if bool(arm_obj.get("saberrig_body_built", False)):
        _attach_global_controls_to_master(context, arm_obj)
    if bool(arm_obj.get("saberrig_animation_spaces_built", False)):
        try:
            _build_animation_spaces(context, arm_obj, analyze_armature(arm_obj))
        except Exception:
            pass
    _setup_control_visuals(arm_obj); _set_rig_view(arm_obj, True)
    return generated, errors

# -----------------------------------------------------------------------------
# Animation Spaces & seamless IK workflow
# -----------------------------------------------------------------------------

def _animation_space_switch_name(kind, side):
    suffix = str(side).upper()
    label = "HandIK" if str(kind).upper() == "HAND" else "FootMaster"
    return f"SR_MCH_SpaceSwitch_{label}.{suffix}"

def _animation_space_anchor_name(kind, side, space):
    suffix = str(side).upper()
    label = "HandIK" if str(kind).upper() == "HAND" else "FootMaster"
    return f"SR_MCH_SpaceAnchor_{label}_{str(space).title()}.{suffix}"

def _animation_space_control_name(kind, side):
    kind = str(kind).upper()
    suffix = str(side).upper()
    if kind == "HAND":
        return _arm_ik_names(suffix)["ctrl_hand"]
    if kind == "FOOT":
        return _leg_ik_names(suffix)["ctrl_master"]
    return ""

def _animation_space_switch_for_control(arm_obj, control_name):
    bone = arm_obj.data.bones.get(control_name or "") if arm_obj and arm_obj.type == 'ARMATURE' else None
    if bone:
        stored = str(bone.get("saberrig_space_switch", ""))
        if stored and arm_obj.data.bones.get(stored):
            return stored
    # Name-based fallback lets Body Controls find a surviving switch even when
    # its child control was rebuilt and therefore lost the custom metadata.
    for kind in ("HAND", "FOOT"):
        for side in ("L", "R"):
            if _animation_space_control_name(kind, side) == control_name:
                candidate = _animation_space_switch_name(kind, side)
                return candidate if arm_obj.data.bones.get(candidate) else ""
    return ""

def _animation_space_base_parent(arm_obj, control_name):
    """Return the stable WORLD parent for an animator IK control."""
    body_names = _body_control_names()
    master = body_names["ctrl_master"]
    if bool(arm_obj.get("saberrig_body_built", False)) and arm_obj.data.bones.get(master):
        return master
    bone = arm_obj.data.bones.get(control_name or "")
    if bone and bone.parent:
        parent_name = bone.parent.name
        # Never use an old SaberRig space-switch MCH as the base parent.
        if not str(parent_name).startswith("SR_MCH_SpaceSwitch_"):
            return parent_name
    return None

def _animation_space_targets(arm_obj, analysis, kind, side):
    """Resolve available space parents using body controls when possible.

    Source-bone fallbacks keep the feature useful even if Body Controls were not
    generated yet. COG intentionally prefers SR_CTRL_COG because no source bone
    represents that extra animation layer.
    """
    kind = str(kind).upper()
    suffix = str(side).upper()
    body_src = _body_sources(analysis)
    body_names = _body_control_names()
    arm_src = _arm_sources_for_side(analysis, suffix)

    body_built = bool(arm_obj.get("saberrig_body_built", False))
    ctrl_root = body_names["ctrl_root"] if body_built and arm_obj.data.bones.get(body_names["ctrl_root"]) else None
    ctrl_cog = body_names["ctrl_cog"] if body_built and arm_obj.data.bones.get(body_names["ctrl_cog"]) else None
    ctrl_chest = body_names["ctrl_chest"] if body_built and arm_obj.data.bones.get(body_names["ctrl_chest"]) else None
    ctrl_spine = body_names["ctrl_spine"] if body_built and arm_obj.data.bones.get(body_names["ctrl_spine"]) else None

    root_target = ctrl_root or body_src.get("root") or body_src.get("hips")
    cog_target = ctrl_cog or body_src.get("hips")
    chest_target = ctrl_chest or ctrl_spine or body_src.get("chest") or body_src.get("spine")

    targets = {
        "WORLD": None,
        "ROOT": root_target,
        "COG": cog_target,
    }
    if kind == "HAND":
        if chest_target:
            targets["CHEST"] = chest_target
        # Shoulder space follows the evaluated source clavicle so IK shoulder
        # assist and authored clavicle motion are both respected.
        if arm_src.get("clavicle"):
            targets["SHOULDER"] = arm_src["clavicle"]

    # Drop unavailable/self-referential targets while preserving stable indices.
    control_name = _animation_space_control_name(kind, suffix)
    clean = {"WORLD": None}
    for space, target in targets.items():
        if space == "WORLD":
            continue
        if target and target != control_name and arm_obj.data.bones.get(target):
            clean[space] = target
    return clean

def _animation_space_specs(arm_obj, analysis):
    specs = []
    for side in ("L", "R"):
        hand = _animation_space_control_name("HAND", side)
        if hand and arm_obj.data.bones.get(hand):
            specs.append({
                "kind": "HAND", "side": side, "control": hand,
                "switch": _animation_space_switch_name("HAND", side),
                "targets": _animation_space_targets(arm_obj, analysis, "HAND", side),
            })
        foot = _animation_space_control_name("FOOT", side)
        if foot and arm_obj.data.bones.get(foot):
            specs.append({
                "kind": "FOOT", "side": side, "control": foot,
                "switch": _animation_space_switch_name("FOOT", side),
                "targets": _animation_space_targets(arm_obj, analysis, "FOOT", side),
            })
    return specs

def _animation_space_remove_drivers(arm_obj):
    removed = 0
    ad = getattr(arm_obj, "animation_data", None)
    drivers = getattr(ad, "drivers", None) if ad else None
    if drivers is None:
        return removed
    for fc in list(drivers):
        path = str(getattr(fc, "data_path", ""))
        if "SR_MCH_SpaceSwitch_" in path or "SR_SPACE_" in path:
            try:
                drivers.remove(fc)
                removed += 1
            except Exception:
                pass
    return removed

def _animation_space_set_int_prop(pb, name, value, maximum):
    pb[name] = int(value)
    try:
        pb.id_properties_ui(name).update(
            min=0, max=int(maximum), soft_min=0, soft_max=int(maximum),
            description="SaberRig animation space. Use panel buttons to switch while keeping the control transform.",
        )
    except Exception:
        pass

def _animation_space_driver(arm_obj, constraint, control_name, index):
    try:
        fc = constraint.driver_add("influence")
    except Exception as exc:
        raise RuntimeError(f"Could not add animation-space driver: {exc}")
    drv = fc.driver
    drv.type = 'SCRIPTED'
    while drv.variables:
        drv.variables.remove(drv.variables[0])
    var = drv.variables.new()
    var.name = "sp"
    var.type = 'SINGLE_PROP'
    var.targets[0].id = arm_obj
    var.targets[0].data_path = f'pose.bones["{control_name}"]["space"]'
    drv.expression = f"1.0 if sp=={int(index)} else 0.0"
    return fc

def _animation_space_keyframe_constant(arm_obj, data_path, frame):
    ad = getattr(arm_obj, "animation_data", None)
    action = getattr(ad, "action", None) if ad else None
    if not action:
        return
    for fc in action.fcurves:
        if str(getattr(fc, "data_path", "")) != str(data_path):
            continue
        for kp in fc.keyframe_points:
            if abs(float(kp.co.x) - float(frame)) < 1.0e-4:
                kp.interpolation = 'CONSTANT'

def _animation_space_autokey_control(context, arm_obj, pb):
    try:
        enabled = bool(context.scene.tool_settings.use_keyframe_insert_auto)
    except Exception:
        enabled = False
    if not enabled or not pb:
        return
    frame = context.scene.frame_current
    try:
        pb.keyframe_insert(data_path='["space"]', frame=frame)
        _animation_space_keyframe_constant(
            arm_obj, f'pose.bones["{pb.name}"]["space"]', frame
        )
    except Exception:
        pass
    try:
        pb.keyframe_insert(data_path="location", frame=frame)
        if pb.rotation_mode == 'QUATERNION':
            pb.keyframe_insert(data_path="rotation_quaternion", frame=frame)
        elif pb.rotation_mode == 'AXIS_ANGLE':
            pb.keyframe_insert(data_path="rotation_axis_angle", frame=frame)
        else:
            pb.keyframe_insert(data_path="rotation_euler", frame=frame)
        pb.keyframe_insert(data_path="scale", frame=frame)
    except Exception:
        pass

def _animation_space_mode_key(kind, side):
    return f"saberrig_space_mode_{str(kind).upper()}_{str(side).upper()}"

def _remove_animation_spaces(context, arm_obj, preserve_modes=True):
    if not arm_obj or arm_obj.type != 'ARMATURE':
        return 0

    saved_modes = {}
    if preserve_modes:
        for kind in ("HAND", "FOOT"):
            for side in ("L", "R"):
                key = _animation_space_mode_key(kind, side)
                if key in arm_obj:
                    saved_modes[key] = str(arm_obj.get(key, "WORLD")).upper()

    snapshots = {}
    controls = []
    for kind in ("HAND", "FOOT"):
        for side in ("L", "R"):
            ctrl_name = _animation_space_control_name(kind, side)
            bone = arm_obj.data.bones.get(ctrl_name or "")
            if not bone:
                continue
            switch = str(bone.get("saberrig_space_switch", ""))
            if switch or (bone.parent and bone.parent.name.startswith("SR_MCH_SpaceSwitch_")):
                pb = arm_obj.pose.bones.get(ctrl_name)
                if pb:
                    snapshots[ctrl_name] = pb.matrix.copy()
                controls.append((ctrl_name, kind, side))

    _animation_space_remove_drivers(arm_obj)

    if controls:
        _activate_armature(context, arm_obj, mode='EDIT')
        eb = arm_obj.data.edit_bones
        master_name = _body_control_names()["ctrl_master"]
        master_available = bool(
            arm_obj.get("saberrig_body_built", False) and eb.get(master_name)
        )
        for ctrl_name, _kind, _side in controls:
            b = eb.get(ctrl_name)
            if not b:
                continue
            data_bone = arm_obj.data.bones.get(ctrl_name)
            stored_parent = str(data_bone.get("saberrig_space_original_parent", "")) if data_bone else ""
            restore_parent = master_name if master_available else stored_parent
            b.parent = eb.get(restore_parent) if restore_parent and eb.get(restore_parent) else None
            b.use_connect = False
        bpy.ops.object.mode_set(mode='POSE')
        _view_layer_update(context)
        for ctrl_name, matrix in snapshots.items():
            pb = arm_obj.pose.bones.get(ctrl_name)
            if pb:
                try:
                    pb.matrix = matrix.copy()
                except Exception:
                    pass
        _view_layer_update(context)

    removed = _remove_generated_bones(
        context, arm_obj, component=SR_ANIMATION_SPACE_COMPONENT
    )

    for kind in ("HAND", "FOOT"):
        for side in ("L", "R"):
            ctrl_name = _animation_space_control_name(kind, side)
            bone = arm_obj.data.bones.get(ctrl_name or "")
            if bone:
                for prop in (
                    "saberrig_space_switch", "saberrig_space_original_parent",
                    "saberrig_space_kind", "saberrig_space_side",
                ):
                    if prop in bone:
                        try:
                            del bone[prop]
                        except Exception:
                            pass
            key = _animation_space_mode_key(kind, side)
            if not preserve_modes and key in arm_obj:
                try:
                    del arm_obj[key]
                except Exception:
                    pass
            avail_key = f"saberrig_space_available_{kind}_{side}"
            if avail_key in arm_obj:
                try:
                    del arm_obj[avail_key]
                except Exception:
                    pass

    for key in ("saberrig_animation_spaces_built", "saberrig_animation_spaces_version", "saberrig_animation_spaces_count"):
        if key in arm_obj:
            try:
                del arm_obj[key]
            except Exception:
                pass

    for key, value in saved_modes.items():
        arm_obj[key] = value
    return removed

def _build_animation_spaces(context, arm_obj, analysis):
    requested_modes = {}
    for kind in ("HAND", "FOOT"):
        for side in ("L", "R"):
            key = _animation_space_mode_key(kind, side)
            requested_modes[(kind, side)] = str(arm_obj.get(key, "WORLD")).upper()

    _remove_animation_spaces(context, arm_obj, preserve_modes=True)
    analysis = analyze_armature(arm_obj)
    specs = _animation_space_specs(arm_obj, analysis)
    if not specs:
        raise RuntimeError("Build Arms IK and/or Legs IK before Animation Spaces")

    mechanism_collection = _find_bone_collection(arm_obj.data, SR_COLLECTION_MECHANISM)
    if not mechanism_collection:
        raise RuntimeError("SaberRig Mechanism collection is missing")

    pose_snapshots = {}
    build_rows = []

    _activate_armature(context, arm_obj, mode='EDIT')
    eb = arm_obj.data.edit_bones
    master_name = _body_control_names()["ctrl_master"]
    body_master = master_name if bool(arm_obj.get("saberrig_body_built", False)) and eb.get(master_name) else None

    for spec in specs:
        kind, side = spec["kind"], spec["side"]
        ctrl_name, switch_name = spec["control"], spec["switch"]
        ctrl = eb.get(ctrl_name)
        if not ctrl:
            continue

        pb = arm_obj.pose.bones.get(ctrl_name)
        if pb:
            pose_snapshots[ctrl_name] = pb.matrix.copy()

        # WORLD uses Master if Body Controls exist; otherwise preserve the
        # control's current non-space parent.
        current_parent = ctrl.parent.name if ctrl.parent else None
        if current_parent and current_parent.startswith("SR_MCH_SpaceSwitch_"):
            current_parent = None
        base_parent = body_master or current_parent

        switch = _clone_edit_bone(eb, switch_name, ctrl_name, base_parent)
        mechanism_collection.assign(switch)

        anchor_names = {}
        for space, target_parent in spec["targets"].items():
            if space == "WORLD" or not target_parent:
                continue
            anchor_name = _animation_space_anchor_name(kind, side, space)
            anchor = _clone_edit_bone(eb, anchor_name, ctrl_name, target_parent)
            mechanism_collection.assign(anchor)
            anchor_names[space] = anchor_name

        ctrl.parent = switch
        ctrl.use_connect = False
        build_rows.append((spec, base_parent, anchor_names))

    bpy.ops.object.mode_set(mode='OBJECT')

    generated = []
    for spec, base_parent, anchor_names in build_rows:
        kind, side = spec["kind"], spec["side"]
        ctrl_name, switch_name = spec["control"], spec["switch"]
        _mark_generated_bone(
            arm_obj, switch_name, "MCH", f"space_switch.{kind.lower()}.{side}",
            ctrl_name, component=SR_ANIMATION_SPACE_COMPONENT
        )
        generated.append(switch_name)
        for space, anchor_name in anchor_names.items():
            _mark_generated_bone(
                arm_obj, anchor_name, "MCH",
                f"space_anchor.{kind.lower()}.{side}.{space.lower()}",
                spec["targets"].get(space) or "",
                component=SR_ANIMATION_SPACE_COMPONENT
            )
            generated.append(anchor_name)

        ctrl_data = arm_obj.data.bones.get(ctrl_name)
        if ctrl_data:
            ctrl_data["saberrig_space_switch"] = switch_name
            ctrl_data["saberrig_space_original_parent"] = base_parent or ""
            ctrl_data["saberrig_space_kind"] = kind
            ctrl_data["saberrig_space_side"] = side

    _activate_armature(context, arm_obj, mode='POSE')
    _view_layer_update(context)

    for spec, _base_parent, anchor_names in build_rows:
        kind, side = spec["kind"], spec["side"]
        ctrl_name, switch_name = spec["control"], spec["switch"]
        ctrl_pb = arm_obj.pose.bones.get(ctrl_name)
        switch_pb = arm_obj.pose.bones.get(switch_name)
        if not ctrl_pb or not switch_pb:
            continue

        available = ["WORLD"] + [
            space for space in ("ROOT", "COG", "CHEST", "SHOULDER")
            if space in anchor_names
        ]
        arm_obj[f"saberrig_space_available_{kind}_{side}"] = "|".join(available)

        requested = requested_modes.get((kind, side), "WORLD")
        if requested not in available:
            requested = "WORLD"
        index = SR_ANIMATION_SPACE_INDEX[requested]
        _animation_space_set_int_prop(
            ctrl_pb, "space", index, max(SR_ANIMATION_SPACE_INDEX.values())
        )

        for space, anchor_name in anchor_names.items():
            c = _add_copy_transforms(
                switch_pb, arm_obj, anchor_name,
                f"SR_SPACE_{kind}_{side}_{space}"
            )
            c.influence = 0.0
            _animation_space_driver(
                arm_obj, c, ctrl_name, SR_ANIMATION_SPACE_INDEX[space]
            )

        arm_obj[_animation_space_mode_key(kind, side)] = requested

    _view_layer_update(context)

    # Parenting and the requested starting space must never pop an existing pose.
    for ctrl_name, matrix in pose_snapshots.items():
        pb = arm_obj.pose.bones.get(ctrl_name)
        if pb:
            try:
                pb.matrix = matrix.copy()
            except Exception:
                pass
    _view_layer_update(context)

    arm_obj["saberrig_animation_spaces_built"] = True
    arm_obj["saberrig_animation_spaces_version"] = ADDON_VERSION
    arm_obj["saberrig_animation_spaces_count"] = int(len(build_rows))
    _setup_control_visuals(arm_obj)
    _set_rig_view(arm_obj, True)
    return generated

def _set_animation_space(context, arm_obj, kind, side, space):
    kind = str(kind).upper()
    side = str(side).upper()
    space = str(space).upper()
    ctrl_name = _animation_space_control_name(kind, side)
    ctrl_pb = arm_obj.pose.bones.get(ctrl_name or "")
    if not ctrl_pb:
        raise RuntimeError(f"{kind} {side}: animator control is unavailable")
    available = str(arm_obj.get(f"saberrig_space_available_{kind}_{side}", "WORLD")).split("|")
    if space not in available:
        raise RuntimeError(f"{kind} {side}: {space.title()} space is unavailable")

    snapshot = ctrl_pb.matrix.copy()
    index = int(SR_ANIMATION_SPACE_INDEX[space])
    ctrl_pb["space"] = index
    arm_obj[_animation_space_mode_key(kind, side)] = space
    _view_layer_update(context)

    # The hidden switch parent may jump to a different anchor; rewrite the
    # animator control's basis from its previous evaluated matrix.
    ctrl_pb = arm_obj.pose.bones.get(ctrl_name)
    if ctrl_pb:
        ctrl_pb.matrix = snapshot
    _view_layer_update(context)
    _animation_space_autokey_control(context, arm_obj, ctrl_pb)
    return True

class SR_OT_BuildAnimationSpaces(Operator):
    bl_idname = "saberrig.build_animation_spaces"
    bl_label = "Build Animation Spaces"
    bl_description = "Build/refresh seamless World, Root, COG, Chest and Shoulder spaces for SaberRig IK controls"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        state = context.scene.saberrig_state
        arm_obj = _resolve_armature(context, state)
        if not arm_obj or arm_obj.type != 'ARMATURE':
            self.report({'ERROR'}, "SaberRig target armature is unavailable")
            return {'CANCELLED'}
        if not _armature_is_editable(arm_obj):
            self.report({'ERROR'}, "Target armature is linked/read-only")
            return {'CANCELLED'}
        try:
            analysis = analyze_armature(arm_obj)
            generated = _build_animation_spaces(context, arm_obj, analysis)
        except Exception as exc:
            self.report({'ERROR'}, f"Animation Spaces build failed: {exc}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Animation Spaces ready: {len(generated)} hidden mechanism bones")
        return {'FINISHED'}

class SR_OT_SetAnimationSpace(Operator):
    bl_idname = "saberrig.set_animation_space"
    bl_label = "Switch Animation Space"
    bl_description = "Switch the selected SaberRig IK control to another parent space while keeping its current transform"
    bl_options = {'REGISTER', 'UNDO'}

    kind: StringProperty(default="HAND")
    side: StringProperty(default="L")
    space: StringProperty(default="WORLD")

    def execute(self, context):
        state = context.scene.saberrig_state
        arm_obj = _resolve_armature(context, state)
        if not arm_obj or arm_obj.type != 'ARMATURE':
            self.report({'ERROR'}, "SaberRig target armature is unavailable")
            return {'CANCELLED'}
        if not bool(arm_obj.get("saberrig_animation_spaces_built", False)):
            self.report({'ERROR'}, "Build Animation Spaces first")
            return {'CANCELLED'}
        try:
            _set_animation_space(context, arm_obj, self.kind, self.side, self.space)
        except Exception as exc:
            self.report({'ERROR'}, f"Space switch failed: {exc}")
            return {'CANCELLED'}
        label = SR_ANIMATION_SPACE_LABELS.get(self.space.upper(), self.space.title())
        self.report({'INFO'}, f"{self.kind.title()} {self.side.upper()}: {label} space, transform preserved")
        return {'FINISHED'}

# -----------------------------------------------------------------------------
# Finger controls
# -----------------------------------------------------------------------------

def _finger_control_names(side):
    suffix = str(side).upper()
    return {"ctrl_master": f"SR_CTRL_Fingers.{suffix}"}

def _finger_mch_name(side, finger, index):
    suffix = str(side).upper()
    label = SR_FINGER_LABELS.get(str(finger).lower(), str(finger).title())
    return f"SR_MCH_Finger_{label}{int(index) + 1}.{suffix}"

def _finger_fist_thumb_target_name(side, index):
    suffix = str(side).upper()
    return f"SR_MCH_FistThumbTarget{int(index) + 1}.{suffix}"

def _finger_chains_for_side(analysis, side):
    suffix = str(side).upper()
    rows = {}
    resolved = analysis.get("fingers", {}) if analysis else {}
    for finger in SR_FINGER_ORDER:
        chain = [n for n in resolved.get(f"{finger}.{suffix}", []) if n]
        if chain:
            rows[finger] = chain[:3]
    return rows

def _finger_local_axis(bone, axis_index):
    try:
        m = bone.matrix_local.to_3x3()
        v = m @ Vector((1.0, 0.0, 0.0)) if axis_index == 0 else m @ Vector((0.0, 0.0, 1.0))
        if v.length > 1.0e-8:
            return v.normalized()
    except Exception:
        pass
    return Vector((1.0, 0.0, 0.0)) if axis_index == 0 else Vector((0.0, 0.0, 1.0))

def _finger_palm_frame(arm_obj, analysis, side):
    suffix = str(side).upper()
    chains = _finger_chains_for_side(analysis, suffix)
    roots = []
    forward_rows = []
    for finger in ("index", "middle", "ring", "pinky"):
        chain = chains.get(finger, [])
        if not chain:
            continue
        b = arm_obj.data.bones.get(chain[0])
        if not b:
            continue
        roots.append(b.head_local.copy())
        d = b.tail_local - b.head_local
        if d.length > 1.0e-8:
            forward_rows.append(d.normalized())
    hand_name = _semantic_bone(analysis, f"hand.{suffix}")
    hand = arm_obj.data.bones.get(hand_name or "")
    if roots:
        center = sum(roots, Vector((0.0, 0.0, 0.0))) / len(roots)
    elif hand:
        center = hand.tail_local.copy()
    else:
        center = Vector((0.0, 0.0, 0.0))
    if forward_rows:
        forward = sum(forward_rows, Vector((0.0, 0.0, 0.0)))
    elif hand:
        forward = hand.tail_local - hand.head_local
    else:
        forward = Vector((0.0, 1.0, 0.0))
    if forward.length < 1.0e-8:
        forward = Vector((0.0, 1.0, 0.0))
    forward.normalize()

    index_chain = chains.get("index", [])
    pinky_chain = chains.get("pinky", [])
    across = Vector((0.0, 0.0, 0.0))
    if index_chain and pinky_chain:
        ib = arm_obj.data.bones.get(index_chain[0]); pb = arm_obj.data.bones.get(pinky_chain[0])
        if ib and pb:
            across = pb.head_local - ib.head_local
    if across.length < 1.0e-8 and hand:
        across = _finger_local_axis(hand, 0)
    across = across - forward * across.dot(forward)
    if across.length < 1.0e-8:
        across = Vector((1.0, 0.0, 0.0))
        across = across - forward * across.dot(forward)
    if across.length < 1.0e-8:
        across = Vector((0.0, 0.0, 1.0)).cross(forward)
    across.normalize()

    # pinky-index × forward yields the same anatomical palm-facing hemisphere
    # on mirrored hands for the usual humanoid layout. If degenerate, fall back
    # to the hand's authored local Z axis.
    normal = across.cross(forward)
    if normal.length < 1.0e-8 and hand:
        normal = _finger_local_axis(hand, 2)
    if normal.length < 1.0e-8:
        normal = Vector((0.0, 0.0, 1.0))
    normal.normalize()

    # UMA's imported/MMD-hybrid hand convention uses the opposite curl
    # hemisphere from the generic cross-product convention above. Calibrate the
    # profile here; a trustworthy authored natural bend (below) can still
    # override this when the source hand is not perfectly straight.
    profile_id = str((analysis or {}).get("detection", {}).get("profile_id", ""))
    curl_hemisphere_source = "GEOMETRIC"
    if profile_id == "UMA_MUSUME_MMD_HYBRID":
        normal.negate()
        curl_hemisphere_source = "UMA_PROFILE"

    # If the authored rest fingers already contain even a small natural bend,
    # use it to orient the palm-normal hemisphere. This avoids a mirrored
    # positive-curl convention on imports whose hand axes are flipped.
    natural_bends = []
    for finger in ("index", "middle", "ring", "pinky"):
        chain = chains.get(finger, [])
        if len(chain) < 2:
            continue
        b0 = arm_obj.data.bones.get(chain[0]); b1 = arm_obj.data.bones.get(chain[1])
        if not b0 or not b1:
            continue
        d0 = b0.tail_local - b0.head_local; d1 = b1.tail_local - b1.head_local
        if d0.length < 1.0e-8 or d1.length < 1.0e-8:
            continue
        d0.normalize(); d1.normalize()
        bend = d1 - d0 * d1.dot(d0)
        if bend.length > 0.015:
            natural_bends.append(bend.normalized())
    if natural_bends:
        authored_bend = sum(natural_bends, Vector((0.0, 0.0, 0.0)))
        if authored_bend.length > 1.0e-8:
            if authored_bend.dot(normal) < 0.0:
                normal.negate()
            curl_hemisphere_source = "AUTHORED_BEND"

    return {
        "center": center, "forward": forward, "across": across, "normal": normal,
        "hand": hand_name, "curl_hemisphere_source": curl_hemisphere_source,
    }

def _finger_chain_plane(arm_obj, chain, palm_frame):
    """Return one anatomical flexion axis for an entire finger chain.

    The axis is derived from the root finger direction and palm normal in
    armature-local space. Every segment projects the same axis into its local
    X/Z candidates so roll differences cannot select a different bend plane.
    """
    if not chain:
        return {"axis": palm_frame["across"].copy(), "confidence": 0.0}
    root = arm_obj.data.bones.get(chain[0])
    if not root:
        return {"axis": palm_frame["across"].copy(), "confidence": 0.0}
    direction = root.tail_local - root.head_local
    if direction.length < 1.0e-8:
        direction = palm_frame["forward"].copy()
    direction.normalize()
    desired = palm_frame["normal"] - direction * palm_frame["normal"].dot(direction)
    if desired.length < 1.0e-8:
        desired = palm_frame["normal"].copy()
    desired.normalize()
    # axis × direction = desired  =>  axis = direction × desired
    axis = direction.cross(desired)
    if axis.length < 1.0e-8:
        axis = palm_frame["across"].copy()
    axis.normalize()
    # Confidence measures whether root direction and desired palm curl form a
    # clean perpendicular pair; near 1.0 is stable.
    confidence = max(0.0, min(1.0, axis.cross(direction).normalized().dot(desired))) if axis.cross(direction).length > 1.0e-8 else 0.0
    return {"axis": axis, "confidence": float(abs(confidence))}

def _finger_chain_axis_diagnostics(arm_obj, bone_name, chain_plane, palm_frame, root=False):
    bone = arm_obj.data.bones.get(bone_name)
    if not bone:
        return {"curl_axis": 0, "curl_sign": 1.0, "spread_axis": 2, "spread_sign": 1.0, "curl_conf": 0.0, "spread_conf": 0.0}
    direction = bone.tail_local - bone.head_local
    if direction.length < 1.0e-8:
        direction = palm_frame["forward"].copy()
    direction.normalize()

    # Curl: project the SAME chain hinge axis into each segment's local X/Z.
    target_axis = chain_plane["axis"]
    axes = {0: _finger_local_axis(bone, 0), 2: _finger_local_axis(bone, 2)}
    curl_scores = {idx: axis.dot(target_axis) for idx, axis in axes.items()}
    curl_axis = max(curl_scores, key=lambda i: abs(curl_scores[i]))
    curl_score = curl_scores[curl_axis]

    # Spread remains root-local and uses the already validated palm-plane logic.
    outward = bone.head_local - palm_frame["center"]
    outward = outward - palm_frame["normal"] * outward.dot(palm_frame["normal"])
    outward = outward - direction * outward.dot(direction)
    if outward.length < 1.0e-8:
        outward = palm_frame["across"].copy()
    outward.normalize()
    spread_scores = {}
    for idx, axis in axes.items():
        motion = axis.cross(direction)
        spread_scores[idx] = motion.normalized().dot(outward) if motion.length > 1.0e-8 else 0.0
    spread_axis = max(spread_scores, key=lambda i: abs(spread_scores[i]))
    if spread_axis == curl_axis:
        spread_axis = 2 if curl_axis == 0 else 0
    spread_score = spread_scores.get(spread_axis, 0.0)
    return {
        "curl_axis": int(curl_axis), "curl_sign": 1.0 if curl_score >= 0.0 else -1.0,
        "spread_axis": int(spread_axis), "spread_sign": 1.0 if spread_score >= 0.0 else -1.0,
        "curl_conf": abs(float(curl_score)), "spread_conf": abs(float(spread_score)),
    }

def _finger_thumb_axis_lock_diagnostics(arm_obj, bone_name, chain_plane, palm_frame, diag, force_axis=None):
    """Override only the thumb flexion axis while preserving palm-aware signs.

    UMA thumb curl is locked to the intended local axis. Curl sign/confidence
    is recomputed against the anatomical chain plane; Spread is moved to an
    orthogonal candidate when necessary so the controls do not fight.
    """
    result = dict(diag or {})
    if force_axis is None:
        return result
    bone = arm_obj.data.bones.get(bone_name or "")
    if not bone:
        result["curl_axis"] = int(force_axis)
        return result

    direction = bone.tail_local - bone.head_local
    if direction.length < 1.0e-8:
        direction = palm_frame.get("forward", Vector((0.0, 1.0, 0.0))).copy()
    if direction.length < 1.0e-8:
        direction = Vector((0.0, 1.0, 0.0))
    direction.normalize()

    curl_axis = int(force_axis)
    curl_vec = _finger_local_axis(bone, curl_axis)
    target_axis = chain_plane.get("axis", palm_frame.get("across", Vector((1.0, 0.0, 0.0))))
    curl_score = curl_vec.dot(target_axis) if curl_vec.length > 1.0e-8 and target_axis.length > 1.0e-8 else 0.0
    old_sign = float(result.get("curl_sign", 1.0))
    result["curl_axis"] = curl_axis
    result["curl_sign"] = (1.0 if curl_score >= 0.0 else -1.0) if abs(curl_score) > 1.0e-6 else (1.0 if old_sign >= 0.0 else -1.0)
    result["curl_conf"] = abs(float(curl_score)) if abs(curl_score) > 1.0e-6 else float(result.get("curl_conf", 0.0))

    if int(result.get("spread_axis", 2)) == curl_axis:
        spread_axis = 2 if curl_axis == 0 else 0
        spread_vec = _finger_local_axis(bone, spread_axis)
        outward = bone.head_local - palm_frame.get("center", bone.head_local)
        normal = palm_frame.get("normal", Vector((0.0, 0.0, 1.0)))
        outward = outward - normal * outward.dot(normal)
        outward = outward - direction * outward.dot(direction)
        if outward.length < 1.0e-8:
            outward = palm_frame.get("across", Vector((1.0, 0.0, 0.0))).copy()
        if outward.length > 1.0e-8:
            outward.normalize()
        motion = spread_vec.cross(direction)
        spread_score = motion.normalized().dot(outward) if motion.length > 1.0e-8 and outward.length > 1.0e-8 else 0.0
        result["spread_axis"] = spread_axis
        result["spread_sign"] = 1.0 if spread_score >= 0.0 else -1.0
        result["spread_conf"] = abs(float(spread_score))
    return result

def _finger_thumb_project_armature_axis_local(bone, armature_axis):
    """Project one armature-space axis into a bone's authored local rest basis.

    Bone.matrix_local maps bone-local coordinates into armature space, so the
    inverse 3x3 rotation maps Armature-X back into the local XYZ coordinates
    required by the pose delta/quaternion. This is deliberately per-bone:
    imported MMD thumb rolls differ substantially along the chain.
    """
    axis_arm = Vector(armature_axis)
    if axis_arm.length < 1.0e-8:
        axis_arm = Vector((1.0, 0.0, 0.0))
    axis_arm.normalize()
    if not bone:
        return axis_arm.copy()
    try:
        local_axis = bone.matrix_local.to_3x3().inverted_safe() @ axis_arm
    except Exception:
        local_axis = axis_arm.copy()
    if local_axis.length < 1.0e-8:
        local_axis = axis_arm.copy()
    local_axis.normalize()
    return local_axis

def _finger_thumb_armature_bend_sign(bone, palm_frame, armature_axis):
    """Return the sign that makes rotation about the armature axis curl inward."""
    axis_arm = Vector(armature_axis)
    if axis_arm.length < 1.0e-8:
        axis_arm = Vector((1.0, 0.0, 0.0))
    axis_arm.normalize()
    if not bone:
        return 1.0, 0.0

    direction = bone.tail_local - bone.head_local
    if direction.length < 1.0e-8:
        direction = palm_frame.get("forward", Vector((0.0, 1.0, 0.0))).copy()
    if direction.length < 1.0e-8:
        direction = Vector((0.0, 1.0, 0.0))
    direction.normalize()

    desired = palm_frame.get("normal", Vector((0.0, 0.0, 1.0))).copy()
    desired = desired - direction * desired.dot(direction)
    if desired.length < 1.0e-8:
        desired = palm_frame.get("center", bone.head_local) - bone.head_local
        desired = desired - direction * desired.dot(direction)
    if desired.length < 1.0e-8:
        desired = palm_frame.get("across", Vector((1.0, 0.0, 0.0))).copy()
    if desired.length > 1.0e-8:
        desired.normalize()

    motion_plus = axis_arm.cross(direction)
    score = motion_plus.normalized().dot(desired) if motion_plus.length > 1.0e-8 and desired.length > 1.0e-8 else 0.0
    return (1.0 if score >= 0.0 else -1.0), abs(float(score))

def _finger_thumb_signed_twist_about_axis(q, axis_local):
    """Return signed quaternion twist angle about a normalized local axis."""
    from mathutils import Quaternion
    axis = axis_local.copy()
    if axis.length < 1.0e-8:
        axis = Vector((1.0, 0.0, 0.0))
    axis.normalize()
    qq = q.copy()
    try:
        qq.normalize()
    except Exception:
        pass
    v = Vector((qq.x, qq.y, qq.z))
    projected = axis * v.dot(axis)
    twist = Quaternion((qq.w, projected.x, projected.y, projected.z))
    if twist.magnitude < 1.0e-8:
        twist = Quaternion((1.0, 0.0, 0.0, 0.0))
    else:
        twist.normalize()
    signed = 2.0 * math.atan2(
        Vector((twist.x, twist.y, twist.z)).dot(axis),
        float(twist.w),
    )
    while signed > math.pi:
        signed -= 2.0 * math.pi
    while signed < -math.pi:
        signed += 2.0 * math.pi
    return signed, twist

def _finger_signed_angle_about_axis(from_vec, to_vec, axis):
    """Signed shortest angle from from_vec to to_vec around axis."""
    axis = axis.copy()
    if axis.length < 1.0e-8:
        return 0.0
    axis.normalize()
    a = from_vec - axis * from_vec.dot(axis)
    b = to_vec - axis * to_vec.dot(axis)
    if a.length < 1.0e-8 or b.length < 1.0e-8:
        return 0.0
    a.normalize(); b.normalize()
    dot = max(-1.0, min(1.0, a.dot(b)))
    return math.atan2(axis.dot(a.cross(b)), dot)

def _finger_virtual_fist_polyline(arm_obj, chain, finger, palm_frame, curl_driver_multiplier=1.0):
    """Return a virtual closed-Fist polyline in armature space."""
    chain = list(chain or [])
    if not chain:
        return []
    plane = _finger_chain_plane(arm_obj, chain, palm_frame)
    axis = plane.get("axis", palm_frame["across"]).copy()
    if axis.length < 1.0e-8:
        axis = palm_frame["across"].copy()
    if axis.length < 1.0e-8:
        axis = Vector((1.0, 0.0, 0.0))
    axis.normalize()
    angles = SR_FINGER_FIST_ANGLES.get(finger, SR_FINGER_FIST_ANGLES["index"])
    root = arm_obj.data.bones.get(chain[0])
    if not root:
        return []
    points = [root.head_local.copy()]
    cumulative = 0.0
    for i, name in enumerate(chain):
        bone = arm_obj.data.bones.get(name)
        if not bone:
            continue
        rest_dir = bone.tail_local - bone.head_local
        if rest_dir.length < 1.0e-8:
            rest_dir = palm_frame["forward"].copy()
        if rest_dir.length < 1.0e-8:
            rest_dir = Vector((0.0, 1.0, 0.0))
        rest_dir.normalize()
        cumulative += float(angles[min(i, len(angles) - 1)]) * float(curl_driver_multiplier)
        posed_dir = Matrix.Rotation(cumulative, 4, axis) @ rest_dir
        if posed_dir.length < 1.0e-8:
            posed_dir = rest_dir.copy()
        posed_dir.normalize()
        points.append(points[-1] + posed_dir * max(float(bone.length), 1.0e-6))
    return points

def _finger_polyline_point(points, segment_index, factor):
    if not points or len(points) < 2:
        return None
    i = min(max(int(segment_index), 0), len(points) - 2)
    f = max(0.0, min(1.0, float(factor)))
    return points[i].lerp(points[i + 1], f)

def _finger_thumb_target_roll_ref(source_bone, target_dir, palm_normal, pronation):
    """Preserve authored roll while adding controlled fist pronation."""
    if not source_bone or target_dir.length < 1.0e-8:
        return palm_normal.copy()
    rest_dir = source_bone.tail_local - source_bone.head_local
    if rest_dir.length < 1.0e-8:
        rest_dir = target_dir.copy()
    rest_dir.normalize()
    target_dir = target_dir.normalized()
    rest_z = _finger_local_axis(source_bone, 2)
    try:
        aim_q = rest_dir.rotation_difference(target_dir)
        base_ref = aim_q @ rest_z
    except Exception:
        base_ref = rest_z.copy()
    base_ref = base_ref - target_dir * base_ref.dot(target_dir)
    if base_ref.length < 1.0e-8:
        base_ref = palm_normal - target_dir * palm_normal.dot(target_dir)
    if base_ref.length < 1.0e-8:
        base_ref = target_dir.cross(Vector((1.0, 0.0, 0.0)))
    if base_ref.length < 1.0e-8:
        base_ref = target_dir.cross(Vector((0.0, 0.0, 1.0)))
    base_ref.normalize()

    goal = palm_normal - target_dir * palm_normal.dot(target_dir)
    if goal.length < 1.0e-8:
        goal = base_ref.copy()
    goal.normalize()
    if rest_z.dot(palm_normal) < 0.0:
        goal.negate()

    if abs(float(pronation)) > 1.0e-8:
        pos = Matrix.Rotation(float(pronation), 4, target_dir) @ base_ref
        neg = Matrix.Rotation(-float(pronation), 4, target_dir) @ base_ref
        base_ref = pos if pos.dot(goal) >= neg.dot(goal) else neg
    base_ref = base_ref - target_dir * base_ref.dot(target_dir)
    if base_ref.length < 1.0e-8:
        base_ref = goal.copy()
    base_ref.normalize()
    return base_ref

def _finger_thumb_fist_target_geometry(arm_obj, chains, palm_frame, curl_driver_multiplier=1.0):
    """Construct absolute fist-space thumb orientations for parent-relative local quaternion conversion."""
    thumb_chain = list(chains.get("thumb", []))[:3]
    if not thumb_chain:
        return {"rows": [], "quality": 0.0, "mode": "PARENT_RELATIVE_QUATERNION"}
    index_points = _finger_virtual_fist_polyline(
        arm_obj, chains.get("index", []), "index", palm_frame, curl_driver_multiplier
    )
    middle_points = _finger_virtual_fist_polyline(
        arm_obj, chains.get("middle", []), "middle", palm_frame, curl_driver_multiplier
    )
    if len(index_points) < 2 or len(middle_points) < 2:
        return {"rows": [], "quality": 0.0, "mode": "PARENT_RELATIVE_QUATERNION"}

    def _avg(rows, fallback):
        rows = [p for p in rows if p is not None]
        return (sum(rows, Vector((0.0, 0.0, 0.0))) / len(rows)) if rows else fallback.copy()

    palm_normal = palm_frame["normal"].copy()
    palm_forward = palm_frame["forward"].copy()
    if palm_normal.length < 1.0e-8:
        palm_normal = Vector((0.0, 0.0, 1.0))
    if palm_forward.length < 1.0e-8:
        palm_forward = Vector((0.0, 1.0, 0.0))
    palm_normal.normalize(); palm_forward.normalize()

    thumb_bones = [arm_obj.data.bones.get(n) for n in thumb_chain]
    while len(thumb_bones) < 3:
        thumb_bones.append(None)
    root = thumb_bones[0]
    if not root:
        return {"rows": [], "quality": 0.0, "mode": "PARENT_RELATIVE_QUATERNION"}
    root_len = max(float(root.length), 1.0e-6)

    # These bands live on the *closed* index/middle pose. The root aims to the
    # upper proximal band, MCP crosses the folded proximal/intermediate area,
    # and IP only tucks enough to finish the outside contact.
    contact_a = _avg([
        _finger_polyline_point(index_points, 0, 0.28),
        _finger_polyline_point(middle_points, 0, 0.28),
    ], palm_frame["center"])
    contact_b = _avg([
        _finger_polyline_point(index_points, 0, 0.70),
        _finger_polyline_point(middle_points, 0, 0.70),
        _finger_polyline_point(index_points, 1, 0.18),
        _finger_polyline_point(middle_points, 1, 0.18),
    ], contact_a)
    contact_c = _avg([
        _finger_polyline_point(index_points, 1, 0.42),
        _finger_polyline_point(middle_points, 1, 0.42),
    ], contact_b)

    # Progressive contact depth keeps the root slightly outside
    # clearance, while MCP and especially IP sit increasingly closer to the
    # folded-finger surface. This changes contact position only; the Armature-X
    # while preserving the projected bend/quaternion solution.
    contact_a += palm_normal * (root_len * float(SR_THUMB_FIST_OUTSIDE_ROOT))
    contact_b += palm_normal * (root_len * float(SR_THUMB_FIST_OUTSIDE_MCP))
    contact_c += palm_normal * (root_len * float(SR_THUMB_FIST_OUTSIDE_IP))

    rows = []
    prev = root.head_local.copy()
    contacts = (contact_a, contact_b, contact_c)
    normal_blends = (
        float(SR_THUMB_FIST_ROOT_NORMAL_BLEND),
        float(SR_THUMB_FIST_MCP_NORMAL_BLEND),
        float(SR_THUMB_FIST_IP_NORMAL_BLEND),
    )
    pronations = (
        float(SR_THUMB_FIST_ROOT_PRONATION),
        float(SR_THUMB_FIST_MCP_PRONATION),
        float(SR_THUMB_FIST_IP_PRONATION),
    )

    for i, source_bone in enumerate(thumb_bones[:3]):
        if not source_bone:
            break
        aim = contacts[i] - prev
        if aim.length < 1.0e-8:
            aim = source_bone.tail_local - source_bone.head_local
        if aim.length < 1.0e-8:
            aim = palm_forward.copy()
        aim.normalize()

        # Root remains mostly transverse across the palm; later joints may tuck
        # progressively toward the palm-facing side of the closed fist.
        planar = aim - palm_normal * aim.dot(palm_normal)
        if planar.length > 1.0e-8:
            planar.normalize()
            nb = normal_blends[i]
            normal_sign = 1.0 if aim.dot(palm_normal) >= 0.0 else -1.0
            aim = planar * (1.0 - nb) + palm_normal * (normal_sign * nb)
            if aim.length > 1.0e-8:
                aim.normalize()

        # The final thumb phalanx needs a visible IP closure arc.
        # Clamp its angle relative to the preceding Fist target rather than
        # accepting a near-collinear contact vector. The bend hemisphere still
        # comes from the 3D contact target, so mirrored hands remain symmetric.
        if i == 2 and rows:
            parent_dir = rows[-1]["tail"] - rows[-1]["head"]
            if parent_dir.length > 1.0e-8:
                parent_dir.normalize()
                contact_dir = aim.copy()
                dot = max(-1.0, min(1.0, parent_dir.dot(contact_dir)))
                bend = math.acos(dot)
                desired_bend = min(max(bend, float(SR_THUMB_FIST_IP_MIN_BEND)), float(SR_THUMB_FIST_IP_MAX_BEND))
                if abs(desired_bend - bend) > math.radians(0.25):
                    axis = parent_dir.cross(contact_dir)
                    if axis.length < 1.0e-8:
                        axis = parent_dir.cross(palm_normal)
                    if axis.length < 1.0e-8:
                        axis = parent_dir.cross(palm_forward)
                    if axis.length > 1.0e-8:
                        axis.normalize()
                        plus = Matrix.Rotation(desired_bend, 4, axis) @ parent_dir
                        minus = Matrix.Rotation(-desired_bend, 4, axis) @ parent_dir
                        # Pick the hemisphere that remains closest to the actual
                        # contact direction. This guards left/right mirroring.
                        aim = plus if plus.dot(contact_dir) >= minus.dot(contact_dir) else minus
                        if aim.length > 1.0e-8:
                            aim.normalize()

        length = max(float(source_bone.length), 1.0e-6)
        head = prev.copy()
        tail = head + aim * length
        roll_ref = _finger_thumb_target_roll_ref(source_bone, aim, palm_normal, pronations[i])
        rows.append({
            "source": source_bone.name,
            "head": head,
            "tail": tail,
            "roll_ref": roll_ref,
            "contact": contacts[i].copy(),
        })
        prev = tail.copy()

    return {
        "rows": rows,
        "quality": float(min(1.0, len(rows) / 3.0)),
        "mode": "PARENT_RELATIVE_QUATERNION",
    }

def _finger_thumb_target_armature_matrix(row, palm_frame):
    """Build an armature-space bone matrix from one geometric fist target row.

    Blender bones point along local +Y. ``roll_ref`` is treated as the desired
    local +Z reference after projecting it off the aim direction.
    """
    head = row.get("head", Vector((0.0, 0.0, 0.0))).copy()
    tail = row.get("tail", head + Vector((0.0, 1.0, 0.0))).copy()
    y_axis = tail - head
    if y_axis.length < 1.0e-8:
        y_axis = palm_frame.get("forward", Vector((0.0, 1.0, 0.0))).copy()
    if y_axis.length < 1.0e-8:
        y_axis = Vector((0.0, 1.0, 0.0))
    y_axis.normalize()

    z_axis = row.get("roll_ref", palm_frame.get("normal", Vector((0.0, 0.0, 1.0)))).copy()
    z_axis = z_axis - y_axis * z_axis.dot(y_axis)
    if z_axis.length < 1.0e-8:
        z_axis = palm_frame.get("normal", Vector((0.0, 0.0, 1.0))).copy()
        z_axis = z_axis - y_axis * z_axis.dot(y_axis)
    if z_axis.length < 1.0e-8:
        z_axis = y_axis.cross(Vector((1.0, 0.0, 0.0)))
    if z_axis.length < 1.0e-8:
        z_axis = y_axis.cross(Vector((0.0, 0.0, 1.0)))
    z_axis.normalize()

    x_axis = y_axis.cross(z_axis)
    if x_axis.length < 1.0e-8:
        x_axis = Vector((1.0, 0.0, 0.0))
    x_axis.normalize()
    z_axis = x_axis.cross(y_axis)
    if z_axis.length < 1.0e-8:
        z_axis = Vector((0.0, 0.0, 1.0))
    z_axis.normalize()

    return Matrix((
        (x_axis.x, y_axis.x, z_axis.x, head.x),
        (x_axis.y, y_axis.y, z_axis.y, head.y),
        (x_axis.z, y_axis.z, z_axis.z, head.z),
        (0.0,      0.0,      0.0,      1.0),
    ))

def _finger_thumb_parent_relative_quaternions(arm_obj, chains, palm_frame, geometry):
    """Convert absolute fist target orientations into local thumb joint deltas.

    Target bones are rest-clones of Thumb0/1/2 with a parallel parent chain.
    For child joints the baseline already includes the parent's desired fist
    orientation, so the returned quaternion is the *relative* MCP/IP rotation,
    not another absolute armature-space orientation.
    """
    rows = list((geometry or {}).get("rows", []))
    thumb_chain = list((chains or {}).get("thumb", []))[:len(rows)]
    if not rows or not thumb_chain:
        return []

    rest_mats = []
    desired_mats = []
    for i, row in enumerate(rows):
        source_name = thumb_chain[i] if i < len(thumb_chain) else row.get("source", "")
        source = arm_obj.data.bones.get(source_name or "")
        if not source:
            break
        rest_mats.append(source.matrix_local.copy())
        desired_mats.append(_finger_thumb_target_armature_matrix(row, palm_frame))

    count = min(len(rest_mats), len(desired_mats))
    if not count:
        return []

    quats = []
    for i in range(count):
        if i == 0:
            # Root target and owner have the same rest bone and the same source
            # parent, so its local fist delta is rest^-1 × desired.
            baseline = rest_mats[i].copy()
        else:
            # Preserve the authored rest relationship from parent to child, but
            # evaluate it beneath the parent's DESIRED fist orientation.
            rest_rel = rest_mats[i - 1].inverted_safe() @ rest_mats[i]
            baseline = desired_mats[i - 1] @ rest_rel
        delta = baseline.inverted_safe() @ desired_mats[i]
        try:
            q = delta.to_quaternion()
            q.normalize()
        except Exception:
            q = Matrix.Identity(3).to_quaternion()
        quats.append(q)
    return quats

def _finger_thumb_enforce_bend_hemisphere(
    arm_obj, chains, palm_frame, quats,
    thumb_curl_multiplier=1.0, enabled=False, armature_axis=(1.0, 0.0, 0.0)
):
    """Keep MCP/IP fist bend on the projected Armature-X inward hemisphere.

    The requested Armature-X axis is projected through each source bone's rest
    orientation. The local quaternion is swing/twist decomposed around that
    projected axis; only bend twist is corrected, preserving opposition and
    pronation from the contact solve.
    """
    from mathutils import Quaternion

    rows = list(quats or [])
    if not rows:
        return [], 0, [], []
    if not enabled:
        return [q.copy() for q in rows], 0, ["RAW" for _ in rows], []

    thumb_chain = list((chains or {}).get("thumb", []))[:len(rows)]
    corrected = []
    corrections = 0
    labels = []
    projections = []

    for i, q in enumerate(rows):
        qq = q.copy()
        if i >= len(thumb_chain):
            corrected.append(qq)
            labels.append("RAW")
            projections.append("")
            continue

        bone = arm_obj.data.bones.get(thumb_chain[i])
        local_axis = _finger_thumb_project_armature_axis_local(bone, armature_axis)
        inward_sign, inward_conf = _finger_thumb_armature_bend_sign(
            bone, palm_frame, armature_axis
        )
        expected_sign = inward_sign * float(thumb_curl_multiplier)
        expected_sign = 1.0 if expected_sign >= 0.0 else -1.0
        projections.append(
            f"({local_axis.x:+.2f},{local_axis.y:+.2f},{local_axis.z:+.2f})"
        )

        changed = False
        if i >= 1:
            try:
                raw_angle, twist = _finger_thumb_signed_twist_about_axis(
                    qq, local_axis
                )
                # q = swing @ twist. Replace only twist, leaving contact roll /
                # pronation in the swing portion untouched.
                swing = qq @ twist.inverted()
                min_mag = float(
                    SR_THUMB_FIST_MCP_HEMISPHERE_MIN
                    if i == 1 else SR_THUMB_FIST_IP_HEMISPHERE_MIN
                )
                max_mag = math.radians(82.0 if i == 1 else 62.0)
                mag = min(max(abs(raw_angle), min_mag), max_mag)
                desired_angle = expected_sign * mag

                if abs(raw_angle - desired_angle) > math.radians(0.25):
                    desired_twist = Quaternion(local_axis, desired_angle)
                    qq = swing @ desired_twist
                    qq.normalize()
                    changed = True
            except Exception:
                qq = q.copy()
                changed = False

        corrected.append(qq)
        if changed:
            corrections += 1
            labels.append("ARM_X_CORRECTED")
        else:
            labels.append("ARM_X_OK" if i >= 1 else "CMC_FREE")

    return corrected, corrections, labels, projections

def _finger_thumb_opposite_side_bone_name(arm_obj, bone_name, wanted_side="L"):
    """Find the opposite-side partner for common MMD/game suffix conventions."""
    name = str(bone_name or "")
    side = str(wanted_side).upper()
    replacements = (
        (".R", ".L"), (".L", ".R"),
        ("_R", "_L"), ("_L", "_R"),
        ("-R", "-L"), ("-L", "-R"),
        (" R", " L"), (" L", " R"),
    )
    candidates = []
    for old, new in replacements:
        if old in name:
            candidate = name.replace(old, new)
            if side == "L" and (".L" in candidate or "_L" in candidate or "-L" in candidate or " L" in candidate):
                candidates.append(candidate)
            elif side == "R" and (".R" in candidate or "_R" in candidate or "-R" in candidate or " R" in candidate):
                candidates.append(candidate)

    # Japanese side prefixes are less common in some converted rigs but are
    # cheap to support.
    if side == "L" and "右" in name:
        candidates.append(name.replace("右", "左"))
    elif side == "R" and "左" in name:
        candidates.append(name.replace("左", "右"))

    for candidate in candidates:
        if arm_obj.data.bones.get(candidate):
            return candidate
    return ""

def _finger_thumb_mirror_local_quaternion(arm_obj, left_bone_name, right_bone_name, left_q):
    """Mirror a left LOCAL pose delta to the right thumb using rest matrices.

    A local quaternion cannot safely be mirrored by changing component signs
    because imported thumb bone rolls differ. Convert the local delta to
    armature space through the left rest basis, reflect it across armature X,
    then express the mirrored rotation in the right rest basis.
    """
    left_bone = arm_obj.data.bones.get(left_bone_name or "")
    right_bone = arm_obj.data.bones.get(right_bone_name or "")
    if not left_bone or not right_bone:
        return left_q.copy()

    try:
        left_basis = left_bone.matrix_local.to_3x3()
        right_basis = right_bone.matrix_local.to_3x3()
        left_delta_arm = left_basis @ left_q.to_matrix() @ left_basis.inverted_safe()

        mirror_x = Matrix((
            (-1.0, 0.0, 0.0),
            ( 0.0, 1.0, 0.0),
            ( 0.0, 0.0, 1.0),
        ))
        right_delta_arm = mirror_x @ left_delta_arm @ mirror_x
        right_delta_local = right_basis.inverted_safe() @ right_delta_arm @ right_basis

        q = right_delta_local.to_quaternion()
        q.normalize()
        return q
    except Exception:
        return left_q.copy()

def _finger_thumb_apply_fist_quaternion_calibration(
    arm_obj, chains, side, profile_id, quats
):
    """Apply the validated UMA fist calibration to 親指１.

    The left side uses the stored normalized quaternion. The right side receives
    the mirrored armature-space rotation converted into its authored local rest
    basis.
    """
    from mathutils import Quaternion

    rows = [q.copy() for q in (quats or [])]
    if profile_id != "UMA_MUSUME_MMD_HYBRID":
        return rows, False, "", ""

    joint_index = int(SR_UMA_THUMB_FIST_CALIBRATED_JOINT_INDEX)
    thumb_chain = list((chains or {}).get("thumb", []))
    if joint_index >= len(rows) or joint_index >= len(thumb_chain):
        return rows, False, "", ""

    source_name = str(thumb_chain[joint_index])
    if SR_UMA_THUMB_FIST_CALIBRATED_SOURCE_TOKEN not in source_name:
        return rows, False, source_name, "SOURCE_MISMATCH"

    q_left = Quaternion(SR_UMA_THUMB_FIST_CALIBRATED_LEFT_QUATERNION)
    q_left.normalize()

    suffix = str(side).upper()
    if suffix == "L":
        q_final = q_left
        mode = "LEFT_REFERENCE"
    else:
        left_name = _finger_thumb_opposite_side_bone_name(
            arm_obj, source_name, wanted_side="L"
        )
        if left_name:
            q_final = _finger_thumb_mirror_local_quaternion(
                arm_obj, left_name, source_name, q_left
            )
            mode = "MIRRORED_FROM_LEFT"
        else:
            # Better to preserve the procedural right solve than inject a
            # left-local quaternion into a potentially different right basis.
            return rows, False, source_name, "RIGHT_MIRROR_SOURCE_MISSING"

    rows[joint_index] = q_final.copy()
    label = (
        f"{mode} "
        f"W{q_final.w:+.3f} X{q_final.x:+.3f} "
        f"Y{q_final.y:+.3f} Z{q_final.z:+.3f}"
    )
    return rows, True, source_name, label

def _finger_apply_thumb_target_pose(arm_obj, target_names, quats):
    """Store the fist target as local quaternion pose deltas on hidden MCHs."""
    applied = 0
    angles = []
    for i, target_name in enumerate(target_names):
        if i >= len(quats):
            break
        pb = arm_obj.pose.bones.get(target_name)
        if not pb:
            continue
        q = quats[i].copy()
        try:
            q.normalize()
        except Exception:
            pass
        pb.rotation_mode = 'QUATERNION'
        try:
            pb.location = (0.0, 0.0, 0.0)
            pb.scale = (1.0, 1.0, 1.0)
            pb.rotation_quaternion = q
        except Exception:
            continue
        applied += 1
        try:
            angles.append(float(math.degrees(q.angle)))
        except Exception:
            angles.append(0.0)
    return applied, angles

def _finger_add_constraint_influence_driver(arm_obj, constraint, ctrl_name, prop_name="fist", expression=None):
    """Drive a generated constraint influence from a finger master property.

    ``expression`` may be supplied for a shaped transition. The thumb uses a
    uniform Fist blend because each target represents a parent-relative joint
    quaternion.
    """
    try:
        fc = constraint.driver_add("influence")
    except Exception as exc:
        raise RuntimeError(f"Could not add finger constraint driver on {constraint.name}: {exc}")
    drv = fc.driver
    drv.type = 'SCRIPTED'
    while drv.variables:
        drv.variables.remove(drv.variables[0])
    var = drv.variables.new()
    var.name = "fi"
    var.type = 'SINGLE_PROP'
    target = var.targets[0]
    target.id = arm_obj
    target.data_path = f'pose.bones["{ctrl_name}"]["{prop_name}"]'
    drv.expression = str(expression) if expression else "min(max(fi,0.0),1.0)"
    return fc

def _finger_thumb_contact_target_solve(arm_obj, chains, palm_frame, thumb_diags, curl_driver_multiplier=1.0):
    """Solve a 3D thumb fist pose from contact targets instead of raw presets.

    The solver uses three spatial goals inspired by real fists:
    - the thumb root (CMC / metacarpal) opposes toward the index+middle
      knuckle band rather than curling downward like a fifth finger;
    - the thumb MCP crosses outside the curled fingers;
    - the thumb IP closes only enough to finish the wrap.

    Returned joint angles are direct signed local-axis amplitudes ready to be
    fed into the existing driver pipeline. The root spread/opposition remains a
    dedicated second axis on the first thumb segment.
    """
    chain = list(chains.get("thumb", []))
    if not chain:
        return {
            "joint_curls": [0.0, 0.0, 0.0],
            "root_spread": 0.0,
            "wrap_confidence": 0.0,
            "curl_confidence": 0.0,
            "mode": "CONTACT_TARGET_3D",
        }

    def _bone(name):
        return arm_obj.data.bones.get(name or "")

    def _dir(bone, fallback=None):
        if not bone:
            return fallback.copy() if fallback is not None else Vector((0.0, 1.0, 0.0))
        d = bone.tail_local - bone.head_local
        if d.length < 1.0e-8:
            return fallback.copy() if fallback is not None else Vector((0.0, 1.0, 0.0))
        d.normalize()
        return d

    def _point_on_chain(finger, segment_index, factor):
        segs = list(chains.get(finger, []))
        if not segs:
            return None
        idx = min(max(int(segment_index), 0), len(segs) - 1)
        bone = _bone(segs[idx])
        if not bone:
            return None
        d = bone.tail_local - bone.head_local
        if d.length < 1.0e-8:
            return bone.head_local.copy()
        d.normalize()
        return bone.head_local + d * (float(bone.length) * float(factor))

    thumb_bones = [_bone(name) for name in chain[:3]]
    if not thumb_bones or not thumb_bones[0]:
        return {
            "joint_curls": [0.0, 0.0, 0.0],
            "root_spread": 0.0,
            "wrap_confidence": 0.0,
            "curl_confidence": 0.0,
            "mode": "CONTACT_TARGET_3D",
        }
    while len(thumb_bones) < 3:
        thumb_bones.append(None)
    while len(thumb_diags) < 3:
        thumb_diags.append({"curl_axis": 0, "curl_sign": 1.0, "spread_axis": 2, "spread_sign": 1.0})

    palm_center = palm_frame["center"].copy()
    palm_forward = palm_frame["forward"].copy()
    palm_normal = palm_frame["normal"].copy()
    if palm_forward.length < 1.0e-8:
        palm_forward = Vector((0.0, 1.0, 0.0))
    if palm_normal.length < 1.0e-8:
        palm_normal = Vector((0.0, 0.0, 1.0))
    palm_forward.normalize(); palm_normal.normalize()

    root = thumb_bones[0]
    root_dir = _dir(root, palm_forward)
    root_len = max(float(root.length), 1.0e-6)

    # Contact references from index/middle. We prefer the outer knuckle band for
    # opposition, then progressively deeper zones for MCP/IP wrap.
    knuckle_rows = [
        _point_on_chain("index", 0, 0.18), _point_on_chain("middle", 0, 0.18),
        _point_on_chain("index", 0, 0.42), _point_on_chain("middle", 0, 0.42),
    ]
    knuckle_rows = [p for p in knuckle_rows if p is not None]
    fold_rows = [
        _point_on_chain("index", 0, 0.68), _point_on_chain("middle", 0, 0.68),
        _point_on_chain("index", 1, 0.34), _point_on_chain("middle", 1, 0.34),
    ]
    fold_rows = [p for p in fold_rows if p is not None]
    tip_rows = [
        _point_on_chain("index", 1, 0.70), _point_on_chain("middle", 1, 0.70),
        _point_on_chain("index", 0, 0.92), _point_on_chain("middle", 0, 0.92),
    ]
    tip_rows = [p for p in tip_rows if p is not None]

    knuckle_band = sum(knuckle_rows, Vector((0.0, 0.0, 0.0))) / len(knuckle_rows) if knuckle_rows else palm_center.copy()
    fold_band = sum(fold_rows, Vector((0.0, 0.0, 0.0))) / len(fold_rows) if fold_rows else knuckle_band.copy()
    tip_band = sum(tip_rows, Vector((0.0, 0.0, 0.0))) / len(tip_rows) if tip_rows else fold_band.copy()

    inward = palm_center - root.head_local
    inward = inward - palm_normal * inward.dot(palm_normal)
    if inward.length < 1.0e-8:
        inward = knuckle_band - root.head_local
        inward = inward - palm_normal * inward.dot(palm_normal)
    if inward.length < 1.0e-8:
        inward = fold_band - root.head_local
    if inward.length < 1.0e-8:
        inward = palm_frame["across"].copy()
    if inward.length < 1.0e-8:
        inward = Vector((1.0, 0.0, 0.0))
    inward.normalize()

    target_root = (
        knuckle_band * float(SR_THUMB_FIST_TARGET_ROOT_BLEND)
        + palm_center * float(SR_THUMB_FIST_TARGET_PALM_BLEND)
        + palm_forward * (root_len * float(SR_THUMB_FIST_TARGET_FORWARD_OFFSET))
        + inward * (root_len * float(SR_THUMB_FIST_TARGET_INWARD_OFFSET))
    )

    spread_axis = int(thumb_diags[0].get("spread_axis", 2))
    curl_axis = int(thumb_diags[0].get("curl_axis", 0))
    spread_axis_vec = _finger_local_axis(root, spread_axis)
    curl_axis_vec = _finger_local_axis(root, curl_axis)

    desired_root = target_root - root.head_local
    if desired_root.length < 1.0e-8:
        desired_root = knuckle_band - root.head_local
    if desired_root.length < 1.0e-8:
        desired_root = palm_center - root.head_local
    if desired_root.length < 1.0e-8:
        desired_root = inward.copy()
    desired_root.normalize()

    raw_spread = _finger_signed_angle_about_axis(root_dir, desired_root, spread_axis_vec)
    spread_sign = 1.0 if raw_spread >= 0.0 else -1.0
    spread_mag = abs(raw_spread)
    if spread_mag < math.radians(2.0):
        spread_mag = float(SR_UMA_FIST_THUMB_OPPOSITION)
    spread_mag = min(max(spread_mag * 0.92, float(SR_FINGER_FIST_THUMB_OPPOSITION_MIN)), float(max(SR_FINGER_FIST_THUMB_OPPOSITION_MAX, SR_UMA_FIST_THUMB_OPPOSITION)))
    root_spread = spread_sign * spread_mag

    rotated_root = root_dir.copy()
    if spread_axis_vec.length > 1.0e-8 and abs(root_spread) > 1.0e-8:
        rotated_root = Matrix.Rotation(root_spread, 4, spread_axis_vec.normalized()) @ rotated_root
        if rotated_root.length > 1.0e-8:
            rotated_root.normalize()

    root_ref_sign = float(thumb_diags[0].get("curl_sign", 1.0)) * float(curl_driver_multiplier)
    root_ref = float(SR_THUMB_FIST_ROOT_REFERENCE) * root_ref_sign
    root_ref_sign = 1.0 if root_ref >= 0.0 else -1.0
    root_curl_raw = _finger_signed_angle_about_axis(rotated_root, desired_root, curl_axis_vec)
    root_curl_mag = abs(root_curl_raw)
    if root_curl_mag < math.radians(1.5):
        root_curl_geom = root_ref
    else:
        root_curl_geom = root_ref_sign * min(max(root_curl_mag, float(SR_THUMB_FIST_ROOT_CURL_MINMAG)), float(SR_THUMB_FIST_ROOT_CURL_MAXMAG))
    root_curl = (1.0 - float(SR_THUMB_FIST_ROOT_BLEND)) * root_ref + float(SR_THUMB_FIST_ROOT_BLEND) * root_curl_geom

    joint_targets = [root_curl]
    curl_conf_rows = []
    joint_rows = [
        (1, thumb_bones[1], fold_band, float(SR_THUMB_FIST_MCP_REFERENCE), float(SR_THUMB_FIST_MCP_FLEX_MIN), float(SR_THUMB_FIST_MCP_FLEX_MAX), float(SR_THUMB_FIST_MCP_BLEND)),
        (2, thumb_bones[2], tip_band, float(SR_THUMB_FIST_IP_REFERENCE), float(SR_THUMB_FIST_IP_FLEX_MIN), float(SR_THUMB_FIST_IP_FLEX_MAX), float(SR_THUMB_FIST_IP_BLEND)),
    ]
    prev_target = target_root
    for joint_index, bone, band, ref_mag, min_mag, max_mag, blend in joint_rows:
        if not bone:
            joint_targets.append(joint_targets[-1])
            continue
        current_dir = _dir(bone, root_dir)
        desired_point = prev_target.lerp(band, float(SR_THUMB_FIST_CONTACT_MCP_BLEND if joint_index == 1 else SR_THUMB_FIST_CONTACT_TIP_BLEND))
        desired_point = desired_point + inward * (float(bone.length) * (0.10 if joint_index == 1 else 0.06))
        desired_dir = desired_point - bone.head_local
        if desired_dir.length < 1.0e-8:
            desired_dir = band - bone.head_local
        if desired_dir.length < 1.0e-8:
            desired_dir = current_dir.copy()
        desired_dir.normalize()
        diag = thumb_diags[joint_index] if joint_index < len(thumb_diags) else {"curl_axis": 0, "curl_sign": 1.0}
        axis_vec = _finger_local_axis(bone, int(diag.get("curl_axis", 0)))
        raw = _finger_signed_angle_about_axis(current_dir, desired_dir, axis_vec)
        preferred_sign = float(diag.get("curl_sign", 1.0)) * float(curl_driver_multiplier)
        if preferred_sign >= 0.0:
            geom = preferred_sign * min(max(abs(raw), min_mag), max_mag) if abs(raw) > 1.0e-8 else preferred_sign * ref_mag
            ref = preferred_sign * ref_mag
        else:
            geom = preferred_sign * min(max(abs(raw), min_mag), max_mag) if abs(raw) > 1.0e-8 else preferred_sign * ref_mag
            ref = preferred_sign * ref_mag
        target = (1.0 - blend) * ref + blend * geom
        joint_targets.append(target)
        curl_conf_rows.append(max(0.0, min(1.0, abs(raw) / max(max_mag, 1.0e-6))))
        prev_target = desired_point

    wrap_conf = max(0.0, min(1.0, abs(raw_spread) / max(float(max(SR_FINGER_FIST_THUMB_OPPOSITION_MAX, SR_UMA_FIST_THUMB_OPPOSITION)), 1.0e-6)))
    root_conf = max(0.0, min(1.0, abs(root_curl_raw) / max(float(SR_THUMB_FIST_ROOT_CURL_MAXMAG), 1.0e-6)))
    if curl_conf_rows:
        curl_conf = (root_conf + sum(curl_conf_rows)) / (1.0 + len(curl_conf_rows))
    else:
        curl_conf = root_conf
    return {
        "joint_curls": joint_targets[:3],
        "root_spread": float(root_spread),
        "wrap_confidence": float(wrap_conf),
        "curl_confidence": float(curl_conf),
        "mode": "CONTACT_TARGET_3D",
    }

def _finger_set_prop(pb, name, default, minimum, maximum, description):
    pb[name] = float(default)
    try:
        pb.id_properties_ui(name).update(
            min=float(minimum), max=float(maximum), soft_min=float(minimum), soft_max=float(maximum),
            description=str(description),
        )
    except Exception:
        pass

def _finger_master_properties(pb):
    _finger_set_prop(pb, "fist", 0.0, 0.0, 1.0, "Close all fingers into a fist")
    _finger_set_prop(pb, "curl", 0.0, -0.25, 1.0, "Curl the four main fingers together")
    _finger_set_prop(pb, "spread", 0.0, -1.0, 1.0, "Spread or close the four main fingers")
    _finger_set_prop(pb, "thumb_curl", 0.0, -0.25, 1.0, "Curl the thumb")
    _finger_set_prop(pb, "thumb_spread", 0.0, -1.0, 1.0, "Move the thumb away from or toward the palm")
    for finger in ("index", "middle", "ring", "pinky"):
        _finger_set_prop(pb, f"{finger}_curl", 0.0, -0.35, 1.0, f"Individual {finger} curl")

def _finger_remove_driver_curves(arm_obj):
    removed = 0
    ad = getattr(arm_obj, "animation_data", None)
    drivers = getattr(ad, "drivers", None) if ad else None
    if drivers is not None:
        for fc in list(drivers):
            if "SR_MCH_Finger_" in str(getattr(fc, "data_path", "")):
                try:
                    drivers.remove(fc); removed += 1
                except Exception:
                    pass
    action = getattr(ad, "action", None) if ad else None
    if action:
        for fc in list(action.fcurves):
            path = str(getattr(fc, "data_path", ""))
            if "SR_CTRL_Fingers." in path or "SR_MCH_Finger_" in path:
                try:
                    action.fcurves.remove(fc)
                except Exception:
                    pass
    return removed

def _finger_add_rotation_driver(arm_obj, pb, axis_index, base_value, terms):
    """Drive one MCH local Euler axis from properties on SR_CTRL_Fingers.*."""
    try:
        fc = pb.driver_add("rotation_euler", int(axis_index))
    except Exception as exc:
        raise RuntimeError(f"Could not add finger driver on {pb.name}: {exc}")
    drv = fc.driver
    drv.type = 'SCRIPTED'
    while drv.variables:
        drv.variables.remove(drv.variables[0])
    expr_parts = [f"({float(base_value):.10f})"]
    for var_name, ctrl_name, prop_name, coefficient in terms:
        var = drv.variables.new()
        var.name = str(var_name)
        var.type = 'SINGLE_PROP'
        target = var.targets[0]
        target.id = arm_obj
        target.data_path = f'pose.bones["{ctrl_name}"]["{prop_name}"]'
        expr_parts.append(f"({var.name}*{float(coefficient):.10f})")
    drv.expression = "+".join(expr_parts)
    return fc

def _finger_add_expression_driver(arm_obj, pb, axis_index, base_value, variables, delta_expression):
    """Drive one Euler axis with a clamped/blended procedural expression."""
    try:
        fc = pb.driver_add("rotation_euler", int(axis_index))
    except Exception as exc:
        raise RuntimeError(f"Could not add finger expression driver on {pb.name}: {exc}")
    drv = fc.driver
    drv.type = 'SCRIPTED'
    while drv.variables:
        drv.variables.remove(drv.variables[0])
    for var_name, ctrl_name, prop_name in variables:
        var = drv.variables.new()
        var.name = str(var_name)
        var.type = 'SINGLE_PROP'
        target = var.targets[0]
        target.id = arm_obj
        target.data_path = f'pose.bones["{ctrl_name}"]["{prop_name}"]'
    drv.expression = f"({float(base_value):.10f})+({delta_expression})"
    return fc

def _remove_finger_controls(context, arm_obj):
    _finger_remove_driver_curves(arm_obj)
    removed_constraints = _remove_constraints(arm_obj, "SR_FINGERS_")
    removed_bones = _remove_generated_bones(context, arm_obj, component=SR_FINGER_COMPONENT)
    for key in list(arm_obj.keys()):
        if str(key).startswith("saberrig_fingers_"):
            try:
                del arm_obj[key]
            except Exception:
                pass
    _cleanup_unused_widgets()
    return removed_bones, removed_constraints

def _build_finger_side(context, arm_obj, analysis, side):
    suffix = str(side).upper()
    chains = _finger_chains_for_side(analysis, suffix)
    if not chains:
        raise RuntimeError(f"{suffix}: no semantic finger chains were resolved")
    hand_name = _semantic_bone(analysis, f"hand.{suffix}")
    hand_data = arm_obj.data.bones.get(hand_name or "")
    if not hand_data:
        raise RuntimeError(f"{suffix}: semantic hand bone is missing")

    source_names = [n for chain in chains.values() for n in chain]
    animated = [n for n in source_names if _wiggle_has_authored_animation(arm_obj, n)]
    if animated:
        raise RuntimeError(f"{suffix}: authored finger animation detected on {animated[0]}; bake/remove it before procedural finger controls")
    conflicts = _source_constraint_conflicts(arm_obj, source_names)
    if conflicts:
        raise RuntimeError(f"{suffix}: existing finger transform constraint requires compatibility handling: {conflicts[0]}")

    frame = _finger_palm_frame(arm_obj, analysis, suffix)
    profile_id = str((analysis or {}).get("detection", {}).get("profile_id", ""))
    curl_driver_multiplier = float(SR_FINGER_CURL_DRIVER_MULTIPLIER.get(profile_id, 1.0))
    thumb_curl_driver_multiplier = float(SR_THUMB_CURL_DRIVER_MULTIPLIER.get(profile_id, 1.0))
    thumb_bend_armature_axis = SR_THUMB_BEND_ARMATURE_AXIS.get(profile_id, None)
    thumb_target_geometry = _finger_thumb_fist_target_geometry(
        arm_obj, chains, frame, curl_driver_multiplier
    )
    controls_collection = _find_bone_collection(arm_obj.data, SR_COLLECTION_CONTROLS)
    mechanism_collection = _find_bone_collection(arm_obj.data, SR_COLLECTION_MECHANISM)
    if not controls_collection or not mechanism_collection:
        raise RuntimeError("SaberRig foundation collections are missing")

    # Snapshot current local source pose so rebuilding does not snap an already
    # posed hand back to rest.
    source_basis = {}
    for name in source_names:
        pb = arm_obj.pose.bones.get(name)
        if pb:
            source_basis[name] = pb.matrix_basis.copy()

    names = _finger_control_names(suffix)
    hand_len = max(float(hand_data.length), 1.0e-4)
    ctrl_head = frame["center"] + frame["normal"] * (hand_len * 0.16)
    ctrl_tail = ctrl_head + frame["forward"] * max(hand_len * 0.45, 0.01)

    _activate_armature(context, arm_obj, mode='EDIT')
    eb = arm_obj.data.edit_bones
    ctrl = _new_body_control_edit_bone(
        eb, names["ctrl_master"], head=ctrl_head, tail=ctrl_tail, parent_name=hand_name
    )
    controls_collection.assign(ctrl)

    generated = [names["ctrl_master"]]
    mch_rows = []
    for finger in SR_FINGER_ORDER:
        chain = chains.get(finger, [])
        previous_mch = None
        for i, source_name in enumerate(chain):
            source_edit = eb.get(source_name)
            if not source_edit:
                continue
            if i == 0:
                parent_name = source_edit.parent.name if source_edit.parent else hand_name
            else:
                parent_name = previous_mch
            mch_name = _finger_mch_name(suffix, finger, i)
            mch = _clone_edit_bone(eb, mch_name, source_name, parent_name)
            mechanism_collection.assign(mch)
            previous_mch = mch_name
            mch_rows.append((finger, i, source_name, mch_name))
            generated.append(mch_name)

    # Fist target bones are REST clones of the source thumb and
    # use a parallel hierarchy. Their actual fist pose is applied later as
    # local quaternion matrix_basis/rotation_quaternion values. This makes
    # Thumb1/Thumb2 true relative joints instead of independent absolute aims.
    fist_thumb_targets = []
    thumb_chain = list(chains.get("thumb", []))
    for i, row in enumerate(thumb_target_geometry.get("rows", [])):
        if i >= len(thumb_chain):
            break
        source_name = thumb_chain[i]
        source_edit = eb.get(source_name)
        if not source_edit:
            break
        target_name = _finger_fist_thumb_target_name(suffix, i)
        if i == 0:
            parent_name = source_edit.parent.name if source_edit.parent else hand_name
        else:
            parent_name = fist_thumb_targets[i - 1]
        target_bone = _clone_edit_bone(eb, target_name, source_name, parent_name)
        target_bone.use_connect = False
        target_bone.use_deform = False
        mechanism_collection.assign(target_bone)
        fist_thumb_targets.append(target_name)
        generated.append(target_name)
    bpy.ops.object.mode_set(mode='OBJECT')

    _mark_generated_bone(arm_obj, names["ctrl_master"], "CTRL", f"fingers.{suffix}", hand_name, component=SR_FINGER_COMPONENT)
    for finger, i, source_name, mch_name in mch_rows:
        _mark_generated_bone(arm_obj, mch_name, "MCH", f"finger.{finger}.{suffix}.{i+1}", source_name, component=SR_FINGER_COMPONENT)
    for i, target_name in enumerate(fist_thumb_targets):
        thumb_chain = chains.get("thumb", [])
        source_name = thumb_chain[i] if i < len(thumb_chain) else ""
        _mark_generated_bone(
            arm_obj, target_name, "MCH", f"finger.thumb_fist_target.{suffix}.{i+1}",
            source_name, component=SR_FINGER_COMPONENT
        )

    _activate_armature(context, arm_obj, mode='POSE')
    ctrl_pb = arm_obj.pose.bones.get(names["ctrl_master"])
    if not ctrl_pb:
        raise RuntimeError(f"{suffix}: finger master control could not be initialized")
    ctrl_pb.lock_location = (True, True, True)
    ctrl_pb.lock_rotation = (True, True, True)
    ctrl_pb.lock_scale = (True, True, True)
    _finger_master_properties(ctrl_pb)

    # Convert the absolute closed-fist contact geometry into local joint deltas
    # now that the parallel target hierarchy exists in Pose Mode.
    thumb_relative_quats = _finger_thumb_parent_relative_quaternions(
        arm_obj, chains, frame, thumb_target_geometry
    )
    thumb_relative_quats, thumb_hemi_corrections, thumb_hemi_labels, thumb_axis_projections = _finger_thumb_enforce_bend_hemisphere(
        arm_obj, chains, frame, thumb_relative_quats,
        thumb_curl_multiplier=thumb_curl_driver_multiplier,
        enabled=(profile_id == "UMA_MUSUME_MMD_HYBRID" and thumb_bend_armature_axis is not None),
        armature_axis=thumb_bend_armature_axis or (1.0, 0.0, 0.0),
    )

    # The procedural solve remains responsible for the complete
    # thumb, but 親指１ gets the user-validated final local quaternion at Fist=1.
    thumb_relative_quats, thumb_calibrated, thumb_calibrated_source, thumb_calibration_label = _finger_thumb_apply_fist_quaternion_calibration(
        arm_obj, chains, suffix, profile_id, thumb_relative_quats
    )

    thumb_relative_applied, thumb_relative_angles = _finger_apply_thumb_target_pose(
        arm_obj, fist_thumb_targets, thumb_relative_quats
    )

    curl_conf_rows = []
    spread_conf_rows = []

    # One anatomical flexion plane per complete finger chain.
    chain_planes = {finger: _finger_chain_plane(arm_obj, chain, frame) for finger, chain in chains.items()}
    chain_axis_labels = defaultdict(list)
    thumb_wrap_angle = 0.0
    thumb_wrap_confidence = float(thumb_target_geometry.get("quality", 0.0))
    thumb_root_curl_angle = 0.0
    thumb_root_curl_confidence = float(thumb_target_geometry.get("quality", 0.0))
    thumb_fist_mode = str(thumb_target_geometry.get("mode", "FIST_SPACE_QUATERNION"))

    for finger, i, source_name, mch_name in mch_rows:
        source_pb = arm_obj.pose.bones.get(source_name)
        mch_pb = arm_obj.pose.bones.get(mch_name)
        if not source_pb or not mch_pb:
            continue
        try:
            mch_pb.matrix_basis = source_basis.get(source_name, source_pb.matrix_basis).copy()
        except Exception:
            pass
        mch_pb.rotation_mode = 'XYZ'
        base_euler = mch_pb.matrix_basis.to_euler('XYZ')
        try:
            mch_pb.rotation_euler = base_euler
        except Exception:
            pass

        plane = chain_planes.get(finger, {"axis": frame["across"], "confidence": 0.0})
        diag = _finger_chain_axis_diagnostics(arm_obj, source_name, plane, frame, root=(i == 0))
        thumb_projected_axis_local = None
        thumb_projected_sign = 1.0
        thumb_projected_conf = 0.0
        if finger == "thumb" and thumb_bend_armature_axis is not None:
            source_bone = arm_obj.data.bones.get(source_name)
            thumb_projected_axis_local = _finger_thumb_project_armature_axis_local(
                source_bone, thumb_bend_armature_axis
            )
            thumb_projected_sign, thumb_projected_conf = _finger_thumb_armature_bend_sign(
                source_bone, frame, thumb_bend_armature_axis
            )
        curl_conf_rows.append(
            (thumb_projected_conf if thumb_projected_axis_local is not None else diag["curl_conf"])
            * max(float(plane.get("confidence", 0.0)), 0.5)
        )
        if i == 0:
            spread_conf_rows.append(diag["spread_conf"])
        curl_axis = int(diag["curl_axis"])
        spread_axis = int(diag["spread_axis"])
        curl_sign = float(diag["curl_sign"])
        spread_sign = float(diag["spread_sign"])
        effective_curl_multiplier = thumb_curl_driver_multiplier if finger == "thumb" else curl_driver_multiplier

        curl_angles = SR_FINGER_CURL_ANGLES.get(finger, SR_FINGER_CURL_ANGLES["index"])
        fist_angles = SR_FINGER_FIST_ANGLES.get(finger, SR_FINGER_FIST_ANGLES["index"])
        curl_angle_value = float(curl_angles[min(i, len(curl_angles)-1)])
        fist_angle_value = float(fist_angles[min(i, len(fist_angles)-1)])

        if finger == "thumb" and thumb_projected_axis_local is not None:
            projected_sign = float(thumb_projected_sign) * float(effective_curl_multiplier)
            curl_amp = curl_angle_value * projected_sign
            fist_amp = fist_angle_value * projected_sign
            chain_axis_labels[finger].append(
                f"ARMX→({thumb_projected_axis_local.x:+.2f},"
                f"{thumb_projected_axis_local.y:+.2f},"
                f"{thumb_projected_axis_local.z:+.2f})"
            )
        else:
            axis_letter = 'X' if curl_axis == 0 else 'Z'
            chain_axis_labels[finger].append(
                f"{axis_letter}{'+' if curl_sign * effective_curl_multiplier >= 0.0 else '-'}"
            )
            curl_amp = curl_angle_value * curl_sign * effective_curl_multiplier
            fist_amp = fist_angle_value * curl_sign * effective_curl_multiplier
        # Fist is a target pose, not another additive curl.
        # When Fist rises, manual Curl/Individual Curl fades out and the joint
        # blends toward the dedicated Fist target. Manual controls are clamped.
        thumb_projected_manual = finger == "thumb" and thumb_projected_axis_local is not None
        if thumb_projected_manual:
            vars_curl = [
                ("fi", names["ctrl_master"], "fist"),
                ("tc", names["ctrl_master"], "thumb_curl"),
            ]
            manual = f"min(max(tc,-{SR_FINGER_MANUAL_BACKCURL:.6f}),1.0)"
            # Approximate one rest-local axis-angle using its XYZ components.
            # On some UMA distal thumbs Armature-X projects almost exactly to -Z,
            # so this is exact for the problem joint and remains roll-aware on
            # Thumb0/Thumb1. Fist itself remains exact quaternion swing/twist.
            for projected_channel in range(3):
                coeff = float(thumb_projected_axis_local[projected_channel]) * float(curl_amp)
                extra = ""
                if i == 0 and projected_channel == spread_axis:
                    spread_amp_local = float(SR_FINGER_SPREAD_ANGLES.get(finger, math.radians(8.0))) * spread_sign
                    extra = f" + (1.0-fi)*(sp*0.35+ts)*({spread_amp_local:.10f})"
                vars_axis = list(vars_curl)
                if i == 0 and projected_channel == spread_axis:
                    vars_axis.extend([
                        ("sp", names["ctrl_master"], "spread"),
                        ("ts", names["ctrl_master"], "thumb_spread"),
                    ])
                delta_axis = f"((1.0-fi)*({manual})*({coeff:.10f}){extra})"
                _finger_add_expression_driver(
                    arm_obj, mch_pb, projected_channel,
                    base_euler[projected_channel], vars_axis, delta_axis
                )
        elif finger == "thumb":
            vars_curl = [
                ("fi", names["ctrl_master"], "fist"),
                ("tc", names["ctrl_master"], "thumb_curl"),
            ]
            manual = f"min(max(tc,-{SR_FINGER_MANUAL_BACKCURL:.6f}),1.0)"
            delta = f"((1.0-fi)*({manual})*({curl_amp:.10f}))"
            _finger_add_expression_driver(
                arm_obj, mch_pb, curl_axis, base_euler[curl_axis], vars_curl, delta
            )
        else:
            vars_curl = [
                ("fi", names["ctrl_master"], "fist"),
                ("cu", names["ctrl_master"], "curl"),
                ("ic", names["ctrl_master"], f"{finger}_curl"),
            ]
            manual = f"min(max(cu*{SR_FINGER_GLOBAL_CURL_WEIGHT:.6f}+ic,-{SR_FINGER_MANUAL_BACKCURL:.6f}),1.0)"
            delta = f"((1.0-fi)*({manual})*({curl_amp:.10f}) + fi*({fist_amp:.10f}))"
            _finger_add_expression_driver(
                arm_obj, mch_pb, curl_axis, base_euler[curl_axis], vars_curl, delta
            )

        if i == 0:
            spread_amp = float(SR_FINGER_SPREAD_ANGLES.get(finger, math.radians(8.0))) * spread_sign
            if finger == "thumb" and thumb_projected_manual:
                # Spread was folded into the projected manual driver on the
                # selected root channel above, avoiding two drivers on one RNA
                # array element.
                delta_spread = None
            elif finger == "thumb":
                vars_spread = [
                    ("fi", names["ctrl_master"], "fist"),
                    ("sp", names["ctrl_master"], "spread"),
                    ("ts", names["ctrl_master"], "thumb_spread"),
                ]
                manual_spread = f"(sp*0.35+ts)*({spread_amp:.10f})"
                # The hidden target chain owns the full Fist rotation.
                # Manual Thumb Spread fades away as the quaternion/transform
                # target blend takes over.
                delta_spread = f"((1.0-fi)*({manual_spread}))"
            else:
                vars_spread = [
                    ("fi", names["ctrl_master"], "fist"),
                    ("sp", names["ctrl_master"], "spread"),
                ]
                manual_spread = f"sp*({spread_amp:.10f})"
                fist_spread = -spread_amp * float(SR_FINGER_FIST_FAN.get(finger, 0.12))
                delta_spread = f"((1.0-fi)*({manual_spread}) + fi*({fist_spread:.10f}))"
            if delta_spread is not None:
                _finger_add_expression_driver(
                    arm_obj, mch_pb, spread_axis,
                    base_euler[spread_axis], vars_spread, delta_spread
                )

        if finger == "thumb" and i < len(fist_thumb_targets):
            target_name = fist_thumb_targets[i]
            target_c = _add_copy_rotation(
                mch_pb, arm_obj, target_name, f"SR_FINGERS_FIST_THUMB_TARGET_{suffix}_{i+1}"
            )
            # The target and owner now have matching rest orientations and
            # parallel parents, so copy the JOINT rotation in local space.
            if hasattr(target_c, "target_space"):
                target_c.target_space = 'LOCAL'
            if hasattr(target_c, "owner_space"):
                target_c.owner_space = 'LOCAL'
            target_c.influence = 0.0
            _finger_add_constraint_influence_driver(
                arm_obj, target_c, names["ctrl_master"], "fist"
            )

        c = _add_copy_transforms(source_pb, arm_obj, mch_name, f"SR_FINGERS_DEF_{suffix}_{finger.upper()}_{i+1}")
        c.influence = 1.0

    for finger, labels in chain_axis_labels.items():
        arm_obj[f"saberrig_fingers_chain_axes_{suffix}_{finger}"] = "/".join(labels)
    arm_obj[f"saberrig_fingers_chain_plane_mode_{suffix}"] = "ANATOMICAL_CHAIN"
    arm_obj[f"saberrig_fingers_fist_mode_{suffix}"] = "TARGET_BLEND_CLAMPED"
    arm_obj[f"saberrig_fingers_thumb_fist_mode_{suffix}"] = str(thumb_fist_mode)
    arm_obj[f"saberrig_fingers_thumb_wrap_deg_{suffix}"] = float(math.degrees(thumb_wrap_angle))
    arm_obj[f"saberrig_fingers_thumb_wrap_confidence_{suffix}"] = float(thumb_wrap_confidence)
    arm_obj[f"saberrig_fingers_thumb_root_curl_deg_{suffix}"] = float(math.degrees(thumb_root_curl_angle))
    arm_obj[f"saberrig_fingers_thumb_root_curl_confidence_{suffix}"] = float(thumb_root_curl_confidence)
    arm_obj[f"saberrig_fingers_thumb_target_quality_{suffix}"] = float(thumb_target_geometry.get("quality", 0.0))
    arm_obj[f"saberrig_fingers_thumb_target_count_{suffix}"] = int(len(fist_thumb_targets))
    arm_obj[f"saberrig_fingers_thumb_contact_depth_{suffix}"] = (
        f"root {SR_THUMB_FIST_OUTSIDE_ROOT:.3f} / "
        f"mcp {SR_THUMB_FIST_OUTSIDE_MCP:.3f} / "
        f"ip {SR_THUMB_FIST_OUTSIDE_IP:.3f}"
    )
    arm_obj[f"saberrig_fingers_thumb_relative_applied_{suffix}"] = int(thumb_relative_applied)
    arm_obj[f"saberrig_fingers_thumb_relative_angles_{suffix}"] = "/".join(f"{a:.1f}" for a in thumb_relative_angles)
    arm_obj[f"saberrig_fingers_thumb_curl_multiplier_{suffix}"] = float(thumb_curl_driver_multiplier)
    arm_obj[f"saberrig_fingers_thumb_curl_polarity_{suffix}"] = "UMA_INWARD_POSITIVE" if profile_id == "UMA_MUSUME_MMD_HYBRID" else "GEOMETRIC"
    arm_obj[f"saberrig_fingers_thumb_curl_axis_{suffix}"] = "ARMATURE X → REST-LOCAL" if thumb_bend_armature_axis is not None else "AUTO"
    arm_obj[f"saberrig_fingers_thumb_axis_projection_{suffix}"] = " / ".join(thumb_axis_projections)
    arm_obj[f"saberrig_fingers_thumb_hemisphere_corrections_{suffix}"] = int(thumb_hemi_corrections)
    arm_obj[f"saberrig_fingers_thumb_hemisphere_labels_{suffix}"] = "/".join(thumb_hemi_labels)
    arm_obj[f"saberrig_fingers_thumb_calibrated_{suffix}"] = bool(thumb_calibrated)
    arm_obj[f"saberrig_fingers_thumb_calibrated_source_{suffix}"] = str(thumb_calibrated_source)
    arm_obj[f"saberrig_fingers_thumb_calibration_{suffix}"] = str(thumb_calibration_label)
    arm_obj[f"saberrig_fingers_thumb_distal_mode_{suffix}"] = "PARENT_RELATIVE_IP"
    arm_obj[f"saberrig_fingers_thumb_distal_min_bend_deg_{suffix}"] = float(math.degrees(SR_THUMB_FIST_IP_MIN_BEND))
    arm_obj[f"saberrig_fingers_thumb_distal_max_bend_deg_{suffix}"] = float(math.degrees(SR_THUMB_FIST_IP_MAX_BEND))
    arm_obj[f"saberrig_fingers_segments_{suffix}"] = int(len(mch_rows))
    arm_obj[f"saberrig_fingers_curl_confidence_{suffix}"] = float(sum(curl_conf_rows) / len(curl_conf_rows)) if curl_conf_rows else 0.0
    arm_obj[f"saberrig_fingers_spread_confidence_{suffix}"] = float(sum(spread_conf_rows) / len(spread_conf_rows)) if spread_conf_rows else 0.0
    arm_obj[f"saberrig_fingers_control_{suffix}"] = names["ctrl_master"]
    arm_obj[f"saberrig_fingers_curl_hemisphere_{suffix}"] = str(frame.get("curl_hemisphere_source", "GEOMETRIC"))
    arm_obj[f"saberrig_fingers_curl_driver_multiplier_{suffix}"] = float(curl_driver_multiplier)
    arm_obj[f"saberrig_fingers_curl_direction_{suffix}"] = "UMA_DRIVER_FLIP" if curl_driver_multiplier < 0.0 else "GEOMETRIC"
    return generated

def _build_finger_controls(context, arm_obj, analysis):
    if not _foundation_is_prepared(arm_obj):
        _prepare_foundation(arm_obj, analysis)
    _remove_finger_controls(context, arm_obj)
    generated, errors = [], []
    # Refresh analysis after cleanup so generated bones cannot pollute finger resolution.
    analysis = analyze_armature(arm_obj)
    for side in ("L", "R"):
        try:
            generated.extend(_build_finger_side(context, arm_obj, analysis, side))
        except Exception as exc:
            errors.append(str(exc))
    if not generated:
        raise RuntimeError(errors[0] if errors else "No finger controls could be generated")
    arm_obj["saberrig_fingers_built"] = True
    arm_obj["saberrig_fingers_version"] = ADDON_VERSION
    arm_obj["saberrig_fingers_mode"] = "PROCEDURAL"
    _setup_control_visuals(arm_obj)
    _set_rig_view(arm_obj, True)
    return generated, errors

def _reset_finger_controls(arm_obj, side=None):
    sides = (str(side).upper(),) if side else ("L", "R")
    changed = 0
    props = ("fist", "curl", "spread", "thumb_curl", "thumb_spread", "index_curl", "middle_curl", "ring_curl", "pinky_curl")
    for suffix in sides:
        ctrl = arm_obj.pose.bones.get(_finger_control_names(suffix)["ctrl_master"]) if arm_obj.pose else None
        if not ctrl:
            continue
        for prop in props:
            if prop in ctrl:
                ctrl[prop] = 0.0; changed += 1
    return changed

# -----------------------------------------------------------------------------
# Blender properties
# -----------------------------------------------------------------------------

class SR_MappingItem(PropertyGroup):
    role: StringProperty()
    label: StringProperty()
    bone_name: StringProperty()
    confidence: FloatProperty(min=0.0, max=1.0)
    found: BoolProperty(default=False)

class SR_CountItem(PropertyGroup):
    name: StringProperty()
    value: IntProperty(default=0)

class SR_MessageItem(PropertyGroup):
    text: StringProperty()

class SR_AnalysisState(PropertyGroup):
    target: PointerProperty(type=bpy.types.Object, poll=_object_poll_armature)
    analyzed: BoolProperty(default=False)
    armature_name: StringProperty()
    game_family: StringProperty()
    rig_dialect: StringProperty()
    profile_id: StringProperty()
    source_style: StringProperty()
    profile_confidence: FloatProperty(min=0.0, max=1.0)
    semantic_coverage: FloatProperty(min=0.0, max=1.0)
    semantic_confidence: FloatProperty(min=0.0, max=1.0)
    bone_count: IntProperty(default=0)
    pose_bone_count: IntProperty(default=0)
    ik_constraint_count: IntProperty(default=0)
    constraint_count: IntProperty(default=0)
    finger_segment_count: IntProperty(default=0)
    mappings: CollectionProperty(type=SR_MappingItem)
    class_counts: CollectionProperty(type=SR_CountItem)
    warnings: CollectionProperty(type=SR_MessageItem)
    show_more_info: BoolProperty(default=False)
    show_mapping: BoolProperty(default=True)
    show_classes: BoolProperty(default=False)
    show_warnings: BoolProperty(default=True)

def _clear_state(state):
    state.analyzed = False
    state.armature_name = ""
    state.game_family = ""
    state.rig_dialect = ""
    state.profile_id = ""
    state.source_style = ""
    state.profile_confidence = 0.0
    state.semantic_coverage = 0.0
    state.semantic_confidence = 0.0
    state.bone_count = 0
    state.pose_bone_count = 0
    state.ik_constraint_count = 0
    state.constraint_count = 0
    state.finger_segment_count = 0
    state.mappings.clear()
    state.class_counts.clear()
    state.warnings.clear()

def _fill_state(scene, state, arm_obj, analysis):
    _clear_state(state)
    state.target = arm_obj
    state.analyzed = True
    state.armature_name = analysis["armature"]["object_name"]
    state.game_family = analysis["detection"]["game_family"]
    state.rig_dialect = analysis["detection"]["rig_dialect"]
    state.profile_id = analysis["detection"]["profile_id"]
    state.source_style = analysis["detection"]["source_style_hint"]
    state.profile_confidence = analysis["detection"]["profile_confidence"]
    state.semantic_coverage = analysis["semantic_summary"]["coverage"]
    state.semantic_confidence = analysis["semantic_summary"]["average_confidence"]
    state.bone_count = analysis["armature"]["bone_count"]
    state.pose_bone_count = analysis["armature"]["pose_bone_count"]
    state.ik_constraint_count = analysis["constraints"]["ik_count"]
    state.constraint_count = analysis["constraints"]["total"]
    state.finger_segment_count = analysis["semantic_summary"]["finger_segments_found"]

    for role, row in analysis["semantic_map"].items():
        item = state.mappings.add()
        item.role = role
        item.label = ROLE_LABELS.get(role, role)
        item.bone_name = row["bone"] or ""
        item.confidence = row["confidence"]
        item.found = bool(row["bone"])

    for class_name, count in analysis["classifications"]["counts"].items():
        item = state.class_counts.add()
        item.name = class_name
        item.value = count

    for warning in analysis["warnings"]:
        item = state.warnings.add()
        item.text = warning

    _ANALYSIS_CACHE[scene.as_pointer()] = analysis

# -----------------------------------------------------------------------------
# Production Bake / Export Pipeline
# -----------------------------------------------------------------------------

def _bake_source_bone_names(arm_obj):
    """Return original/source pose bones currently driven by SaberRig."""
    names = []
    if not arm_obj or arm_obj.type != 'ARMATURE' or not arm_obj.pose:
        return names
    for pb in arm_obj.pose.bones:
        if bool(pb.bone.get("saberrig_generated", False)):
            continue
        if pb.name.startswith(("SR_CTRL_", "SR_MCH_")):
            continue
        if any(str(c.name).startswith(SR_CONSTRAINT_PREFIX) for c in pb.constraints):
            names.append(pb.name)
    return names

def _bake_unique_action_name(arm_obj):
    base = _safe_filename(arm_obj.name) or "Armature"
    stem = f"{base}_SaberRig_Baked"
    if bpy.data.actions.get(stem) is None:
        return stem
    index = 2
    while bpy.data.actions.get(f"{stem}_{index:02d}") is not None:
        index += 1
    return f"{stem}_{index:02d}"

def _bake_prepare_action(arm_obj):
    """Create a non-destructive action copy before writing baked keys."""
    ad = arm_obj.animation_data_create()
    previous = getattr(ad, "action", None)
    if previous:
        try:
            baked = previous.copy()
            baked.name = _bake_unique_action_name(arm_obj)
        except Exception:
            baked = bpy.data.actions.new(_bake_unique_action_name(arm_obj))
    else:
        baked = bpy.data.actions.new(_bake_unique_action_name(arm_obj))
    ad.action = baked
    return previous, baked

def _bake_pose_to_source(context, arm_obj, frame_start, frame_end, step=1):
    """Bake evaluated SaberRig motion to original source pose bones."""
    frame_start = int(frame_start)
    frame_end = int(frame_end)
    step = max(1, int(step))
    if frame_end < frame_start:
        raise RuntimeError("Bake end frame is before start frame")

    source_names = _bake_source_bone_names(arm_obj)
    if not source_names:
        raise RuntimeError("No SaberRig-driven source bones were found to bake")

    scene = context.scene
    old_frame = int(scene.frame_current)
    old_active_bone = arm_obj.data.bones.active.name if arm_obj.data.bones.active else ""
    old_selected = [b.name for b in arm_obj.data.bones if b.select]
    previous_action = None
    baked_action = None

    try:
        _wiggle_reset_runtime(arm_obj)
    except Exception:
        pass

    try:
        _activate_armature(context, arm_obj, mode='POSE')

        for bone in arm_obj.data.bones:
            bone.select = False
        for name in source_names:
            bone = arm_obj.data.bones.get(name)
            if bone:
                bone.select = True
        arm_obj.data.bones.active = arm_obj.data.bones.get(source_names[0])

        previous_action, baked_action = _bake_prepare_action(arm_obj)

        scene.frame_set(frame_start)
        _view_layer_update(context)

        kwargs = dict(
            frame_start=frame_start,
            frame_end=frame_end,
            step=step,
            only_selected=True,
            visual_keying=True,
            clear_constraints=False,
            clear_parents=False,
            use_current_action=True,
            clean_curves=False,
            bake_types={'POSE'},
        )
        try:
            result = bpy.ops.nla.bake(**kwargs)
        except TypeError:
            kwargs.pop("use_current_action", None)
            result = bpy.ops.nla.bake(**kwargs)

        if 'FINISHED' not in set(result):
            raise RuntimeError(f"Blender NLA bake returned {result}")

        arm_obj["saberrig_bake_action"] = baked_action.name if baked_action else ""
        arm_obj["saberrig_bake_frame_start"] = frame_start
        arm_obj["saberrig_bake_frame_end"] = frame_end
        arm_obj["saberrig_bake_step"] = step
        arm_obj["saberrig_bake_source_bones"] = int(len(source_names))
        arm_obj["saberrig_bake_version"] = ADDON_VERSION
        return {
            "action": baked_action.name if baked_action else "",
            "source_bones": len(source_names),
            "frame_start": frame_start,
            "frame_end": frame_end,
            "step": step,
        }
    except Exception:
        try:
            if previous_action is not None and arm_obj.animation_data:
                arm_obj.animation_data.action = previous_action
        except Exception:
            pass
        raise
    finally:
        try:
            scene.frame_set(old_frame)
        except Exception:
            pass
        try:
            for bone in arm_obj.data.bones:
                bone.select = bone.name in old_selected
            if old_active_bone:
                arm_obj.data.bones.active = arm_obj.data.bones.get(old_active_bone)
        except Exception:
            pass
        _view_layer_update(context)

def _bake_run_preflight(arm_obj):
    """Run Rig QA and block only structural ERROR-level findings."""
    analysis = analyze_armature(arm_obj)
    report = _run_rig_qa(arm_obj, analysis)
    error_count = int(report["counts"].get("errors", 0))
    if error_count > 0:
        raise RuntimeError(
            f"Rig QA found {error_count} error(s). Fix them before production bake."
        )
    return report

class SR_OT_BakeToSource(Operator):
    bl_idname = "saberrig.bake_to_source"
    bl_label = "Bake to Source"
    bl_description = "Bake evaluated SaberRig motion over the scene frame range to a new source-skeleton action while keeping SaberRig installed"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        state = context.scene.saberrig_state
        arm_obj = _resolve_armature(context, state)
        if not arm_obj or arm_obj.type != 'ARMATURE':
            self.report({'ERROR'}, "SaberRig target armature is unavailable")
            return {'CANCELLED'}
        if not bool(arm_obj.get("saberrig_prepared", False)):
            self.report({'ERROR'}, "Prepare/build SaberRig before baking")
            return {'CANCELLED'}

        try:
            report = _bake_run_preflight(arm_obj)
            result = _bake_pose_to_source(
                context, arm_obj,
                context.scene.frame_start,
                context.scene.frame_end,
                step=1,
            )
        except Exception as exc:
            self.report({'ERROR'}, f"Bake failed: {exc}")
            return {'CANCELLED'}

        warnings = int(report["counts"].get("warnings", 0))
        suffix = f" · QA warnings: {warnings}" if warnings else ""
        self.report(
            {'INFO'},
            f"Baked {result['source_bones']} source bones to {result['action']} "
            f"({result['frame_start']}–{result['frame_end']}){suffix}"
        )
        return {'FINISHED'}

class SR_OT_BakeCleanExport(Operator):
    bl_idname = "saberrig.bake_clean_export"
    bl_label = "Bake + Remove SaberRig"
    bl_description = "Bake the scene frame range to a new source action, then remove SaberRig controls/mechanisms for export"
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        state = context.scene.saberrig_state
        arm_obj = _resolve_armature(context, state)
        if not arm_obj or arm_obj.type != 'ARMATURE':
            self.report({'ERROR'}, "SaberRig target armature is unavailable")
            return {'CANCELLED'}
        if not bool(arm_obj.get("saberrig_prepared", False)):
            self.report({'ERROR'}, "Prepare/build SaberRig before baking")
            return {'CANCELLED'}

        try:
            report = _bake_run_preflight(arm_obj)
            result = _bake_pose_to_source(
                context, arm_obj,
                context.scene.frame_start,
                context.scene.frame_end,
                step=1,
            )
            baked_action_name = result["action"]
            removed_bones, removed_constraints = _remove_saberrig_setup(context, arm_obj)

            arm_obj["saberrig_export_baked_action"] = baked_action_name
            arm_obj["saberrig_export_frame_start"] = int(result["frame_start"])
            arm_obj["saberrig_export_frame_end"] = int(result["frame_end"])
            arm_obj["saberrig_export_source_bones"] = int(result["source_bones"])
            arm_obj["saberrig_export_rc"] = SR_RELEASE_CANDIDATE

            analysis = analyze_armature(arm_obj)
            _fill_state(context.scene, state, arm_obj, analysis)
        except Exception as exc:
            self.report({'ERROR'}, f"Bake + Remove failed: {exc}")
            return {'CANCELLED'}

        warnings = int(report["counts"].get("warnings", 0))
        suffix = f" · QA warnings: {warnings}" if warnings else ""
        self.report(
            {'INFO'},
            f"Export-ready action {baked_action_name}; removed "
            f"{removed_bones} SaberRig bones / {removed_constraints} constraints{suffix}"
        )
        return {'FINISHED'}

# -----------------------------------------------------------------------------
# Rig QA / Diagnostics
# -----------------------------------------------------------------------------

SR_QA_REPORT_VERSION = "1.0"
SR_QA_MAX_PANEL_ISSUES = 10

def _qa_issue(issues, severity, code, message, component="", bone=""):
    issues.append({
        "severity": str(severity).upper(),
        "code": str(code),
        "message": str(message),
        "component": str(component or ""),
        "bone": str(bone or ""),
    })

def _qa_bone_exists(arm_obj, name):
    return bool(name and arm_obj.data.bones.get(name))

def _qa_check_expected_bones(arm_obj, issues, component, names):
    missing = [n for n in names if n and not _qa_bone_exists(arm_obj, n)]
    if missing:
        _qa_issue(
            issues, "ERROR", "COMPONENT_MISSING_BONES",
            f"{component}: missing generated bones: {', '.join(missing[:8])}"
            + ("…" if len(missing) > 8 else ""),
            component=component,
        )
    return missing

def _qa_component_integrity(arm_obj, analysis, issues):
    # Foundation.
    if _foundation_is_prepared(arm_obj):
        for col_name in (SR_COLLECTION_ROOT, SR_COLLECTION_CONTROLS, SR_COLLECTION_MECHANISM, SR_COLLECTION_SOURCE):
            if not _find_bone_collection(arm_obj.data, col_name):
                _qa_issue(
                    issues, "ERROR", "FOUNDATION_COLLECTION_MISSING",
                    f"Foundation collection is missing: {col_name}",
                    component="Foundation",
                )

    # Body.
    if bool(arm_obj.get("saberrig_body_built", False)):
        names = _body_control_names()
        expected = [
            names["ctrl_master"], names["ctrl_root"], names["ctrl_cog"], names["ctrl_hips"],
            names["mch_root"], names["mch_hips"],
        ]
        body_src = _body_sources(analysis)
        for role in ("spine", "chest", "neck", "head"):
            if body_src.get(role):
                expected.extend((names[f"ctrl_{role}"], names[f"mch_{role}"]))
        _qa_check_expected_bones(arm_obj, issues, "Body Controls", expected)

    # Arms FK / IK.
    if bool(arm_obj.get("saberrig_arms_fk_built", False)):
        for side in ("L", "R"):
            src = _arm_sources_for_side(analysis, side)
            if all(src.get(k) for k in ("upper_arm", "forearm", "hand")):
                n = _arm_fk_names(side)
                expected = [n["ctrl_upper"], n["ctrl_forearm"], n["ctrl_hand"],
                            n["mch_upper"], n["mch_forearm"], n["mch_hand"]]
                if src.get("clavicle"):
                    expected += [n["ctrl_clavicle"], n["mch_clavicle"]]
                _qa_check_expected_bones(arm_obj, issues, f"Arm FK {side}", expected)

    if bool(arm_obj.get("saberrig_arms_ik_built", False)):
        for side in ("L", "R"):
            src = _arm_sources_for_side(analysis, side)
            if all(src.get(k) for k in ("upper_arm", "forearm", "hand")):
                n = _arm_ik_names(side)
                expected = [
                    n["mch_upper"], n["mch_forearm"], n["mch_hand"],
                    n["mch_reach"], n["mch_target"], n["mch_pole_space"],
                    n["ctrl_hand"], n["ctrl_pole"],
                ]
                if src.get("clavicle"):
                    expected.append(n["mch_shoulder"])
                _qa_check_expected_bones(arm_obj, issues, f"Arm IK {side}", expected)

    # Legs.
    if bool(arm_obj.get("saberrig_legs_fk_built", False)):
        for side in ("L", "R"):
            src = _leg_sources_for_side(analysis, side)
            if all(src.get(k) for k in ("thigh", "shin", "foot")):
                n = _leg_fk_names(side)
                _qa_check_expected_bones(
                    arm_obj, issues, f"Leg FK {side}",
                    [n["ctrl_thigh"], n["ctrl_shin"], n["ctrl_foot"],
                     n["mch_thigh"], n["mch_shin"], n["mch_foot"]],
                )

    if bool(arm_obj.get("saberrig_legs_ik_built", False)):
        for side in ("L", "R"):
            src = _leg_sources_for_side(analysis, side)
            if all(src.get(k) for k in ("thigh", "shin", "foot")):
                n = _leg_ik_names(side)
                expected = [
                    n["mch_thigh"], n["mch_shin"], n["mch_foot"], n["mch_reach"],
                    n["mch_target"], n["mch_pole_space"], n["ctrl_master"],
                    n["ctrl_heel"], n["ctrl_ball"], n["ctrl_toe"], n["ctrl_foot"], n["ctrl_pole"],
                ]
                _qa_check_expected_bones(arm_obj, issues, f"Leg IK {side}", expected)

    # Fingers.
    if bool(arm_obj.get("saberrig_fingers_built", False)):
        for side in ("L", "R"):
            chains = _finger_chains_for_side(analysis, side)
            if not chains:
                continue
            expected = [_finger_control_names(side)["ctrl_master"]]
            for finger, chain in chains.items():
                for i, _source in enumerate(chain[:3]):
                    expected.append(_finger_mch_name(side, finger, i))
            _qa_check_expected_bones(arm_obj, issues, f"Fingers {side}", expected)

    # Animation spaces.
    if bool(arm_obj.get("saberrig_animation_spaces_built", False)):
        for kind in ("HAND", "FOOT"):
            for side in ("L", "R"):
                ctrl_name = _animation_space_control_name(kind, side)
                if not _qa_bone_exists(arm_obj, ctrl_name):
                    continue
                switch_name = _animation_space_switch_name(kind, side)
                _qa_check_expected_bones(
                    arm_obj, issues, f"Animation Space {kind} {side}", [switch_name]
                )
                available = str(
                    arm_obj.get(f"saberrig_space_available_{kind}_{side}", "WORLD")
                ).split("|")
                anchors = [
                    _animation_space_anchor_name(kind, side, space)
                    for space in available if space != "WORLD"
                ]
                _qa_check_expected_bones(
                    arm_obj, issues, f"Animation Space {kind} {side}", anchors
                )

    # Secondary motion.
    if bool(arm_obj.get("saberrig_wiggle_built", False)):
        wiggle_bones = [
            b for b in arm_obj.data.bones
            if bool(b.get("saberrig_generated", False))
            and str(b.get("saberrig_component", "")) == SR_WIGGLE_COMPONENT
        ]
        expected_count = int(arm_obj.get("saberrig_wiggle_bone_count", 0))
        if not wiggle_bones:
            _qa_issue(
                issues, "ERROR", "SECONDARY_EMPTY",
                "Secondary Motion is marked built, but no generated wiggle bones exist.",
                component="Secondary Motion",
            )
        elif expected_count and len(wiggle_bones) != expected_count:
            _qa_issue(
                issues, "WARN", "SECONDARY_COUNT_MISMATCH",
                f"Secondary Motion metadata expects {expected_count} bones; found {len(wiggle_bones)}.",
                component="Secondary Motion",
            )

def _qa_constraint_integrity(arm_obj, issues):
    target_required = {
        'COPY_LOCATION', 'COPY_ROTATION', 'COPY_SCALE', 'COPY_TRANSFORMS',
        'LIMIT_DISTANCE', 'TRACK_TO', 'DAMPED_TRACK', 'LOCKED_TRACK',
        'STRETCH_TO', 'CHILD_OF', 'TRANSFORM', 'IK',
    }
    checked = 0
    for pb in arm_obj.pose.bones:
        owner_is_sr = pb.name.startswith("SR_") or bool(pb.bone.get("saberrig_generated", False))
        for c in pb.constraints:
            if not (owner_is_sr or str(c.name).startswith("SR_")):
                continue
            checked += 1
            ctype = str(getattr(c, "type", ""))
            target = getattr(c, "target", None) if hasattr(c, "target") else None
            subtarget = str(getattr(c, "subtarget", "")) if hasattr(c, "subtarget") else ""
            if ctype in target_required and target is None:
                # LIMIT_DISTANCE can technically use origin modes, but every SaberRig
                # generated version uses an explicit target.
                _qa_issue(
                    issues, "ERROR", "CONSTRAINT_TARGET_MISSING",
                    f"{pb.name}: {c.name} ({ctype}) has no target.",
                    component="Constraints", bone=pb.name,
                )
                continue
            if target is arm_obj and subtarget and not arm_obj.data.bones.get(subtarget):
                _qa_issue(
                    issues, "ERROR", "CONSTRAINT_SUBTARGET_MISSING",
                    f"{pb.name}: {c.name} points to missing bone {subtarget}.",
                    component="Constraints", bone=pb.name,
                )
    return checked

def _qa_driver_integrity(arm_obj, issues):
    ad = getattr(arm_obj, "animation_data", None)
    drivers = getattr(ad, "drivers", None) if ad else None
    if not drivers:
        return 0
    checked = 0
    bone_path_rx = re.compile(r'pose\.bones\["([^"]+)"\]')
    for fc in drivers:
        path = str(getattr(fc, "data_path", ""))
        if "SR_" not in path and "saberrig" not in path.lower():
            continue
        checked += 1
        if hasattr(fc, "is_valid") and not bool(fc.is_valid):
            _qa_issue(
                issues, "ERROR", "DRIVER_INVALID",
                f"Invalid driver: {path}",
                component="Drivers",
            )
        drv = getattr(fc, "driver", None)
        if not drv:
            continue
        for var in drv.variables:
            for target in var.targets:
                if getattr(target, "id", None) is None:
                    _qa_issue(
                        issues, "ERROR", "DRIVER_TARGET_MISSING",
                        f"Driver {path}: variable {var.name} has no ID target.",
                        component="Drivers",
                    )
                    continue
                data_path = str(getattr(target, "data_path", ""))
                if getattr(target, "id", None) is arm_obj and data_path:
                    for bone_name in bone_path_rx.findall(data_path):
                        if not arm_obj.data.bones.get(bone_name):
                            _qa_issue(
                                issues, "ERROR", "DRIVER_BONE_MISSING",
                                f"Driver {path}: variable {var.name} references missing bone {bone_name}.",
                                component="Drivers", bone=bone_name,
                            )
    return checked

def _qa_generated_metadata(arm_obj, issues):
    generated = 0
    for bone in arm_obj.data.bones:
        name = str(bone.name)
        marked = bool(bone.get("saberrig_generated", False))
        if name.startswith(("SR_CTRL_", "SR_MCH_")) and not marked:
            _qa_issue(
                issues, "WARN", "GENERATED_METADATA_MISSING",
                f"{name}: SaberRig-style generated bone is missing generated metadata.",
                component="Metadata", bone=name,
            )
        if not marked:
            continue
        generated += 1
        component = str(bone.get("saberrig_component", ""))
        kind = str(bone.get("saberrig_kind", ""))
        source = str(bone.get("saberrig_source_bone", ""))
        if not component:
            _qa_issue(
                issues, "WARN", "COMPONENT_METADATA_MISSING",
                f"{name}: generated bone has no component metadata.",
                component="Metadata", bone=name,
            )
        if kind not in {"CTRL", "MCH"}:
            _qa_issue(
                issues, "WARN", "KIND_METADATA_INVALID",
                f"{name}: generated bone kind is {kind or 'empty'}.",
                component="Metadata", bone=name,
            )
        if source and not arm_obj.data.bones.get(source):
            _qa_issue(
                issues, "WARN", "SOURCE_METADATA_BROKEN",
                f"{name}: recorded source bone no longer exists: {source}.",
                component="Metadata", bone=name,
            )
        if bone.use_deform:
            _qa_issue(
                issues, "WARN", "GENERATED_BONE_DEFORMS",
                f"{name}: generated SaberRig bone has Deform enabled.",
                component="Metadata", bone=name,
            )
        if kind == "CTRL":
            pb = arm_obj.pose.bones.get(name) if arm_obj.pose else None
            if pb and getattr(pb, "custom_shape", None) is None:
                _qa_issue(
                    issues, "WARN", "CONTROL_SHAPE_MISSING",
                    f"{name}: animator control has no Rig Widget/custom shape.",
                    component="Rig Widgets", bone=name,
                )
    return generated

def _qa_transform_checks(arm_obj, issues):
    scale = tuple(float(v) for v in arm_obj.scale)
    if any(v <= 0.0 for v in scale):
        _qa_issue(
            issues, "ERROR", "ARMATURE_NEGATIVE_SCALE",
            f"Armature object has non-positive scale: {scale}.",
            component="Object Transform",
        )
    elif max(scale) - min(scale) > 1.0e-4:
        _qa_issue(
            issues, "WARN", "ARMATURE_NONUNIFORM_SCALE",
            f"Armature object has non-uniform scale: ({scale[0]:.4f}, {scale[1]:.4f}, {scale[2]:.4f}).",
            component="Object Transform",
        )
    if any(abs(v - 1.0) > 1.0e-4 for v in scale):
        _qa_issue(
            issues, "INFO", "ARMATURE_SCALE_NOT_APPLIED",
            "Armature object scale is not identity. Keep it if required by the source pipeline; avoid applying it mid-animation.",
            component="Object Transform",
        )
    try:
        if arm_obj.matrix_world.to_3x3().determinant() < 0.0:
            _qa_issue(
                issues, "ERROR", "ARMATURE_MIRRORED_TRANSFORM",
                "Armature world transform has a negative determinant (mirrored coordinate frame).",
                component="Object Transform",
            )
    except Exception:
        pass

def _qa_solver_checks(arm_obj, issues):
    # Arm pole calibration / straight source pose diagnostics.
    if bool(arm_obj.get("saberrig_arms_ik_built", False)):
        for side in ("L", "R"):
            err = abs(float(arm_obj.get(f"saberrig_pole_error_{side}", 0.0)))
            if err > SR_POLE_CALIBRATION_WARN_RADIANS:
                _qa_issue(
                    issues, "WARN", "ELBOW_POLE_CALIBRATION",
                    f"Arm {side}: pole calibration error is {math.degrees(err):.2f}°.",
                    component="Arm IK",
                )
            singular = bool(arm_obj.get(f"saberrig_pole_singular_{side}", False))
            prebend = bool(arm_obj.get(f"saberrig_arm_prebend_active_{side}", False))
            if singular and not prebend:
                _qa_issue(
                    issues, "ERROR", "STRAIGHT_ARM_WITHOUT_PREBEND",
                    f"Arm {side}: source chain is singular/straight but SaberRig MCH pre-bend is not active.",
                    component="Arm IK",
                )
            elif singular and prebend:
                angle = math.degrees(float(
                    arm_obj.get(f"saberrig_arm_prebend_angle_{side}", 0.0)
                ))
                preferred_active = bool(
                    arm_obj.get(f"saberrig_arm_preferred_reach_active_{side}", False)
                )
                signed_lock = bool(
                    arm_obj.get(f"saberrig_elbow_signed_lock_{side}", True)
                )
                if not preferred_active:
                    _qa_issue(
                        issues, "WARN", "STRAIGHT_ARM_PREFERRED_REACH_INACTIVE",
                        f"Arm {side}: pre-bend is active but preferred reach reserve is not.",
                        component="Arm IK",
                    )
                canonical_quality = float(
                    arm_obj.get(f"saberrig_elbow_canonical_quality_{side}", 0.0)
                )
                hinge_mode = str(
                    arm_obj.get(f"saberrig_elbow_hinge_mode_{side}", "")
                )
                if not signed_lock or hinge_mode != "PREBEND_LOCK":
                    _qa_issue(
                        issues, "WARN", "STRAIGHT_ARM_CANONICAL_LOCK_MISSING",
                        f"Arm {side}: pre-bent elbow is not using the canonical PREBEND_LOCK.",
                        component="Arm IK",
                    )
                elif canonical_quality < SR_ELBOW_CANONICAL_SIGN_MIN_QUALITY:
                    _qa_issue(
                        issues, "WARN", "ELBOW_CANONICAL_SIGN_LOW_QUALITY",
                        f"Arm {side}: canonical flex sign quality is only {canonical_quality * 100:.0f}%.",
                        component="Arm IK",
                    )

                guard = _elbow_pole_guard_constraint(arm_obj, side)
                if guard is None:
                    _qa_issue(
                        issues, "WARN", "ELBOW_POLE_GUARD_MISSING",
                        f"Arm {side}: Elbow Pole Plane Guard is missing.",
                        component="Arm IK",
                    )

                _qa_issue(
                    issues, "INFO", "STRAIGHT_ARM_STABILIZED",
                    f"Arm {side}: {angle:.2f}° MCH pre-bend + preferred reach + anatomical PREBEND_LOCK + pole-plane guard.",
                    component="Arm IK",
                )

    # Leg pole calibration / straight source pose diagnostics.
    if bool(arm_obj.get("saberrig_legs_ik_built", False)):
        for side in ("L", "R"):
            err = abs(float(arm_obj.get(f"saberrig_leg_pole_error_{side}", 0.0)))
            if err > SR_POLE_CALIBRATION_WARN_RADIANS:
                _qa_issue(
                    issues, "WARN", "KNEE_POLE_CALIBRATION",
                    f"Leg {side}: pole calibration error is {math.degrees(err):.2f}°.",
                    component="Leg IK",
                )
            singular = bool(arm_obj.get(f"saberrig_leg_pole_singular_{side}", False))
            prebend = bool(arm_obj.get(f"saberrig_leg_prebend_active_{side}", False))
            if singular and not prebend:
                _qa_issue(
                    issues, "ERROR", "STRAIGHT_LEG_WITHOUT_PREBEND",
                    f"Leg {side}: source chain is singular/straight but SaberRig pre-bend is not active.",
                    component="Leg IK",
                )
            elif singular and prebend:
                _qa_issue(
                    issues, "INFO", "STRAIGHT_LEG_STABILIZED",
                    f"Leg {side}: straight source chain is stabilized by MCH pre-bend.",
                    component="Leg IK",
                )

    # Animation-space metadata should agree with the actual custom property.
    if bool(arm_obj.get("saberrig_animation_spaces_built", False)):
        for kind in ("HAND", "FOOT"):
            for side in ("L", "R"):
                ctrl_name = _animation_space_control_name(kind, side)
                pb = arm_obj.pose.bones.get(ctrl_name or "")
                if not pb:
                    continue
                available = str(
                    arm_obj.get(f"saberrig_space_available_{kind}_{side}", "WORLD")
                ).split("|")
                mode = str(arm_obj.get(_animation_space_mode_key(kind, side), "WORLD")).upper()
                if mode not in available:
                    _qa_issue(
                        issues, "WARN", "SPACE_MODE_UNAVAILABLE",
                        f"{kind.title()} {side}: stored space {mode} is not currently available.",
                        component="Animation Spaces", bone=ctrl_name,
                    )

def _run_rig_qa(arm_obj, analysis):
    issues = []
    summary = analysis.get("semantic_summary", {})
    coverage = float(summary.get("coverage", 0.0))
    avg_conf = float(summary.get("average_confidence", 0.0))

    if coverage < 0.50:
        _qa_issue(
            issues, "ERROR", "SEMANTIC_COVERAGE_LOW",
            f"Core semantic coverage is only {coverage * 100:.0f}%.",
            component="Semantic Analysis",
        )
    elif coverage < 0.80:
        _qa_issue(
            issues, "WARN", "SEMANTIC_COVERAGE_PARTIAL",
            f"Core semantic coverage is {coverage * 100:.0f}%; verify missing roles before building more components.",
            component="Semantic Analysis",
        )

    if avg_conf < 0.55:
        _qa_issue(
            issues, "ERROR", "SEMANTIC_CONFIDENCE_LOW",
            f"Average semantic confidence is {avg_conf * 100:.0f}%.",
            component="Semantic Analysis",
        )
    elif avg_conf < 0.75:
        _qa_issue(
            issues, "WARN", "SEMANTIC_CONFIDENCE_MODERATE",
            f"Average semantic confidence is {avg_conf * 100:.0f}%.",
            component="Semantic Analysis",
        )

    for warning in analysis.get("warnings", []):
        msg = str(warning)
        if msg.startswith("Missing core semantic roles"):
            continue
        _qa_issue(
            issues, "INFO", "ANALYZER_NOTE", msg,
            component="Semantic Analysis",
        )

    _qa_transform_checks(arm_obj, issues)
    _qa_component_integrity(arm_obj, analysis, issues)
    generated_count = _qa_generated_metadata(arm_obj, issues)
    constraint_count = _qa_constraint_integrity(arm_obj, issues)
    driver_count = _qa_driver_integrity(arm_obj, issues)
    _qa_solver_checks(arm_obj, issues)

    counts = Counter(row["severity"] for row in issues)
    errors = int(counts.get("ERROR", 0))
    warnings = int(counts.get("WARN", 0))
    infos = int(counts.get("INFO", 0))

    score = max(0, min(100, 100 - errors * 16 - warnings * 5))
    if errors:
        status = "BLOCKED" if errors >= 3 else "ATTENTION"
    elif warnings:
        status = "REVIEW" if score < 90 else "GOOD"
    else:
        status = "READY"

    report = {
        "saberrig": {
            "version": ADDON_VERSION,
            "qa_report_version": SR_QA_REPORT_VERSION,
        },
        "armature": arm_obj.name,
        "status": status,
        "score": int(score),
        "counts": {
            "errors": errors,
            "warnings": warnings,
            "info": infos,
            "generated_bones_checked": int(generated_count),
            "constraints_checked": int(constraint_count),
            "drivers_checked": int(driver_count),
        },
        "semantic": {
            "coverage": coverage,
            "average_confidence": avg_conf,
        },
        "issues": issues,
    }

    arm_obj["saberrig_qa_last_version"] = ADDON_VERSION
    arm_obj["saberrig_qa_status"] = status
    arm_obj["saberrig_qa_score"] = int(score)
    arm_obj["saberrig_qa_error_count"] = errors
    arm_obj["saberrig_qa_warning_count"] = warnings
    arm_obj["saberrig_qa_info_count"] = infos
    arm_obj["saberrig_qa_generated_checked"] = int(generated_count)
    arm_obj["saberrig_qa_constraints_checked"] = int(constraint_count)
    arm_obj["saberrig_qa_drivers_checked"] = int(driver_count)
    arm_obj["saberrig_qa_issues_json"] = json.dumps(issues, ensure_ascii=False)
    return report

def _qa_report_from_armature(arm_obj):
    try:
        issues = json.loads(str(arm_obj.get("saberrig_qa_issues_json", "[]")))
    except Exception:
        issues = []
    return {
        "saberrig": {
            "version": ADDON_VERSION,
            "qa_report_version": SR_QA_REPORT_VERSION,
        },
        "armature": arm_obj.name,
        "status": str(arm_obj.get("saberrig_qa_status", "NOT_RUN")),
        "score": int(arm_obj.get("saberrig_qa_score", 0)),
        "counts": {
            "errors": int(arm_obj.get("saberrig_qa_error_count", 0)),
            "warnings": int(arm_obj.get("saberrig_qa_warning_count", 0)),
            "info": int(arm_obj.get("saberrig_qa_info_count", 0)),
            "generated_bones_checked": int(arm_obj.get("saberrig_qa_generated_checked", 0)),
            "constraints_checked": int(arm_obj.get("saberrig_qa_constraints_checked", 0)),
            "drivers_checked": int(arm_obj.get("saberrig_qa_drivers_checked", 0)),
        },
        "issues": issues,
    }

# -----------------------------------------------------------------------------
# Operators
# -----------------------------------------------------------------------------

class SR_OT_AnalyzeRig(Operator):
    bl_idname = "saberrig.analyze_rig"
    bl_label = "Analyze Rig"
    bl_description = "Analyze the selected/active armature without modifying its rig"
    bl_options = {'REGISTER'}

    def execute(self, context):
        state = context.scene.saberrig_state
        arm_obj = _resolve_armature(context, state)
        if not arm_obj:
            self.report({'ERROR'}, "SaberRig could not resolve a target armature")
            return {'CANCELLED'}

        try:
            analysis = analyze_armature(arm_obj)
            _fill_state(context.scene, state, arm_obj, analysis)
        except Exception as exc:
            self.report({'ERROR'}, f"SaberRig analysis failed: {exc}")
            return {'CANCELLED'}

        coverage = analysis["semantic_summary"]["coverage"] * 100.0
        self.report({'INFO'}, f"SaberRig analyzed {arm_obj.name}: {coverage:.0f}% core semantic coverage")
        return {'FINISHED'}

class SR_OT_ClearAnalysis(Operator):
    bl_idname = "saberrig.clear_analysis"
    bl_label = "Clear Analysis"
    bl_description = "Clear the current SaberRig analysis results"

    def execute(self, context):
        _clear_state(context.scene.saberrig_state)
        _ANALYSIS_CACHE.pop(context.scene.as_pointer(), None)
        return {'FINISHED'}

class SR_OT_ExportAnalysis(Operator, ExportHelper):
    bl_idname = "saberrig.export_analysis"
    bl_label = "Export Analysis"
    bl_description = "Export the current SaberRig analysis as a JSON file"

    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})

    def invoke(self, context, event):
        state = context.scene.saberrig_state
        if not state.analyzed:
            self.report({'ERROR'}, "Run Analyze Rig first")
            return {'CANCELLED'}
        base = _safe_filename(state.armature_name)
        self.filepath = base + "_SaberRig_Analysis.json"
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        scene_key = context.scene.as_pointer()
        analysis = _ANALYSIS_CACHE.get(scene_key)
        if not analysis:
            state = context.scene.saberrig_state
            arm_obj = _resolve_armature(context, state)
            if not arm_obj:
                self.report({'ERROR'}, "No cached analysis and no target armature available")
                return {'CANCELLED'}
            analysis = analyze_armature(arm_obj)
            _fill_state(context.scene, state, arm_obj, analysis)

        try:
            with open(self.filepath, "w", encoding="utf-8") as fh:
                json.dump(analysis, fh, ensure_ascii=False, indent=2)
        except Exception as exc:
            self.report({'ERROR'}, f"Could not write analysis: {exc}")
            return {'CANCELLED'}

        self.report({'INFO'}, f"SaberRig analysis exported: {os.path.basename(self.filepath)}")
        return {'FINISHED'}

class SR_OT_RunRigQA(Operator):
    bl_idname = "saberrig.run_rig_qa"
    bl_label = "Validate Rig"
    bl_description = "Run SaberRig structural, solver, constraint, driver, metadata and semantic QA checks"
    bl_options = {'REGISTER'}

    def execute(self, context):
        state = context.scene.saberrig_state
        arm_obj = _resolve_armature(context, state)
        if not arm_obj or arm_obj.type != 'ARMATURE':
            self.report({'ERROR'}, "SaberRig target armature is unavailable")
            return {'CANCELLED'}
        try:
            analysis = analyze_armature(arm_obj)
            report = _run_rig_qa(arm_obj, analysis)
        except Exception as exc:
            self.report({'ERROR'}, f"Rig QA failed: {exc}")
            return {'CANCELLED'}

        counts = report["counts"]
        self.report(
            {'INFO'},
            f"Rig QA {report['status']} — {report['score']}% | "
            f"{counts['errors']} errors, {counts['warnings']} warnings"
        )
        return {'FINISHED'}

class SR_OT_ExportRigQA(Operator, ExportHelper):
    bl_idname = "saberrig.export_rig_qa"
    bl_label = "Export QA Report"
    bl_description = "Export the last SaberRig Rig QA result as JSON"

    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})

    def invoke(self, context, event):
        state = context.scene.saberrig_state
        arm_obj = _resolve_armature(context, state)
        if not arm_obj:
            self.report({'ERROR'}, "No target armature available")
            return {'CANCELLED'}
        if "saberrig_qa_status" not in arm_obj:
            self.report({'ERROR'}, "Run Validate Rig first")
            return {'CANCELLED'}
        self.filepath = _safe_filename(arm_obj.name) + "_SaberRig_QA.json"
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        state = context.scene.saberrig_state
        arm_obj = _resolve_armature(context, state)
        if not arm_obj:
            self.report({'ERROR'}, "No target armature available")
            return {'CANCELLED'}
        report = _qa_report_from_armature(arm_obj)
        try:
            with open(self.filepath, "w", encoding="utf-8") as fh:
                json.dump(report, fh, ensure_ascii=False, indent=2)
        except Exception as exc:
            self.report({'ERROR'}, f"Could not write QA report: {exc}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Rig QA report exported: {os.path.basename(self.filepath)}")
        return {'FINISHED'}

class SR_OT_SelectMappedBone(Operator):
    bl_idname = "saberrig.select_mapped_bone"
    bl_label = "Select Bone"
    bl_description = "Select this mapped bone on the analyzed armature"

    bone_name: StringProperty()

    def execute(self, context):
        state = context.scene.saberrig_state
        arm_obj = state.target
        if not arm_obj or arm_obj.type != 'ARMATURE':
            self.report({'ERROR'}, "Analyzed armature is unavailable")
            return {'CANCELLED'}
        bone = arm_obj.data.bones.get(self.bone_name)
        if not bone:
            self.report({'ERROR'}, f"Bone not found: {self.bone_name}")
            return {'CANCELLED'}

        try:
            if context.object and context.object.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            bpy.ops.object.select_all(action='DESELECT')
            arm_obj.select_set(True)
            context.view_layer.objects.active = arm_obj
            bpy.ops.object.mode_set(mode='POSE')
            for b in arm_obj.data.bones:
                b.select = False
            bone.select = True
            arm_obj.data.bones.active = bone
        except Exception as exc:
            self.report({'WARNING'}, f"Bone resolved, but selection failed: {exc}")
        return {'FINISHED'}

class SR_OT_PrepareFoundation(Operator):
    bl_idname = "saberrig.prepare_foundation"
    bl_label = "Prepare SaberRig"
    bl_description = "Create SaberRig collections and metadata without changing the original hierarchy or weights"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        state = context.scene.saberrig_state
        arm_obj = _resolve_armature(context, state)
        if not arm_obj:
            self.report({'ERROR'}, "SaberRig could not resolve a target armature")
            return {'CANCELLED'}
        if not _armature_is_editable(arm_obj):
            self.report({'ERROR'}, "Target armature is linked/read-only")
            return {'CANCELLED'}

        try:
            analysis = analyze_armature(arm_obj)
            result = _prepare_foundation(arm_obj, analysis)
            _fill_state(context.scene, state, arm_obj, analysis)
        except Exception as exc:
            self.report({'ERROR'}, f"Could not prepare SaberRig: {exc}")
            return {'CANCELLED'}

        self.report(
            {'INFO'},
            f"SaberRig prepared; {result['assigned_source_bones']} source bones indexed · "
            f"{result.get('widgets', 0)} Rig Widgets ready"
        )
        return {'FINISHED'}

class SR_OT_BuildBodyControls(Operator):
    bl_idname = "saberrig.build_body_controls"
    bl_label = "Build Body Controls"
    bl_description = "Build non-destructive Master, Root, COG, Hips, Spine, Chest, Neck and Head controls"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        state = context.scene.saberrig_state
        arm_obj = _resolve_armature(context, state)
        if not arm_obj:
            self.report({'ERROR'}, "SaberRig could not resolve a target armature")
            return {'CANCELLED'}
        if not _armature_is_editable(arm_obj):
            self.report({'ERROR'}, "Target armature is linked/read-only")
            return {'CANCELLED'}
        try:
            analysis = analyze_armature(arm_obj)
            generated = _build_body_controls(context, arm_obj, analysis)
            refreshed = analyze_armature(arm_obj)
            _fill_state(context.scene, state, arm_obj, refreshed)
        except Exception as exc:
            self.report({'ERROR'}, f"Body controls build failed: {exc}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Built {len(generated)} SaberRig Root/COG/Spine bones")
        return {'FINISHED'}

class SR_OT_BuildFingerControls(Operator):
    bl_idname = "saberrig.build_finger_controls"
    bl_label = "Build Finger Controls"
    bl_description = "Build anatomical chain-plane Curl and dedicated target-pose Fist controls from semantic finger chains"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        state = context.scene.saberrig_state
        arm_obj = _resolve_armature(context, state)
        if not arm_obj:
            self.report({'ERROR'}, "SaberRig could not resolve a target armature")
            return {'CANCELLED'}
        if not _armature_is_editable(arm_obj):
            self.report({'ERROR'}, "Target armature is linked/read-only")
            return {'CANCELLED'}
        try:
            analysis = analyze_armature(arm_obj)
            generated, errors = _build_finger_controls(context, arm_obj, analysis)
            refreshed = analyze_armature(arm_obj)
            _fill_state(context.scene, state, arm_obj, refreshed)
        except Exception as exc:
            self.report({'ERROR'}, f"Finger controls build failed: {exc}")
            return {'CANCELLED'}
        if errors:
            self.report({'WARNING'}, f"Built {len(generated)} finger rig bones; partial build: {' | '.join(errors[:2])}")
        else:
            self.report({'INFO'}, f"Built {len(generated)} SaberRig finger-control bones")
        return {'FINISHED'}

class SR_OT_ResetFingerControls(Operator):
    bl_idname = "saberrig.reset_finger_controls"
    bl_label = "Reset Finger Controls"
    bl_description = "Reset SaberRig Fist, Curl, Spread, Thumb and individual finger sliders to neutral"
    bl_options = {'REGISTER', 'UNDO'}
    side: StringProperty(default="")

    def execute(self, context):
        arm_obj = _resolve_armature(context, context.scene.saberrig_state)
        if not arm_obj:
            return {'CANCELLED'}
        changed = _reset_finger_controls(arm_obj, self.side or None)
        _view_layer_update(context)
        self.report({'INFO'}, f"Reset {changed} finger-control values")
        return {'FINISHED'}

class SR_OT_BuildArmsFK(Operator):
    bl_idname = "saberrig.build_arms_fk"
    bl_label = "Build Arms FK"
    bl_description = "Build experimental non-deforming FK controls and mechanism bones for both arms"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        state = context.scene.saberrig_state
        arm_obj = _resolve_armature(context, state)
        if not arm_obj:
            self.report({'ERROR'}, "SaberRig could not resolve a target armature")
            return {'CANCELLED'}
        if not _armature_is_editable(arm_obj):
            self.report({'ERROR'}, "Target armature is linked/read-only")
            return {'CANCELLED'}

        try:
            analysis = analyze_armature(arm_obj)
            generated, errors = _build_arms_fk(context, arm_obj, analysis)
            refreshed = analyze_armature(arm_obj)
            _fill_state(context.scene, state, arm_obj, refreshed)
        except Exception as exc:
            self.report({'ERROR'}, f"Arm FK build failed: {exc}")
            return {'CANCELLED'}

        if errors:
            self.report({'WARNING'}, f"Built {len(generated)} SaberRig bones; partial build: {' | '.join(errors[:2])}")
        else:
            self.report({'INFO'}, f"Built {len(generated)} SaberRig arm FK bones")
        return {'FINISHED'}

class SR_OT_BuildArmsIK(Operator):
    bl_idname = "saberrig.build_arms_ik"
    bl_label = "Build Arms IK"
    bl_description = "Build roll-safe arm IK with bounded reach, semantic limits, shoulder assist, and source twist preservation"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        state = context.scene.saberrig_state
        arm_obj = _resolve_armature(context, state)
        if not arm_obj:
            self.report({'ERROR'}, "SaberRig could not resolve a target armature")
            return {'CANCELLED'}
        if not _armature_is_editable(arm_obj):
            self.report({'ERROR'}, "Target armature is linked/read-only")
            return {'CANCELLED'}

        try:
            analysis = analyze_armature(arm_obj)
            generated, errors = _build_arms_ik(context, arm_obj, analysis)
            refreshed = analyze_armature(arm_obj)
            _fill_state(context.scene, state, arm_obj, refreshed)
        except Exception as exc:
            self.report({'ERROR'}, f"Arm IK build failed: {exc}")
            return {'CANCELLED'}

        if errors:
            self.report({'WARNING'}, f"Built {len(generated)} IK bones; partial build: {' | '.join(errors[:2])}")
        else:
            self.report({'INFO'}, "Arm IK built with roll calibration, bounded reach, SAFE limits, and shoulder assist")
        return {'FINISHED'}

class SR_OT_SetArmMode(Operator):
    bl_idname = "saberrig.set_arm_mode"
    bl_label = "Set Arm FK/IK Mode"
    bl_description = "Match the destination controls, then switch a SaberRig arm between FK and IK"
    bl_options = {'REGISTER', 'UNDO'}

    side: StringProperty(default="L")
    mode: StringProperty(default="IK")

    def execute(self, context):
        state = context.scene.saberrig_state
        arm_obj = _resolve_armature(context, state)
        if not arm_obj or arm_obj.type != 'ARMATURE':
            self.report({'ERROR'}, "SaberRig target armature is unavailable")
            return {'CANCELLED'}
        if not bool(arm_obj.get("saberrig_arms_ik_built", False)):
            self.report({'ERROR'}, "Build Arms IK first")
            return {'CANCELLED'}
        side = self.side.upper()
        use_ik = self.mode.upper() == "IK"
        try:
            if use_ik:
                diagnostics = _snap_fk_to_ik(context, arm_obj, side, calibrate=False)
            else:
                diagnostics = None
                _snap_ik_to_fk(context, arm_obj, side)
            changed = _set_arm_mode_raw(arm_obj, side, use_ik)
            _view_layer_update(context)
        except Exception as exc:
            self.report({'ERROR'}, f"Arm {side} matching failed: {exc}")
            return {'CANCELLED'}
        if not changed:
            self.report({'ERROR'}, f"Could not find SaberRig source constraints for arm {side}")
            return {'CANCELLED'}
        if use_ik and diagnostics:
            error_deg = math.degrees(float(diagnostics.get("error", 0.0)))
            self.report({'INFO'}, f"Arm {side}: IK matched; pole error {error_deg:.2f}°")
        else:
            self.report({'INFO'}, f"Arm {side}: FK matched and active")
        return {'FINISHED'}

class SR_OT_SetArmJointLimits(Operator):
    bl_idname = "saberrig.set_arm_joint_limits"
    bl_label = "Set Arm Joint Limits"
    bl_description = "Enable or disable SaberRig SAFE joint limits on generated IK mechanism bones"
    bl_options = {'REGISTER', 'UNDO'}

    mode: StringProperty(default="SAFE")

    def execute(self, context):
        state = context.scene.saberrig_state
        arm_obj = _resolve_armature(context, state)
        if not arm_obj or arm_obj.type != 'ARMATURE':
            self.report({'ERROR'}, "SaberRig target armature is unavailable")
            return {'CANCELLED'}
        if not bool(arm_obj.get("saberrig_arms_ik_built", False)):
            self.report({'ERROR'}, "Build Arms IK first")
            return {'CANCELLED'}
        enabled = self.mode.upper() != "OFF"
        diagnostics = _apply_all_arm_joint_limits(arm_obj, enabled=enabled)
        _view_layer_update(context)
        errors = [f"{side}: {data.get('error')}" for side, data in diagnostics.items()
                  if isinstance(data, dict) and data.get("mode") == "ERROR"]
        if errors:
            self.report({'WARNING'}, "Joint limits updated with warnings: " + " | ".join(errors[:2]))
        else:
            self.report({'INFO'}, "SAFE arm joint limits enabled" if enabled else "Arm joint limits disabled")
        return {'FINISHED'}

class SR_OT_SetShoulderAssist(Operator):
    bl_idname = "saberrig.set_shoulder_assist"
    bl_label = "Set Shoulder Assist"
    bl_description = "Enable or disable automatic IK clavicle/shoulder follow on SaberRig mechanism bones"
    bl_options = {'REGISTER', 'UNDO'}

    mode: StringProperty(default="AUTO")

    def execute(self, context):
        state = context.scene.saberrig_state
        arm_obj = _resolve_armature(context, state)
        if not arm_obj or arm_obj.type != 'ARMATURE':
            self.report({'ERROR'}, "SaberRig target armature is unavailable")
            return {'CANCELLED'}
        if not bool(arm_obj.get("saberrig_arms_ik_built", False)):
            self.report({'ERROR'}, "Build Arms IK first")
            return {'CANCELLED'}
        enabled = self.mode.upper() != "OFF"
        applied = _apply_shoulder_assist_mode(arm_obj, enabled=enabled)
        _view_layer_update(context)
        if not applied:
            self.report({'WARNING'}, "No semantic clavicle/shoulder assist mechanism was available")
        else:
            strength = float(arm_obj.get("saberrig_shoulder_assist_strength", SR_SHOULDER_ASSIST_DEFAULT))
            self.report({'INFO'}, f"Shoulder Assist {'AUTO' if enabled else 'OFF'} ({strength * 100:.0f}% follow)")
        return {'FINISHED'}

class SR_OT_BuildLegsIK(Operator):
    bl_idname = "saberrig.build_legs_ik"
    bl_label = "Build Legs IK"
    bl_description = "Build production leg IK with Smart Knee Pole and reverse-foot Heel/Ball/Toe roll controls"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        state = context.scene.saberrig_state
        arm_obj = _resolve_armature(context, state)
        if not arm_obj:
            self.report({'ERROR'}, "SaberRig could not resolve a target armature"); return {'CANCELLED'}
        if not _armature_is_editable(arm_obj):
            self.report({'ERROR'}, "Target armature is linked/read-only"); return {'CANCELLED'}
        try:
            analysis = analyze_armature(arm_obj)
            generated, errors = _build_legs_ik(context, arm_obj, analysis)
            refreshed = analyze_armature(arm_obj); _fill_state(context.scene, state, arm_obj, refreshed)
        except Exception as exc:
            self.report({'ERROR'}, f"Leg IK build failed: {exc}"); return {'CANCELLED'}
        if errors:
            self.report({'WARNING'}, f"Built {len(generated)} leg controls; partial build: {' | '.join(errors[:2])}")
        else:
            self.report({'INFO'}, "Leg IK built with Smart Knee Pole, Heel/Ball/Toe Foot Roll, and SAFE joint limits")
        return {'FINISHED'}

class SR_OT_SetLegMode(Operator):
    bl_idname = "saberrig.set_leg_mode"
    bl_label = "Set Leg FK/IK Mode"
    bl_description = "Match destination controls, then switch a SaberRig leg between FK and IK"
    bl_options = {'REGISTER', 'UNDO'}
    side: StringProperty(default="L")
    mode: StringProperty(default="IK")

    def execute(self, context):
        state = context.scene.saberrig_state; arm_obj = _resolve_armature(context, state)
        if not arm_obj or arm_obj.type != 'ARMATURE':
            self.report({'ERROR'}, "SaberRig target armature is unavailable"); return {'CANCELLED'}
        if not bool(arm_obj.get("saberrig_legs_ik_built", False)):
            self.report({'ERROR'}, "Build Legs IK first"); return {'CANCELLED'}
        side = self.side.upper(); use_ik = self.mode.upper() == "IK"
        try:
            if use_ik: diagnostics = _snap_leg_fk_to_ik(context, arm_obj, side, calibrate=False)
            else: diagnostics = None; _snap_leg_ik_to_fk(context, arm_obj, side)
            changed = _set_leg_mode_raw(arm_obj, side, use_ik); _view_layer_update(context)
        except Exception as exc:
            self.report({'ERROR'}, f"Leg {side} matching failed: {exc}"); return {'CANCELLED'}
        if not changed:
            self.report({'ERROR'}, f"Could not find SaberRig source constraints for leg {side}"); return {'CANCELLED'}
        if use_ik and diagnostics:
            self.report({'INFO'}, f"Leg {side}: IK matched; pole error {math.degrees(float(diagnostics.get('error', 0.0))):.2f}°")
        else:
            self.report({'INFO'}, f"Leg {side}: FK matched and active")
        return {'FINISHED'}

class SR_OT_SetLegJointLimits(Operator):
    bl_idname = "saberrig.set_leg_joint_limits"
    bl_label = "Set Leg Joint Limits"
    bl_description = "Enable or disable semantic SAFE hip/knee/ankle limits on SaberRig leg mechanism bones"
    bl_options = {'REGISTER', 'UNDO'}
    mode: StringProperty(default="SAFE")

    def execute(self, context):
        state = context.scene.saberrig_state; arm_obj = _resolve_armature(context, state)
        if not arm_obj or arm_obj.type != 'ARMATURE':
            self.report({'ERROR'}, "SaberRig target armature is unavailable"); return {'CANCELLED'}
        if not bool(arm_obj.get("saberrig_legs_ik_built", False)):
            self.report({'ERROR'}, "Build Legs IK first"); return {'CANCELLED'}
        enabled = self.mode.upper() != "OFF"
        diagnostics = _apply_all_leg_joint_limits(arm_obj, enabled=enabled); _view_layer_update(context)
        errors = [f"{side}: {data.get('error')}" for side, data in diagnostics.items() if isinstance(data, dict) and data.get("mode") == "ERROR"]
        if errors: self.report({'WARNING'}, "Leg limits updated with warnings: " + " | ".join(errors[:2]))
        else: self.report({'INFO'}, "SAFE hip/knee/ankle limits enabled" if enabled else "Leg joint limits disabled")
        return {'FINISHED'}

class SR_OT_SetLegBendMode(Operator):
    bl_idname = "saberrig.set_leg_bend_mode"
    bl_label = "Set Knee Behaviour"
    bl_description = "Adjust the soft extension reserve layered on top of SaberRig's real pre-bent pole-driven MCH knee"
    bl_options = {'REGISTER', 'UNDO'}
    mode: StringProperty(default="SMOOTH")

    def execute(self, context):
        state = context.scene.saberrig_state; arm_obj = _resolve_armature(context, state)
        if not arm_obj or arm_obj.type != 'ARMATURE':
            self.report({'ERROR'}, "SaberRig target armature is unavailable"); return {'CANCELLED'}
        if not bool(arm_obj.get("saberrig_legs_ik_built", False)):
            self.report({'ERROR'}, "Build Legs IK first"); return {'CANCELLED'}
        smooth = self.mode.upper() != "RIGID"
        changed = _apply_all_leg_preferred_bend(arm_obj, smooth=smooth)
        _view_layer_update(context)
        if not changed:
            self.report({'WARNING'}, "Soft-extension constraints were not found; rebuild Legs IK")
        else:
            factor = float(arm_obj.get("saberrig_leg_preferred_reach_factor_L", SR_DEFAULT_LEG_PREFERRED_REACH_FACTOR))
            label = f"SMOOTH ({factor * 100:.2f}% soft cap + MCH pre-bend)" if smooth else "RIGID (100% target reach + MCH pre-bend)"
            self.report({'INFO'}, "Knee behaviour: " + label)
        return {'FINISHED'}

class SR_OT_SetKneePoleSpace(Operator):
    bl_idname = "saberrig.set_knee_pole_space"
    bl_label = "Set Knee Pole Space"
    bl_description = "Use a stable Smart Knee Pole space that tracks the hip-to-ankle direction, or a rig-global pole space for manual workflows"
    bl_options = {'REGISTER', 'UNDO'}
    mode: StringProperty(default="SMART")

    def execute(self, context):
        state = context.scene.saberrig_state; arm_obj = _resolve_armature(context, state)
        if not arm_obj or arm_obj.type != 'ARMATURE':
            self.report({'ERROR'}, "SaberRig target armature is unavailable"); return {'CANCELLED'}
        if not bool(arm_obj.get("saberrig_legs_ik_built", False)):
            self.report({'ERROR'}, "Build Legs IK first"); return {'CANCELLED'}
        mode = "GLOBAL" if self.mode.upper() == "GLOBAL" else "SMART"
        changed = _apply_all_knee_pole_space_mode(arm_obj, mode=mode, preserve_world=True)
        _view_layer_update(context)
        if not changed:
            self.report({'WARNING'}, "Smart Knee Pole mechanism was not found; rebuild Legs IK")
        else:
            self.report({'INFO'}, f"Knee Pole Space: {mode}")
        return {'FINISHED'}

class SR_OT_ResetFootRoll(Operator):
    bl_idname = "saberrig.reset_foot_roll"
    bl_label = "Reset Foot Roll"
    bl_description = "Reset Heel, Ball and Toe reverse-foot pivots to their neutral rotations"
    bl_options = {'REGISTER', 'UNDO'}
    side: StringProperty(default="L")

    def execute(self, context):
        arm_obj = _resolve_armature(context, context.scene.saberrig_state)
        if not arm_obj or arm_obj.type != 'ARMATURE':
            self.report({'ERROR'}, "SaberRig target armature is unavailable")
            return {'CANCELLED'}
        side = self.side.upper()
        count = _reset_foot_roll_controls(arm_obj, side)
        _view_layer_update(context)
        if not count:
            self.report({'WARNING'}, f"No reverse-foot controls found for leg {side}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Leg {side}: Heel/Ball/Toe Foot Roll reset")
        return {'FINISHED'}

class SR_OT_SetRigView(Operator):
    bl_idname = "saberrig.set_rig_view"
    bl_label = "Set SaberRig View"
    bl_description = "Show only animator controls or restore the original source rig visibility"
    bl_options = {'REGISTER', 'UNDO'}

    mode: StringProperty(default="CONTROLS")

    def execute(self, context):
        state = context.scene.saberrig_state
        arm_obj = _resolve_armature(context, state)
        if not arm_obj or arm_obj.type != 'ARMATURE':
            self.report({'ERROR'}, "SaberRig target armature is unavailable")
            return {'CANCELLED'}
        if not _foundation_is_prepared(arm_obj):
            self.report({'ERROR'}, "Prepare SaberRig first")
            return {'CANCELLED'}

        controls_only = self.mode.upper() == "CONTROLS"
        try:
            _setup_control_visuals(arm_obj)
            _set_rig_view(arm_obj, controls_only)
            _activate_armature(context, arm_obj, mode='POSE')
        except Exception as exc:
            self.report({'ERROR'}, f"Could not change rig view: {exc}")
            return {'CANCELLED'}

        self.report({'INFO'}, "Controls-only view enabled" if controls_only else "Source rig visibility restored")
        return {'FINISHED'}

class SR_OT_RefreshControlVisuals(Operator):
    bl_idname = "saberrig.refresh_control_visuals"
    bl_label = "Refresh Rig Widgets"
    bl_description = "Recreate/upgrade and reattach SaberRig procedural custom bone shapes without rebuilding the rig"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        state = context.scene.saberrig_state
        arm_obj = _resolve_armature(context, state)
        if not arm_obj or arm_obj.type != 'ARMATURE':
            self.report({'ERROR'}, "SaberRig target armature is unavailable")
            return {'CANCELLED'}
        try:
            count = _setup_control_visuals(arm_obj)
        except Exception as exc:
            self.report({'ERROR'}, f"Could not refresh control shapes: {exc}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Refreshed {count} SaberRig Rig Widgets/custom control shapes")
        return {'FINISHED'}

class SR_OT_BuildSecondaryMotion(Operator):
    bl_idname = "saberrig.build_secondary_motion"
    bl_label = "Build Secondary Motion"
    bl_description = "Build safe semantic secondary-motion chains for UMA, MMD, game-FBX and other supported armatures"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        state = context.scene.saberrig_state
        arm_obj = _resolve_armature(context, state)
        if not arm_obj:
            self.report({'ERROR'}, "SaberRig could not resolve a target armature")
            return {'CANCELLED'}
        try:
            analysis = analyze_armature(arm_obj)
            count, families = _build_wiggle_setup(context, arm_obj, analysis)
            _ensure_wiggle_handler()
        except Exception as exc:
            self.report({'ERROR'}, f"Could not build Secondary Motion: {exc}")
            return {'CANCELLED'}
        family_text = ", ".join(f"{k}:{v}" for k, v in sorted(families.items()) if v)
        preset = str(arm_obj.get("saberrig_wiggle_preset", "Semantic"))
        self.report({'INFO'}, f"Built {count} {preset} secondary bones ({family_text})")
        return {'FINISHED'}

class SR_OT_BuildUmaWiggle(Operator):
    bl_idname = "saberrig.build_uma_wiggle"
    bl_label = "Build UMA Secondary Motion"
    bl_description = "Legacy compatibility alias for generalized SaberRig Secondary Motion"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        state = context.scene.saberrig_state
        arm_obj = _resolve_armature(context, state)
        if not arm_obj:
            self.report({'ERROR'}, "SaberRig could not resolve a target armature"); return {'CANCELLED'}
        try:
            analysis = analyze_armature(arm_obj)
            count, families = _build_wiggle_setup(context, arm_obj, analysis)
            _ensure_wiggle_handler()
        except Exception as exc:
            self.report({'ERROR'}, f"Could not build Secondary Motion: {exc}"); return {'CANCELLED'}
        family_text = ", ".join(f"{k}:{v}" for k, v in sorted(families.items()) if v)
        self.report({'INFO'}, f"Built {count} secondary bones ({family_text})")
        return {'FINISHED'}

class SR_OT_ToggleWiggle(Operator):
    bl_idname = "saberrig.toggle_wiggle"
    bl_label = "Toggle Wiggle Preview"
    bl_options = {'REGISTER', 'UNDO'}
    enabled: BoolProperty(default=True)

    def execute(self, context):
        arm_obj = _resolve_armature(context, context.scene.saberrig_state)
        if not arm_obj or not bool(arm_obj.get("saberrig_wiggle_built", False)):
            self.report({'ERROR'}, "Build Secondary Motion first"); return {'CANCELLED'}
        arm_obj["saberrig_wiggle_enabled"] = bool(self.enabled)
        _wiggle_reset_runtime(arm_obj)
        _wiggle_apply_constraint_influences(arm_obj)
        if self.enabled: _ensure_wiggle_handler()
        self.report({'INFO'}, "Secondary-motion preview enabled" if self.enabled else "Secondary-motion preview disabled")
        return {'FINISHED'}

class SR_OT_ToggleWiggleFamily(Operator):
    bl_idname = "saberrig.toggle_wiggle_family"
    bl_label = "Toggle Wiggle Family"
    bl_options = {'REGISTER', 'UNDO'}
    family: StringProperty(default="HAIR")

    def execute(self, context):
        arm_obj = _resolve_armature(context, context.scene.saberrig_state)
        family = self.family.upper()
        if not arm_obj or family not in SR_WIGGLE_PROFILES:
            return {'CANCELLED'}
        key = f"saberrig_wiggle_family_{family}"
        arm_obj[key] = not bool(arm_obj.get(key, SR_WIGGLE_PROFILES[family]["default"]))
        _wiggle_reset_runtime(arm_obj, families={family})
        _wiggle_apply_constraint_influences(arm_obj)
        return {'FINISHED'}

class SR_OT_ResetWiggle(Operator):
    bl_idname = "saberrig.reset_wiggle"
    bl_label = "Reset Wiggle"
    bl_description = "Clear spring velocity and return secondary MCH bones to their neutral pose"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        arm_obj = _resolve_armature(context, context.scene.saberrig_state)
        if not arm_obj:
            return {'CANCELLED'}
        _wiggle_reset_runtime(arm_obj)
        _view_layer_update(context)
        self.report({'INFO'}, "Secondary Motion state reset")
        return {'FINISHED'}

class SR_OT_RemoveWiggle(Operator):
    bl_idname = "saberrig.remove_wiggle"
    bl_label = "Remove Secondary Motion"
    bl_description = "Remove only SaberRig secondary-motion bones, constraints and runtime state"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        arm_obj = _resolve_armature(context, context.scene.saberrig_state)
        if not arm_obj:
            return {'CANCELLED'}
        try:
            bones, constraints = _remove_wiggle_setup(context, arm_obj)
        except Exception as exc:
            self.report({'ERROR'}, f"Could not remove Wiggle Bones: {exc}"); return {'CANCELLED'}
        self.report({'INFO'}, f"Removed Secondary Motion setup: {bones} bones, {constraints} constraints")
        return {'FINISHED'}

class SR_OT_RemoveSetup(Operator):
    bl_idname = "saberrig.remove_setup"
    bl_label = "Remove SaberRig Setup"
    bl_description = "Remove only SaberRig-generated bones, constraints, collections, and metadata"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        state = context.scene.saberrig_state
        arm_obj = _resolve_armature(context, state)
        if not arm_obj:
            self.report({'ERROR'}, "SaberRig could not resolve a target armature")
            return {'CANCELLED'}
        if not bool(arm_obj.get("saberrig_prepared", False)) and not _generated_bones(arm_obj):
            self.report({'INFO'}, "No SaberRig setup was found on this armature")
            return {'CANCELLED'}

        try:
            bones, constraints = _remove_saberrig_setup(context, arm_obj)
            analysis = analyze_armature(arm_obj)
            _fill_state(context.scene, state, arm_obj, analysis)
        except Exception as exc:
            self.report({'ERROR'}, f"Could not remove SaberRig setup: {exc}")
            return {'CANCELLED'}

        self.report({'INFO'}, f"Removed SaberRig setup: {bones} bones, {constraints} constraints")
        return {'FINISHED'}

# -----------------------------------------------------------------------------
# UI
# -----------------------------------------------------------------------------

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
    bl_label = "SaberRig"
    bl_idname = "SR_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "SaberRig"

    def draw(self, context):
        layout = self.layout
        state = context.scene.saberrig_state

        header = layout.column(align=True)
        header.label(text=f"SaberRig {ADDON_VERSION}", icon='ARMATURE_DATA')

        # Keep the primary workflow immediately visible.
        arm_obj = _resolve_armature(context, state)
        prepared = bool(arm_obj and _foundation_is_prepared(arm_obj))

        row = layout.row()
        row.scale_y = 1.35
        row.operator(
            "saberrig.prepare_foundation",
            text="Prepare SaberRig" if not prepared else "Prepare SaberRig ✓",
            icon='ADD' if not prepared else 'CHECKMARK'
        )

        row = layout.row(align=True)
        row.scale_y = 1.20
        row.operator("saberrig.analyze_rig", text="Analyze Rig", icon='VIEWZOOM')
        row.operator("saberrig.run_rig_qa", text="Validate Rig", icon='CHECKMARK')

        more_box = layout.box()
        row = more_box.row(align=True)
        row.prop(
            state, "show_more_info", text="",
            icon='TRIA_DOWN' if state.show_more_info else 'TRIA_RIGHT',
            emboss=False
        )
        row.label(text="More info", icon='INFO')

        if state.show_more_info:
            more_box.label(
                text=f"{SR_RELEASE_CHANNEL} {SR_RELEASE_CANDIDATE} · Blender {TARGET_BLENDER}",
                icon='CHECKMARK'
            )
            more_box.label(
                text="Semantic Animation Rig · Production Pipeline · QA",
                icon='CON_KINEMATIC'
            )

            target_box = more_box.box()
            target_box.label(text="Target", icon='EYEDROPPER')
            target_box.prop(state, "target", text="Armature")
            if state.analyzed:
                row = target_box.row(align=True)
                row.label(text=f"Analyzed: {state.armature_name}", icon='CHECKMARK')
                row.operator("saberrig.clear_analysis", text="", icon='X')
            else:
                target_box.label(
                    text="Leave Target empty to use the active/selected armature automatically.",
                    icon='INFO'
                )

            if state.analyzed:
                detect = more_box.box()
                detect.label(text="Detected Rig", icon='OUTLINER_OB_ARMATURE')
                detect.label(text=f"Family: {state.game_family}")
                detect.label(text=f"Dialect: {state.rig_dialect}")
                detect.label(text=f"Source style: {state.source_style}")
                support = "RC profile path" if state.profile_id in SR_SUPPORTED_PROFILE_IDS else "Generic / experimental path"
                detect.label(
                    text=f"Profile: {state.profile_id} · {support}",
                    icon='CHECKMARK' if state.profile_id in SR_SUPPORTED_PROFILE_IDS else 'INFO'
                )
                row = detect.row()
                row.label(
                    text=f"Profile confidence: {state.profile_confidence * 100:.0f}%",
                    icon=_confidence_icon(state.profile_confidence)
                )
                row = detect.row()
                row.label(
                    text=f"Core coverage: {state.semantic_coverage * 100:.0f}%",
                    icon=_confidence_icon(state.semantic_coverage)
                )
                detect.label(text=f"Bones: {state.bone_count}  |  Pose bones: {state.pose_bone_count}")
                detect.label(text=f"Constraints: {state.constraint_count}  |  IK: {state.ik_constraint_count}")
                if state.finger_segment_count:
                    detect.label(text=f"Finger segments detected: {state.finger_segment_count}")

                map_box = more_box.box()
                row = map_box.row()
                row.prop(
                    state, "show_mapping", text="",
                    icon='TRIA_DOWN' if state.show_mapping else 'TRIA_RIGHT',
                    emboss=False
                )
                row.label(text="Semantic Skeleton", icon='BONE_DATA')

                if state.show_mapping:
                    for group_name, roles in DISPLAY_GROUPS:
                        rows = []
                        for role in roles:
                            item = _find_mapping(state, role)
                            if item and (
                                item.found or role in {
                                    "pelvis", "spine_01", "neck", "head",
                                    "upper_arm.L", "forearm.L", "hand.L",
                                    "upper_arm.R", "forearm.R", "hand.R",
                                    "thigh.L", "shin.L", "foot.L",
                                    "thigh.R", "shin.R", "foot.R"
                                }
                            ):
                                rows.append(item)
                        if not rows:
                            continue
                        sub = map_box.column(align=True)
                        sub.label(text=group_name)
                        for item in rows:
                            row = sub.row(align=True)
                            if item.found:
                                row.label(text=item.label, icon=_confidence_icon(item.confidence))
                                op = row.operator(
                                    "saberrig.select_mapped_bone",
                                    text=item.bone_name,
                                    emboss=False
                                )
                                op.bone_name = item.bone_name
                                row.label(text=f"{item.confidence * 100:.0f}%")
                            else:
                                row.label(text=item.label, icon='ERROR')
                                row.label(text="Not found")

                class_box = more_box.box()
                row = class_box.row()
                row.prop(
                    state, "show_classes", text="",
                    icon='TRIA_DOWN' if state.show_classes else 'TRIA_RIGHT',
                    emboss=False
                )
                row.label(text="Bone Classification", icon='GROUP_BONE')
                if state.show_classes:
                    grid = class_box.grid_flow(
                        row_major=True, columns=2,
                        even_columns=True, even_rows=False, align=True
                    )
                    for item in state.class_counts:
                        grid.label(text=item.name.replace("_", " ").title())
                        grid.label(text=str(item.value))

                if state.warnings:
                    warn_box = more_box.box()
                    row = warn_box.row()
                    row.prop(
                        state, "show_warnings", text="",
                        icon='TRIA_DOWN' if state.show_warnings else 'TRIA_RIGHT',
                        emboss=False
                    )
                    row.label(text=f"Warnings ({len(state.warnings)})", icon='ERROR')
                    if state.show_warnings:
                        for item in state.warnings:
                            col = warn_box.column(align=True)
                            col.label(text=item.text, icon='DOT')

                qa_box = more_box.box()
                qa_box.label(text="Rig QA / Diagnostics", icon='CHECKMARK')
                qa_arm = _resolve_armature(context, state)
                qa_ran = bool(qa_arm and "saberrig_qa_status" in qa_arm)
                if qa_ran:
                    score = int(qa_arm.get("saberrig_qa_score", 0))
                    status = str(qa_arm.get("saberrig_qa_status", "NOT_RUN"))
                    err = int(qa_arm.get("saberrig_qa_error_count", 0))
                    warn = int(qa_arm.get("saberrig_qa_warning_count", 0))
                    info = int(qa_arm.get("saberrig_qa_info_count", 0))
                    status_icon = 'ERROR' if err else ('QUESTION' if warn else 'CHECKMARK')
                    qa_box.label(text=f"Rig Health: {score}% — {status}", icon=status_icon)
                    qa_box.label(text=f"Errors {err}  |  Warnings {warn}  |  Info {info}")
                    generated = int(qa_arm.get("saberrig_qa_generated_checked", 0))
                    constraints = int(qa_arm.get("saberrig_qa_constraints_checked", 0))
                    drivers = int(qa_arm.get("saberrig_qa_drivers_checked", 0))
                    qa_box.label(
                        text=f"Checked: {generated} generated bones · {constraints} constraints · {drivers} drivers",
                        icon='INFO'
                    )
                    try:
                        issues = json.loads(str(qa_arm.get("saberrig_qa_issues_json", "[]")))
                    except Exception:
                        issues = []
                    visible = [i for i in issues if i.get("severity") in {"ERROR", "WARN"}]
                    if not visible:
                        visible = issues
                    for issue in visible[:SR_QA_MAX_PANEL_ISSUES]:
                        sev = str(issue.get("severity", "INFO"))
                        icon = 'ERROR' if sev == "ERROR" else ('QUESTION' if sev == "WARN" else 'INFO')
                        qa_box.label(text=f"{sev}: {issue.get('message', '')}", icon=icon)
                    if len(visible) > SR_QA_MAX_PANEL_ISSUES:
                        qa_box.label(
                            text=f"+ {len(visible) - SR_QA_MAX_PANEL_ISSUES} more issue(s) in exported QA report.",
                            icon='INFO'
                        )
                    qa_box.operator("saberrig.export_rig_qa", text="Export QA Report", icon='EXPORT')
                else:
                    qa_box.label(
                        text="Use Validate Rig above to check semantics, components, constraints, drivers and solver state.",
                        icon='INFO'
                    )

                more_box.operator(
                    "saberrig.export_analysis",
                    text="Export Analysis",
                    icon='EXPORT'
                )
            else:
                more_box.label(
                    text="Prepare SaberRig or Analyze Rig to populate detection and diagnostics.",
                    icon='INFO'
                )

        if not state.analyzed:
            hint = layout.box()
            hint.label(
                text="Prepare SaberRig will analyze the active/selected armature automatically.",
                icon='INFO'
            )
            return

        layout.separator()

        arm_obj = state.target if state.target and state.target.type == 'ARMATURE' else None
        rig_box = layout.box()
        rig_box.label(text="Control Rig", icon='CON_KINEMATIC')
        if arm_obj:
            prepared = _foundation_is_prepared(arm_obj)
            generated_count = len(_generated_bones(arm_obj))
            arms_built = bool(arm_obj.get("saberrig_arms_fk_built", False))

            status_row = rig_box.row()
            status_row.label(text="Status: Prepared" if prepared else "Status: Not prepared",
                             icon='CHECKMARK' if prepared else 'INFO')
            if generated_count:
                rig_box.label(text=f"Generated SaberRig bones: {generated_count}")

            if prepared:
                view_mode = arm_obj.get("saberrig_view_mode", "SOURCE")
                view_box = rig_box.box()
                view_box.label(text="Rig Widgets / View", icon='POSE_HLT')
                widget_count = int(arm_obj.get("saberrig_widget_control_count", 0))
                widget_lib = str(arm_obj.get("saberrig_widget_library_version", ""))
                view_box.label(
                    text=f"Custom control shapes: {widget_count} attached"
                    + (f" · library {widget_lib}" if widget_lib else ""),
                    icon='CHECKMARK' if widget_lib else 'INFO'
                )
                row = view_box.row(align=True)
                op = row.operator("saberrig.set_rig_view", text="Controls Only", icon='POSE_HLT', depress=(view_mode == "CONTROLS"))
                op.mode = "CONTROLS"
                op = row.operator("saberrig.set_rig_view", text="Show Source Rig", icon='ARMATURE_DATA', depress=(view_mode != "CONTROLS"))
                op.mode = "SOURCE"
                row = view_box.row(align=True)
                row.operator("saberrig.refresh_control_visuals", text="Refresh Rig Widgets", icon='FILE_REFRESH')
                view_box.label(text="Animator controls use procedural bone shapes; MCH stays hidden.", icon='INFO')

            if not prepared:
                rig_box.label(text="Use Prepare SaberRig above to enable rig components.", icon='INFO')
            else:
                rig_box.label(text="Source bones are preserved; MCH bones are non-deforming.", icon='LOCKED')

                body_box = rig_box.box()
                body_box.label(text="Body Controls", icon='ARMATURE_DATA')
                body_built = bool(arm_obj.get("saberrig_body_built", False))
                row = body_box.row()
                row.scale_y = 1.15
                row.operator("saberrig.build_body_controls",
                             text="Rebuild Root / COG / Spine" if body_built else "Build Root / COG / Spine",
                             icon='BONE_DATA')
                if body_built:
                    bn = _body_control_names()
                    row = body_box.row(align=True)
                    op = row.operator("saberrig.select_mapped_bone", text="Master", icon='EMPTY_ARROWS'); op.bone_name = bn["ctrl_master"]
                    op = row.operator("saberrig.select_mapped_bone", text="Root", icon='EMPTY_ARROWS'); op.bone_name = bn["ctrl_root"]
                    op = row.operator("saberrig.select_mapped_bone", text="COG", icon='CON_ROTLIKE'); op.bone_name = bn["ctrl_cog"]
                    row = body_box.row(align=True)
                    op = row.operator("saberrig.select_mapped_bone", text="Hips", icon='CON_ROTLIKE'); op.bone_name = bn["ctrl_hips"]
                    op = row.operator("saberrig.select_mapped_bone", text="Spine", icon='CON_ROTLIKE'); op.bone_name = bn["ctrl_spine"]
                    if arm_obj.pose.bones.get(bn["ctrl_chest"]):
                        op = row.operator("saberrig.select_mapped_bone", text="Chest", icon='CON_ROTLIKE'); op.bone_name = bn["ctrl_chest"]
                    row = body_box.row(align=True)
                    if arm_obj.pose.bones.get(bn["ctrl_neck"]):
                        op = row.operator("saberrig.select_mapped_bone", text="Neck", icon='CON_ROTLIKE'); op.bone_name = bn["ctrl_neck"]
                    op = row.operator("saberrig.select_mapped_bone", text="Head", icon='CON_ROTLIKE'); op.bone_name = bn["ctrl_head"]
                    body_box.label(text="Master moves all IK + body; Root/COG keep hand/foot IK planted.", icon='INFO')
                    body_box.label(text=f"Root source: {arm_obj.get('saberrig_body_root_source', 'SYNTHETIC')} | Chest: {arm_obj.get('saberrig_body_chest_source', 'NONE')}", icon='INFO')
                else:
                    body_box.label(text="Master → Root → COG → Hips → Spine → Chest → Neck → Head", icon='INFO')

                row = rig_box.row()
                row.scale_y = 1.15
                row.operator("saberrig.build_arms_fk",
                             text="Rebuild Arms FK" if arms_built else "Build Arms FK",
                             icon='BONE_DATA')

                ik_built = bool(arm_obj.get("saberrig_arms_ik_built", False))
                row = rig_box.row()
                row.scale_y = 1.15
                row.operator("saberrig.build_arms_ik",
                             text="Rebuild Arms IK" if ik_built else "Build Arms IK",
                             icon='CON_KINEMATIC')

                if ik_built:
                    rig_box.label(text="IK is roll-calibrated, reach-limited, joint-safe, and shoulder-assisted.", icon='INFO')
                    limits_mode = str(arm_obj.get("saberrig_joint_limits_mode", "SAFE")).upper()
                    limits_box = rig_box.box()
                    limits_box.label(text="Joint Limits", icon='CON_ROTLIMIT')
                    row = limits_box.row(align=True)
                    op = row.operator("saberrig.set_arm_joint_limits", text="Off", depress=(limits_mode == "OFF"))
                    op.mode = "OFF"
                    op = row.operator("saberrig.set_arm_joint_limits", text="Safe", depress=(limits_mode != "OFF"))
                    op.mode = "SAFE"
                    limits_box.label(text="SAFE constrains SaberRig MCH only; source bones stay untouched.", icon='INFO')

                    assist_mode = str(arm_obj.get("saberrig_shoulder_assist_mode", "AUTO")).upper()
                    assist_strength = float(arm_obj.get("saberrig_shoulder_assist_strength", SR_SHOULDER_ASSIST_DEFAULT))
                    assist_box = rig_box.box()
                    assist_box.label(text="Shoulder Assist", icon='CON_TRACKTO')
                    row = assist_box.row(align=True)
                    op = row.operator("saberrig.set_shoulder_assist", text="Off", depress=(assist_mode == "OFF"))
                    op.mode = "OFF"
                    op = row.operator("saberrig.set_shoulder_assist", text="Auto", depress=(assist_mode != "OFF"))
                    op.mode = "AUTO"
                    assist_box.label(text=f"Auto clavicle follow: {assist_strength * 100:.0f}% (IK only)", icon='INFO')

                    for side in ("L", "R"):
                        mode = arm_obj.get(f"saberrig_arm_mode_{side}", "FK")
                        sub = rig_box.box()
                        row = sub.row(align=True)
                        row.label(text=f"Arm {side}: {mode}", icon='CON_KINEMATIC' if mode == "IK" else 'BONE_DATA')
                        op = row.operator("saberrig.set_arm_mode", text="Use FK", depress=(mode == "FK"))
                        op.side = side
                        op.mode = "FK"
                        op = row.operator("saberrig.set_arm_mode", text="Use IK", depress=(mode == "IK"))
                        op.side = side
                        op.mode = "IK"

                        names = _arm_ik_names(side)
                        row = sub.row(align=True)
                        op = row.operator("saberrig.select_mapped_bone", text="Select Hand IK", icon='HAND')
                        op.bone_name = names["ctrl_hand"]
                        op = row.operator("saberrig.select_mapped_bone", text="Select Elbow Pole", icon='EMPTY_ARROWS')
                        op.bone_name = names["ctrl_pole"]

                        angle = float(arm_obj.get(f"saberrig_pole_angle_{side}", 0.0))
                        err = float(arm_obj.get(f"saberrig_pole_error_{side}", 0.0))
                        reach_factor = float(arm_obj.get(f"saberrig_arm_reach_factor_{side}", SR_DEFAULT_ARM_REACH_FACTOR))
                        preferred_factor = float(arm_obj.get(f"saberrig_arm_preferred_reach_factor_{side}", SR_DEFAULT_ARM_PREFERRED_REACH_FACTOR))
                        preferred_active = bool(arm_obj.get(f"saberrig_arm_preferred_reach_active_{side}", False))
                        singular = bool(arm_obj.get(f"saberrig_pole_singular_{side}", False))
                        prebend_active = bool(arm_obj.get(f"saberrig_arm_prebend_active_{side}", False))
                        prebend_angle = math.degrees(float(arm_obj.get(f"saberrig_arm_prebend_angle_{side}", 0.0)))
                        solver_state = str(arm_obj.get(f"saberrig_arm_solver_state_{side}", "STABLE"))
                        diag = sub.row()
                        if preferred_active:
                            diag.label(text=f"Pole {math.degrees(angle):+.1f}° | error {math.degrees(err):.2f}° | preferred {preferred_factor * 100:.1f}%")
                        else:
                            diag.label(text=f"Pole {math.degrees(angle):+.1f}° | error {math.degrees(err):.2f}° | guard {reach_factor * 100:.0f}%")
                        if singular and prebend_active:
                            sub.label(
                                text=f"Straight-rest stabilized | MCH pre-bend {prebend_angle:.1f}° | solver {solver_state}",
                                icon='CHECKMARK'
                            )
                        elif singular:
                            sub.label(text="Straight-arm singularity detected; fallback only.", icon='ERROR')

                        hinge_axis = str(arm_obj.get(f"saberrig_elbow_hinge_axis_{side}", "?"))
                        hinge_mode = str(arm_obj.get(f"saberrig_elbow_hinge_mode_{side}", "OFF"))
                        hinge_conf = float(arm_obj.get(f"saberrig_elbow_hinge_confidence_{side}", 0.0))
                        canonical_sign = float(arm_obj.get(f"saberrig_elbow_canonical_sign_{side}", 0.0))
                        canonical_quality = float(arm_obj.get(f"saberrig_elbow_canonical_quality_{side}", 0.0))
                        flex_deg = float(arm_obj.get(f"saberrig_elbow_flexion_deg_{side}", math.degrees(SR_ELBOW_SAFE_FLEXION)))
                        hyper_deg = float(arm_obj.get(f"saberrig_elbow_hyper_deg_{side}", math.degrees(SR_ELBOW_SAFE_HYPEREXTENSION)))
                        if limits_mode != "OFF":
                            side_label = "+" if canonical_sign > 0.0 else ("-" if canonical_sign < 0.0 else "?")
                            sub.label(
                                text=f"Elbow {hinge_mode}: axis {hinge_axis} | flex side {side_label} | range {hyper_deg:.0f}° / {flex_deg:.0f}°",
                                icon='CON_ROTLIMIT'
                            )
                            if prebend_active:
                                sub.label(
                                    text=f"Canonical hinge quality: {canonical_quality * 100:.0f}%",
                                    icon='CHECKMARK' if canonical_quality >= SR_ELBOW_CANONICAL_SIGN_MIN_QUALITY else 'INFO'
                                )

                        guard_mode = str(arm_obj.get(f"saberrig_elbow_pole_guard_mode_{side}", "OFF"))
                        guard_axis = str(arm_obj.get(f"saberrig_elbow_pole_guard_axis_{side}", "?"))
                        guard_quality = float(arm_obj.get(f"saberrig_elbow_pole_guard_quality_{side}", 0.0))
                        if guard_mode != "OFF":
                            sub.label(
                                text=f"Pole Plane Guard: {guard_mode} | axis {guard_axis} | quality {guard_quality * 100:.0f}%",
                                icon='CON_LOCLIMIT'
                            )

                        twist_count = int(arm_obj.get(f"saberrig_twist_preserve_count_{side}", 0))
                        shoulder_available = bool(arm_obj.pose.bones.get(names.get("mch_shoulder")))
                        if shoulder_available:
                            sub.label(text=f"Shoulder assist: {assist_mode} | twist helpers preserved: {twist_count}", icon='CON_TRACKTO')
                        elif twist_count:
                            sub.label(text=f"Twist helpers preserved: {twist_count}", icon='CON_ROTLIKE')

                    rig_box.label(text="Straight-rest arms use canonical one-sided elbow flexion plus an AUTO pole-plane guard.", icon='INFO')
                else:
                    rig_box.label(text="Build IK to move the arms from hand targets.", icon='INFO')

                space_box = rig_box.box()
                space_box.label(text="Animation Spaces", icon='CON_CHILDOF')
                spaces_built = bool(arm_obj.get("saberrig_animation_spaces_built", False))
                row = space_box.row()
                row.scale_y = 1.15
                row.operator(
                    "saberrig.build_animation_spaces",
                    text="Refresh Animation Spaces" if spaces_built else "Build Animation Spaces",
                    icon='CON_CHILDOF'
                )
                if spaces_built:
                    space_box.label(text="Switch Space — Keep Transform. World follows Master when Body Controls exist.", icon='INFO')
                    for kind, label in (("HAND", "Hand IK"), ("FOOT", "Foot Master")):
                        for side in ("L", "R"):
                            ctrl_name = _animation_space_control_name(kind, side)
                            if not arm_obj.pose.bones.get(ctrl_name or ""):
                                continue
                            available = str(arm_obj.get(f"saberrig_space_available_{kind}_{side}", "WORLD")).split("|")
                            current = str(arm_obj.get(_animation_space_mode_key(kind, side), "WORLD")).upper()
                            sub = space_box.box()
                            row = sub.row(align=True)
                            row.label(text=f"{label} {side}: {SR_ANIMATION_SPACE_LABELS.get(current, current.title())}", icon='CON_CHILDOF')
                            for space in ("WORLD", "ROOT", "COG", "CHEST", "SHOULDER"):
                                if space not in available:
                                    continue
                                op = row.operator(
                                    "saberrig.set_animation_space",
                                    text=SR_ANIMATION_SPACE_LABELS[space],
                                    depress=(current == space)
                                )
                                op.kind = kind
                                op.side = side
                                op.space = space
                else:
                    space_box.label(text="Adds World/Root/COG spaces; hands also get Chest/Shoulder when available.", icon='INFO')

                finger_box = rig_box.box()
                finger_box.label(text="Finger Controls", icon='HAND')
                fingers_built = bool(arm_obj.get("saberrig_fingers_built", False))
                row = finger_box.row()
                row.scale_y = 1.15
                row.operator("saberrig.build_finger_controls",
                             text="Rebuild Finger Controls" if fingers_built else "Build Finger Controls",
                             icon='HAND')
                if fingers_built:
                    finger_box.label(text="Fist = target pose | Curl = anatomical chain flex | Spread = fan/close.", icon='INFO')
                    for side in ("L", "R"):
                        fn = _finger_control_names(side)
                        ctrl = arm_obj.pose.bones.get(fn["ctrl_master"])
                        if not ctrl:
                            continue
                        sub = finger_box.box()
                        row = sub.row(align=True)
                        row.label(text=f"Hand {side}", icon='HAND')
                        op = row.operator("saberrig.select_mapped_bone", text="Select", icon='BONE_DATA'); op.bone_name = fn["ctrl_master"]
                        op = row.operator("saberrig.reset_finger_controls", text="Reset", icon='RECOVER_LAST'); op.side = side
                        try:
                            sub.prop(ctrl, '["fist"]', text="Fist", slider=True)
                            sub.prop(ctrl, '["curl"]', text="Curl", slider=True)
                            sub.prop(ctrl, '["spread"]', text="Spread", slider=True)
                            row = sub.row(align=True)
                            row.prop(ctrl, '["thumb_curl"]', text="Thumb Curl", slider=True)
                            row.prop(ctrl, '["thumb_spread"]', text="Thumb Spread", slider=True)
                            sub.label(text="Individual Curl", icon='CON_ROTLIKE')
                            grid = sub.grid_flow(columns=2, even_columns=True, even_rows=True, align=True)
                            for finger in ("index", "middle", "ring", "pinky"):
                                grid.prop(ctrl, f'["{finger}_curl"]', text=SR_FINGER_LABELS[finger], slider=True)
                        except Exception:
                            pass
                        segs = int(arm_obj.get(f"saberrig_fingers_segments_{side}", 0))
                        cconf = float(arm_obj.get(f"saberrig_fingers_curl_confidence_{side}", 0.0))
                        sconf = float(arm_obj.get(f"saberrig_fingers_spread_confidence_{side}", 0.0))
                        sub.label(text=f"{segs} segments | axis confidence curl {cconf * 100:.0f}% / spread {sconf * 100:.0f}%", icon='INFO')
                        hemi = str(arm_obj.get(f"saberrig_fingers_curl_hemisphere_{side}", "GEOMETRIC"))
                        cdir = str(arm_obj.get(f"saberrig_fingers_curl_direction_{side}", "GEOMETRIC"))
                        sub.label(text=f"Palm frame: {hemi} | Curl driver: {cdir}", icon='CON_ROTLIKE')
                        twmode = str(arm_obj.get(f"saberrig_fingers_thumb_fist_mode_{side}", ""))
                        if twmode:
                            twdeg = float(arm_obj.get(f"saberrig_fingers_thumb_wrap_deg_{side}", 0.0))
                            twconf = float(arm_obj.get(f"saberrig_fingers_thumb_wrap_confidence_{side}", 0.0))
                            tcdeg = float(arm_obj.get(f"saberrig_fingers_thumb_root_curl_deg_{side}", 0.0))
                            tcconf = float(arm_obj.get(f"saberrig_fingers_thumb_root_curl_confidence_{side}", 0.0))
                            tq = float(arm_obj.get(f"saberrig_fingers_thumb_target_quality_{side}", max(twconf, tcconf)))
                            tcount = int(arm_obj.get(f"saberrig_fingers_thumb_target_count_{side}", 0))
                            if twmode in {"FIST_SPACE_QUATERNION", "PARENT_RELATIVE_QUATERNION"}:
                                sub.label(text=f"Thumb Fist: {twmode} | targets {tcount}/3 | quality {tq * 100:.0f}%", icon='CON_ROTLIKE')
                                tdepth = str(arm_obj.get(f"saberrig_fingers_thumb_contact_depth_{side}", ""))
                                if tdepth:
                                    sub.label(text=f"Contact depth: {tdepth}", icon='CON_DISTLIMIT')
                                tcal = str(arm_obj.get(f"saberrig_fingers_thumb_calibration_{side}", ""))
                                if tcal:
                                    sub.label(text=f"Thumb1 calibration: {tcal}", icon='CON_ROTLIKE')
                                rangles = str(arm_obj.get(f"saberrig_fingers_thumb_relative_angles_{side}", ""))
                                rapplied = int(arm_obj.get(f"saberrig_fingers_thumb_relative_applied_{side}", 0))
                                if twmode == "PARENT_RELATIVE_QUATERNION":
                                    sub.label(text=f"Local joints: {rapplied}/3 | quaternion Δ {rangles}°", icon='CON_ROTLIKE')
                                    tpol = str(arm_obj.get(f"saberrig_fingers_thumb_curl_polarity_{side}", "GEOMETRIC"))
                                    hc = int(arm_obj.get(f"saberrig_fingers_thumb_hemisphere_corrections_{side}", 0))
                                    hl = str(arm_obj.get(f"saberrig_fingers_thumb_hemisphere_labels_{side}", ""))
                                    taxis = str(arm_obj.get(f"saberrig_fingers_thumb_curl_axis_{side}", "AUTO"))
                                    sub.label(text=f"Thumb Curl: {tpol} | Axis {taxis}", icon='CON_ROTLIKE')
                                    tproj = str(arm_obj.get(f"saberrig_fingers_thumb_axis_projection_{side}", ""))
                                    if tproj:
                                        sub.label(text=f"Arm-X local projection: {tproj}", icon='CON_ROTLIKE')
                                    sub.label(text=f"Fist bend fixes {hc} | {hl}", icon='CON_ROTLIKE')
                                dmode = str(arm_obj.get(f"saberrig_fingers_thumb_distal_mode_{side}", ""))
                                if dmode:
                                    dmin = float(arm_obj.get(f"saberrig_fingers_thumb_distal_min_bend_deg_{side}", 0.0))
                                    dmax = float(arm_obj.get(f"saberrig_fingers_thumb_distal_max_bend_deg_{side}", 0.0))
                                    sub.label(text=f"Distal Thumb: {dmode} | bend {dmin:.0f}–{dmax:.0f}°", icon='CON_ROTLIKE')
                            elif twmode == "UMA_REFERENCE_LOCKED":
                                sub.label(text=f"Thumb Fist: UMA reference | wrap {twdeg:+.1f}° | root {tcdeg:+.1f}°", icon='CON_ROTLIKE')
                            else:
                                sub.label(text=f"Thumb Fist: {twmode} | wrap {twdeg:+.1f}° | root {tcdeg:+.1f}° | confidence {max(twconf, tcconf) * 100:.0f}%", icon='CON_ROTLIKE')
                        pmode = str(arm_obj.get(f"saberrig_fingers_chain_plane_mode_{side}", "LEGACY"))
                        fmode = str(arm_obj.get(f"saberrig_fingers_fist_mode_{side}", "ADDITIVE"))
                        sub.label(text=f"Curl plane: {pmode} | Fist: {fmode}", icon='CON_ROTLIKE')
                else:
                    finger_box.label(text="Uses semantic finger chains; source bones stay untouched through hidden MCH fingers.", icon='INFO')

                leg_box = rig_box.box()
                leg_box.label(text="Leg Controls", icon='CON_KINEMATIC')
                leg_ik_built = bool(arm_obj.get("saberrig_legs_ik_built", False))
                row = leg_box.row()
                row.scale_y = 1.15
                row.operator("saberrig.build_legs_ik",
                             text="Rebuild Legs IK" if leg_ik_built else "Build Legs IK",
                             icon='CON_KINEMATIC')
                if leg_ik_built:
                    leg_box.label(text="Foot Master + Heel/Ball/Toe reverse-foot roll + Smart Knee Pole.", icon='INFO')
                    leg_limits = str(arm_obj.get("saberrig_leg_joint_limits_mode", "SAFE")).upper()
                    limits_box = leg_box.box()
                    limits_box.label(text="Leg Joint Limits", icon='CON_ROTLIMIT')
                    row = limits_box.row(align=True)
                    op = row.operator("saberrig.set_leg_joint_limits", text="Off", depress=(leg_limits == "OFF")); op.mode = "OFF"
                    op = row.operator("saberrig.set_leg_joint_limits", text="Safe", depress=(leg_limits != "OFF")); op.mode = "SAFE"
                    bend_mode = str(arm_obj.get("saberrig_leg_bend_mode", "SMOOTH")).upper()
                    bend_box = leg_box.box()
                    bend_box.label(text="Knee Behaviour", icon='CON_KINEMATIC')
                    row = bend_box.row(align=True)
                    op = row.operator("saberrig.set_leg_bend_mode", text="Rigid", depress=(bend_mode == "RIGID")); op.mode = "RIGID"
                    op = row.operator("saberrig.set_leg_bend_mode", text="Smooth", depress=(bend_mode != "RIGID")); op.mode = "SMOOTH"
                    pref = float(arm_obj.get("saberrig_leg_preferred_reach_factor_L", SR_DEFAULT_LEG_PREFERRED_REACH_FACTOR))
                    bend_box.label(text=f"Soft extension cap: {pref * 100:.2f}% (Smooth)", icon='INFO')
                    pole_space_mode = str(arm_obj.get("saberrig_knee_pole_space_mode", SR_KNEE_POLE_SPACE_DEFAULT)).upper()
                    pole_space_box = leg_box.box()
                    pole_space_box.label(text="Knee Pole Space", icon='CON_TRACKTO')
                    row = pole_space_box.row(align=True)
                    op = row.operator("saberrig.set_knee_pole_space", text="Global", depress=(pole_space_mode == "GLOBAL")); op.mode = "GLOBAL"
                    op = row.operator("saberrig.set_knee_pole_space", text="Smart", depress=(pole_space_mode != "GLOBAL")); op.mode = "SMART"
                    pole_space_box.label(text="Smart tracks Hip→Ankle direction and keeps the pole hemisphere stable.", icon='INFO')
                    for side in ("L", "R"):
                        mode = arm_obj.get(f"saberrig_leg_mode_{side}", "FK")
                        sub = leg_box.box()
                        row = sub.row(align=True)
                        row.label(text=f"Leg {side}: {mode}", icon='CON_KINEMATIC' if mode == "IK" else 'BONE_DATA')
                        op = row.operator("saberrig.set_leg_mode", text="Use FK", depress=(mode == "FK")); op.side = side; op.mode = "FK"
                        op = row.operator("saberrig.set_leg_mode", text="Use IK", depress=(mode == "IK")); op.side = side; op.mode = "IK"
                        names = _leg_ik_names(side)
                        row = sub.row(align=True)
                        op = row.operator("saberrig.select_mapped_bone", text="Foot Master", icon='EMPTY_ARROWS'); op.bone_name = names["ctrl_master"]
                        op = row.operator("saberrig.select_mapped_bone", text="Foot Pivot", icon='CON_ROTLIKE'); op.bone_name = names["ctrl_foot"]
                        row = sub.row(align=True)
                        op = row.operator("saberrig.select_mapped_bone", text="Heel / Bank", icon='CON_ROTLIKE'); op.bone_name = names["ctrl_heel"]
                        op = row.operator("saberrig.select_mapped_bone", text="Ball Roll", icon='CON_ROTLIKE'); op.bone_name = names["ctrl_ball"]
                        op = row.operator("saberrig.select_mapped_bone", text="Toe Roll", icon='CON_ROTLIKE'); op.bone_name = names["ctrl_toe"]
                        row = sub.row(align=True)
                        op = row.operator("saberrig.select_mapped_bone", text="Knee Pole", icon='EMPTY_ARROWS'); op.bone_name = names["ctrl_pole"]
                        op = row.operator("saberrig.reset_foot_roll", text="Reset Roll", icon='RECOVER_LAST'); op.side = side
                        roll_mode = str(arm_obj.get(f"saberrig_foot_roll_mode_{side}", "UNKNOWN"))
                        sub.label(text=f"Reverse Foot: {roll_mode} | Heel bank ±{math.degrees(SR_FOOT_HEEL_BANK):.0f}°", icon='CON_ROTLIKE')
                        angle = float(arm_obj.get(f"saberrig_leg_pole_angle_{side}", 0.0))
                        err = float(arm_obj.get(f"saberrig_leg_pole_error_{side}", 0.0))
                        reach_factor = float(arm_obj.get(f"saberrig_leg_reach_factor_{side}", SR_DEFAULT_LEG_REACH_FACTOR))
                        min_reach_factor = float(arm_obj.get(f"saberrig_leg_min_reach_factor_{side}", SR_DEFAULT_LEG_MIN_REACH_FACTOR))
                        preferred_factor = float(arm_obj.get(f"saberrig_leg_preferred_reach_factor_{side}", SR_DEFAULT_LEG_PREFERRED_REACH_FACTOR))
                        singular = bool(arm_obj.get(f"saberrig_leg_pole_singular_{side}", False))
                        prebend_active = bool(arm_obj.get(f"saberrig_leg_prebend_active_{side}", False))
                        prebend_angle = float(arm_obj.get(f"saberrig_leg_prebend_angle_{side}", 0.0))
                        sub.label(text=f"Pole {math.degrees(angle):+.1f}° | error {math.degrees(err):.2f}° | hard {reach_factor * 100:.1f}%")
                        if prebend_active:
                            sub.label(text=f"MCH pre-bend {math.degrees(prebend_angle):.2f}° toward Knee Pole", icon='CON_KINEMATIC')
                        if bend_mode != "RIGID": sub.label(text=f"Smooth extension reserve: {preferred_factor * 100:.2f}%", icon='CON_KINEMATIC')
                        if singular: sub.label(text="Source leg was straight: TRUE pole-driven knee mode active.", icon='INFO')
                        guard_axis = str(arm_obj.get(f"saberrig_knee_pole_guard_axis_{side}", "?"))
                        guard_sign = float(arm_obj.get(f"saberrig_knee_pole_guard_sign_{side}", 1.0))
                        guard_quality = float(arm_obj.get(f"saberrig_knee_pole_guard_quality_{side}", 0.0))
                        sub.label(text=f"Pole Space {pole_space_mode}: guard {guard_axis}{'+' if guard_sign >= 0 else '-'} | quality {guard_quality * 100:.0f}%", icon='CON_TRACKTO')
                        if leg_limits != "OFF":
                            hflex = str(arm_obj.get(f"saberrig_hip_flex_axis_{side}", "?"))
                            habd = str(arm_obj.get(f"saberrig_hip_abd_axis_{side}", "?"))
                            hconf = min(float(arm_obj.get(f"saberrig_hip_flex_confidence_{side}", 0.0)), float(arm_obj.get(f"saberrig_hip_abd_confidence_{side}", 0.0)))
                            sub.label(text=f"Hip SAFE: flex {hflex} / abduct {habd} | confidence {hconf * 100:.0f}%", icon='CON_ROTLIMIT')
                            axis = str(arm_obj.get(f"saberrig_knee_hinge_axis_{side}", "?"))
                            hmode = str(arm_obj.get(f"saberrig_knee_hinge_mode_{side}", "OFF"))
                            conf = float(arm_obj.get(f"saberrig_knee_hinge_confidence_{side}", 0.0))
                            flex = float(arm_obj.get(f"saberrig_knee_flexion_deg_{side}", math.degrees(SR_KNEE_SAFE_FLEXION)))
                            sub.label(text=f"Knee {hmode}: axis {axis} | confidence {conf * 100:.0f}% | flex {flex:.0f}°", icon='CON_ROTLIMIT')
                            if hmode == "PREBEND_HINGE_LEGACY":
                                psign = float(arm_obj.get(f"saberrig_knee_prebend_sign_{side}", 1.0))
                                psconf = float(arm_obj.get(f"saberrig_knee_prebend_sign_confidence_{side}", 0.0))
                                sub.label(text=f"One-way hinge learned from pre-bend: {'+' if psign >= 0 else '-'} | confidence {psconf * 100:.0f}%", icon='CHECKMARK')
                            aflex = str(arm_obj.get(f"saberrig_ankle_flex_axis_{side}", "?"))
                            aconf = float(arm_obj.get(f"saberrig_ankle_flex_confidence_{side}", 0.0))
                            sub.label(text=f"Ankle SAFE: flex {aflex} | confidence {aconf * 100:.0f}% | foot/target decoupled", icon='CON_ROTLIMIT')
                            if bool(arm_obj.get(f"saberrig_foot_ctrl_limit_{side}", False)):
                                sub.label(text="Foot Pivot LIMIT_ROTATION active (local semantic axes)", icon='CON_ROTLIMIT')
                    leg_box.label(text="Production leg rig: reverse-foot pivots + Smart Knee Pole + hip/ankle rotation safety.", icon='INFO')

                # Semantic secondary motion stays separate
                # from control-rig generation so animators can use IK/FK without physics.
                profile_id = str(arm_obj.get("saberrig_profile_id", ""))
                secondary_preview_preset = _secondary_preset_for_profile(profile_id)
                wiggle_box = rig_box.box()
                wiggle_box.label(text="Secondary Motion — Semantic", icon='PHYSICS')
                wiggle_built = bool(arm_obj.get("saberrig_wiggle_built", False))
                row = wiggle_box.row()
                row.scale_y = 1.15
                row.operator(
                    "saberrig.build_secondary_motion",
                    text="Rebuild Secondary Motion" if wiggle_built else "Build Secondary Motion",
                    icon='PHYSICS'
                )
                if wiggle_built:
                    enabled = bool(arm_obj.get("saberrig_wiggle_enabled", True))
                    row = wiggle_box.row(align=True)
                    op = row.operator("saberrig.toggle_wiggle", text="Preview Off", depress=not enabled); op.enabled = False
                    op = row.operator("saberrig.toggle_wiggle", text="Preview On", depress=enabled); op.enabled = True
                    row.operator("saberrig.reset_wiggle", text="Reset", icon='RECOVER_LAST')

                    preset_name = str(arm_obj.get("saberrig_wiggle_preset", "Semantic"))
                    source_profile = str(arm_obj.get("saberrig_wiggle_source_profile", profile_id or "UNKNOWN"))
                    spring_count = int(arm_obj.get("saberrig_wiggle_bone_count", 0))
                    wiggle_box.label(text=f"Preset: {preset_name} | source: {source_profile} | spring bones: {spring_count}", icon='INFO')
                    try:
                        wiggle_box.prop(arm_obj, '["saberrig_wiggle_strength"]', text="Intensity", slider=True)
                    except Exception:
                        pass
                    try:
                        wiggle_box.prop(arm_obj, '["saberrig_wiggle_collisions"]', text="Body Collisions")
                    except Exception:
                        pass

                    seeds = int(arm_obj.get("saberrig_wiggle_seed_count", 0))
                    propagated = int(arm_obj.get("saberrig_wiggle_propagated_count", 0))
                    skip_anim = int(arm_obj.get("saberrig_wiggle_skipped_animation", 0))
                    skip_con = int(arm_obj.get("saberrig_wiggle_skipped_constraints", 0))
                    wiggle_box.label(text=f"Detection: {seeds} named/semantic + {propagated} chain continuations | protected anim {skip_anim} / constraints {skip_con}", icon='INFO')
                    wiggle_box.label(text="Root→tip tuning + semantic head/chest/pelvis/thigh/shoulder collision guards", icon='INFO')
                    grid = wiggle_box.grid_flow(columns=2, even_columns=True, even_rows=True, align=True)
                    for family, cfg in SR_WIGGLE_PROFILES.items():
                        count = int(arm_obj.get(f"saberrig_wiggle_count_{family}", 0))
                        if count <= 0:
                            continue
                        active = bool(arm_obj.get(f"saberrig_wiggle_family_{family}", cfg["default"]))
                        op = grid.operator("saberrig.toggle_wiggle_family", text=f"{cfg['label']} ({count})", depress=active)
                        op.family = family
                    wiggle_box.label(text="Source-aware SaberRig presets; existing animation and transform-constraint chains are skipped.", icon='INFO')
                    wiggle_box.operator("saberrig.remove_wiggle", text="Remove Secondary Motion", icon='TRASH')
                else:
                    wiggle_box.label(text=f"Detected rig preset: {secondary_preview_preset['name']} — hair / skirt / cloth / cape / tail / ears / ribbons / veil / accessories.", icon='INFO')

                row = rig_box.row()
                row.operator("saberrig.remove_setup", text="Remove SaberRig Setup", icon='TRASH')
        else:
            rig_box.label(text="Analyze an armature before preparing the control rig.", icon='INFO')

        layout.separator()

        bake_box = layout.box()
        bake_box.label(text="Production Bake / Export", icon='EXPORT')
        arm_obj = _resolve_armature(context, state)
        can_bake = bool(arm_obj and arm_obj.get("saberrig_prepared", False))
        row = bake_box.row(align=True)
        row.enabled = can_bake
        row.operator("saberrig.bake_to_source", text="Bake to Source", icon='ACTION')
        row.operator("saberrig.bake_clean_export", text="Bake + Remove SaberRig", icon='EXPORT')
        bake_box.label(text=f"Scene range: {context.scene.frame_start}–{context.scene.frame_end} · Visual pose bake · step 1", icon='INFO')
        bake_box.label(text="Rig QA preflight runs automatically; ERROR findings block production bake.", icon='CHECKMARK')
        if arm_obj:
            baked_action = str(arm_obj.get("saberrig_bake_action", arm_obj.get("saberrig_export_baked_action", "")))
            if baked_action:
                bake_box.label(text=f"Last baked action: {baked_action}", icon='ACTION')
        if not can_bake:
            bake_box.label(text="Prepare SaberRig before using the production bake pipeline.", icon='INFO')

# -----------------------------------------------------------------------------
# Registration
# -----------------------------------------------------------------------------

CLASSES = (
    SR_MappingItem,
    SR_CountItem,
    SR_MessageItem,
    SR_AnalysisState,
    SR_OT_AnalyzeRig,
    SR_OT_ClearAnalysis,
    SR_OT_ExportAnalysis,
    SR_OT_RunRigQA,
    SR_OT_ExportRigQA,
    SR_OT_BakeToSource,
    SR_OT_BakeCleanExport,
    SR_OT_SelectMappedBone,
    SR_OT_PrepareFoundation,
    SR_OT_BuildBodyControls,
    SR_OT_BuildFingerControls,
    SR_OT_ResetFingerControls,
    SR_OT_BuildArmsFK,
    SR_OT_BuildArmsIK,
    SR_OT_SetArmMode,
    SR_OT_SetArmJointLimits,
    SR_OT_SetShoulderAssist,
    SR_OT_BuildAnimationSpaces,
    SR_OT_SetAnimationSpace,
    SR_OT_BuildLegsIK,
    SR_OT_SetLegMode,
    SR_OT_SetLegJointLimits,
    SR_OT_SetLegBendMode,
    SR_OT_SetKneePoleSpace,
    SR_OT_ResetFootRoll,
    SR_OT_BuildSecondaryMotion,
    SR_OT_BuildUmaWiggle,
    SR_OT_ToggleWiggle,
    SR_OT_ToggleWiggleFamily,
    SR_OT_ResetWiggle,
    SR_OT_RemoveWiggle,
    SR_OT_SetRigView,
    SR_OT_RefreshControlVisuals,
    SR_OT_RemoveSetup,
    SR_PT_MainPanel,
)

def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.saberrig_state = PointerProperty(type=SR_AnalysisState)
    _ensure_wiggle_handler()

def unregister():
    _remove_wiggle_handler()
    _WIGGLE_STATE.clear()
    _WIGGLE_CACHE.clear()
    _ANALYSIS_CACHE.clear()
    if hasattr(bpy.types.Scene, "saberrig_state"):
        del bpy.types.Scene.saberrig_state
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()
