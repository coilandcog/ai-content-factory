"""Kokoro-82M text-to-speech on the CPU (Apache 2.0; INTEGRATIONS.md §9).

The model (~330 MB) downloads once from Hugging Face into ~/.cache/huggingface on first use.
PyTorch takes a few seconds to load, so it is imported only when speech is actually needed.
"""

from __future__ import annotations

import logging
import os
import warnings
from typing import Any

log = logging.getLogger("factory.kokoro")

SAMPLE_RATE = 24_000  # what Kokoro produces; audio.normalize_to_wav resamples to 48 kHz
REPO_ID = "hexgrad/Kokoro-82M"


class KokoroTTS:
    """`synthesize(text, voice_id, speed)` returns mono 32-bit float samples at 24 kHz."""

    def __init__(self) -> None:
        self._pipelines: dict[str, Any] = {}

    def synthesize(self, text: str, voice_id: str, speed: float = 1.0) -> bytes:
        pipeline = self._pipeline(voice_id[0])  # "am_michael" -> "a" = American English
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # PyTorch deprecation notices are not ours to fix
            chunks = [c.audio for c in pipeline(text, voice=voice_id, speed=speed)]
        audio = [c for c in chunks if c is not None]
        log.info(
            "Kokoro %s: %d chunk(s), %.1f s",
            voice_id,
            len(audio),
            sum(len(c) for c in audio) / SAMPLE_RATE,
            extra={"event": "tts_done"},
        )
        # Little-endian float32 is what FFmpeg reads as "f32le".
        return b"".join(c.numpy().astype("<f4").tobytes() for c in audio)

    def _pipeline(self, lang_code: str) -> Any:
        if lang_code not in self._pipelines:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                import torch
                from kokoro import KPipeline

                # Half the logical cores (= physical cores with hyper-threading): measured on the
                # owner's PC, 4 threads synthesise ~2x faster than 8, which fight each other.
                torch.set_num_threads(max(1, (os.cpu_count() or 2) // 2))

                self._pipelines[lang_code] = KPipeline(lang_code=lang_code, repo_id=REPO_ID)
        return self._pipelines[lang_code]
