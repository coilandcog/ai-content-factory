"""A stand-in for KokoroTTS().synthesize: a sine tone plus evenly spaced word timings.

Fast and deterministic, so unit tests never load PyTorch or the Kokoro model.
"""

from __future__ import annotations

import math
from array import array
from collections.abc import Callable

from factory.integrations.kokoro_tts import SAMPLE_RATE, Speech, TimedToken


def make_fake_tts(
    seconds: float = 2.5, amplitude: float = 0.02
) -> Callable[[str, str, float], Speech]:
    """A synthesizer whose audio is a 220 Hz tone of `seconds` (amplitude 0.02 is about -30
    LUFS, so loudness normalisation has real work to do). Each word of the text gets an equal
    slice of the time, like Kokoro's token timestamps."""

    def synthesize(text: str, voice_id: str, speed: float = 1.0) -> Speech:
        n = int(seconds * SAMPLE_RATE)
        tone = array(
            "f", (amplitude * math.sin(2 * math.pi * 220 * i / SAMPLE_RATE) for i in range(n))
        )
        words = text.split()
        step = seconds / max(len(words), 1)
        tokens = [TimedToken(w, i * step, (i + 0.8) * step) for i, w in enumerate(words)]
        return Speech(tone.tobytes(), tokens)  # x86 is little-endian, matching "f32le"

    return synthesize
