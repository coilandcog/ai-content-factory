from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from factory.config import REPO_ROOT, load_channel
from factory.media import qa
from factory.media.ffmpeg import run_ffmpeg
from factory.media.subtitles import Word, write_ass

FONT = REPO_ROOT / "assets_static" / "fonts" / "Montserrat-ExtraBold.ttf"


def clip(path: Path, size: str = "1080x1920", seconds: float = 2, tone: bool = True) -> Path:
    source = f"sine=f=440:d={seconds}" if tone else f"anullsrc=r=48000:cl=stereo:d={seconds}"
    audio = ["-f", "lavfi", "-i", source]
    run_ffmpeg(["-f", "lavfi", "-i", f"color=c=gray:s={size}:r=30:d={seconds}", *audio,
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000",
                "-shortest", str(path)])  # fmt: skip
    return path


def subs_and_words(folder: Path, text: str) -> tuple[Path, Path]:
    words = [Word(w, i * 0.3, i * 0.3 + 0.25) for i, w in enumerate(text.split())]
    subs = write_ass(words, load_channel("coil-and-cog"), FONT, folder / "subs.ass", "9:16")
    words_json = folder / "voice_words.json"
    words_json.write_text(json.dumps([w.__dict__ for w in words]))
    return subs, words_json


# --- format, duration, loudness ----------------------------------------------------------------


@pytest.mark.slow
def test_video_format_passes_and_reports_a_wrong_size(tmp_path: Path) -> None:
    good, bad = clip(tmp_path / "good.mp4"), clip(tmp_path / "bad.mp4", size="720x1280")

    assert qa.video_format([good], "9:16")[0] is True
    ok, detail = qa.video_format([good, bad], "9:16")
    assert ok is False and "bad.mp4" in detail and "720" in detail


@pytest.mark.slow
def test_duration_bounds(tmp_path: Path) -> None:
    video = clip(tmp_path / "v.mp4", seconds=2)

    assert qa.duration(video, 1, 3)[0] is True
    ok, detail = qa.duration(video, 30, 50)
    assert ok is False and "needs 30-50 s" in detail


@pytest.mark.slow
def test_loudness_flags_a_loud_render(tmp_path: Path) -> None:
    loud = clip(tmp_path / "loud.mp4")  # a full-scale sine is far louder than -14 LUFS

    ok, detail = qa.loudness([loud])
    assert ok is False and "needs -14 ±1" in detail


# --- subtitles and safe zones ------------------------------------------------------------------


def test_subtitles_pass_and_missing_file_fails(tmp_path: Path) -> None:
    subs, words = subs_and_words(tmp_path, "The rotor spins inside the stator coils.")

    assert qa.subtitle_coverage(subs, words)[0] is True
    subs.unlink()
    ok, detail = qa.subtitle_coverage(subs, words)
    assert ok is False and "subs.ass missing" in detail


def test_too_many_words_on_a_line_fails(tmp_path: Path) -> None:
    subs, words = subs_and_words(tmp_path, "Spin the rotor.")
    text = subs.read_text().replace("Spin", "One two three four five Spin", 1)
    subs.write_text(text)

    ok, detail = qa.subtitle_coverage(subs, words)
    assert ok is False and "too much text" in detail


def test_safe_zones_catch_a_card_outside_the_box(tmp_path: Path) -> None:
    subs, _ = subs_and_words(tmp_path, "Spin the rotor.")
    card = tmp_path / "diag_02.png"
    image = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    image.putpixel((1000, 1500), (255, 255, 255, 255))  # in the button zone
    image.save(card)

    ok, detail = qa.safe_zones(subs, FONT, [card])
    assert ok is False and "diag_02.png reaches outside the safe box" in detail
    assert qa.safe_zones(subs, FONT, [])[0] is True


def test_diagram_count(tmp_path: Path) -> None:
    pngs = [tmp_path / "diag_02.png", tmp_path / "diag_05.png"]
    for png in pngs:
        Image.new("RGBA", (10, 10)).save(png)

    assert qa.diagram_count(pngs, 2)[0] is True
    assert qa.diagram_count(pngs[:1], 2) == (False, "1 diagram(s) (needs at least 2)")
    pngs[1].unlink()
    assert "missing diag_05.png" in qa.diagram_count(pngs, 2)[1]


# --- music, variety, preview -------------------------------------------------------------------


@pytest.mark.slow
def test_music_rules(tmp_path: Path) -> None:
    silent_end = clip(tmp_path / "tiktok.mp4", tone=False)
    with_music = clip(tmp_path / "tiktok_music.mp4", tone=True)

    assert qa.music_rules("youtube_audio_library", silent_end)[0] is True
    ok, detail = qa.music_rules("youtube_audio_library", with_music)
    assert ok is False and "not silent under the sting" in detail
    assert "not the Audio Library" in qa.music_rules(None, silent_end)[1]


def test_variety() -> None:
    assert qa.variety(("reveal", "classic"), (None, None))[0] is True
    assert qa.variety(("reveal", "classic"), ("what_if", "cold_open"))[0] is True
    ok, detail = qa.variety(("reveal", "classic"), ("reveal", "cold_open"))
    assert ok is False and detail == "same hook_type as the previous video"


def test_preview_size(tmp_path: Path) -> None:
    preview = tmp_path / "preview_720p.mp4"
    with preview.open("wb") as f:
        f.truncate(46_000_000)  # a sparse 46 MB file: instant, takes no real disk space

    ok, detail = qa.preview_size(preview)
    assert ok is False and "under 45 MB" in detail


def test_a_check_that_crashes_counts_as_a_failure(tmp_path: Path) -> None:
    inputs = qa.QaInputs(
        folder=tmp_path, aspect="9:16", min_s=30, max_s=50, min_diagrams=2, diagram_shots=[2],
        subtitle_font=FONT, music_source="youtube_audio_library",
    )  # fmt: skip

    checks = {c.name: c for c in qa.run_checks(inputs)}

    assert checks["duration"].ok is False and "could not check" in checks["duration"].detail
    assert checks["metadata"].ok is None and checks["similarity"].ok is None
