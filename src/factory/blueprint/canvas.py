"""Blueprint films (ROADMAP 2.23, D-111): explainer films drawn by code, so the mechanism is
calculated, never guessed by a video model.

This module holds what every machine shares: the 9:16 canvas with its grid, drawing helpers,
scene timing from the narration's word timings, the verified-facts check and the render loop.
Each machine module (jet_engine.py, cvt_gearbox.py, ...) draws its own scenes with these.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from types import ModuleType

from PIL import Image, ImageDraw, ImageFont

from factory.errors import FactoryError
from factory.media.ffmpeg import encode_rgb_frames

W, H, FPS = 1080, 1920, 30
FADE_S = 0.3  # crossfade between scenes
LEAD_S = 0.15  # a scene starts just before its sentence
# Blueprint palette (DESIGN_SYSTEM §11): navy paper and grid, plus the channel's energy colours.
BG, GRID, GRID2 = (10, 30, 54), (20, 50, 84), (26, 62, 102)
LINE, DIM, PANEL = (207, 232, 255), (120, 160, 200), (16, 44, 76)
CYAN, AMBER, RED, BLUE = (0, 209, 255), (255, 138, 0), (255, 70, 40), (58, 123, 255)
# Subtitles sit around y 1180-1360 (subs.ass MarginV 576): scenes leave that band empty.
SUBTITLE_BAND = (1180, 1360)
FONTS = {"head": ("Montserrat-ExtraBold.ttf", 44), "big": ("Montserrat-ExtraBold.ttf", 52),
         "label": ("Inter-SemiBold.ttf", 30), "small": ("Inter-SemiBold.ttf", 25)}  # fmt: skip
ROLES = ("hook", "body", "note", "close")
NUMBER = re.compile(r"\d[\d,.]*")


@dataclass(frozen=True)
class Fact:
    """A number or claim shown on screen, with the page that proves it (owner rule: no
    discrepancy anywhere, D-111)."""

    text: str
    source: str
    value: float | None = None  # for counters that count up to it
    about: str = ""  # what the number describes, so a script can't use it for something else
    second_source: str = ""  # an independent page that agrees; required for Blender models (D-122)


def lerp(a: float, b: float, f: float) -> float:
    return a + (b - a) * f


def ease(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


def mix(c1: tuple, c2: tuple, f: float) -> tuple:
    f = min(1.0, max(0.0, f))
    return tuple(round(lerp(a, b, f)) for a, b in zip(c1, c2, strict=True))


@cache
def _font(fonts: Path, name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(fonts / name), size)


@cache
def _background(ss: float) -> Image.Image:
    img = Image.new("RGB", (round(W * ss), round(H * ss)), BG)
    d = ImageDraw.Draw(img)
    width = max(1, round(ss))
    for x in range(0, W, 30):
        d.line([(x * ss, 0), (x * ss, H * ss)], fill=GRID2 if x % 150 == 0 else GRID, width=width)
    for y in range(0, H, 30):
        d.line([(0, y * ss), (W * ss, y * ss)], fill=GRID2 if y % 150 == 0 else GRID, width=width)
    return img


class Canvas:
    """One frame. Scenes draw in 1080x1920 coordinates; `ss` supersamples for smooth edges.
    Every text drawn is kept in `drawn`, so the facts check sees exactly what is on screen."""

    def __init__(self, fonts: Path, ss: float = 2.0, drawn: set[str] | None = None):
        self.fonts, self.ss = fonts, ss
        self.img = _background(ss).copy()
        self.d = ImageDraw.Draw(self.img)
        self.drawn = drawn if drawn is not None else set()

    def p(self, xy: tuple[float, float]) -> tuple[float, float]:
        return (xy[0] * self.ss, xy[1] * self.ss)

    def w(self, px: float) -> int:
        return max(1, round(px * self.ss))

    def line(self, pts: Sequence, fill: tuple, width: float = 3, curve: bool = False) -> None:
        if len(pts) > 1:
            self.d.line([self.p(q) for q in pts], fill=fill, width=self.w(width),
                        joint="curve" if curve else None)  # fmt: skip

    def polygon(self, pts: Sequence, fill=None, outline=None, width: float = 3) -> None:
        self.d.polygon([self.p(q) for q in pts], fill=fill, outline=outline, width=self.w(width))

    def ellipse(self, c: tuple, r: float, fill=None, outline=None, width: float = 3) -> None:
        box = [self.p((c[0] - r, c[1] - r)), self.p((c[0] + r, c[1] + r))]
        self.d.ellipse(box, fill=fill, outline=outline, width=self.w(width))

    def arc(self, c: tuple, r: float, start: float, end: float, fill, width: float = 3) -> None:
        box = [self.p((c[0] - r, c[1] - r)), self.p((c[0] + r, c[1] + r))]
        self.d.arc(box, start, end, fill=fill, width=self.w(width))

    def rect(self, a: tuple, b: tuple, fill=None, outline=None, width: float = 2) -> None:
        self.d.rectangle([self.p(a), self.p(b)], fill=fill, outline=outline, width=self.w(width))

    def rounded(self, a: tuple, b: tuple, radius: float, outline, width: float = 3) -> None:
        self.d.rounded_rectangle([self.p(a), self.p(b)], radius=self.w(radius), outline=outline,
                                 width=self.w(width))  # fmt: skip

    def text(self, xy: tuple, s: str, size: str, fill, anchor: str = "mm") -> None:
        name, px = FONTS[size]
        self.drawn.add(s)
        self.d.text(self.p(xy), s, font=_font(self.fonts, name, self.w(px)), fill=fill,
                    anchor=anchor)  # fmt: skip

    def arrow(self, a: tuple, b: tuple, fill, width: float = 4, head: float = 16) -> None:
        self.line([a, b], fill, width)
        ang = math.atan2(b[1] - a[1], b[0] - a[0])
        left = (b[0] - head * math.cos(ang - 0.45), b[1] - head * math.sin(ang - 0.45))
        right = (b[0] - head * math.cos(ang + 0.45), b[1] - head * math.sin(ang + 0.45))
        self.polygon([b, left, right], fill=fill, width=0)


# --- timing ------------------------------------------------------------------------------------


def sentences(words: Sequence[dict]) -> list[tuple[float, float]]:
    """(start, end) of each spoken sentence, from voice_words.json."""
    out: list[tuple[float, float]] = []
    start = None
    for w in words:
        start = w["start_s"] if start is None else start
        if w["text"].rstrip().endswith((".", "?", "!")):
            out.append((start, w["end_s"]))
            start = None
    if start is not None:
        out.append((start, words[-1]["end_s"]))
    return out


def count_sentences(text: str) -> int:
    return max(1, len(re.findall(r"[.?!](?:\s|$)", text.strip())))


def scene_starts(roles: Sequence[str], spoken: Sequence[tuple[float, float]], note_n: int,
                 duration: float) -> list[float]:  # fmt: skip
    """When each scene starts. The script is hook + body + Engineer's Note + closing line, so:
    the hook scene starts the film, the close scene starts on the last sentence, the note scene on
    the note's first sentence, and the body scenes share the sentences between in order (split
    evenly in time when there are fewer sentences than body scenes)."""
    if list(roles[:1]) != ["hook"] or roles[-1] != "close" or roles.count("note") != 1:
        raise FactoryError(f"a blueprint needs hook ... note, close scenes, got {list(roles)}")
    if len(spoken) < note_n + 2:
        raise FactoryError(f"the narration has {len(spoken)} sentences: too few for the scenes")
    body = [s for s in spoken[1 : len(spoken) - 1 - note_n]]
    note_start, close_start = spoken[-1 - note_n][0], spoken[-1][0]
    body_scenes = roles.count("body")
    body_from = body[0][0] if body else spoken[1][0]
    starts: list[float] = []
    k = 0
    for role in roles:
        if role == "hook":
            starts.append(0.0)
        elif role == "body":
            if len(body) >= body_scenes:
                t = body[(k * len(body)) // body_scenes][0]
            else:
                t = lerp(body_from, note_start, k / body_scenes)
            starts.append(t)
            k += 1
        elif role == "note":
            starts.append(note_start)
        else:
            starts.append(close_start)
    starts = [0.0] + [max(0.0, s - LEAD_S) for s in starts[1:]]
    if any(b <= a for a, b in zip(starts, starts[1:], strict=False)) or starts[-1] >= duration:
        raise FactoryError(f"scene times are not in order: {[round(s, 2) for s in starts]}")
    return starts


# --- facts ---------------------------------------------------------------------------------------


def fact_problems(drawn: set[str], facts: dict[str, Fact], live: Sequence[str]) -> list[str]:
    """Every on-screen text with a number must contain a verified fact holding that number, or be
    a live readout the drawing calculates (e.g. a ratio), listed in the module's LIVE patterns."""
    problems = [f"fact '{k}' has no source page" for k, f in facts.items()
                if not f.source.startswith("https://")]  # fmt: skip
    for s in sorted(drawn):
        nums = NUMBER.findall(s)
        if not nums or any(re.fullmatch(p, s) for p in live):
            continue
        holding = " ".join(f.text for f in facts.values() if f.text in s)
        unproven = [n for n in nums if n not in holding]
        if unproven:
            problems.append(f"on-screen '{s}' shows {', '.join(unproven)} without a verified fact")
    return problems


# --- render --------------------------------------------------------------------------------------


@dataclass
class Scene:
    role: str
    draw: Callable  # draw(canvas, t, state, t0, t1)


@dataclass
class Part:
    """One video of a topic series (D-112): what it covers and its own scenes."""

    title: str  # e.g. "Compressor and Combustion"
    focus: str  # what the script must explain, for the script prompt
    scenes: list[Scene]


def scenes_for(module: ModuleType, part_n: int | None) -> list[Scene]:
    """The whole-machine film, or one part of the machine's series."""
    if part_n is None:
        return module.SCENES
    parts = getattr(module, "PARTS", [])
    if not 1 <= part_n <= len(parts):
        raise FactoryError(f"the {module.MACHINE} drawing has no part {part_n}")
    return parts[part_n - 1].scenes


def frames(module: ModuleType, starts: Sequence[float], duration: float, fonts: Path, *,
           scenes: Sequence[Scene] | None = None,
           ss: float = 2.0, size: tuple[int, int] = (W, H), fps: int = FPS,
           drawn: set[str] | None = None) -> Iterator[bytes]:  # fmt: skip
    scenes = list(scenes if scenes is not None else module.SCENES)
    ends = [*starts[1:], duration]
    state = module.new_state(list(starts), duration)
    dt = 1 / fps

    def render(i: int, t: float) -> Image.Image:
        c = Canvas(fonts, ss, drawn)
        scenes[i].draw(c, t, state, starts[i], ends[i])
        return c.img

    for n in range(round(duration * fps)):
        t = n * dt
        i = max(k for k, s in enumerate(starts) if s <= t)
        img = render(i, t)
        if i + 1 < len(scenes) and t > starts[i + 1] - FADE_S:
            img = Image.blend(img, render(i + 1, t), (t - (starts[i + 1] - FADE_S)) / FADE_S)
        yield img.resize(size, Image.Resampling.LANCZOS).tobytes()
        module.step(state, t, dt)


def render_film(module: ModuleType, words: Sequence[dict], note_n: int, duration: float,
                fonts: Path, out_path: Path, *, part_n: int | None = None, ss: float = 2.0,
                size: tuple[int, int] = (W, H), fps: int = FPS) -> list[str]:  # fmt: skip
    """Render the machine's film (or one part of its series) to `out_path`, timed to the
    narration, and return any fact problems found in what was drawn (the caller refuses the film
    if there are any)."""
    scenes = scenes_for(module, part_n)
    starts = scene_starts([s.role for s in scenes], sentences(words), note_n, duration)
    drawn: set[str] = set()
    encode_rgb_frames(
        frames(module, starts, duration, fonts, scenes=scenes, ss=ss, size=size, fps=fps,
               drawn=drawn),
        width=size[0], height=size[1], fps=fps, duration_s=duration, out_path=out_path,
    )  # fmt: skip
    return fact_problems(drawn, module.FACTS, module.LIVE)
