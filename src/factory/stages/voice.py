"""Voiceover: the voice audition (OQ-7), voice.wav and its word-highlight subtitles.

render() writes voice.wav (ROADMAP 1.4), voice_words.json and subs.ass (1.7) into the item's
asset folder; the generate stage calls it for script_approved items.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from factory.context import AppContext
from factory.errors import FactoryError
from factory.integrations.kokoro_tts import SAMPLE_RATE, Speech
from factory.media import subtitles
from factory.media.audio import Loudness, normalize_to_wav
from factory.media.ffmpeg import probe

log = logging.getLogger("factory.voice")

# text, voice_id, speed -> Speech (audio + token timings): KokoroTTS().synthesize or a fake
Synthesizer = Callable[[str, str, float], Speech]
WORDS_FILE = "voice_words.json"
SUBS_FILE = "subs.ass"

AUDITION_VOICES = ("am_michael", "am_adam", "am_onyx")  # DESIGN_SYSTEM.md §2 candidates
AUDITION_TEXT = (
    "Why doesn't your car battery go flat while you drive? Because a belt from the engine spins "
    "a small generator called the alternator. Inside, a spinning magnet sweeps past copper coils "
    "and pushes current through them. A set of diodes turns that current into one-way flow the "
    "battery can store. Here's the surprising part: how much power such a small part makes "
    "from a rubber belt."
)


@dataclass(frozen=True)
class VoiceFile:
    path: Path
    voice_id: str
    duration_s: float
    loudness: Loudness
    speech: Speech  # kept so render() can time the subtitles


def audition(
    ctx: AppContext, tts: Synthesizer, *, voices: tuple[str, ...], text: str
) -> list[VoiceFile]:
    """Render the same text with each voice into data/audition/voice_<id>.wav."""
    out_dir = ctx.root / ctx.settings.paths.data_dir / "audition"
    speed = ctx.channel.voice.speed
    return [_render(tts, text, voice, speed, out_dir / f"voice_{voice}.wav") for voice in voices]


def render(ctx: AppContext, content_id: int, tts: Synthesizer) -> VoiceFile:
    """Render the current script of one item: voice.wav, voice_words.json and subs.ass.

    Re-running replaces the files and their assets rows, so they always match the latest script.
    """
    script = ctx.conn.execute(
        """
        SELECT s.body_text, c.format FROM scripts s JOIN contents c ON c.id = s.content_id
        WHERE s.content_id = ? AND s.is_current = 1
        """,
        (content_id,),
    ).fetchone()
    if script is None:
        raise FactoryError(f"content #{content_id} has no script yet: run `factory script`")
    voice = ctx.channel.voice
    folder = asset_dir(ctx, content_id)
    result = _render(tts, script["body_text"], voice.voice_id, voice.speed, folder / "voice.wav")
    _record_asset(ctx, content_id, result)
    if not result.speech.tokens:
        raise FactoryError("the voice engine returned no word timings, so no subtitles")
    words = subtitles.align_words(script["body_text"], result.speech.tokens)
    (folder / WORDS_FILE).write_text(
        json.dumps([w.__dict__ for w in words], indent=1, ensure_ascii=False)
    )
    aspect = getattr(ctx.channel.formats, script["format"]).aspect
    subs = subtitles.write_ass(words, ctx.channel, subtitle_font(ctx), folder / SUBS_FILE, aspect)
    _record_subtitles(ctx, content_id, subs, result.duration_s)
    return result


def subtitle_font(ctx: AppContext) -> Path:
    """The subtitle font file named in the channel YAML (brand.fonts.subtitle)."""
    path = fonts_dir(ctx) / f"{ctx.channel.brand.fonts.subtitle}.ttf"
    if not path.is_file():
        raise FactoryError(f"subtitle font missing: {path}")
    return path


def fonts_dir(ctx: AppContext) -> Path:
    return ctx.root / ctx.settings.paths.assets_static_dir / "fonts"


def asset_dir(ctx: AppContext, content_id: int) -> Path:
    """data/assets/<channel slug>/<id as 6 digits>/ (DATABASE.md §5)."""
    return ctx.root / ctx.settings.paths.assets_dir / ctx.channel.slug / f"{content_id:06d}"


def _render(tts: Synthesizer, text: str, voice_id: str, speed: float, out: Path) -> VoiceFile:
    speech = tts(text, voice_id, speed)
    loudness = normalize_to_wav(speech.pcm, input_rate=SAMPLE_RATE, out_path=out)
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
    return VoiceFile(out, voice_id, duration, loudness, speech)


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


def _record_subtitles(ctx: AppContext, content_id: int, path: Path, duration_s: float) -> None:
    rel_path = path.relative_to(ctx.root).as_posix()
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    with ctx.conn:
        ctx.conn.execute(
            "DELETE FROM assets WHERE content_id = ? AND kind = 'subtitles'", (content_id,)
        )
        ctx.conn.execute(
            """
            INSERT INTO assets (content_id, kind, path, sha256, generator, license, duration_s)
            VALUES (?, 'subtitles', ?, ?, 'factory', 'own_work', ?)
            """,
            (content_id, rel_path, sha, round(duration_s, 2)),
        )
