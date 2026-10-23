"""Real Kokoro speech on the CPU. Off by default; run with: uv run pytest -m integration -k kokoro

The first run downloads the model (~330 MB) from Hugging Face.
"""

from __future__ import annotations

import pytest

from factory.integrations.kokoro_tts import SAMPLE_RATE, KokoroTTS

pytestmark = pytest.mark.integration


def test_kokoro_speaks_a_sentence() -> None:
    pcm = KokoroTTS().synthesize("The alternator keeps your battery charged.", "am_michael")

    seconds = len(pcm) / 4 / SAMPLE_RATE  # 4 bytes per float32 sample
    assert 1.0 < seconds < 10.0
