"""Blueprint film: a push-belt CVT (D-111).

Two pulleys, each two cones; one steel belt of fixed length. The belt length is solved for every
frame, so when the input pulley grows the output pulley shrinks by exactly the right amount, and
the smaller pulley spins faster (same belt speed).
"""

from __future__ import annotations

import math

from factory.blueprint.canvas import (
    AMBER,
    CYAN,
    DIM,
    LINE,
    PANEL,
    Fact,
    Scene,
    W,
    ease,
)  # fmt: skip

MACHINE = "cvt gearbox"
CVT = "https://en.wikipedia.org/wiki/Continuously_variable_transmission"
# No on-screen number needs a source: the ratio is calculated from the drawing's own geometry and
# the gear numbers 1-5 only label a schematic gearbox. The mechanism itself follows the page below
# (cones moving together/apart, fixed belt length, both pulleys adjusting at once, push belt).
FACTS: dict[str, Fact] = {}
SOURCE = CVT
LIVE = (r"ratio \d+\.\d\d : 1", r"\d")

X, Y1, Y2 = 540, 420, 900  # pulley centres (front view)
D = Y2 - Y1
R_LOW, R_HIGH = 95, 190
BELT_BROWN = (120, 60, 0)


def belt_length(r1: float, r2: float) -> float:
    b = math.asin((r1 - r2) / D)
    return 2 * D * math.cos(b) + math.pi * (r1 + r2) + 2 * b * (r1 - r2)


L0 = belt_length(R_LOW, R_HIGH)


def r2_for(r1: float) -> float:
    """The output radius that keeps the belt at its one fixed length."""
    lo, hi = 20.0, 400.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (lo, mid) if belt_length(r1, mid) > L0 else (mid, hi)
    return (lo + hi) / 2


def r1_at(t: float, starts: list[float]) -> float:
    """Input radius over the film: low gear, a squeeze-and-release while the walls are explained,
    the shift up while the ratios are explained, high gear, then back down at the close."""
    _, _, walls, ratios, push, close = starts
    if t < walls:
        return R_LOW
    if t < ratios:
        return (
            R_LOW
            + (R_HIGH - R_LOW) * 0.75 * math.sin(math.pi * (t - walls) / (ratios - walls)) ** 2
        )
    if t < push:
        return R_LOW + (R_HIGH - R_LOW) * ease((t - ratios - 0.3) / max(0.5, push - ratios - 1.5))
    if t < close + 0.3:
        return R_HIGH
    return R_HIGH - (R_HIGH - R_LOW) * ease((t - close - 0.3) / 3.0)


def new_state(starts: list[float], duration: float) -> dict:
    st = {"starts": starts, "th1": 0.0, "th2": 0.0, "off": 0.0, "r1_prev": R_LOW}
    st["r1"] = r1_at(0, starts)
    st["r2"] = r2_for(st["r1"])
    return st


def step(st: dict, t: float, dt: float) -> None:
    st["th1"] += 2.0 * dt
    st["th2"] += 2.0 * st["r1"] / st["r2"] * dt  # same belt speed: the smaller pulley spins faster
    st["off"] += 2.0 * st["r1"] * dt
    st["r1_prev"] = st["r1"]
    st["r1"] = r1_at(t + dt, st["starts"])
    st["r2"] = r2_for(st["r1"])


# --- geometry ------------------------------------------------------------------------------------


def tangent_normals(r1, r2):
    a = (r1 - r2) / D
    b = math.sqrt(1 - a * a)
    return (b, -a), (-b, -a)


def arc(c, r, n_from, n_to, away, steps=70):
    a0, a1 = math.atan2(n_from[1], n_from[0]), math.atan2(n_to[1], n_to[0])
    sweep = (a1 - a0) % (2 * math.pi)
    mid = a0 + sweep / 2
    if math.cos(mid) * away[0] + math.sin(mid) * away[1] < 0:
        sweep -= 2 * math.pi
    angles = [a0 + sweep * i / steps for i in range(steps + 1)]
    return [(c[0] + r * math.cos(a), c[1] + r * math.sin(a)) for a in angles]


def belt_path(r1, r2):
    """The closed belt loop, starting where it leaves the input pulley on the right: that run
    carries the belt from the input to the output pulley, the side that pushes."""
    nr, nl = tangent_normals(r1, r2)
    path = [(X + r1 * nr[0], Y1 + r1 * nr[1])]
    path += arc((X, Y2), r2, nr, nl, (0, 1))
    path += arc((X, Y1), r1, nl, nr, (0, -1))
    return path


def along(path, spacing, offset):
    seg = [math.dist(path[i], path[(i + 1) % len(path)]) for i in range(len(path))]
    total = sum(seg)
    pts, s, i, acc = [], offset % spacing, 0, 0.0
    while s < total:
        while acc + seg[i] < s:
            acc += seg[i]
            i += 1
        p, q = path[i], path[(i + 1) % len(path)]
        f = (s - acc) / seg[i] if seg[i] else 0
        pts.append(
            ((p[0] + (q[0] - p[0]) * f, p[1] + (q[1] - p[1]) * f), (q[0] - p[0], q[1] - p[1]))
        )
        s += spacing
    return pts


# --- drawing -------------------------------------------------------------------------------------


def front(c, st, oy=0.0, k=1.0, progress=1.0, blocks=False, labels=True):
    """Front view: two pulleys with spokes and the belt loop (scaled by k, shifted by oy)."""
    r1, r2 = st["r1"], st["r2"]

    def T(p):
        return (X + (p[0] - X) * k, oy + Y1 + (p[1] - Y1) * k)

    path = belt_path(r1, r2)
    for cen, r, th in (((X, Y1), r1, st["th1"]), ((X, Y2), r2, st["th2"])):
        cc = T(cen)
        c.arc(cc, r * k, -90, -90 + 360 * progress, LINE, 3 * k)
        c.arc(cc, 30 * k, -90, -90 + 360 * progress, LINE, 3 * k)
        c.ellipse(cc, 9 * k, fill=CYAN, width=0)
        if progress >= 1:
            for j in range(6):
                a = th + j * math.pi / 3
                c.line([(cc[0] + 30 * k * math.cos(a), cc[1] + 30 * k * math.sin(a)),
                        (cc[0] + (r - 14) * k * math.cos(a), cc[1] + (r - 14) * k * math.sin(a))],
                       DIM, 2 * k)  # fmt: skip
    shown = path[: max(2, int(len(path) * progress))]
    if not blocks:
        c.line([T(p) for p in shown] + ([T(path[0])] if progress >= 1 else []), AMBER, 15 * k,
               curve=True)  # fmt: skip
        if progress >= 1:
            for p, v in along(path, 24, st["off"]):
                n = math.hypot(*v) or 1
                nx, ny = -v[1] / n * 8, v[0] / n * 8
                c.line([T((p[0] - nx, p[1] - ny)), T((p[0] + nx, p[1] + ny))], BELT_BROWN, 2 * k)
    else:  # the push belt as separate steel blocks; the run from input to output glows cyan
        nr, _ = tangent_normals(r1, r2)
        y_top, y_bot = Y1 + r1 * nr[1], Y2 + r2 * nr[1]
        for p, v in along(path, 15, st["off"]):
            n = math.hypot(*v) or 1
            ux, uy = v[0] / n, v[1] / n
            push = p[0] > X and y_top - 2 <= p[1] <= y_bot + 2
            hl, hw = 5, 13
            corners = [(p[0] + ux * hl - uy * hw, p[1] + uy * hl + ux * hw),
                       (p[0] + ux * hl + uy * hw, p[1] + uy * hl - ux * hw),
                       (p[0] - ux * hl + uy * hw, p[1] - uy * hl - ux * hw),
                       (p[0] - ux * hl - uy * hw, p[1] - uy * hl + ux * hw)]  # fmt: skip
            c.polygon([T(q) for q in corners], fill=CYAN if push else AMBER, width=0)
    if labels and progress >= 1:
        lx = X + 225
        c.text((lx, oy + Y1 - 16), "INPUT", "label", CYAN, "lm")
        c.text((lx, oy + Y1 + 20), "from the engine", "small", DIM, "lm")
        c.text((lx, oy + Y2 - 16), "OUTPUT", "label", CYAN, "lm")
        c.text((lx, oy + Y2 + 20), "to the wheels", "small", DIM, "lm")


def section(c, cx, ya, r, k, title=None, move=0):
    """Cross-section of one pulley: two cones on a shaft, the belt pinched in the V at radius r.
    The cones never overlap: at the largest radius they are still apart."""
    h0, rmax, slope, half_w, bh = 40, 230, 0.2, 46, 34
    s = half_w - (r - h0) * slope

    def P(x, y):
        return (cx + x * k, ya - y * k)

    c.rect(P(-200, h0), P(200, 0), outline=DIM)
    for side in (-1, 1):
        c.polygon([P(side * s, h0), P(side * (s + (rmax - h0) * slope), rmax),
                   P(side * (s + (rmax - h0) * slope + 26), rmax), P(side * (s + 150), h0)],
                  fill=PANEL, outline=LINE)  # fmt: skip

    def hw(y):
        return half_w + (y - r) * slope

    yb, yt = r - bh / 2, r + bh / 2
    c.polygon([P(-hw(yb), yb), P(hw(yb), yb), P(hw(yt), yt), P(-hw(yt), yt)], fill=AMBER, width=0)
    if title:
        c.text((cx, ya - (rmax + 45) * k), title, "label", CYAN)
    if move:
        for side in (-1, 1):
            x0 = cx + side * 185 * k
            c.arrow((x0, ya - 120 * k), (x0 - side * move * 42 * k, ya - 120 * k), CYAN)


def title(c, s):
    c.text((W / 2, 120), s, "head", LINE)


def hook(c, t, st, t0, t1):
    title(c, "WHY NO GEAR CHANGES?")
    x0, x1, y0, y1 = 150, 940, 1020, 330  # engine speed against car speed
    c.arrow((x0, y0), (x0, y1 - 20), DIM, 3)
    c.arrow((x0, y0), (x1 + 20, y0), DIM, 3)
    c.text((x0 + 14, y1 - 10), "engine speed", "small", DIM, "lm")
    c.text((x1, y0 + 34), "car speed", "small", DIM, "rm")
    span = t1 - t0
    u = ease((t - t0 - 0.2) / (span * 0.55))
    pts, teeth = [], 5
    for i in range(teeth):
        for j in range(21):
            fx = (i + j / 20) / teeth
            if fx > u:
                break
            rpm = (0.35 + 0.55 * (j / 20) ** 0.8) if i else (0.08 + 0.82 * (j / 20) ** 0.8)
            pts.append((x0 + fx * (x1 - x0), y0 - rpm * (y0 - y1)))
        if (i + 1) / teeth > u:
            break
        pts.append((x0 + (i + 1) / teeth * (x1 - x0), y0 - 0.35 * (y0 - y1)))
    cvt_at = t0 + span * 0.6
    c.line(pts, DIM if t > cvt_at else LINE, 4, curve=True)
    for i in range(teeth):
        if (i + 0.5) / teeth <= u:
            c.text((x0 + (i + 0.5) / teeth * (x1 - x0), y1 + 40), f"{i + 1}", "big",
                   DIM if t > cvt_at else AMBER)  # fmt: skip
    c.text((W / 2, 1450), "gearbox: fixed steps", "label", DIM if t > cvt_at else LINE)
    if t > cvt_at:
        v = ease((t - cvt_at) / 1.4)
        line = [(x0 + j / 40 * (x1 - x0), y0 - (0.08 + 0.64 * min(1, (j / 40) / 0.12)) * (y0 - y1))
                for j in range(41) if j / 40 <= v]  # fmt: skip
        c.line(line, CYAN, 7, curve=True)
        c.text((W / 2, 1520), "CVT: one smooth line", "big", CYAN)


def parts(c, t, st, t0, t1):
    title(c, "TWO PULLEYS, ONE STEEL BELT")
    prog = ease((t - t0) / 2.4)
    front(c, st, progress=prog)
    if prog >= 1:
        section(c, 285, 1840, st["r1"], 0.95, "input cones")
        section(c, 795, 1840, st["r2"], 0.95, "output cones")


def walls(c, t, st, t0, t1):
    title(c, "THE PULLEY WALLS MOVE")
    r1, prev = st["r1"], st["r1_prev"]
    move = 1 if r1 > prev + 0.05 else -1 if r1 < prev - 0.05 else 0
    section(c, W / 2, 1060, r1, 2.6, None, move)
    if move > 0:
        c.text((W / 2, 230), "walls closer → belt rides HIGHER", "label", CYAN)
    elif move < 0:
        c.text((W / 2, 230), "walls apart → belt rides LOWER", "label", AMBER)
    front(c, st, oy=1046, k=0.42, labels=False)  # small loop under the subtitles
    c.text((690, 1540), "same belt,", "small", DIM, "lm")
    c.text((690, 1575), "bigger circle", "small", DIM, "lm")


def ratios(c, t, st, t0, t1):
    title(c, "ENDLESS RATIOS")
    front(c, st, oy=-20)
    c.text((W / 2, 1430), f"ratio {st['r2'] / st['r1']:.2f} : 1", "big", AMBER)
    x0, x1, y0, y1 = 170, 910, 1830, 1520
    u = ease((t - t0) / max(0.5, t1 - t0 - 0.6))
    c.line([(x0, y0), (x1, y0)], DIM, 2)
    fs = [i / 40 for i in range(41) if i / 40 <= u]
    car = [(x0 + f * (x1 - x0), y0 - (0.1 + 0.85 * f) * (y0 - y1)) for f in fs]
    eng = [(x0 + f * (x1 - x0), y0 - (0.1 + 0.62 * min(1, f / 0.1)) * (y0 - y1)) for f in fs]
    if len(car) > 1:
        c.line(car, AMBER, 5, curve=True)
        c.line(eng, CYAN, 5, curve=True)
        c.text((car[-1][0] - 10, car[-1][1] - 30), "car speed", "small", AMBER, "rm")
        c.text(
            (x0 + 10, y0 - 0.72 * (y0 - y1) - 26), "engine at its best speed", "small", CYAN, "lm"
        )


def push(c, t, st, t0, t1):
    title(c, "THE BELT PUSHES")
    front(c, st, oy=-20, blocks=True)
    nr, _ = tangent_normals(st["r1"], st["r2"])
    a = (X + st["r1"] * nr[0] + 60, Y1 + st["r1"] * nr[1] + 20)
    b = (X + st["r2"] * nr[0] + 60, Y2 + st["r2"] * nr[1] - 60)
    c.arrow(a, b, CYAN, 6, 22)
    c.text((a[0] + 34, (a[1] + b[1]) / 2), "PUSH", "big", CYAN, "lm")
    y = 1640
    half = (t0 + t1) / 2
    c.text((W / 2, 1450), "many thin steel blocks" if t < half else "held by thin steel bands",
           "label", LINE)  # fmt: skip
    off = (st["off"] * 0.6) % 36
    for i in range(-1, 26):
        x = 120 + i * 36 + off
        if 110 <= x <= 940:
            c.polygon([(x, y - 60), (x + 30, y - 60), (x + 24, y + 60), (x + 6, y + 60)],
                      fill=AMBER, outline=BELT_BROWN, width=2)  # fmt: skip
    if t >= half:
        for yy in (y - 36, y - 26):
            c.line([(100, yy), (960, yy)], LINE, 4)
    c.arrow((760, y + 110), (950, y + 110), CYAN, 5, 18)


def close(c, t, st, t0, t1):
    title(c, "NO GEAR CHANGES. EVER.")
    front(c, st, oy=-20)
    c.text((W / 2, 1450), f"ratio {st['r2'] / st['r1']:.2f} : 1", "big", AMBER)
    c.text((W / 2, 1560), "no steps between low and high", "label", CYAN)


SCENES = [Scene("hook", hook), Scene("body", parts), Scene("body", walls), Scene("body", ratios),
          Scene("note", push), Scene("close", close)]  # fmt: skip
