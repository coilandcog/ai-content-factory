"""Real Kokoro speech on the CPU. Off by default; run with: uv run pytest -m integration -k kokoro

The first run downloads the model (~330 MB) from Hugging Face.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from factory.integrations.kokoro_tts import SAMPLE_RATE, KokoroTTS
from factory.media.audio import normalize_to_wav
from factory.media.ffmpeg import run_ffmpeg
from factory.media.subtitles import align_words

pytestmark = pytest.mark.integration

TEXT = "The alternator keeps your battery charged. Diodes turn its current into direct current."


def test_kokoro_speaks_and_word_times_match_the_audio(tmp_path: Path) -> None:
    speech = KokoroTTS().synthesize(TEXT, "am_michael")
    seconds = len(speech.pcm) / 4 / SAMPLE_RATE  # 4 bytes per float32 sample
    assert 3.0 < seconds < 15.0

    words = align_words(TEXT, speech.tokens)
    assert [w.text for w in words] == TEXT.split()  # spelling from the script
    assert all(a.start_s <= b.start_s for a, b in zip(words, words[1:], strict=False))
    assert words[-1].end_s <= seconds + 0.05

    # ROADMAP 1.7: timing within ±0.15 s. The highlight switches when a word STARTS, so check
    # starts against the audio: wherever speech resumes after a pause in the final 48 kHz
    # voice.wav (silencedetect), a word must start within 0.15 s. Kokoro stretches the last word
    # of a sentence over part of the following pause, so word END times are not compared (D-065).
    wav = tmp_path / "voice.wav"
    normalize_to_wav(speech.pcm, input_rate=SAMPLE_RATE, out_path=wav)
    report = run_ffmpeg(["-i", str(wav), "-af", "silencedetect=n=-35dB:d=0.1", "-f", "null", "-"])
    resumes = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", report)]
    resumes = [r for r in resumes if r < seconds - 0.2]  # the trailing silence has no next word
    assert len(resumes) >= 2  # the start of the audio and the pause between the sentences
    for resume in resumes:
        nearest = min(words, key=lambda w: abs(w.start_s - resume))
        print(f"\nspeech resumes {resume:.2f} s; '{nearest.text}' starts {nearest.start_s:.2f} s")
        assert abs(nearest.start_s - resume) <= 0.15
