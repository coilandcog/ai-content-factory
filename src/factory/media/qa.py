"""Automated quality checks on a finished item (TESTING.md §4, ROADMAP 1.10, D-070).

Each check returns a Check: ok=True (pass), ok=False (fail, with a reason the owner can act on)
or ok=None (not checked yet, because the feature it needs comes in a later task).
The stage gathers the facts that live in the database into QaInputs; this module only reads
files, so every check can be tested on its own.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageFont

from factory.errors import FactoryError
from factory.media import diagrams, subtitles
from factory.media.audio import TARGET_LUFS, TRUE_PEAK_DBTP, measure_loudness
from factory.media.ffmpeg import probe, run_ffmpeg

LOUDNESS_TOLERANCE_LU = 1.0
PREVIEW_MAX_MB = 45
SILENCE_DB = -60.0  # quieter than this counts as silence
STING_CHECK_S = 0.8  # the end of the render, under the logo sting: no voice, no clip sound
BOTTOM_KEEP_FREE_PX = 480  # DESIGN_SYSTEM.md §6: no text in the bottom 480 px


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool | None
    detail: str


@dataclass(frozen=True)
class QaInputs:
    """Everything the checks need, gathered by the assemble stage."""

    folder: Path
    aspect: str
    min_s: int
    max_s: int
    min_diagrams: int
    diagram_shots: Sequence[int]
    subtitle_font: Path
    music_source: str | None  # music_tracks.source of the YouTube render's track
    variety: tuple[tuple[str | None, str | None], tuple[str | None, str | None]] = field(
        default=((None, None), (None, None))
    )  # ((hook_type, beat_order) of this item, of the previous item)


def run_checks(inputs: QaInputs) -> list[Check]:
    """All checks, in a fixed order. A check that crashes counts as a failure, never a pass."""
    folder = inputs.folder
    renders = [folder / "render_youtube.mp4", folder / "render_tiktok.mp4"]
    steps = [
        ("video format", lambda: video_format(renders, inputs.aspect)),
        ("duration", lambda: duration(renders[0], inputs.min_s, inputs.max_s)),
        ("loudness", lambda: loudness(renders)),
        ("subtitles", lambda: subtitle_coverage(folder / "subs.ass", folder / "voice_words.json")),
        (
            "safe zones",
            lambda: safe_zones(folder / "subs.ass", inputs.subtitle_font, _diagram_files(inputs)),
        ),  # fmt: skip
        ("diagrams", lambda: diagram_count(_diagram_files(inputs), inputs.min_diagrams)),
        ("music", lambda: music_rules(inputs.music_source, renders[1])),
        ("variety", lambda: variety(*inputs.variety)),
        ("preview size", lambda: preview_size(folder / "preview_720p.mp4")),
    ]
    results = []
    for name, step in steps:
        try:
            ok, detail = step()
        except (FactoryError, OSError, ValueError, KeyError) as exc:
            ok, detail = False, f"could not check: {exc}"
        results.append(Check(name, ok, detail))
    results.append(Check("metadata", None, "not checked yet: metadata.json comes in task 1.11"))
    results.append(
        Check("similarity", None, "not checked yet: similarity check comes in task 1.13")
    )
    return results


def failures(checks: Sequence[Check]) -> list[Check]:
    return [c for c in checks if c.ok is False]


def write_report(checks: Sequence[Check], path: Path) -> Path:
    data = [{"check": c.name, "ok": c.ok, "detail": c.detail} for c in checks]
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return path


# --- the checks: each returns (ok, detail) -----------------------------------------------------


def video_format(renders: Sequence[Path], aspect: str) -> tuple[bool, str]:
    size = (1080, 1920) if aspect == "9:16" else (1920, 1080)
    problems = []
    for render in renders:
        if not render.is_file():
            problems.append(f"{render.name} missing")
            continue
        streams = probe(render)["streams"]
        video = next((s for s in streams if s["codec_type"] == "video"), None)
        audio = next((s for s in streams if s["codec_type"] == "audio"), None)
        if video is None or audio is None:
            problems.append(f"{render.name} lacks a video or audio stream")
            continue
        found = (video["codec_name"], video["width"], video["height"], video["r_frame_rate"],
                 video.get("pix_fmt"), audio["codec_name"], audio["sample_rate"])  # fmt: skip
        wanted = ("h264", *size, "30/1", "yuv420p", "aac", "48000")
        if found != wanted:
            problems.append(f"{render.name} is {found}, needs {wanted}")
    return (
        not problems,
        "; ".join(problems) or f"both renders {size[0]}x{size[1]}, 30 fps, H.264/AAC",
    )


def duration(render: Path, min_s: int, max_s: int) -> tuple[bool, str]:
    seconds = float(probe(render)["format"]["duration"])
    ok = min_s <= seconds <= max_s
    return ok, f"{seconds:.1f} s" + ("" if ok else f" (needs {min_s}-{max_s} s for this format)")


def loudness(renders: Sequence[Path]) -> tuple[bool, str]:
    problems, notes = [], []
    for render in renders:
        result = measure_loudness(render)
        notes.append(f"{render.stem}: {result.integrated_lufs:.1f} LUFS")
        if abs(result.integrated_lufs - TARGET_LUFS) > LOUDNESS_TOLERANCE_LU:
            problems.append(f"{render.name}: {result.integrated_lufs:.1f} LUFS (needs -14 ±1)")
        if result.true_peak_dbtp > TRUE_PEAK_DBTP:
            problems.append(f"{render.name}: peak {result.true_peak_dbtp:.1f} dBTP (max -1)")
    return (not problems, "; ".join(problems) or ", ".join(notes))


def subtitle_coverage(subs: Path, words_json: Path) -> tuple[bool, str]:
    """subs.ass exists, its events run from the first to the last spoken word, and no page has
    more than 2 lines or a line more than 4 words."""
    if not subs.is_file():
        return False, "subs.ass missing (run `factory voice render <id>`)"
    events = _events(subs)
    if not events:
        return False, "subs.ass has no subtitle events"
    words = json.loads(words_json.read_text())
    first_word, last_word = words[0]["start_s"], words[-1]["end_s"]
    problems = []
    if events[0][0] > first_word + 0.05:
        problems.append(f"subtitles start at {events[0][0]:.2f} s, speech at {first_word:.2f} s")
    if max(end for _, end, _ in events) < last_word - 0.05:
        problems.append(f"subtitles end before the last word ({last_word:.2f} s)")
    for _, _, lines in events:
        if len(lines) > subtitles.LINES_PER_PAGE or any(
            len(line.split()) > subtitles.WORDS_PER_LINE for line in lines
        ):
            problems.append(f"too much text on screen: {' / '.join(lines)}")
            break
    return (not problems, "; ".join(problems) or f"{len(events)} word events cover the narration")


def safe_zones(subs: Path, font_path: Path, diagram_pngs: Sequence[Path]) -> tuple[bool, str]:
    """Subtitle lines fit the 760 px column above the bottom button zone; diagram cards stay
    inside the label box (DESIGN_SYSTEM.md §6-7)."""
    problems = []
    if subs.is_file():
        font = ImageFont.truetype(str(font_path), subtitles.FONT_SIZE)
        margin_v = int(_style_field(subs, "MarginV"))
        if margin_v < BOTTOM_KEEP_FREE_PX:
            problems.append(f"subtitle baseline only {margin_v} px above the bottom (needs 480+)")
        widest = max((font.getlength(line) for _, _, lines in _events(subs) for line in lines),
                     default=0)  # fmt: skip
        if widest > subtitles.MAX_LINE_PX:
            problems.append(
                f"a subtitle line is {widest:.0f} px wide (max {subtitles.MAX_LINE_PX})"
            )
    left, top, right, bottom = diagrams.SAFE_BOX
    for png in diagram_pngs:
        if not png.is_file():
            continue
        box = Image.open(png).getchannel("A").getbbox()
        if box and (box[0] < left or box[1] < top or box[2] > right or box[3] > bottom):
            problems.append(f"{png.name} reaches outside the safe box: {box}")
    return (
        not problems,
        "; ".join(problems) or "subtitles and diagram cards inside the safe zones",
    )


def diagram_count(diagram_pngs: Sequence[Path], minimum: int) -> tuple[bool, str]:
    present = [p for p in diagram_pngs if p.is_file()]
    missing = [p.name for p in diagram_pngs if not p.is_file()]
    if missing:
        return False, f"missing {', '.join(missing)} (run `factory generate`)"
    ok = len(present) >= minimum
    return ok, f"{len(present)} diagram(s)" + ("" if ok else f" (needs at least {minimum})")


def music_rules(music_source: str | None, tiktok_render: Path) -> tuple[bool, str]:
    """The YouTube track comes from the Audio Library; the TikTok render has no music, checked
    where nothing else plays: under the logo sting at the very end."""
    problems = []
    if music_source != "youtube_audio_library":
        problems.append(f"YouTube music source is {music_source!r}, not the Audio Library")
    report = run_ffmpeg(["-sseof", f"-{STING_CHECK_S}", "-i", str(tiktok_render),
                         "-af", "volumedetect", "-f", "null", "-"])  # fmt: skip
    match = re.search(r"mean_volume: (-?[\d.]+|-inf) dB", report)
    level = float(match.group(1)) if match and match.group(1) != "-inf" else -99.0
    if level > SILENCE_DB:
        problems.append(f"TikTok render is not silent under the sting ({level:.0f} dB): music?")
    return (
        not problems,
        "; ".join(problems) or "Audio Library track on YouTube; no music on TikTok",
    )


def variety(
    current: tuple[str | None, str | None], previous: tuple[str | None, str | None]
) -> tuple[bool, str]:
    if previous == (None, None):
        return True, "first video: nothing to compare with"
    repeats = [name for name, a, b in zip(("hook_type", "beat_order"), current, previous,
                                          strict=True) if a and a == b]  # fmt: skip
    if repeats:
        return False, f"same {' and '.join(repeats)} as the previous video"
    return True, f"hook {current[0]} / beats {current[1]} differ from the previous video"


def preview_size(preview: Path) -> tuple[bool, str]:
    if not preview.is_file():
        return False, "preview_720p.mp4 missing"
    mb = preview.stat().st_size / 1_000_000
    ok = mb < PREVIEW_MAX_MB
    return ok, f"{mb:.1f} MB" + ("" if ok else f" (Telegram needs under {PREVIEW_MAX_MB} MB)")


# --- helpers -----------------------------------------------------------------------------------


def _diagram_files(inputs: QaInputs) -> list[Path]:
    return [inputs.folder / "diagrams" / f"diag_{n:02d}.png" for n in inputs.diagram_shots]


def _events(subs: Path) -> list[tuple[float, float, list[str]]]:
    """(start s, end s, lines of plain text) for every Dialogue line of an ASS file."""
    events = []
    for line in subs.read_text(encoding="utf-8").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        fields = line.split(",", 9)
        text = re.sub(r"\{[^}]*\}", "", fields[9])
        events.append((_seconds(fields[1]), _seconds(fields[2]), text.split(r"\N")))
    return events


def _seconds(ass_time: str) -> float:
    hours, minutes, seconds = ass_time.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _style_field(subs: Path, name: str) -> str:
    lines = subs.read_text(encoding="utf-8").splitlines()
    fmt = next(line for line in lines if line.startswith("Format:") and "MarginV" in line)
    style = next(line for line in lines if line.startswith("Style:"))
    keys = [k.strip() for k in fmt.removeprefix("Format:").split(",")]
    values = [v.strip() for v in style.removeprefix("Style:").split(",")]
    return values[keys.index(name)]
