from __future__ import annotations

import math
from array import array
from pathlib import Path

import pytest

from factory.config import EnvSettings, load_channel, load_settings
from factory.context import AppContext
from factory.db import add_content, connect, migrate, upsert_channel
from factory.errors import FactoryError
from factory.integrations.kokoro_tts import SAMPLE_RATE
from factory.media.audio import measure_loudness
from factory.media.ffmpeg import probe
from factory.stages import voice

pytestmark = pytest.mark.slow  # runs ffmpeg


def fake_tts(text: str, voice_id: str, speed: float) -> bytes:
    """A quiet 220 Hz tone (about -30 LUFS), 2.5 s long: normalisation has real work to do."""
    samples = array(
        "f",
        (
            0.02 * math.sin(2 * math.pi * 220 * i / SAMPLE_RATE)
            for i in range(int(2.5 * SAMPLE_RATE))
        ),
    )
    return samples.tobytes()  # x86 is little-endian, matching "f32le"


@pytest.fixture
def ctx(tmp_path: Path) -> AppContext:
    conn = connect(tmp_path / "test.db")
    migrate(conn)
    channel = load_channel("coil-and-cog")
    upsert_channel(conn, channel)
    env = EnvSettings(dry_run=True, factory_channel="coil-and-cog")
    yield AppContext(load_settings(), env, channel, conn, tmp_path)  # outputs go under tmp_path
    conn.close()


def add_script(ctx: AppContext) -> int:
    content_id = add_content(
        ctx.conn, ctx.channel, object_name="alternator", fmt="short", pillar="machines"
    )
    with ctx.conn:
        ctx.conn.execute(
            "INSERT INTO scripts (content_id, version, body_text) VALUES (?, 1, 'Hello there.')",
            (content_id,),
        )
    return content_id


def test_audio_is_48k_mono_at_minus_14_lufs(ctx: AppContext) -> None:
    [result] = voice.audition(ctx, fake_tts, voices=("am_michael",), text="x")

    stream = probe(result.path)["streams"][0]
    assert (stream["codec_name"], int(stream["sample_rate"]), stream["channels"]) == (
        "pcm_s16le",
        48000,
        1,
    )
    assert result.loudness.integrated_lufs == pytest.approx(-14.0, abs=1.0)
    assert result.loudness.true_peak_dbtp <= -0.9
    assert result.duration_s == pytest.approx(2.5, abs=0.1)
    assert measure_loudness(result.path) == result.loudness


def test_audition_writes_one_file_per_voice(ctx: AppContext) -> None:
    files = voice.audition(ctx, fake_tts, voices=voice.AUDITION_VOICES, text=voice.AUDITION_TEXT)

    names = sorted(p.name for p in (ctx.root / "data" / "audition").iterdir())
    assert names == ["voice_am_adam.wav", "voice_am_michael.wav", "voice_am_onyx.wav"]
    assert [f.voice_id for f in files] == list(voice.AUDITION_VOICES)


def test_render_writes_voice_wav_and_one_asset_row(ctx: AppContext) -> None:
    content_id = add_script(ctx)
    spoken: list[tuple[str, str, float]] = []

    def recording_tts(text: str, voice_id: str, speed: float) -> bytes:
        spoken.append((text, voice_id, speed))
        return fake_tts(text, voice_id, speed)

    voice.render(ctx, content_id, recording_tts)
    voice.render(ctx, content_id, recording_tts)  # re-run replaces, never duplicates

    path = ctx.root / "data" / "assets" / "coil-and-cog" / f"{content_id:06d}" / "voice.wav"
    assert path.is_file()
    assert spoken[0] == ("Hello there.", "am_michael", 1.0)  # voice and speed from the YAML
    rows = ctx.conn.execute("SELECT * FROM assets WHERE content_id = ?", (content_id,)).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert (row["kind"], row["generator"], row["model"], row["license"]) == (
        "voiceover",
        "kokoro",
        "am_michael",
        "apache-2.0",
    )
    assert row["path"] == f"data/assets/coil-and-cog/{content_id:06d}/voice.wav"
    assert row["duration_s"] == pytest.approx(2.5, abs=0.1)
    assert len(row["sha256"]) == 64


def test_render_needs_a_script(ctx: AppContext) -> None:
    content_id = add_content(
        ctx.conn, ctx.channel, object_name="zipper", fmt="short", pillar="everyday_things"
    )

    with pytest.raises(FactoryError, match="has no script yet"):
        voice.render(ctx, content_id, fake_tts)
