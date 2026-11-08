"""Coil & Cog Blender model: a car starter motor (ROADMAP 2.35, D-131). Runs only inside Blender.

A simplified gear-reduction starter built from measurements: DC motor (yoke with 6 permanent
magnets, armature with 12 slots, commutator with 24 bars, 4 brushes), planetary gears (sun 13,
planets 11, ring 35 teeth: 3.7 to 1, inside the 2.5-4.5 range of the facts), output shaft with
the overrunning (one-way) clutch and a 9-tooth pinion, solenoid (coil, plunger, return spring,
contact disc, contacts) with the shift lever, and a segment of the engine's flywheel with a
135-tooth ring gear (15 to 1, inside the 15-20 range). Long videos only: one view per scene.

Units: 1.0 = 100 mm. The model is built with its shaft along local Z, then the whole machine is
turned to lie flat: local Z points to world +X (the pinion end), the solenoid sits on top and the
flywheel below. Gears turn at ratios from their tooth counts, so they always mesh.
"""

import math

import kit

SUN, PLANET, RING = 13, 11, 35  # planetary teeth: (13 + 35) / 3 = 16, so 3 planets fit evenly
PINION, FLYWHEEL = 9, 135  # 135 / 9 = 15
REDUCTION = 1 + RING / SUN  # 3.69: armature turns per output-shaft turn
ARMATURE_REV_S = 0.9  # on screen: slow enough to follow the commutator and the planets

M_PLAN = 0.16 / SUN  # planetary module (sun pitch radius 0.08)
R_SUN, R_PLANET, R_RING = SUN * M_PLAN / 2, PLANET * M_PLAN / 2, RING * M_PLAN / 2
ORBIT = R_SUN + R_PLANET
M_RING = 0.025  # pinion/flywheel module (2.5 mm)
R_PINION, R_FLY = PINION * M_RING / 2, FLYWHEEL * M_RING / 2
FLY_X = R_PINION + R_FLY  # flywheel centre, local x (below the starter)
FLY_Z = (1.16, 1.32)  # the ring gear's face
SLIDE = 0.19  # the pinion's travel into the ring gear
PLUNGER = 0.08  # the plunger's pull
PIVOT = (-0.50, 0.80)  # shift lever pivot (local x, z): 0.16 up to the plunger, 0.38 down
LEVER = math.asin(SLIDE / 0.38)


def world(x, y, z):
    """A local point (shaft along Z) in world coordinates (the machine lying flat)."""
    return (z, y, -x)


def local(x, y, z):
    return (-z, y, x)


# --- the parts ---------------------------------------------------------------------------------


def materials():
    m = kit.base_materials()
    copper = (0.96, 0.52, 0.30)
    m.update(
        paint=kit.principled("Black yoke", (0.025, 0.026, 0.03), 0.4, 0.35, coat=0.6),
        zinc=kit.principled("Zinc-plated steel", (0.72, 0.74, 0.70), 1.0, 0.30),
        magnet=kit.principled("Ferrite magnet", (0.10, 0.10, 0.11), 0.0, 0.65),
        brush=kit.principled("Carbon brush", (0.04, 0.04, 0.045), 0.0, 0.7),
        winding=kit.principled("Armature winding", copper, 1.0, 0.24, emission=kit.ELECTRIC,
                               bump=("lines", 140.0, 0.25)),
        coil=kit.principled("Solenoid coil", copper, 1.0, 0.24, emission=kit.ELECTRIC,
                            bump=("lines", 140.0, 0.25)),
        commutator=kit.principled("Commutator", copper, 1.0, 0.18, emission=kit.ELECTRIC),
        contact=kit.principled("Contacts", copper, 1.0, 0.2, emission=kit.ELECTRIC),
        roller=kit.principled("Clutch rollers", (0.62, 0.63, 0.65), 1.0, 0.2,
                              emission=kit.ELECTRIC),
    )  # fmt: skip
    return m


def build(m, cut: bool = False):
    """The starter and the flywheel. cut: the fixed casings lose their half facing the camera
    (world -Y), a cutaway that shows the parts inside."""
    root = kit.empty("Starter motor")
    root.rotation_euler = (0, math.pi / 2, 0)  # local Z -> world X
    names = ("rear", "motor", "armature", "ring", "output", "housing", "solenoid", "flywheel")
    g = {n: kit.empty(f"{n} group", root) for n in names}
    g["root"] = root
    halves = []  # objects the cutaway opens

    # motor: black yoke with 6 permanent magnets inside
    yoke = kit.mesh_object("Yoke", kit.tube(0.38, 0.42, -0.75, 0.20, 128), m["paint"],
                           g["motor"])  # fmt: skip
    halves.append(yoke)
    for i in range(6):
        a = 2 * math.pi * i / 6 + math.pi / 6
        if cut and math.sin(a) < 0:
            continue
        kit.mesh_object(f"Magnet {i}", kit.box((0.07, 0.24, 0.80), (0.34 * math.cos(a),
                        0.34 * math.sin(a), -0.30), a), m["magnet"], g["motor"])  # fmt: skip

    # armature: slotted core, windings, commutator, shaft and sun gear turn together
    spin_a = kit.empty("Armature spin", g["armature"])
    core = kit.mesh_object("Armature core", kit.cylinder(0.29, -0.70, 0.10, 96), m["dark_steel"],
                           spin_a, smooth=False)  # fmt: skip
    slots = [kit.box((0.08, 0.05, 0.9), (0.27 * math.cos(a), 0.27 * math.sin(a), -0.3), a)
             for a in (2 * math.pi * i / 12 for i in range(12))]  # fmt: skip
    kit.subtract(core, kit.merge(*slots))
    rods = [_rod(0.022, -0.72, 0.12, 0.265, 2 * math.pi * i / 12) for i in range(12)]
    kit.mesh_object("Windings", kit.merge(*rods), m["winding"], spin_a)
    for z in (-0.74, 0.14):
        kit.mesh_object("End turns", kit.torus(0.21, 0.045, 48, 12), m["winding"],
                        spin_a).location = (0, 0, z)  # fmt: skip
    bars = [kit.box((0.035, 0.03, 0.18), (0.14 * math.cos(a), 0.14 * math.sin(a), -0.86), a)
            for a in (2 * math.pi * i / 24 for i in range(24))]  # fmt: skip
    kit.mesh_object("Commutator", kit.merge(kit.cylinder(0.125, -0.95, -0.77, 48), *bars),
                    m["commutator"], spin_a)  # fmt: skip
    kit.mesh_object("Armature shaft", kit.cylinder(0.05, -1.08, 0.36, 32), m["steel"], spin_a)
    kit.mesh_object("Sun gear", _gear(R_SUN, M_PLAN, SUN, 0.22, 0.36), m["steel"], spin_a)
    g["armature spin"] = spin_a

    # rear: end cap with the brush plate and 4 carbon brushes on the commutator
    cap = kit.mesh_object("Rear cap", kit.merge(
        kit.tube(0.36, 0.42, -1.00, -0.75, 128), kit.tube(0.06, 0.42, -1.06, -1.00, 128),
        kit.cylinder(0.08, -1.12, -1.06, 48)), m["aluminium"], g["rear"])  # fmt: skip
    kit.bevel(cap, 0.006)
    halves.append(cap)
    kit.mesh_object("Brush plate", kit.tube(0.24, 0.36, -0.93, -0.90, 96), m["plastic"],
                    g["rear"])  # fmt: skip
    for i in range(4):
        a = 2 * math.pi * i / 4 + math.pi / 4
        kit.mesh_object(f"Brush {i}", kit.box((0.10, 0.06, 0.07), (0.20 * math.cos(a),
                        0.20 * math.sin(a), -0.86), a), m["brush"], g["rear"])  # fmt: skip

    # planetary gears: the ring is fixed; the planets ride on the carrier, which is the output
    ring = kit.mesh_object("Ring gear", _gear(R_RING, M_PLAN, RING, 0.22, 0.36, inner=0.29),
                           m["dark_steel"], g["ring"])  # fmt: skip
    halves.append(ring)
    spin_c = kit.empty("Carrier spin", g["output"])
    arms = [kit.box((ORBIT, 0.03, 0.03), (ORBIT / 2 * math.cos(a), ORBIT / 2 * math.sin(a),
            0.385), a) for a in (2 * math.pi * k / 3 for k in range(3))]  # fmt: skip
    pins = [_rod(0.025, 0.35, 0.40, ORBIT, 2 * math.pi * k / 3) for k in range(3)]
    kit.mesh_object("Carrier", kit.merge(kit.cylinder(0.075, 0.37, 0.40, 48), *arms, *pins),
                    m["steel"], spin_c)  # fmt: skip
    kit.mesh_object("Output shaft", kit.cylinder(0.065, 0.37, 1.52, 48), m["steel"], spin_c)
    g["planets"] = []
    for k in range(3):
        a = 2 * math.pi * k / 3
        planet = kit.empty(f"Planet {k}", spin_c, (ORBIT * math.cos(a), ORBIT * math.sin(a), 0))
        kit.mesh_object(f"Planet gear {k}", _gear(R_PLANET, M_PLAN, PLANET, 0.225, 0.355,
                        phase=_planet_phase(a)), m["steel"], planet)  # fmt: skip
        g["planets"].append(planet)

    # drive: slides along the shaft's splines; shell, ramps and rollers turn with the shaft, the
    # hub and pinion turn on their own once the engine runs faster (the one-way clutch)
    drive = kit.empty("Drive", spin_c)
    g["drive"] = drive
    g["clutch case"] = kit.mesh_object("Clutch shell", kit.merge(
        kit.tube(0.145, 0.17, 0.83, 0.98, 96), kit.tube(0.07, 0.17, 0.96, 0.98, 96)),
        m["steel"], drive)  # fmt: skip
    kit.mesh_object("Clutch back", kit.tube(0.07, 0.17, 0.83, 0.85, 96), m["steel"], drive)
    kit.mesh_object("Collar", kit.tube(0.07, 0.14, 0.785, 0.815, 64), m["steel"], drive)
    ramps = [kit.box((0.05, 0.035, 0.11), (0.125 * math.cos(a), 0.125 * math.sin(a), 0.905), a)
             for a in (2 * math.pi * (i + 0.5) / 5 for i in range(5))]  # fmt: skip
    kit.mesh_object("Ramps", kit.merge(*ramps), m["steel"], drive)
    rollers = [_rod(0.022, 0.86, 0.95, 0.12, 2 * math.pi * i / 5) for i in range(5)]
    kit.mesh_object("Rollers", kit.merge(*rollers), m["roller"], drive)
    spin_p = kit.empty("Pinion spin", drive)
    kit.mesh_object("Clutch hub", kit.cylinder(0.096, 0.85, 0.96, 64), m["dark_steel"], spin_p)
    kit.mesh_object("Pinion", _gear(R_PINION, M_RING, PINION, 0.98, 1.10), m["steel"], spin_p)
    g["output spin"], g["pinion spin"] = spin_c, spin_p

    # drive-end housing: body over the gears and the lever, mounting flange, open nose
    body = kit.mesh_object("Drive housing", kit.merge(
        kit.tube(0.30, 0.42, 0.20, 0.98, 128),
        kit.box((0.50, 0.40, 0.28), (-0.60, 0, 0.84)),
    ), m["aluminium"], g["housing"])  # fmt: skip
    kit.subtract(body, kit.merge(kit.cylinder(0.30, 0.15, 1.0, 96),
                                 kit.box((0.42, 0.30, 0.30), (-0.56, 0, 0.84))))  # fmt: skip
    kit.bevel(body)
    halves.append(body)
    flange = kit.mesh_object("Flange", kit.merge(
        kit.box((0.95, 0.80, 0.07), (-0.22, 0, 1.015)),
    ), m["aluminium"], g["housing"])  # fmt: skip
    holes = [kit.cylinder(0.20, 0.9, 1.1, 64)]
    holes += [_rod(0.05, 0.9, 1.1, 0.36, a) for a in (2.3, -2.3)]
    kit.subtract(flange, kit.merge(*holes))
    kit.bevel(flange)
    kit.mesh_object("Nose", kit.merge(
        kit.box((0.08, 0.16, 0.47), (-0.22, 0, 1.285)),
        kit.box((0.20, 0.14, 0.07), (-0.13, 0, 1.475)),
        kit.cylinder(0.10, 1.425, 1.535, 48),
    ), m["aluminium"], g["housing"])  # fmt: skip

    # solenoid on top: case, coil, plunger with rod and contact disc, return spring, contacts
    sx = -0.62
    sol = g["solenoid"]
    case = kit.mesh_object("Solenoid case", kit.merge(
        kit.tube(0.20, 0.23, 0.05, 0.70, 96), kit.tube(0.105, 0.23, 0.66, 0.70, 96)),
        m["zinc"], sol)  # fmt: skip
    coil = kit.mesh_object("Solenoid coil", kit.tube(0.11, 0.195, 0.10, 0.64, 96), m["coil"], sol)
    cap_s = kit.mesh_object("Solenoid cap", kit.merge(
        kit.cylinder(0.23, -0.16, -0.12, 96), kit.tube(0.19, 0.23, -0.12, 0.05, 96)),
        m["plastic"], sol)  # fmt: skip
    for obj in (case, coil, cap_s):
        obj.location = (sx, 0, 0)
        halves.append(obj)
    for dx in (-0.10, 0.10):
        head = kit.mesh_object("Contact", kit.cylinder(0.045, -0.12, -0.08, 32), m["contact"], sol)
        stud = kit.mesh_object("Terminal", kit.merge(kit.cylinder(0.035, -0.30, -0.16, 32),
                               kit.prism(0.065, -0.24, -0.20, 6)), m["steel"], sol)  # fmt: skip
        head.location = stud.location = (sx + dx, 0, 0)
    plunger = kit.empty("Plunger", sol, (sx, 0, 0))
    kit.mesh_object("Plunger core", kit.merge(
        kit.cylinder(0.10, 0.30, 0.80, 64), kit.box((0.08, 0.10, 0.06), (0, 0, 0.83))),
        m["steel"], plunger)  # fmt: skip
    kit.mesh_object("Plunger rod", kit.cylinder(0.03, 0.0, 0.30, 32), m["steel"], plunger)
    kit.mesh_object("Contact disc", kit.cylinder(0.15, 0.0, 0.03, 64), m["contact"], plunger)
    turns = [(0.055 * math.cos(u), 0.055 * math.sin(u), 0.24 * u / (2 * math.pi * 7))
             for u in (i * math.pi / 12 for i in range(7 * 24 + 1))]  # fmt: skip
    spring = kit.poly_curve("Return spring", turns, 0.008, m["steel"], sol)
    spring.location = (sx, 0, 0.06)
    lever = kit.mesh_object("Shift lever", kit.merge(
        kit.box((0.54, 0.08, 0.04), (0.11, 0, 0)),
        *[kit.box((0.10, 0.03, 0.04), (0.33, s * 0.13, 0)) for s in (1, -1)],
        kit.box((0.04, 0.29, 0.04), (0.28, 0, 0)),
    ), m["steel"], sol, smooth=False)  # fmt: skip
    lever.location = (PIVOT[0], 0, PIVOT[1])
    g["plunger"], g["spring"], g["lever"] = plunger, spring, lever

    # the engine's flywheel with its ring gear (a segment of the engine, below the starter)
    spin_f = kit.empty("Flywheel spin", g["flywheel"], (FLY_X, 0, 0))
    kit.mesh_object("Flywheel ring gear", _gear(R_FLY, M_RING, FLYWHEEL, *FLY_Z, inner=1.52),
                    m["steel"], spin_f)  # fmt: skip
    disc = kit.mesh_object("Flywheel", kit.merge(
        kit.tube(0.30, 1.56, FLY_Z[1], FLY_Z[1] + 0.08, 160),
        kit.tube(0.18, 0.42, FLY_Z[1] + 0.08, FLY_Z[1] + 0.14, 96)),
        m["dark_steel"], spin_f)  # fmt: skip
    kit.subtract(disc, kit.merge(*[_rod(0.05, 1.3, 1.6, 0.30, 2 * math.pi * i / 6)
                                   for i in range(6)]))  # fmt: skip
    g["flywheel spin"] = spin_f

    if cut:
        for obj in halves:
            kit.subtract(obj, kit.box((10, 5, 10), (0, -2.5, 0)))
    return g


def _rod(r: float, z0: float, z1: float, dist: float, a: float):
    """A cylinder parallel to the shaft, dist from it at angle a."""
    import bmesh

    bm = kit.cylinder(r, z0, z1, 24)
    bmesh.ops.translate(bm, vec=(dist * math.cos(a), dist * math.sin(a), 0), verts=bm.verts)
    return bm


def _gear(r_pitch, module, teeth, z0, z1, inner=None, phase=0.0):
    """A spur gear: a tooth centred at angle phase (inner=None), or an internal ring gear
    (inner = its outer radius) with a gap centred at phase."""
    depth = 2.25 * module
    width = math.pi * r_pitch / teeth  # half the circular pitch
    if inner is None:
        r_root, mid = r_pitch - 1.25 * module, r_pitch - 0.125 * module
        base = kit.cylinder(r_root + 0.002, z0, z1, max(48, teeth * 4))
        offset = phase
    else:
        r_root, mid = r_pitch + 1.25 * module, r_pitch + 0.125 * module
        base = kit.tube(r_root - 0.002, inner, z0, z1, max(96, teeth * 4))
        offset = phase + math.pi / teeth
    tooth = [
        kit.box((depth, width, z1 - z0), (mid * math.cos(a), mid * math.sin(a), (z0 + z1) / 2), a)
        for a in (offset + 2 * math.pi * i / teeth for i in range(teeth))
    ]
    return kit.merge(base, *tooth)  # fmt: skip


def _planet_phase(a: float) -> float:
    """Tooth angle of the planet at orbit angle a, so it meshes with the sun (tooth at angle 0):
    where the sun shows a tooth, the planet shows a gap. The ring is meshed by the same rule."""
    s = (a * SUN / (2 * math.pi)) % 1.0  # the sun's tooth phase at the contact point
    return (a + math.pi) - (0.5 - s) * 2 * math.pi / PLANET


# --- motion ------------------------------------------------------------------------------------


def _turn(g, frames: int, armature, pinion_extra=None) -> None:
    """Every gear from one armature angle per frame: carrier = armature / 3.7; planets roll on
    the fixed ring; the pinion turns with the carrier (+ the clutch's slip), the flywheel 15x
    slower the other way. armature(s) and pinion_extra(s) give angles in radians at time s."""
    for n in list(range(1, frames + 1, 2)) + ([frames] if frames % 2 == 0 else []):
        s = (n - 1) / kit.FPS
        a = armature(s)
        c = a / REDUCTION
        p = c + (pinion_extra(s) if pinion_extra else 0.0)
        kit.key(g["armature spin"], "rotation_euler", n, a, "LINEAR", index=2)
        kit.key(g["output spin"], "rotation_euler", n, c, "LINEAR", index=2)
        for planet in g["planets"]:
            kit.key(planet, "rotation_euler", n, -c * RING / PLANET, "LINEAR", index=2)
        kit.key(g["pinion spin"], "rotation_euler", n, p - c, "LINEAR", index=2)
        kit.key(g["flywheel spin"], "rotation_euler", n, -p * PINION / FLYWHEEL, "LINEAR",
                index=2)  # fmt: skip


def _running(t_on: float, rev_s: float = ARMATURE_REV_S):
    """Armature angle: still, then speeding up smoothly from t_on (1 s) to rev_s."""
    w = 2 * math.pi * rev_s

    def angle(s: float) -> float:
        if s <= t_on:
            return 0.0
        x = s - t_on
        return w * (x * x / 2 if x < 1 else x - 0.5)

    return angle


def _engage(g, t0: float, t1: float) -> None:
    """The solenoid pulls: plunger in, spring squeezed, lever turns, pinion slides out."""
    f0, f1 = kit.frame_at(t0), kit.frame_at(t1)
    plunger = g["plunger"].location.copy()
    kit.move(g["plunger"], f0, f1, plunger, (plunger[0], 0, plunger[2] - PLUNGER))
    kit.key(g["spring"], "scale", f0, (1, 1, 1))
    kit.key(g["spring"], "scale", f1, (1, 1, (0.24 - PLUNGER) / 0.24))
    kit.key(g["lever"], "rotation_euler", f0, 0.0, index=1)
    kit.key(g["lever"], "rotation_euler", f1, -LEVER, index=1)
    kit.move(g["drive"], f0, f1, (0, 0, 0), (0, 0, SLIDE))


def _engaged(g) -> None:
    g["plunger"].location.z -= PLUNGER
    g["spring"].scale = (1, 1, (0.24 - PLUNGER) / 0.24)
    g["lever"].rotation_euler = (0, -LEVER, 0)
    g["drive"].location = (0, 0, SLIDE)


def _on(mat, t: float, level: float, t_off=None) -> None:
    kit.glow(mat, kit.frame_at(t), 0.0)
    kit.glow(mat, kit.frame_at(t + 0.6), level)
    if t_off is not None:
        kit.glow(mat, kit.frame_at(t_off), level)
        kit.glow(mat, kit.frame_at(t_off + 0.6), 0.0)


# --- long videos: one animated view per scene (D-130) ------------------------------------------

EXPLODED = {
    "rear": (0, 0, -1.9),
    "armature": (0, 0, -1.1),
    "ring": (0, 0, 0.5),
    "output": (0, 0, 0.9),
    "housing": (0, 0, 2.1),
    "solenoid": (-0.55, 0, 0.3),
    "flywheel": (0.7, 0, 0.9),
}
CUTAWAY = ("armature", "solenoid", "pinion", "clutch")


def template(scene, spec: dict, job: dict) -> None:
    """Build and animate one view of a long video: spec has template and frames (render.py)."""
    name, frames = spec["template"], spec["frames"]
    d = frames / kit.FPS
    wide = job["width"] > job["height"]
    close = 0.85 if wide else 1.0
    m = materials()
    g = build(m, cut=name in CUTAWAY)
    kit.studio(scene)

    def cam(*keys):  # (fraction of the scene, world target, distance, azimuth °, elevation °)
        return kit.camera_rig(scene, [(f * d, t, dist, az, el) for f, t, dist, az, el in keys])

    labels = []
    if name == "whole":
        camera = cam((0, (0.35, 0, -0.35), 6.4 * close, -68, 14),
                     (1, (0.35, 0, -0.35), 6.0 * close, -35, 16))  # fmt: skip
    elif name in ("exploded", "note"):
        f = kit.frame_at(min(2.0, 0.35 * d)) if name == "exploded" else None
        for part, offset in EXPLODED.items():
            if f:
                kit.move(g[part], 1, f, (0, 0, 0), offset)
            else:
                g[part].location = offset
        if name == "exploded":
            camera = cam((0, (0.3, 0, -0.2), 6.0, -75, 14), (0.4, (0.3, 0, -0.5), 6.8, -85, 12),
                         (1, (0.3, 0, -0.5), 6.6, -62, 14))  # fmt: skip
        else:  # the opened machine while the owner speaks
            camera = cam((0, (0.3, 0, -0.5), 6.8, -58, 14), (1, (0.3, 0, -0.5), 6.5, -30, 16))
            _on(m["winding"], 0.0, 2.0)
    elif name == "armature":
        camera = cam((0, (-0.45, 0, 0.0), 3.4 * close, -105, 12),
                     (1, (-0.45, 0, 0.0), 3.1 * close, -75, 14))  # fmt: skip
        _turn(g, frames, _running(0.1 * d))
        _on(m["commutator"], 0.1 * d, 4.0)
        _on(m["winding"], 0.15 * d, 3.0)
        labels.append(("ARMATURE", (-0.45, -0.6, -0.55)))
    elif name == "solenoid":
        camera = cam((0, (0.65, 0, 0.3), 3.6 * close, -95, 10),
                     (1, (0.75, 0, 0.25), 3.3 * close, -78, 12))  # fmt: skip
        t_pull, t_in = 0.25 * d, 0.25 * d + min(0.8, 0.15 * d)
        _on(m["coil"], 0.12 * d, 2.5)
        _engage(g, t_pull, t_in)
        _on(m["contact"], t_in, 8.0)
        _on(m["winding"], t_in + 0.2, 3.0)
        _turn(g, frames, _running(t_in + 0.3))
        labels.append(("SOLENOID", (0.35, -0.35, 1.05)))
    elif name == "pinion":
        _engaged(g)
        camera = cam((0, (1.15, 0, -0.05), 2.8 * close, -98, 8),
                     (1, (1.15, 0, -0.05), 2.6 * close, -72, 10))  # fmt: skip
        _turn(g, frames, _running(0.15 * d))
        labels += [("PINION", (1.2, -0.4, 0.32)), ("RING GEAR", (1.6, -0.5, -0.45))]
    elif name == "clutch":
        _engaged(g)
        g["clutch case"].hide_render = True  # opened: rollers and ramps in view
        camera = cam((0, (1.08, 0, 0.0), 2.3 * close, -100, 14),
                     (1, (1.08, 0, 0.0), 2.1 * close, -75, 16))  # fmt: skip
        t_fire = 0.45 * d
        _turn(g, frames, _running(0.05 * d), _overrun(t_fire))
        _on(m["roller"], 0.1 * d, 6.0, t_off=t_fire)
        labels.append(("ONE-WAY CLUTCH", (1.05, -0.45, 0.33)))
    else:  # "gears": the housing and the drive slid forward, the planets in view from the front
        g["housing"].location = (0, 0, 1.5)
        g["drive"].location = (0, 0, 0.9)
        g["solenoid"].location = (-3.0, 0, 0)  # out of the picture
        camera = cam((0, (0.29, 0, 0.0), 1.5 * close, 10, 26),
                     (1, (0.29, 0, 0.0), 1.35 * close, 35, 22))  # fmt: skip
        _turn(g, frames, _running(0.1 * d))
        labels.append(("PLANETARY GEARS", (0.45, 0.0, 0.36)))
    size = 0.06 if name == "gears" else 0.09
    for text, where in labels:
        obj = kit.label(text, g["root"], local(*where), camera, m["label"], size)
        kit.show_label(obj, min(0.6, 0.1 * d), max(d - 0.6, 0.9 * d))


def _overrun(t_fire: float):
    """The engine fires at t_fire: the flywheel now drives the pinion faster than the motor
    turns it, and the one-way clutch lets the pinion run ahead (extra angle, radians)."""
    w = 2 * math.pi * 1.4

    def extra(s: float) -> float:
        if s <= t_fire:
            return 0.0
        x = s - t_fire
        return w * (x * x / 2 if x < 1 else x - 0.5)

    return extra
