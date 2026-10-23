from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import ImageFont

from factory.config import REPO_ROOT, load_channel
from factory.integrations.kokoro_tts import SAMPLE_RATE, TimedToken, join_chunks
from factory.media import ffmpeg
from factory.media.ffmpeg import probe
from factory.media.subtitles import (
    MAX_LINE_PX,
    Word,
    align_words,
    ass_color,
    ass_inline_color,
    ass_time,
    build_pages,
    render_preview,
    write_ass,
)

FONT = REPO_ROOT / "assets_static" / "fonts" / "Montserrat-ExtraBold.ttf"


def tok(text: str, start: float, end: float) -> TimedToken:
    return TimedToken(text, start, end)


# --- alignment ---------------------------------------------------------------------------------


def test_spelling_comes_from_the_script_and_punctuation_tokens_are_ignored() -> None:
    tokens = [
        tok("Why", 0.0, 0.2),
        tok("so", 0.25, 0.4),
        tok("?", 0.4, 0.45),
        tok("Easy", 0.9, 1.2),
    ]

    words = align_words("Why so? Easy.", tokens)

    assert [w.text for w in words] == ["Why", "so?", "Easy."]
    assert [(w.start_s, w.end_s) for w in words] == [(0.0, 0.2), (0.25, 0.4), (0.9, 1.2)]


def test_a_contraction_split_into_two_tokens_spans_both() -> None:
    tokens = [
        tok("It", 0.0, 0.1),
        tok("does", 0.2, 0.4),
        tok("n't", 0.4, 0.5),
        tok("stop", 0.6, 0.9),
    ]

    words = align_words("It doesn't stop.", tokens)

    assert (words[1].text, words[1].start_s, words[1].end_s) == ("doesn't", 0.2, 0.5)
    assert words[2].start_s == 0.6


def test_a_word_without_a_token_gets_the_time_between_its_neighbours() -> None:
    tokens = [tok("Spin", 0.0, 0.3), tok("rotor", 1.0, 1.4)]  # "the" was not reported

    words = align_words("Spin the rotor", tokens)

    assert words[1].text == "the"
    assert (words[1].start_s, words[1].end_s) == (0.3, 1.0)


def test_chunk_times_are_joined_into_one_timeline() -> None:
    def chunk(seconds: float, tokens: list) -> SimpleNamespace:
        audio = SimpleNamespace(numpy=lambda: np.zeros(int(seconds * SAMPLE_RATE), "float32"))
        return SimpleNamespace(audio=audio, tokens=tokens)

    first = [SimpleNamespace(text="One", start_ts=0.1, end_ts=0.4)]
    second = [SimpleNamespace(text="Two", start_ts=0.2, end_ts=0.5), SimpleNamespace(
        text=" ", start_ts=None, end_ts=None)]  # fmt: skip

    speech = join_chunks([chunk(2.0, first), chunk(1.0, second)])

    assert speech.tokens == [tok("One", 0.1, 0.4), tok("Two", 2.2, 2.5)]
    assert len(speech.pcm) == 3 * SAMPLE_RATE * 4  # 3 s of float32


# --- pages, lines and the ASS file -------------------------------------------------------------


def timed(text: str, step: float = 0.3) -> list[Word]:
    return [Word(w, i * step, i * step + 0.25) for i, w in enumerate(text.split())]


def test_pages_respect_words_per_line_lines_per_page_width_and_sentences() -> None:
    text = (
        "Inside the alternator a spinning rotor sweeps a magnetic field past copper coils. "
        "Diodes convert the current."
    )
    font = ImageFont.truetype(str(FONT), 78)

    pages = build_pages(timed(text), FONT)

    for page in pages:
        assert 1 <= len(page) <= 2
        for line in page:
            assert len(line) <= 4
            assert font.getlength(" ".join(w.text for w in line)) <= MAX_LINE_PX
    last_words = [page[-1][-1].text for page in pages]
    assert "coils." in last_words  # a sentence end closes its page
    assert pages[-1][0][0].text == "Diodes"


def test_long_pause_starts_a_new_page() -> None:
    words = [Word("Wait", 0.0, 0.3), Word("for", 0.35, 0.5), Word("it", 1.5, 1.7)]

    pages = build_pages(words, FONT)

    assert [[w.text for line in p for w in line] for p in pages] == [["Wait", "for"], ["it"]]


def test_ass_file_has_the_brand_style_and_one_cyan_word_per_event(tmp_path: Path) -> None:
    out = write_ass(
        timed("Why does your battery stay charged? The alternator."),
        load_channel("coil-and-cog"),
        FONT,
        tmp_path / "subs.ass",
        "9:16",
    )
    text = out.read_text()

    assert "PlayResX: 1080\nPlayResY: 1920" in text
    style = next(line for line in text.splitlines() if line.startswith("Style: "))
    fields = style.removeprefix("Style: ").split(",")
    assert fields[1:5] == ["Montserrat ExtraBold", "78", "&H00FAF7F5", "&H00FFD100"]
    assert fields[5] == "&H0016110E"  # graphite outline
    assert (fields[16], fields[17], fields[18], fields[21]) == ("6", "0", "2", "576")
    events = [line for line in text.splitlines() if line.startswith("Dialogue:")]
    assert len(events) == 8  # one per spoken word
    for event in events:
        assert event.count(r"{\c&HFFD100&}") == 1
    starts = [re.search(r"Dialogue: 0,([^,]+),", e).group(1) for e in events]
    assert starts == sorted(starts)
    assert r"\N" in text  # the first sentence needs two lines


def test_colour_and_time_formats() -> None:
    assert ass_color("#00D1FF") == "&H00FFD100"
    assert ass_inline_color("#00D1FF") == "&HFFD100&"
    assert ass_time(3.456) == "0:00:03.46"
    assert ass_time(3725.0) == "1:02:05.00"


# --- FFmpeg ------------------------------------------------------------------------------------


def test_configured_ffmpeg_can_burn_subtitles() -> None:
    assert ffmpeg.check_tools() == []


@pytest.mark.slow
def test_preview_burns_subtitles_into_a_small_mp4(tmp_path: Path) -> None:
    voice = tmp_path / "voice.wav"
    ffmpeg.run_ffmpeg(["-f", "lavfi", "-i", "sine=f=220:d=2", "-ar", "48000", str(voice)])
    subs = write_ass(timed("Spin the rotor."), load_channel("coil-and-cog"), FONT,
                     tmp_path / "subs.ass", "9:16")  # fmt: skip

    out = render_preview(voice, subs, FONT.parent, tmp_path / "preview.mp4", "#0E1116")

    streams = {s["codec_type"]: s for s in probe(out)["streams"]}
    assert (streams["video"]["width"], streams["video"]["height"]) == (540, 960)
    assert streams["audio"]["codec_name"] == "aac"
