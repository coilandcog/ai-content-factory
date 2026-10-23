"""Kokoro-82M text-to-speech on the CPU (Apache 2.0; INTEGRATIONS.md §9).

The model (~330 MB) downloads once from Hugging Face into ~/.cache/huggingface on first use.
PyTorch takes a few seconds to load, so it is imported only when speech is actually needed.
"""

from __future__ import annotations

import logging
import os
import warnings
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("factory.kokoro")

SAMPLE_RATE = 24_000  # what Kokoro produces; audio.normalize_to_wav resamples to 48 kHz
REPO_ID = "hexgrad/Kokoro-82M"


@dataclass(frozen=True)
class TimedToken:
    """One token as Kokoro spoke it, with times in seconds from the start of the audio."""

    text: str
    start_s: float
    end_s: float


@dataclass(frozen=True)
class Speech:
    """Mono little-endian float32 samples at SAMPLE_RATE, plus when each token is spoken."""

    pcm: bytes
    tokens: list[TimedToken] = field(default_factory=list)


class KokoroTTS:
    """`synthesize(text, voice_id, speed)` returns Speech: audio plus per-token timings.

    Kokoro builds the audio from predicted token durations and reports them as timestamps,
    so the timings are exact for this audio by construction (D-065).
    """

    def __init__(self) -> None:
        self._pipelines: dict[str, Any] = {}

    def synthesize(self, text: str, voice_id: str, speed: float = 1.0) -> Speech:
        pipeline = self._pipeline(voice_id[0])  # "am_michael" -> "a" = American English
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # PyTorch deprecation notices are not ours to fix
            results = list(pipeline(text, voice=voice_id, speed=speed))
        speech = join_chunks(results)
        log.info(
            "Kokoro %s: %d chunk(s), %.1f s, %d timed tokens",
            voice_id,
            len(results),
            len(speech.pcm) / 4 / SAMPLE_RATE,
            len(speech.tokens),
            extra={"event": "tts_done"},
        )
        return speech

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


def join_chunks(results: list[Any]) -> Speech:
    """Concatenate Kokoro's chunks; shift each chunk's token times by the audio before it.

    Kokoro splits long text into chunks and times tokens from the start of their own chunk.
    """
    pcm: list[bytes] = []
    tokens: list[TimedToken] = []
    offset_s = 0.0
    for result in results:
        if result.audio is None:
            continue
        for token in result.tokens or []:
            if token.start_ts is not None and token.end_ts is not None:
                tokens.append(
                    TimedToken(token.text, offset_s + token.start_ts, offset_s + token.end_ts)
                )
        samples = result.audio.numpy().astype("<f4")  # little-endian float32 = FFmpeg "f32le"
        pcm.append(samples.tobytes())
        offset_s += len(samples) / SAMPLE_RATE
    return Speech(b"".join(pcm), tokens)
