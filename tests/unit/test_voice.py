from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fakes.fake_tts import make_fake_tts

from factory.config import REPO_ROOT, EnvSettings, load_channel, load_settings
from factory.context import AppContext
from factory.db import add_content, connect, migrate, upsert_channel
from factory.errors import FactoryError
from factory.integrations.kokoro_tts import Speech
from factory.media.audio import measure_loudness
from factory.media.ffmpeg import probe
from factory.stages import voice

pytestmark = pytest.mark.slow  # runs ffmpeg


fake_tts = make_fake_tts(seconds=2.5, amplitude=0.02)


@pytest.fixture
def ctx(tmp_path: Path) -> AppContext:
    conn = connect(tmp_path / "test.db")
    migrate(conn)
    channel = load_channel("coil-and-cog")
    upsert_channel(conn, channel)
    env = EnvSettings(dry_run=True, factory_channel="coil-and-cog")
    # outputs go under tmp_path; subtitles need the real font files there too
    shutil.copytree(REPO_ROOT / "assets_static" / "fonts", tmp_path / "assets_static" / "fonts")
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

    def recording_tts(text: str, voice_id: str, speed: float) -> Speech:
        spoken.append((text, voice_id, speed))
        return fake_tts(text, voice_id, speed)

    voice.render(ctx, content_id, recording_tts)
    voice.render(ctx, content_id, recording_tts)  # re-run replaces, never duplicates

    path = ctx.root / "data" / "assets" / "coil-and-cog" / f"{content_id:06d}" / "voice.wav"
    assert path.is_file()
    assert spoken[0] == ("Hello there.", "am_michael", 1.0)  # voice and speed from the YAML
    rows = ctx.conn.execute(
        "SELECT * FROM assets WHERE content_id = ? ORDER BY kind", (content_id,)
    ).fetchall()
    assert [r["kind"] for r in rows] == ["subtitles", "voiceover"]  # one each, even after re-run
    assert (path.parent / "subs.ass").is_file()
    words = json.loads((path.parent / "voice_words.json").read_text())
    assert [w["text"] for w in words] == ["Hello", "there."]
    row = rows[1]
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
