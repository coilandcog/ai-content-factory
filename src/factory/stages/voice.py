"""Voiceover: the voice audition (OQ-7) and voice.wav for a script (ROADMAP 1.4).

The generate stage (1.6) will call render() for script_approved items; for now it is a command.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from factory.context import AppContext
from factory.errors import FactoryError
from factory.integrations.kokoro_tts import SAMPLE_RATE
from factory.media.audio import Loudness, normalize_to_wav
from factory.media.ffmpeg import probe

log = logging.getLogger("factory.voice")

# text, voice_id, speed -> mono float32 samples at SAMPLE_RATE (KokoroTTS().synthesize or a fake)
Synthesizer = Callable[[str, str, float], bytes]

AUDITION_VOICES = ("am_michael", "am_adam", "am_onyx")  # DESIGN_SYSTEM.md §2 candidates
AUDITION_TEXT = (
    "Why doesn't your car battery go flat while you drive? Because a belt from the engine spins "
    "a small generator called the alternator. Inside, a spinning magnet sweeps past copper coils "
    "and pushes current through them. A set of diodes turns that current into one-way flow the "
    "battery can store. As an engineer, what surprises me is how much power such a small part "
    "makes from a rubber belt."
)


@dataclass(frozen=True)
class VoiceFile:
    path: Path
    voice_id: str
    duration_s: float
    loudness: Loudness


def audition(
    ctx: AppContext, tts: Synthesizer, *, voices: tuple[str, ...], text: str
) -> list[VoiceFile]:
    """Render the same text with each voice into data/audition/voice_<id>.wav."""
    out_dir = ctx.root / ctx.settings.paths.data_dir / "audition"
    speed = ctx.channel.voice.speed
    return [_render(tts, text, voice, speed, out_dir / f"voice_{voice}.wav") for voice in voices]


def render(ctx: AppContext, content_id: int, tts: Synthesizer) -> VoiceFile:
    """Render the current script of one item into its asset folder as voice.wav.

    Re-running replaces the file and its assets row, so the result is always the latest script.
    """
    script = ctx.conn.execute(
        "SELECT body_text FROM scripts WHERE content_id = ? AND is_current = 1", (content_id,)
    ).fetchone()
    if script is None:
        raise FactoryError(f"content #{content_id} has no script yet: run `factory script`")
    voice = ctx.channel.voice
    out = asset_dir(ctx, content_id) / "voice.wav"
    result = _render(tts, script["body_text"], voice.voice_id, voice.speed, out)
    _record_asset(ctx, content_id, result)
    return result


def asset_dir(ctx: AppContext, content_id: int) -> Path:
    """data/assets/<channel slug>/<id as 6 digits>/ (DATABASE.md §5)."""
    return ctx.root / ctx.settings.paths.assets_dir / ctx.channel.slug / f"{content_id:06d}"


def _render(tts: Synthesizer, text: str, voice_id: str, speed: float, out: Path) -> VoiceFile:
    pcm = tts(text, voice_id, speed)
    loudness = normalize_to_wav(pcm, input_rate=SAMPLE_RATE, out_path=out)
    duration = float(probe(out)["format"]["duration"])
    log.info(
        "%s: %s, %.1f s, %.1f LUFS, %.1f dBTP",
        out.name,
        voice_id,
        duration,
        loudness.integrated_lufs,
        loudness.true_peak_dbtp,
        extra={"event": "voice_rendered"},
    )
    return VoiceFile(out, voice_id, duration, loudness)


def _record_asset(ctx: AppContext, content_id: int, voice: VoiceFile) -> None:
    rel_path = voice.path.relative_to(ctx.root).as_posix()
    sha = hashlib.sha256(voice.path.read_bytes()).hexdigest()
    with ctx.conn:
        ctx.conn.execute(
            "DELETE FROM assets WHERE content_id = ? AND kind = 'voiceover'", (content_id,)
        )
        ctx.conn.execute(
            """
            INSERT INTO assets (content_id, kind, path, sha256, generator, model, license,
                                license_url, duration_s)
            VALUES (?, 'voiceover', ?, ?, 'kokoro', ?, 'apache-2.0',
                    'https://huggingface.co/hexgrad/Kokoro-82M', ?)
            """,
            (content_id, rel_path, sha, voice.voice_id, round(voice.duration_s, 2)),
        )
