"""Coil & Cog Blender model: a car alternator (D-121, D-123). Runs only inside Blender.

A simplified alternator built from measurements: pulley, fan, front and rear housing, claw-pole
rotor (12 poles) with field coil and 2 slip rings, three-phase stator, brushes, rectifier with 6
diodes, regulator, output terminal (B+) and rear cover. Units: 1.0 = 100 mm; shaft along Z,
pulley on top. Scenes (job.json, in order): hook, body x3, note, close; every moment inside a
scene is a fraction of that scene, so the film fits any narration.
"""

import math

import kit

ROLES = ("hook", "body", "body", "body", "note", "close")
REV_PER_S = 0.45  # rotor speed on screen: slow enough to see the claw poles
POLE_PAIRS = 6  # 12 claw poles


def rotor_angle(t: float) -> float:
    return 2 * math.pi * REV_PER_S * t


# --- the parts ---------------------------------------------------------------------------------


def materials():
    m = kit.base_materials()
    m.update(
        rotor=kit.principled("Rotor poles", (0.30, 0.31, 0.33), 1.0, 0.30),
        diode=kit.principled("Diode", (0.05, 0.05, 0.06), 0.0, 0.3, emission=kit.ELECTRIC),
        brush=kit.principled("Carbon brush", (0.04, 0.04, 0.045), 0.0, 0.7),
        stud=kit.principled("B+ stud", (0.85, 0.60, 0.35), 1.0, 0.25, emission=kit.ELECTRIC),
        slip=kit.principled("Slip rings", (0.96, 0.55, 0.32), 1.0, 0.18, emission=kit.ELECTRIC),
        field=kit.emission_only("Field lines", kit.ELECTRIC, 0.0),
    )
    copper = (0.96, 0.52, 0.30)
    for k in range(3):  # the stator's three phases light up in turn
        m[f"phase{k}"] = kit.principled(
            f"Copper phase {'ABC'[k]}", copper, 1.0, 0.24,
            emission=kit.ELECTRIC, bump=("lines", 140.0, 0.25),
        )  # fmt: skip
    return m


def build(m):
    root = kit.empty("Alternator")
    names = ("front", "rotor", "stator", "brushes", "rear", "rectifier", "cover")
    g = {name: kit.empty(f"{name} group", root) for name in names}
    spin_rotor = kit.empty("Rotor spin", g["rotor"])
    spin_front = kit.empty("Pulley spin", g["front"])
    g["spin"] = (spin_rotor, spin_front)

    # shaft, pulley (poly-V grooves), nut, fan: they turn together
    kit.mesh_object("Shaft", kit.cylinder(0.075, -0.80, 1.10, 48), m["steel"], spin_rotor)
    grooves = [kit.cylinder(0.30 if i % 2 == 0 else 0.275, 0.76 + i * 0.03, 0.79 + i * 0.03, 96)
               for i in range(9)]  # fmt: skip
    pulley = kit.mesh_object("Pulley", kit.merge(*grooves, kit.cylinder(0.20, 0.73, 0.77, 96)),
                             m["steel"], spin_front)  # fmt: skip
    kit.bevel(pulley, 0.004)
    kit.mesh_object("Nut", kit.prism(0.11, 1.025, 1.095, 6), m["steel"], spin_front, smooth=False)
    blades = [kit.box((0.30, 0.025, 0.07), (0.38 * math.cos(a), 0.38 * math.sin(a), 0.68), a + 0.35)
              for a in (2 * math.pi * i / 12 for i in range(12))]  # fmt: skip
    fan = kit.mesh_object("Fan", kit.merge(*blades, kit.tube(0.12, 0.52, 0.64, 0.655)),
                          m["steel"], spin_front)  # fmt: skip
    kit.bevel(fan, 0.004)

    # claw-pole rotor: two pole pieces with 6 tapered fingers each, interleaved; field coil inside
    body = [kit.cylinder(0.47, 0.27, 0.33, 96), kit.cylinder(0.47, -0.33, -0.27, 96)]
    body += [_finger(2 * math.pi * i / 12, upper=i % 2 == 0) for i in range(12)]
    rotor = kit.mesh_object("Claw poles", kit.merge(*body), m["rotor"], spin_rotor)
    kit.bevel(rotor, 0.006)
    kit.mesh_object("Field coil", kit.cylinder(0.33, -0.25, 0.25, 64), m["copper"], spin_rotor)
    for z in (-0.44, -0.53):
        kit.mesh_object("Slip ring", kit.cylinder(0.105, z - 0.03, z + 0.03, 64), m["slip"],
                        spin_rotor)  # fmt: skip
    # magnetic field: flux arcs out of one claw and into its neighbour; half of each loop is inside
    # the rotor, so the visible part is an outward arch
    field = []
    for i in range(12):
        a = 2 * math.pi * (i + 0.5) / 12
        for z in (-0.12, 0.12):
            loop = kit.mesh_object(f"Field loop {i} {z}", kit.torus(0.13, 0.008, 40, 8),
                                   m["field"], spin_rotor)  # fmt: skip
            loop.location = (0.45 * math.cos(a), 0.45 * math.sin(a), z)
            loop.rotation_euler = (0, 0, a)
            loop.scale = (0.75, 1.0, 1.0)
            field.append(loop)
    g["field"] = field

    # stator: laminated core with 36 slots + three-phase copper end turns above and below
    core = kit.mesh_object("Stator core", kit.tube(0.50, 0.715, -0.25, 0.25, 144),
                           m["dark_steel"], g["stator"], smooth=False)  # fmt: skip
    slots = [kit.box((0.05, 0.035, 0.6), (0.50 * math.cos(a), 0.50 * math.sin(a), 0), a)
             for a in (2 * math.pi * i / 36 for i in range(36))]  # fmt: skip
    kit.subtract(core, kit.merge(*slots))
    for side in (1, -1):
        for i in range(18):
            a = 2 * math.pi * (i + 0.5) / 18
            turn = kit.mesh_object(f"End turn {side} {i}", kit.torus(0.075, 0.03, 32, 10),
                                   m[f"phase{i % 3}"], g["stator"])  # fmt: skip
            turn.location = (0.585 * math.cos(a), 0.585 * math.sin(a), side * 0.25)
            turn.rotation_euler = (math.pi / 2, 0, a + math.pi / 2)
            turn.scale = (1.0, 0.85, 1.0)

    # front housing: wall with cooling windows, spoked face, bearing boss, mounting ears
    front = kit.mesh_object("Front housing", kit.merge(
        kit.tube(0.64, 0.70, 0.25, 0.56, 128),
        kit.tube(0.48, 0.70, 0.56, 0.61, 128),
        kit.tube(0.08, 0.20, 0.53, 0.63, 64),
        *[kit.box((0.32, 0.06, 0.05), (0.33 * math.cos(a), 0.33 * math.sin(a), 0.585), a)
          for a in (2 * math.pi * i / 4 + 0.4 for i in range(4))],
        kit.box((0.26, 0.16, 0.12), (0.80, 0, 0.42)),
        kit.box((0.26, 0.16, 0.12), (-0.80, 0, 0.42)),
    ), m["aluminium"], g["front"])  # fmt: skip
    windows = [kit.box((0.09, 0.24, 0.18), (0.67 * math.cos(a), 0.67 * math.sin(a), 0.42), a)
               for a in (2 * math.pi * i / 10 + math.pi / 10 for i in range(10))]  # fmt: skip
    kit.subtract(front, kit.merge(*windows, _ear_holes(0.42, (1, -1))))
    kit.bevel(front)

    # rear housing (slots), brush holder, rectifier with 6 diodes, regulator, B+ stud, rear cover
    rear = kit.mesh_object("Rear housing", kit.merge(
        kit.tube(0.64, 0.70, -0.60, -0.25, 128),
        kit.tube(0.16, 0.70, -0.66, -0.60, 128),
        kit.box((0.26, 0.16, 0.12), (0.80, 0, -0.42)),
    ), m["aluminium"], g["rear"])  # fmt: skip
    slots = [kit.box((0.09, 0.22, 0.20), (0.67 * math.cos(a), 0.67 * math.sin(a), -0.43), a)
             for a in (2 * math.pi * i / 12 + math.pi / 12 for i in range(12))]  # fmt: skip
    kit.subtract(rear, kit.merge(*slots, _ear_holes(-0.42, (1,))))
    kit.bevel(rear)
    holder = kit.mesh_object("Brush holder", kit.box((0.16, 0.22, 0.20), (0.24, 0, -0.485)),
                             m["plastic"], g["brushes"])  # fmt: skip
    kit.bevel(holder, 0.01)
    for z in (-0.44, -0.53):
        kit.mesh_object("Brush", kit.box((0.07, 0.05, 0.05), (0.135, 0, z)), m["brush"],
                        g["brushes"])  # fmt: skip
    plate = kit.mesh_object("Rectifier plate", kit.tube(0.24, 0.56, -0.74, -0.69, 128),
                            m["aluminium"], g["rectifier"])  # fmt: skip
    kit.bevel(plate, 0.005)
    for i in range(6):
        a = 2 * math.pi * i / 6 + 0.26
        diode = kit.mesh_object(f"Diode {i}", kit.cylinder(0.05, -0.80, -0.74, 32), m["diode"],
                                g["rectifier"])  # fmt: skip
        diode.location = (0.40 * math.cos(a), 0.40 * math.sin(a), 0)
    kit.mesh_object("Regulator", kit.box((0.28, 0.20, 0.08), (-0.38, 0.10, -0.79)),
                    m["plastic"], g["rectifier"])  # fmt: skip
    stud = kit.mesh_object("B+ stud", kit.cylinder(0.035, -0.95, -0.74, 32), m["stud"],
                           g["rectifier"])  # fmt: skip
    stud.location = (0.30, -0.42, 0)
    cover = kit.mesh_object("Rear cover", kit.merge(
        kit.tube(0.56, 0.62, -0.92, -0.76, 128), kit.cylinder(0.62, -0.96, -0.92, 128)),
        m["plastic"], g["cover"])  # fmt: skip
    kit.bevel(cover, 0.01)

    # direct current out: a cable from the B+ stud with pulses travelling one way along it
    path = [(0.30 + 0.9 * s, -0.42 - 0.35 * s, -0.95 - 0.55 * math.sin(s * 1.4))
            for s in (i / 40 for i in range(41))]  # fmt: skip
    g["cable"] = kit.poly_curve("Cable", path, 0.022, m["rubber"], g["rectifier"])
    g["sparks"] = [kit.mesh_object(f"Current {i}", kit.sphere(0.03), m["current"], g["rectifier"])
                   for i in range(9)]  # fmt: skip
    g["path"] = path
    return g


def _finger(a: float, upper: bool):
    """One tapered claw finger on the rotor's surface: wide at its pole plate, narrow at the tip."""
    import bmesh

    bm = bmesh.new()
    r_in, r_out = 0.40, 0.47
    z_root, z_tip = (0.30, -0.24) if upper else (-0.30, 0.24)
    v = [
        bm.verts.new((r, s * w / 2 * (r / r_out), z))
        for z, w in ((z_root, 0.24), (z_tip, 0.07))
        for r in (r_in, r_out)
        for s in (-1, 1)
    ]
    faces = ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3))
    for face in faces:
        bm.faces.new([v[i] for i in face])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bmesh.ops.rotate(bm, verts=bm.verts, cent=(0, 0, 0), matrix=kit.rot_z(a))
    return bm


def _ear_holes(z: float, sides):
    import bmesh

    holes = []
    for s in sides:
        hole = bmesh.new()
        where = kit.translate(s * 0.86, 0, z) @ kit.rot_x90()
        bmesh.ops.create_cone(hole, cap_ends=True, segments=32, radius1=0.045, radius2=0.045,
                              depth=0.4, matrix=where)  # fmt: skip
        holes.append(hole)
    return kit.merge(*holes)


# --- the film ----------------------------------------------------------------------------------


def film(scene, job: dict, m=None) -> None:
    """Build the alternator, the camera moves, labels and animation for this job's timings."""
    roles = tuple(s["role"] for s in job["scenes"])
    if roles != ROLES:
        raise ValueError(f"the alternator film needs scenes {ROLES}, the job has {roles}")
    hook, b1, b2, b3, note, close = (s["start"] for s in job["scenes"])
    end = job["duration_s"]
    t = {
        "explode": b1,
        "exploded": b1 + min(2.1, 0.4 * (b2 - b1)),
        "field": kit.at(b1, b2, 0.62),
        "stator": b2,
        "phases": b2 + min(1.4, 0.3 * (b3 - b2)),
        "rectify": b3,
        "diodes": kit.at(b3, note, 0.33),
        "dc": kit.at(b3, note, 0.70),
        "note": note,
        "close": close,
        "closed": close + min(2.15, 0.55 * (end - close)),
        "end": end,
    }
    m = m or materials()
    g = build(m)
    kit.studio(scene)
    cam = kit.camera_rig(scene, [
        (hook, (0, 0, 0.15), 5.6, -60, 16),
        (b1, (0, 0, 0.10), 5.0, -48, 16),
        (kit.at(b1, b2, 0.4), (0, 0, -0.30), 10.5, -35, 12),  # pull back: the whole exploded stack
        (b2 - 0.3, (0, 0, 0.0), 3.6, -18, 18),  # push in on the spinning rotor
        (b3, (0, 0, 0.0), 3.9, 8, 38),  # look down into the stator ring
        (kit.at(b3, note, 0.11), (0, 0, -1.5), 8.5, 18, 2),  # pull back on the way down
        (kit.at(b3, note, 0.24), (0, 0, -2.75), 3.5, 30, -32),  # the rectifier, from below
        (note - 0.2, (0, 0, -2.75), 3.4, 62, -28),
        (kit.at(note, close, 0.4), (0, 0, -0.6), 10.0, 95, 8),  # a slow look at the opened machine
        (close, (0, 0, -0.3), 9.0, 125, 12),
        (kit.at(close, end, 0.6), (0, 0, 0.15), 5.6, 160, 16),  # closed again
        (end, (0, 0, 0.15), 5.3, 172, 16),
    ])  # fmt: skip
    for text, group, where, t_in, t_out in (
        ("ROTOR", "rotor", kit.toward(-25, 0.5, 0.58), kit.at(b1, b2, 0.45), b2),
        ("STATOR", "stator", kit.toward(-5, 0.55, 0.62), t["phases"], b3 + 0.8),
        ("DIODES", "rectifier", kit.toward(45, 0.8, -0.5), t["diodes"], note - 0.3),
    ):
        kit.show_label(kit.label(text, g[group], where, cam, m["label"]), t_in, t_out)
    _animate(g, m, t, job["frames"])


def _animate(g, m, t, frames: int) -> None:
    f = kit.frame_at
    for spin in g["spin"]:  # constant spin, linear keys
        kit.key(spin, "rotation_euler", 1, 0.0, "LINEAR", index=2)
        kit.key(spin, "rotation_euler", frames, rotor_angle(t["end"]), "LINEAR", index=2)

    exploded = {
        "front": (0, 0, 1.35),
        "stator": (0, 0, -1.05),
        "rear": (0, 0, -1.75),
        "rectifier": (0, 0, -2.10),
        "cover": (-1.6, -1.7, -2.6),  # slides away from the side the camera films the diodes from
        "brushes": (0, 0, 0),
    }
    for name, offset in exploded.items():
        obj = g[name]
        kit.move(obj, f(t["explode"]), f(t["exploded"]), (0, 0, 0), offset)
        if name == "stator":  # the stator slides back around the spinning rotor
            kit.move(obj, f(t["stator"]), f(t["phases"]), offset, (0, 0, 0))
        else:
            kit.key(obj, "location", f(t["close"]), offset)
        kit.key(obj, "location", f(t["closed"]), (0, 0, 0))

    for loop in g["field"]:  # the magnetic field appears and stays through the stator scene
        full = tuple(loop.scale)
        loop.scale = (0, 0, 0)
        kit.key(loop, "scale", f(t["field"] - 0.2), (0, 0, 0))
        kit.key(loop, "scale", f(t["field"] + 0.6), full)
        kit.key(loop, "scale", f(t["rectify"] + 0.6), full)
        kit.key(loop, "scale", f(t["rectify"] + 1.4), (0, 0, 0))
    kit.node_value(m["field"], "Glow", "Strength", f(t["field"] - 0.2), 0.0)
    kit.node_value(m["field"], "Glow", "Strength", f(t["field"] + 0.6), 6.0)

    # each phase brightens as the poles sweep past it; the diodes pulse with the phases
    for n in range(1, frames + 1, 2):
        s = (n - 1) / kit.FPS
        level = kit.ramp(s, t["phases"] - 0.2, t["phases"] + 0.6, 0, 7)
        level = min(level, 7 - kit.ramp(s, t["rectify"] + 1.5, t["rectify"] + 3.0, 0, 4))
        level *= 1 - kit.ramp(s, t["close"] + 0.4, t["close"] + 1.6, 0, 1)
        waves = [max(0.0, math.cos(POLE_PAIRS * rotor_angle(s) - k * 2 * math.pi / 3)) ** 2
                 for k in range(3)]  # fmt: skip
        for k in range(3):
            kit.glow(m[f"phase{k}"], n, level * waves[k])
        diodes = kit.ramp(s, t["diodes"] - 0.2, t["diodes"] + 0.5, 0, 9)
        diodes *= 1 - kit.ramp(s, t["note"], t["note"] + 1.0, 0, 1)
        kit.glow(m["diode"], n, diodes * (0.35 + 0.65 * max(waves)))
        stud = kit.ramp(s, t["dc"] - 0.2, t["dc"] + 0.6, 0, 10)
        kit.glow(m["stud"], n, stud * (1 - kit.ramp(s, t["note"], t["note"] + 1, 0, 1)))

    # direct current: steady one-way pulses along the cable from the B+ stud
    show0, show1 = f(t["dc"]), f(t["note"] + 0.5)
    for obj in [g["cable"], *g["sparks"]]:
        kit.set_visible(obj, 1, False)
        kit.set_visible(obj, show0, True)
        kit.set_visible(obj, show1, False)
    for n in range(show0, show1 + 1, 2):
        s = (n - show0) / kit.FPS
        for i, spark in enumerate(g["sparks"]):
            u = (s * 0.45 + i / len(g["sparks"])) % 1.0
            kit.key(spark, "location", n, kit.along(g["path"], u), "LINEAR")


# --- long videos: one animated view per scene (D-130) --------------------------------------------
# Each template is a scene of its own: the model is built, posed and animated over exactly the
# scene's frames (local frame 1 = the scene's start). Cameras move slowly and smoothly; in a 16:9
# frame (same height of view as 9:16) they come closer, so the machine fills more of the picture.

EXPLODED = {
    "front": (0, 0, 1.35),
    "stator": (0, 0, -1.05),
    "rear": (0, 0, -1.75),
    "rectifier": (0, 0, -2.10),
    "cover": (-1.6, -1.7, -2.6),
    "brushes": (0, 0, 0),
}
LABELS = {"rotor": "ROTOR", "stator": "STATOR", "rectifier": "DIODES", "brushes": "SLIP RINGS"}


def template(scene, spec: dict, job: dict) -> None:
    """Build and animate one view of a long video: spec has template and frames (render.py)."""
    name, frames = spec["template"], spec["frames"]
    d = frames / kit.FPS  # the scene's length in seconds
    wide = job["width"] > job["height"]
    m = materials()
    g = build(m)
    kit.studio(scene)
    for spin in g["spin"]:  # the rotor and pulley always turn
        kit.key(spin, "rotation_euler", 1, 0.0, "LINEAR", index=2)
        kit.key(spin, "rotation_euler", frames, rotor_angle(d), "LINEAR", index=2)
    for loop in g["field"]:
        loop.scale = (0, 0, 0)  # the magnetic field shows only where it is explained
    for obj in [g["cable"], *g["sparks"]]:
        kit.set_visible(obj, 1, False)
    close = 0.8 if wide else 1.0  # how much closer a 16:9 camera comes for part views

    def cam(*keys):  # (fraction of the scene, target, distance, azimuth °, elevation °)
        return kit.camera_rig(scene, [(f * d, t, dist, az, el) for f, t, dist, az, el in keys])

    if name == "whole":
        camera = cam((0, (0, 0, 0.1), 5.6 * close, -70, 16), (1, (0, 0, 0.1), 5.2 * close, -30, 18))
    elif name == "exploded":
        f = kit.frame_at(min(2.0, 0.35 * d))
        for part, offset in EXPLODED.items():
            kit.move(g[part], 1, f, (0, 0, 0), offset)
        camera = cam((0, (0, 0, 0.1), 5.6, -55, 16), (0.4, (0, 0, -0.4), 8.6, -40, 12),
                     (1, (0, 0, -0.4), 8.4, -15, 10))  # fmt: skip
    else:
        _pose(g, stator_home=name in ("stator", "brushes"))
        if name == "rotor":
            camera = cam((0, (0, 0, 0), 3.8 * close, -35, 18), (1, (0, 0, 0), 3.4 * close, 5, 20))
            _field(g, m, 0.15 * d)
        elif name == "stator":
            camera = cam((0, (0, 0, 0), 4.0 * close, -5, 38), (1, (0, 0, 0), 3.7 * close, 30, 36))
            _field(g, m, 0.0)
            _glow(m, frames, phases=(0.05 * d, 7.0))
        elif name == "rectifier":
            camera = cam((0, (0, 0, -2.75), 3.6 * close, 25, -30),
                         (1, (0, 0, -2.75), 3.3 * close, 65, -28))  # fmt: skip
            _glow(m, frames, diodes=0.1 * d, stud=0.35 * d)
            _current(g, frames, 0.35 * d)
        elif name == "brushes":
            camera = cam((0, (0, 0, -0.7), 3.2 * close, 85, -12),
                         (1, (0, 0, -0.7), 2.9 * close, 115, -9))  # fmt: skip
            _glow(m, frames, slip=(0.2 * d, 2.5))
        else:  # "note": a slow look around the whole opened machine
            camera = cam((0, (0, 0, -0.6), 8.6, 95, 8), (1, (0, 0, -0.6), 8.2, 140, 10))
            _field(g, m, 0.0)
            _glow(m, frames, phases=(0.0, 3.0))
    if name in LABELS:
        group = {"rotor": "rotor", "stator": "stator", "rectifier": "rectifier",
                 "brushes": "brushes"}[name]  # fmt: skip
        where = {"rotor": kit.toward(-15, 0.5, 0.58), "stator": kit.toward(12, 0.55, 0.62),
                 "rectifier": kit.toward(45, 0.8, -0.5),
                 "brushes": kit.toward(100, 0.6, -0.95)}[name]  # fmt: skip
        size = 0.08 if name == "brushes" else 0.13
        obj = kit.label(LABELS[name], g[group], where, camera, m["label"], size)
        kit.show_label(obj, min(0.6, 0.1 * d), max(d - 0.6, 0.9 * d))


def _pose(g, stator_home: bool) -> None:
    """The opened alternator, still: the parts apart, the stator around the rotor if wanted."""
    for part, offset in EXPLODED.items():
        g[part].location = (0, 0, 0) if part == "stator" and stator_home else offset


def _field(g, m, t_on: float) -> None:
    for loop in g["field"]:
        full = (0.75, 1.0, 1.0)
        kit.key(loop, "scale", kit.frame_at(t_on), (0, 0, 0))
        kit.key(loop, "scale", kit.frame_at(t_on + 0.8), full)
    kit.node_value(m["field"], "Glow", "Strength", kit.frame_at(t_on), 0.0)
    kit.node_value(m["field"], "Glow", "Strength", kit.frame_at(t_on + 0.8), 6.0)


def _glow(m, frames: int, phases=None, diodes=None, slip=None, stud=None) -> None:
    """Three-phase glow, diode pulses and slip-ring glow over the scene (one key every 2 frames)."""
    for n in range(1, frames + 1, 2):
        s = (n - 1) / kit.FPS
        waves = [max(0.0, math.cos(POLE_PAIRS * rotor_angle(s) - k * 2 * math.pi / 3)) ** 2
                 for k in range(3)]  # fmt: skip
        if phases is not None:
            start, level = phases
            for k in range(3):
                kit.glow(m[f"phase{k}"], n, kit.ramp(s, start, start + 0.8, 0, level) * waves[k])
        if diodes is not None:
            kit.glow(
                m["diode"], n, kit.ramp(s, diodes, diodes + 0.6, 0, 9) * (0.35 + 0.65 * max(waves))
            )
        if slip is not None:
            start, level = slip
            kit.glow(m["slip"], n, kit.ramp(s, start, start + 0.6, 0, level))
        if stud is not None:  # direct current leaves through the output terminal
            kit.glow(m["stud"], n, kit.ramp(s, stud, stud + 0.6, 0, 10))


def _current(g, frames: int, t_on: float) -> None:
    """Direct current leaving the B+ stud: steady one-way pulses along the cable."""
    show = kit.frame_at(t_on)
    for obj in [g["cable"], *g["sparks"]]:
        kit.set_visible(obj, show, True)
    for n in range(show, frames + 1, 2):
        s = (n - show) / kit.FPS
        for i, spark in enumerate(g["sparks"]):
            u = (s * 0.45 + i / len(g["sparks"])) % 1.0
            kit.key(spark, "location", n, kit.along(g["path"], u), "LINEAR")
