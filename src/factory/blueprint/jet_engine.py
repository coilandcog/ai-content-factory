"""Blueprint film: a high-bypass turbofan cut along its centreline (D-111).

Air only ever moves left to right. Its speed and colour follow the real engine: most air goes
around the core (bypass); the core air is squeezed (brighter), burned (amber/red) and leaves fast.
"""

from __future__ import annotations

import math
import random

from factory.blueprint.canvas import (
    AMBER,
    BLUE,
    CYAN,
    DIM,
    LINE,
    PANEL,
    RED,
    Fact,
    Scene,
    W,
    ease,
    lerp,
    mix,
)  # fmt: skip

MACHINE = "jet engine"
GE90 = "https://en.wikipedia.org/wiki/General_Electric_GE90"
BLADE = "https://en.wikipedia.org/wiki/Turbine_blade"
BAM = "https://opus4.kobv.de/opus4-bam/frontdoor/index/index/docId/40903"
FACTS = {
    # GE90: static air mass flow 1,350 kg/s, bypass ratio 8.4-9, pressure ratio 40:1-42:1.
    "airflow": Fact("over 1,000 kg", GE90, value=1000),
    "bypass": Fact("about 9 parts", GE90),
    "core_part": Fact("1 part", GE90),
    "pressure": Fact("× 40", GE90, value=40),
    # Turbine entry temperatures above 1,900 K (about 1,630 °C) in modern designs.
    "gas": Fact("over 1,600 °C", BLADE, value=1600),
    # Blade material melts at 1,300-1,400 K (Wikipedia); CMSX-4 about 1,612 K (BAM): the range
    # shown covers both sources.
    "melt": Fact("about 1,100-1,350 °C", BAM),
}
LIVE = (r"[\d,]+ kg", r"× \d+", r"[\d,]+ °C")  # counters that count up to a fact's value

CY = 760
X_FAN, X_SPLIT, X_COMB0, X_COMB1, X_TURB1, X_TAIL, X_NAC = 165, 250, 525, 665, 800, 965, 770
CORE_OUT = [(X_SPLIT, 122), (X_COMB0, 76), (560, 94), (630, 94), (X_COMB1, 80), (X_TURB1, 106),
            (X_TAIL, 72)]  # fmt: skip
CORE_IN = [(X_FAN, 34), (X_SPLIT, 34), (X_COMB0, 38), (X_COMB1, 38), (X_TURB1, 32), (X_TAIL, 4)]
NAC_IN = [(60, 300), (X_FAN, 288), (X_NAC, 238)]
CASE_T = 16
X_LPT = lerp(X_COMB1 + 20, X_TURB1 - 6, 2 / 3)  # the rear turbine rows turn the fan


def seg(x: float, pts: list[tuple[float, float]]) -> float:
    """Piecewise-linear value at x."""
    if x <= pts[0][0]:
        return pts[0][1]
    for (x0, v0), (x1, v1) in zip(pts, pts[1:], strict=False):
        if x <= x1:
            return lerp(v0, v1, (x - x0) / (x1 - x0))
    return pts[-1][1]


def core_speed(x: float) -> float:
    return seg(x, [(0, 1.0), (X_SPLIT, 1.0), (X_COMB0, 0.55), (X_COMB1, 0.75), (X_TURB1, 1.5),
                   (X_TAIL, 2.6), (1100, 2.9)])  # fmt: skip


def bypass_speed(x: float) -> float:
    return seg(x, [(0, 1.0), (X_FAN, 1.1), (X_NAC, 1.7), (1100, 1.9)])


def core_colour(x: float) -> tuple:
    if x < X_SPLIT:
        return BLUE
    if x < X_COMB0:
        return mix(BLUE, (190, 220, 255), (x - X_SPLIT) / (X_COMB0 - X_SPLIT))
    if x < X_COMB1:
        return mix(AMBER, RED, (x - X_COMB0) / (X_COMB1 - X_COMB0))
    return mix(RED, AMBER, (x - X_COMB1) / (X_TAIL - X_COMB1))


class Particle:
    def __init__(self, rng: random.Random, x: float):
        self.rng = rng
        self.reset(x)

    def reset(self, x: float) -> None:
        self.x = x
        self.top = self.rng.random() < 0.5
        self.core = self.rng.random() < 0.28  # drawn share, more than the real 1 in 10, so it shows
        self.lane = self.rng.uniform(0.12, 0.88)

    def r(self) -> float:
        x = self.x
        if self.core:
            if x < X_SPLIT:
                return lerp(30, 118, self.lane) if x < X_FAN else lerp(40, 116, self.lane)
            return lerp(seg(x, CORE_IN), seg(x, CORE_OUT), self.lane)
        lo = (seg(x, CORE_OUT) + CASE_T) if x >= X_SPLIT else 135
        hi = seg(x, NAC_IN) if x <= X_NAC else 238 + (x - X_NAC) * 0.05
        return lerp(lo, hi, self.lane)

    def speed(self) -> float:
        return core_speed(self.x) if self.core else bypass_speed(self.x)

    def colour(self) -> tuple:
        return core_colour(self.x) if self.core else (CYAN if self.x > X_FAN else BLUE)


def new_state(starts: list[float], duration: float) -> dict:
    rng = random.Random(7)
    return {"fan": 0.0, "core": 0.0, "particles": [Particle(rng, rng.uniform(-30, 1110))
                                                   for _ in range(420)]}  # fmt: skip


def step(st: dict, t: float, dt: float) -> None:
    st["fan"] += 3.0 * dt  # the fan turns slower than the core (two shafts)
    st["core"] += 6.0 * dt
    for p in st["particles"]:
        p.x += p.speed() * 120 * dt  # always forward: speed is never negative
        if p.x > 1110:
            p.reset(-30)


# --- drawing -------------------------------------------------------------------------------------


def blade_row(c, x, r_in, r_out, angle, colour, width=3, n=14, half=10):
    """A spinning blade row seen from the side: blade edges sweep up and down as it turns."""
    c.line([(x, CY - r_out), (x, CY - r_in)], DIM, 1)
    c.line([(x, CY + r_in), (x, CY + r_out)], DIM, 1)
    for k in range(n):
        a = angle + k * 2 * math.pi / n
        if math.sin(a) <= 0:
            continue
        for sgn in (-1, 1):
            y = lerp(CY + sgn * r_in, CY + sgn * r_out, (math.cos(a) + 1) / 2)
            c.line([(x - half, y), (x + half, y)], colour, width)


def engine(c, st, progress=1.0, focus="all", shaft=False):
    xe = lerp(40, 1000, progress)
    for sgn in (-1, 1):  # nacelle
        xs = [lerp(60, X_NAC, i / 40) for i in range(41)]
        inner = [(x, CY + sgn * seg(x, NAC_IN)) for x in xs
                 if x <= xe]  # fmt: skip
        outer = [(x, CY + sgn * (seg(x, NAC_IN) + lerp(34, 14, (x - 60) / (X_NAC - 60))))
                 for x, _ in inner]  # fmt: skip
        if len(inner) > 1:
            c.polygon(inner + outer[::-1], fill=PANEL, outline=LINE)
    if xe > 60:  # spinner
        c.polygon([(60, CY), (X_FAN, CY - 46), (X_FAN, CY + 46)], fill=PANEL, outline=LINE)
    for sgn in (-1, 1):  # core casing
        xs = [lerp(X_SPLIT, X_TAIL - 30, i / 60) for i in range(61)]
        inner = [(x, CY + sgn * seg(x, CORE_OUT)) for x in xs if x <= xe]
        outer = [(x, CY + sgn * (seg(x, CORE_OUT) + CASE_T)) for x, _ in inner]
        if len(inner) > 1:
            c.polygon(inner + outer[::-1], fill=PANEL, outline=LINE)
    hub = [
        (x, seg(x, CORE_IN)) for x in [lerp(X_FAN, X_TAIL, i / 60) for i in range(61)] if x <= xe
    ]
    if len(hub) > 1:
        c.polygon([(x, CY - r) for x, r in hub] + [(x, CY + r) for x, r in hub[::-1]],
                  fill=PANEL, outline=LINE, width=2)  # fmt: skip
    if progress < 1:
        return
    c.line([(X_FAN, CY), (X_LPT, CY)], CYAN if shaft else DIM, 8 if shaft else 4)  # inner shaft
    blade_row(c, X_FAN + 6, 46, 284, st["fan"], LINE, 4, 18, 14)
    for i in range(7):  # compressor: rows get shorter as the air is squeezed
        x = lerp(X_SPLIT + 30, X_COMB0 - 18, i / 6)
        blade_row(c, x, seg(x, CORE_IN), seg(x, CORE_OUT), st["core"] + i, mix(DIM, LINE, 0.5), 2,
                  12, 7)  # fmt: skip
    for i in range(4):  # turbine: the front rows drive the compressor, the rear rows the fan
        x = lerp(X_COMB1 + 20, X_TURB1 - 6, i / 3)
        rear = i >= 2
        angle = st["fan"] if rear else -st["core"]
        bright = focus == "all" or (focus == "turbine" and rear)
        blade_row(c, x, seg(x, CORE_IN), seg(x, CORE_OUT), angle + i,
                  AMBER if bright else mix(DIM, AMBER, 0.4), 3, 12, 7)  # fmt: skip
    for sgn in (-1, 1):  # fuel injectors
        y = CY + sgn * (seg(X_COMB0 + 12, CORE_IN) + seg(X_COMB0 + 12, CORE_OUT)) / 2
        c.ellipse((X_COMB0 + 12, y), 6, fill=AMBER, width=0)


def flows(c, st, focus):
    for p in st["particles"]:
        if p.x < 30 or p.x > 1080:
            continue
        if focus == "all":
            bright = True
        elif focus == "bypass":
            bright = not p.core
        else:
            bright = p.core
        col = p.colour() if bright else mix((10, 30, 54), p.colour(), 0.25)
        y = CY - p.r() if p.top else CY + p.r()
        length = 8 + 7 * p.speed()  # faster air = longer streak
        c.line([(p.x - length, y), (p.x, y)], col, 4 if bright else 3)


def title(c, s):
    c.text((W / 2, 120), s, "head", LINE)


def intake(c, t, st, t0, t1):
    title(c, "A TON OF AIR EVERY SECOND")
    prog = min(1.0, (t - t0 + 0.05) / 1.4)
    engine(c, st, progress=prog)
    if prog >= 1:
        flows(c, st, "all")
    f = FACTS["airflow"]
    u = ease((t - t0 - 0.6) / max(0.5, t1 - t0 - 1.2))
    c.text((W / 2, 1490), f"{int(f.value * u):,} kg" if u < 1 else f.text, "big", CYAN)
    c.text((W / 2, 1565), "of air, every second", "label", LINE)
    c.text((W / 2, 1625), "(large airliner engine, take-off)", "small", DIM)


def bypass(c, t, st, t0, t1):
    title(c, "MOST AIR GOES AROUND")
    engine(c, st, focus="bypass")
    flows(c, st, "bypass")
    c.text((W / 2, CY - 345), "bypass air", "label", CYAN)
    c.arrow((380, CY - 300), (620, CY - 300), CYAN)
    x0, x1, y = 120, 960, 1520
    split = lerp(x0, x1, 0.9)
    c.rect((x0, y - 26), (split, y + 26), fill=CYAN)
    c.rect((split, y - 26), (x1, y + 26), fill=AMBER)
    c.text((x0, y - 60), f"around the core: {FACTS['bypass'].text}", "label", CYAN, "lm")
    c.text((x1, y + 62), f"through the core: {FACTS['core_part'].text}", "label", AMBER, "rm")
    c.text((W / 2, 1700), "the fan's push makes most of the thrust", "small", DIM)


def core(c, t, st, t0, t1):
    title(c, "SQUEEZE, ADD FUEL, BURN")
    engine(c, st, focus="core")
    flows(c, st, "core")
    for x, lab, col in ((385, "compressor", LINE), (595, "combustion", AMBER)):
        c.text((x, CY - 340), lab, "label", col)
        c.line([(x, CY - 318), (x, CY - 150)], DIM, 2)
    u = ease((t - t0) / 3.0)
    p, g = FACTS["pressure"], FACTS["gas"]
    c.text((290, 1470), "pressure", "label", LINE)
    c.text((290, 1545), f"× {int(1 + (p.value - 1) * u)}" if u < 1 else p.text, "big",
           mix(BLUE, LINE, u))  # fmt: skip
    v = ease((t - t0 - 1.0) / 2.5)
    c.text((790, 1470), "after burning", "label", LINE)
    c.text((790, 1545), f"{int(g.value * v):,} °C" if v < 1 else g.text, "big", mix(AMBER, RED, v))
    c.text((W / 2, 1650), "squeezed, fuel added, burned", "small", DIM)


def turbine(c, t, st, t0, t1):
    title(c, "THE TURBINE TURNS THE FAN")
    engine(c, st, focus="turbine", shaft=True)
    flows(c, st, "core")
    c.text((X_LPT + 10, CY - 340), "rear turbine", "label", AMBER)
    c.line([(X_LPT + 10, CY - 318), (X_LPT + 10, CY - 140)], DIM, 2)
    c.text((X_FAN + 10, CY - 340), "fan", "label", LINE)
    c.arrow((X_LPT - 30, CY + 26), (X_FAN + 60, CY + 26), CYAN, 3)
    y = 1560
    for x, r, lab, col in ((260, 70, "rear turbine", AMBER), (820, 110, "fan", LINE)):
        c.ellipse((x, y), r, outline=col)
        for k in range(5):
            a = st["fan"] + k * 2 * math.pi / 5
            c.line([(x, y), (x + (r - 8) * math.cos(a), y + (r - 8) * math.sin(a))], col, 2)
        c.text((x, y + r + 34), lab, "label", col)
    c.line([(330, y), (710, y)], CYAN, 10)
    c.text((520, y - 34), "inner shaft", "label", CYAN)


# turbine blade close-up (the Engineer's Note): a slim crescent section, chord along the flow
BX, BY, CHORD = 160, 760, 760


def _airfoil():
    up, lo = [], []
    m, pp, tk = 0.13, 0.38, 0.17
    for i in range(61):
        xc = (1 - math.cos(math.pi * i / 60)) / 2
        yt = 5 * tk * (0.2969 * math.sqrt(xc) - 0.126 * xc - 0.3516 * xc**2 + 0.2843 * xc**3
                       - 0.1036 * xc**4)  # fmt: skip
        if xc < pp:
            yc = m / pp**2 * (2 * pp * xc - xc**2)
        else:
            yc = m / (1 - pp) ** 2 * (1 - 2 * pp + 2 * pp * xc - xc**2)
        up.append((BX + xc * CHORD, BY - (yc + yt) * CHORD))
        lo.append((BX + xc * CHORD, BY - (yc - yt) * CHORD))
    return up, lo


UP, LO = _airfoil()


def at(curve, x):
    for (x0, y0), (x1, y1) in zip(curve, curve[1:], strict=False):
        if x0 <= x <= x1:
            return lerp(y0, y1, (x - x0) / (x1 - x0) if x1 > x0 else 0)
    return curve[0][1] if x < curve[0][0] else curve[-1][1]


def blade(c, t, st, t0, t1):
    title(c, "HOTTER THAN ITS MELTING POINT")
    for j, off in enumerate((-200, -140, -80, 80, 140, 200)):  # hot gas bends around the blade
        top = off < 0
        pts = []
        for i in range(121):
            x = lerp(40, 1040, i / 120)
            xc = min(max(x, BX), BX + CHORD)
            follow = (at(UP, xc) + off + 30) if top else (at(LO, xc) + off - 30)
            straight = BY + off - (40 if top else -40)
            dist = max(BX - x, x - BX - CHORD, 0)
            pts.append((x, lerp(straight, follow, 1 - ease(dist / 160))))
        c.line(pts, mix((10, 30, 54), AMBER, 0.35), 2)
        for k in range(5):
            x, y = pts[int(((t * 0.32 + k / 5 + j * 0.09) % 1) * 120)]
            c.line([(x - 26, y), (x, y)], mix(AMBER, RED, abs(off) / 200), 6)
    c.polygon(UP + LO[::-1], fill=PANEL, outline=LINE, width=4)
    for frac in (0.14, 0.34, 0.54):  # cooling channels inside, cool air moving through them
        x = BX + frac * CHORD
        y0, y1 = at(UP, x) + 16, at(LO, x) - 16
        if y1 - y0 > 24:
            c.rounded((x - 18, y0), (x + 18, y1), 12, CYAN)
            for k in range(4):
                c.ellipse((x, lerp(y1, y0, (t * 0.6 + k / 4) % 1)), 5, fill=CYAN, width=0)
    for frac in (0.08, 0.3, 0.55):  # film cooling: cool air leaves tiny holes, hugs the surface
        x = BX + frac * CHORD
        for k in range(3):
            px = x + ((t * 0.7 + k / 3) % 1) * 110
            c.ellipse((px, at(UP, px) - 9), 5, fill=CYAN, width=0)
    c.text((W / 2, 280), f"gas around the blade: {FACTS['gas'].text}", "label", AMBER)
    c.text((W / 2, 330), f"blade alloys melt at {FACTS['melt'].text}", "label", LINE)
    c.text((W / 2, 1100), "cool air flows inside and out of tiny holes", "label", CYAN)
    x0, x1 = 160, 920
    for y, val, lab, col in ((1460, 1600, f"hot gas: {FACTS['gas'].text}", AMBER),
                             (1560, 1350, f"blade alloys melt: {FACTS['melt'].text}",
                              LINE)):  # fmt: skip
        c.rect((x0, y - 18), (lerp(x0, x1, val / 1700), y + 18), fill=col)
        c.text((x0, y - 36), lab, "small", col, "lm")
    c.text((W / 2, 1660), "cooling keeps the metal below its melting point", "small", CYAN)


def close(c, t, st, t0, t1):
    title(c, "IN SLOW, OUT FAST")
    engine(c, st)
    flows(c, st, "all")
    c.text((110, CY - 360), "in: slow", "label", BLUE, "lm")
    c.text((970, CY - 330), "out: fast", "label", CYAN, "rm")
    c.text((W / 2, 1480), "air leaves faster than it came in", "label", LINE)
    c.text((W / 2, 1550), "= THRUST", "big", AMBER)


SCENES = [Scene("hook", intake), Scene("body", bypass), Scene("body", core),
          Scene("body", turbine), Scene("note", blade), Scene("close", close)]  # fmt: skip
