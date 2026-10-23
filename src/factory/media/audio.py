"""Loudness normalisation and measurement with FFmpeg's loudnorm filter (EBU R128).

Platforms play everything at about -14 LUFS; delivering at that level means our voice is
neither turned down nor sounds quiet next to other videos (TESTING.md §4: -14 ±1 LUFS,
true peak <= -1 dBTP).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from factory.errors import FactoryError
from factory.media.ffmpeg import run_ffmpeg

TARGET_LUFS = -14.0
TRUE_PEAK_DBTP = -1.0  # QA limit (TESTING.md §4)
LIMIT_LINEAR = 0.84  # limiter ceiling, -1.5 dBFS
TOLERANCE_LU = 0.3  # stop correcting once this close to the target
MAX_ROUNDS = 3
OUTPUT_RATE = 48_000


@dataclass(frozen=True)
class Loudness:
    integrated_lufs: float
    true_peak_dbtp: float


def normalize_to_wav(pcm_f32: bytes, *, input_rate: int, out_path: Path) -> Loudness:
    """Turn mono 32-bit float samples into a 48 kHz, 16-bit WAV at -14 LUFS; return its loudness.

    Gain + peak limiter, then measure and correct: the limiter takes back a little loudness when
    it catches peaks, so the gain is nudged by the remaining difference (at most MAX_ROUNDS
    FFmpeg passes). Plain loudnorm could not reach -14 on speech with sharp peaks (D-059).
    """
    if not pcm_f32:
        raise FactoryError("no audio to normalise")
    raw_input = ["-f", "f32le", "-ar", str(input_rate), "-ac", "1", "-i", "-"]
    measured = _loudnorm_report(
        run_ffmpeg([*raw_input, "-af", "loudnorm=print_format=json", "-f", "null", "-"],
                   input_bytes=pcm_f32)
    )  # fmt: skip
    gain_db = TARGET_LUFS - float(measured["input_i"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(MAX_ROUNDS):
        run_ffmpeg([*raw_input, "-af", _gain_chain(gain_db), "-ar", str(OUTPUT_RATE),
                    "-c:a", "pcm_s16le", str(out_path)], input_bytes=pcm_f32)  # fmt: skip
        result = measure_loudness(out_path)
        miss = TARGET_LUFS - result.integrated_lufs
        if abs(miss) <= TOLERANCE_LU:
            break
        gain_db += miss
    return result


def _gain_chain(gain_db: float) -> str:
    # Beginner note: an FFmpeg filter chain, applied left to right. Upsampling to 192 kHz lets
    # the limiter see peaks *between* samples (true peaks); limit 0.84 = -1.5 dBFS keeps the
    # true peak safely under -1 dBTP after converting to 48 kHz.
    return (
        f"aresample=192000,volume={gain_db:.2f}dB,"
        f"alimiter=limit={LIMIT_LINEAR}:level=false:attack=1:release=60,aresample={OUTPUT_RATE}"
    )


def measure_loudness(path: Path) -> Loudness:
    """Integrated loudness (LUFS) and true peak (dBTP) of an audio or video file."""
    report = _loudnorm_report(
        run_ffmpeg(["-i", str(path), "-af", "loudnorm=print_format=json", "-f", "null", "-"])
    )
    return Loudness(float(report["input_i"]), float(report["input_tp"]))


def _loudnorm_report(stderr: str) -> dict[str, str]:
    # loudnorm prints its JSON report as the last {...} block on stderr.
    blocks = re.findall(r"\{[^{}]*\}", stderr)
    if not blocks:
        raise FactoryError("ffmpeg loudnorm printed no report")
    return json.loads(blocks[-1])
