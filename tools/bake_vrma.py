#!/usr/bin/env python3
"""
tools/bake_vrma.py

Converts the VSeeFace SDK sample "Baked Clip.anim" (Unity humanoid YAML) into
VRMA files for use with @pixiv/three-vrm-animation in the VRM overlay.

License of source asset: MIT (VSeeFace SDK by Emiliana_vt)

Usage:
    python tools/bake_vrma.py

Outputs (one per overlay state):
    assets/animations/idle.vrma
    assets/animations/listening.vrma
    assets/animations/thinking.vrma
    assets/animations/speaking.vrma
"""

import json, math, os, re, shutil, struct

ROOT    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC     = os.path.join(ROOT, "assets", "doll", "VSeeFaceSDK Template",
                       "Assets", "VSF SDK", "Animations", "SampleFile",
                       "Baked Clip.anim")
OUT_DIR = os.path.join(ROOT, "assets", "animations")
FPS     = 30

# ── Muscle → (vrm_bone, axis [0=X,1=Y,2=Z], min_deg, max_deg) ───────────────
#
# Unity humanoid uses left-handed coordinates; GLTF/VRM uses right-handed.
# Conversion rule (flip-Z): X rotations keep sign, Y/Z rotations are negated.
# For spine/neck/head "Front-Back" (X axis): same direction.
# For spine/neck/head "Left-Right" (Z axis): negated → swap min/max sign.
# For spine/neck/head "Twist" (Y axis): negated → swap min/max sign.
# For right-side limbs: the lateral axis is additionally mirrored, so it
# reverts to the same sign as left.
MUSCLES = {
    # ── Torso ────────────────────────────────────────────────────────────────
    "Spine Front-Back":            ("spine",        0,  -40,  40),
    "Spine Left-Right":            ("spine",        2,   40, -40),   # Z negated
    "Spine Twist Left-Right":      ("spine",        1,   30, -30),   # Y negated
    "Chest Front-Back":            ("chest",        0,  -40,  40),
    "Chest Left-Right":            ("chest",        2,   40, -40),
    "Chest Twist Left-Right":      ("chest",        1,   30, -30),
    "UpperChest Front-Back":       ("upperChest",   0,  -40,  40),
    "UpperChest Left-Right":       ("upperChest",   2,   40, -40),
    "UpperChest Twist Left-Right": ("upperChest",   1,   30, -30),
    # ── Neck & head ──────────────────────────────────────────────────────────
    "Neck Nod Down-Up":            ("neck",         0,  -40,  40),
    "Neck Tilt Left-Right":        ("neck",         2,   40, -40),
    "Neck Turn Left-Right":        ("neck",         1,   60, -60),
    "Head Nod Down-Up":            ("head",         0,  -40,  40),
    "Head Tilt Left-Right":        ("head",         2,   40, -40),
    "Head Turn Left-Right":        ("head",         1,   60, -60),
    # ── Left arm ─────────────────────────────────────────────────────────────
    # (arm bones intentionally excluded — restPose in vrm_viewer.html controls
    #  the arm angles so the character doesn't stand in T-pose)
    # ── Right arm ────────────────────────────────────────────────────────────
}

# VRM humanoid bone names we'll include (in node index order)
# Arms are deliberately excluded so Three.js AnimationMixer leaves them
# untouched and applyRestPose() in vrm_viewer.html controls them instead.
VRM_BONES = [
    "hips", "spine", "chest", "upperChest",
    "neck", "head",
]
BONE_IDX = {b: i for i, b in enumerate(VRM_BONES)}


# ── YAML parser ──────────────────────────────────────────────────────────────

def parse_float_curves(content: str) -> dict:
    """
    Parse all m_FloatCurves blocks from Unity YAML into
    dict[attribute_name] = [(time, value, out_slope, in_slope), ...]
    """
    curves = {}

    # Each float-curve entry is delimited by "  - curve:\n"
    blocks = re.split(r'\n  - curve:\n', content)

    kf_re = re.compile(
        r'^\s+- serializedVersion: \d+\s*\n'
        r'\s+time:\s*([-\d.e+]+)\s*\n'
        r'\s+value:\s*([-\d.e+]+)\s*\n'
        r'\s+inSlope:\s*([-\d.e+]+)\s*\n'
        r'\s+outSlope:\s*([-\d.e+]+)\s*\n',
        re.MULTILINE,
    )

    for block in blocks[1:]:   # skip preamble before first curve
        attr_m = re.search(r'^\s+attribute:\s*(.+?)\s*$', block, re.MULTILINE)
        if not attr_m:
            continue
        attr = attr_m.group(1).strip()

        curve_m = re.search(r'm_Curve:\n(.*?)m_PreInfinity', block, re.DOTALL)
        if not curve_m:
            continue

        keyframes = []
        for kf in kf_re.finditer(curve_m.group(1)):
            t   = float(kf.group(1))
            v   = float(kf.group(2))
            ins = float(kf.group(3))
            out = float(kf.group(4))
            keyframes.append((t, v, out, ins))   # (time, value, out_slope, in_slope)

        if keyframes:
            # If the same attribute appears multiple times keep the longer one
            if attr not in curves or len(keyframes) > len(curves[attr]):
                curves[attr] = keyframes

    return curves


# ── Interpolation ─────────────────────────────────────────────────────────────

def hermite_sample(keyframes: list, t: float) -> float:
    """Cubic Hermite sample of a Unity float curve at time t."""
    if not keyframes:
        return 0.0
    if t <= keyframes[0][0]:
        return keyframes[0][1]
    if t >= keyframes[-1][0]:
        return keyframes[-1][1]

    lo, hi = 0, len(keyframes) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if keyframes[mid][0] <= t:
            lo = mid
        else:
            hi = mid

    t0, v0, s0_out, _    = keyframes[lo]
    t1, v1, _,      s1_in = keyframes[hi]
    dt = t1 - t0
    if dt == 0.0:
        return v0
    u   = (t - t0) / dt
    h00 = 2*u**3 - 3*u**2 + 1
    h10 = u**3   - 2*u**2 + u
    h01 = -2*u**3 + 3*u**2
    h11 = u**3   - u**2
    return h00*v0 + h10*dt*s0_out + h01*v1 + h11*dt*s1_in


# ── Quaternion helpers ────────────────────────────────────────────────────────

def axis_angle_quat(axis: int, angle_rad: float) -> list:
    """Quaternion [x,y,z,w] for a rotation of angle_rad around axis 0=X,1=Y,2=Z."""
    half = angle_rad * 0.5
    q = [0.0, 0.0, 0.0, math.cos(half)]
    q[axis] = math.sin(half)
    return q


def quat_mul(q1: list, q2: list) -> list:
    x1, y1, z1, w1 = q1
    x2, y2, z2, w2 = q2
    return [
        w1*x2 + x1*w2 + y1*z2 - z1*y2,
        w1*y2 - x1*z2 + y1*w2 + z1*x2,
        w1*z2 + x1*y2 - y1*x2 + z1*w2,
        w1*w2 - x1*x2 - y1*y2 - z1*z2,
    ]


def quat_normalize(q: list) -> list:
    mag = math.sqrt(sum(x*x for x in q))
    return [x/mag for x in q] if mag > 1e-9 else [0.0, 0.0, 0.0, 1.0]


def muscle_to_rad(value: float, min_deg: float, max_deg: float) -> float:
    """Convert a Unity normalized muscle value [-1..1] to radians."""
    if value >= 0.0:
        return math.radians(value * max_deg)
    else:
        return math.radians(value * abs(min_deg))


# ── GLB / VRMA builder ────────────────────────────────────────────────────────

def build_vrma(times: list, bone_quats: dict) -> bytes:
    """
    Pack everything into a binary GLB file with the VRMC_vrm_animation extension.
    times       : [float] — monotonically increasing timestamps
    bone_quats  : {bone_name: [[x,y,z,w], ...]} — one quaternion per timestamp
    """
    n = len(times)
    animated = [b for b in VRM_BONES if b in bone_quats]

    # ── Binary buffer ──────────────────────────────────────────────────────
    bin_parts   = []
    bin_info    = {}     # key → (byte_offset, byte_length)

    time_bytes = struct.pack(f"<{n}f", *times)
    bin_info["__time__"] = (0, len(time_bytes))
    bin_parts.append(time_bytes)

    for bone in animated:
        flat = [c for q in bone_quats[bone] for c in q]
        rot_bytes = struct.pack(f"<{n*4}f", *flat)
        offset = sum(len(b) for b in bin_parts)
        bin_info[bone] = (offset, len(rot_bytes))
        bin_parts.append(rot_bytes)

    binary = b"".join(bin_parts)
    if len(binary) % 4:
        binary += b"\x00" * (4 - len(binary) % 4)

    # ── GLTF accessors & bufferViews ──────────────────────────────────────
    buffer_views = []
    accessors    = []

    t_off, t_len = bin_info["__time__"]
    buffer_views.append({"buffer": 0, "byteOffset": t_off, "byteLength": t_len})
    accessors.append({
        "bufferView": 0, "componentType": 5126, "count": n,
        "type": "SCALAR", "min": [times[0]], "max": [times[-1]],
    })

    bone_acc = {}
    for bone in animated:
        r_off, r_len = bin_info[bone]
        bv = len(buffer_views)
        buffer_views.append({"buffer": 0, "byteOffset": r_off, "byteLength": r_len})
        acc = len(accessors)
        accessors.append({
            "bufferView": bv, "componentType": 5126, "count": n, "type": "VEC4",
        })
        bone_acc[bone] = acc

    # ── Nodes ─────────────────────────────────────────────────────────────
    nodes = [{"name": b} for b in VRM_BONES]

    # ── Animation channels / samplers ─────────────────────────────────────
    channels = []
    samplers  = []
    for bone in animated:
        s = len(samplers)
        samplers.append({"input": 0, "interpolation": "LINEAR", "output": bone_acc[bone]})
        channels.append({"sampler": s, "target": {"node": BONE_IDX[bone], "path": "rotation"}})

    # ── VRMC_vrm_animation extension ──────────────────────────────────────
    human_bones = {b: {"node": BONE_IDX[b]} for b in VRM_BONES}
    gltf = {
        "asset": {"version": "2.0", "generator": "bake_vrma.py"},
        "extensionsUsed": ["VRMC_vrm_animation"],
        "extensions": {
            "VRMC_vrm_animation": {
                "specVersion": "1.0",
                "humanoid": {"humanBones": human_bones},
            },
        },
        "nodes":       nodes,
        "buffers":     [{"byteLength": len(binary)}],
        "bufferViews": buffer_views,
        "accessors":   accessors,
        "animations":  [{"name": "idle", "channels": channels, "samplers": samplers}],
    }

    # ── Pack as GLB ───────────────────────────────────────────────────────
    json_bytes = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    if len(json_bytes) % 4:
        json_bytes += b" " * (4 - len(json_bytes) % 4)

    json_chunk = struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes
    bin_chunk  = struct.pack("<II", len(binary),     0x004E4942) + binary
    header     = struct.pack("<III", 0x46546C67, 2, 12 + len(json_chunk) + len(bin_chunk))
    return header + json_chunk + bin_chunk


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"Source : {SRC}")
    if not os.path.exists(SRC):
        raise FileNotFoundError(SRC)

    with open(SRC, "r", encoding="utf-8") as fh:
        content = fh.read()

    print("Parsing float curves …")
    curves = parse_float_curves(content)
    print(f"  {len(curves)} curves found")

    # Animation duration
    stop_m   = re.search(r"m_StopTime:\s*([\d.]+)", content)
    duration = float(stop_m.group(1)) if stop_m else 21.0
    n_frames = int(duration * FPS) + 1
    times    = [round(i / FPS, 6) for i in range(n_frames)]
    print(f"  Duration: {duration:.2f}s → {n_frames} frames @ {FPS} fps")

    # Group muscles by bone
    from collections import defaultdict
    bone_muscles = defaultdict(list)
    for attr, (bone, axis, min_d, max_d) in MUSCLES.items():
        if attr in curves:
            bone_muscles[bone].append((axis, min_d, max_d, curves[attr]))

    active_bones = sorted(bone_muscles)
    print(f"  Animated bones: {active_bones}")

    # Compute rotation quaternion per frame per bone
    bone_quats = {}
    for bone, muscle_list in bone_muscles.items():
        quats = []
        for t in times:
            q = [0.0, 0.0, 0.0, 1.0]  # identity
            for axis, min_d, max_d, kfs in muscle_list:
                val   = hermite_sample(kfs, t)
                angle = muscle_to_rad(val, min_d, max_d)
                q     = quat_mul(q, axis_angle_quat(axis, angle))
            quats.append(quat_normalize(q))
        bone_quats[bone] = quats

    os.makedirs(OUT_DIR, exist_ok=True)
    print("Building VRMA …")
    glb = build_vrma(times, bone_quats)

    idle_path = os.path.join(OUT_DIR, "idle.vrma")
    with open(idle_path, "wb") as fh:
        fh.write(glb)
    print(f"  Written : {idle_path}  ({len(glb):,} bytes)")

    for state in ("listening", "thinking", "speaking"):
        dst = os.path.join(OUT_DIR, f"{state}.vrma")
        shutil.copy2(idle_path, dst)
        print(f"  Copied  : {dst}")

    print("Done.")


if __name__ == "__main__":
    main()
