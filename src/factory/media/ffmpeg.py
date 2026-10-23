"""Thin wrappers around the system `ffmpeg` and `ffprobe` programs (installed outside uv)."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from factory.errors import FactoryError

# Which binaries to run. The CLI sets them once at start-up from settings.yaml `tools`
# (use_tools); plain names are the fallback for library use outside the CLI.
_BINARIES = {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"}
# What the pipeline needs from FFmpeg: libass to burn subtitles, H.264 video, AAC audio.
REQUIRED_FILTERS = ("ass", "loudnorm", "alimiter")
REQUIRED_ENCODERS = ("libx264", "aac")


def use_tools(ffmpeg: str, ffprobe: str) -> None:
    """Point every FFmpeg/ffprobe call at these binaries (from settings.yaml `tools`)."""
    _BINARIES["ffmpeg"], _BINARIES["ffprobe"] = ffmpeg, ffprobe


def check_tools() -> list[str]:
    """Problems with the configured FFmpeg (missing binary, filter or encoder); [] = all good."""
    try:
        filters = _run_text([_BINARIES["ffmpeg"], "-hide_banner", "-filters"])
        encoders = _run_text([_BINARIES["ffmpeg"], "-hide_banner", "-encoders"])
        _run_text([_BINARIES["ffprobe"], "-version"])
    except (OSError, subprocess.CalledProcessError) as exc:
        return [f"cannot run {exc.filename or 'ffmpeg/ffprobe'}: {exc}"]
    problems = [
        f"{_BINARIES['ffmpeg']} has no '{name}' filter"
        for name in REQUIRED_FILTERS
        if f" {name} " not in filters
    ]
    problems += [
        f"{_BINARIES['ffmpeg']} has no '{name}' encoder"
        for name in REQUIRED_ENCODERS
        if f" {name} " not in encoders
    ]
    return problems


def _run_text(cmd: list[str]) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def encode_rgb_frames(
    frames: Iterable[bytes],
    *,
    width: int,
    height: int,
    fps: int,
    duration_s: float,
    out_path: Path,
) -> None:
    """Encode raw RGB frames into an H.264 MP4 with a silent stereo AAC track.

    Frames are streamed to ffmpeg's stdin, so no temporary image files are written. The silent
    audio track lets this clip be joined to clips that have sound without re-mapping streams.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Beginner note: two inputs. Input 0 is our raw pixels from stdin ("-i -"); input 1 is
    # FFmpeg's built-in silent audio generator (anullsrc). -t cuts the output to duration_s.
    cmd = [
        _BINARIES["ffmpeg"], "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(fps),
        "-i", "-",
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-t", f"{duration_s}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(out_path),
    ]  # fmt: skip
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError as exc:
        raise FactoryError("ffmpeg is not installed or not on PATH") from exc
    try:
        for frame in frames:
            proc.stdin.write(frame)
        proc.stdin.close()
    except BrokenPipeError:
        pass  # ffmpeg exited early; its error message is read below
    stderr = proc.stderr.read().decode(errors="replace")
    if proc.wait() != 0:
        raise FactoryError(f"ffmpeg failed for {out_path.name}: {stderr.strip()[-500:]}")


def run_ffmpeg(args: list[str], *, input_bytes: bytes | None = None) -> str:
    """Run `ffmpeg <args>` (quiet, overwrite) and return its stderr, where filters print reports.

    `input_bytes` is fed to stdin, for inputs given as "-i -".
    """
    cmd = [_BINARIES["ffmpeg"], "-hide_banner", "-nostats", "-y", *args]
    try:
        result = subprocess.run(cmd, input=input_bytes, capture_output=True, check=True)
    except FileNotFoundError as exc:
        raise FactoryError("ffmpeg is not installed or not on PATH") from exc
    except subprocess.CalledProcessError as exc:
        tail = exc.stderr.decode(errors="replace").strip()[-500:]
        raise FactoryError(f"ffmpeg failed: {tail}") from exc
    return result.stderr.decode(errors="replace")


def probe(path: Path) -> dict[str, Any]:
    """Return ffprobe's JSON description (format + streams) of a media file."""
    cmd = [
        _BINARIES["ffprobe"], "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]  # fmt: skip
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise FactoryError("ffprobe is not installed or not on PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise FactoryError(f"ffprobe failed for {path.name}: {exc.stderr.strip()}") from exc
    return json.loads(result.stdout)
