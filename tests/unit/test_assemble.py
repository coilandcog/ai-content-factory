from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fakes.fake_gemini import FakeGenaiClient, fixture_reply
from fakes.fake_tts import make_fake_tts
from PIL import Image

from factory.config import REPO_ROOT, EnvSettings, load_channel, load_settings
from factory.context import AppContext
from factory.db import add_content, connect, migrate, set_status, upsert_channel
from factory.errors import FactoryError
from factory.integrations.gemini import GeminiClient
from factory.media.audio import measure_loudness
from factory.media.compose import MAX_SLOWDOWN, clip_fit, slots
from factory.media.ffmpeg import probe, run_ffmpeg
from factory.music import add_track, list_tracks, pick_track
from factory.stages import assemble, generate

STORY = {"style": "product", "start_picture": "the machine at rest", "continues": False}

SHOTS = [
    {"n": 1, "beat": "studio", "kind": "flow_clip", "description": "Pedestal", "duration_s": 3,
     "prompt_vars": {"hero_look": "A car alternator", "scene": "It turns", "camera": "orbit",
                     **STORY, "end_frame": "the pulley"}},
    {"n": 2, "beat": "cutaway", "kind": "diagram", "description": "Labels", "duration_s": 2,
     "prompt_vars": {}, "labels": ["Rotor", "Stator"], "energy": "electric"},
    {"n": 3, "beat": "cutaway", "kind": "flow_clip", "description": "Casing opens",
     "duration_s": 2, "prompt_vars": {"hero_look": "A car alternator", "scene": "It opens",
                                       "camera": "dolly-in", **STORY, "end_frame": "the rotor"}},
    {"n": 4, "beat": "studio", "kind": "still", "description": "Hero view", "duration_s": 1,
     "prompt_vars": {}},
    {"n": 5, "beat": "studio", "kind": "diagram", "description": "Pulley", "duration_s": 1,
     "prompt_vars": {}, "labels": ["Pulley"], "energy": "heat"},
]  # fmt: skip
VOICE_S = 4.0
SFX_HZ, MUSIC_HZ = 600, 1000  # the clip's "Veo sound" and the "music track" are test tones


# --- timeline maths ----------------------------------------------------------------------------


def test_slots_fill_the_narration_in_whole_frames() -> None:
    result = slots([6, 7, 4, 8, 6], 45.6)

    assert sum(result) == pytest.approx(45.6, abs=1 / 30)
    assert all(round(s * 30, 6).is_integer() for s in result)
    assert result[3] > result[0] > result[2]  # proportions kept


def test_clip_fit_slows_down_a_little_then_holds() -> None:
    assert clip_fit(8.0, 6.0) == (1.0, 0.0)  # long enough: cut
    assert clip_fit(8.0, 9.0) == pytest.approx((9 / 8, 0.0))  # 12.5% slower
    factor, hold = clip_fit(4.0, 8.0)
    assert factor == MAX_SLOWDOWN and hold == pytest.approx(3.0)  # 4 s x 1.25 = 5 s, hold 3 s


# --- the stage ---------------------------------------------------------------------------------


def make_clip(path: Path, *, with_sound: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sound = ["-f", "lavfi", "-i", f"sine=f={SFX_HZ}:d=3.5", "-c:a", "aac"] if with_sound else []
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "testsrc=size=180x320:rate=24:duration=3.5",
         *sound, "-pix_fmt", "yuv420p", "-shortest", str(path)],
        check=True,
    )  # fmt: skip


@pytest.fixture
def ctx(tmp_path: Path) -> AppContext:
    conn = connect(tmp_path / "test.db")
    migrate(conn)
    channel = load_channel("coil-and-cog").model_copy(update={"video_style": "flow"})  # Flow clips
    # Test renders are 5 s long (real-length ones would make the tests slow), so this test channel
    # accepts 2-10 s Shorts; every other QA rule is the real one.
    short = channel.formats.short.model_copy(update={"min_s": 2, "max_s": 10})
    channel = channel.model_copy(
        update={"formats": channel.formats.model_copy(update={"short": short})}
    )
    upsert_channel(conn, channel)
    for part in ("fonts", "logo"):  # fonts for subtitles and cards, the logo sting
        shutil.copytree(REPO_ROOT / "assets_static" / part, tmp_path / "assets_static" / part)
    shutil.copytree(REPO_ROOT / "prompts", tmp_path / "prompts")  # generate writes the Flow sheet
    env = EnvSettings(dry_run=True, factory_channel="coil-and-cog")
    yield AppContext(load_settings(), env, channel, conn, tmp_path)
    conn.close()


def generated_item(ctx: AppContext) -> int:
    """An item taken through script approval and both generate passes, with test clips."""
    content_id = add_content(ctx.conn, ctx.channel, object_name="alternator", fmt="short",
                             pillar="vehicles")  # fmt: skip
    with ctx.conn:
        ctx.conn.execute(
            "INSERT INTO scripts (content_id, version, body_text, est_duration_s, shot_list_json) "
            "VALUES (?, 1, 'The alternator charges your battery while you drive.', 4, ?)",
            (content_id, json.dumps(SHOTS)),
        )
        for url in ("https://en.wikipedia.org/wiki/Alternator", "https://example.edu/alternators"):
            ctx.conn.execute(
                "INSERT INTO sources (content_id, url, title, publisher) VALUES (?, ?, 'T', 'P')",
                (content_id, url),
            )
    set_status(ctx.conn, content_id, "scripted")
    set_status(ctx.conn, content_id, "script_approved")
    tts = make_fake_tts(seconds=VOICE_S, amplitude=0.05)
    generate.run(ctx, tts=lambda: tts)
    inbox = generate.inbox_dir(ctx, content_id)
    make_clip(inbox / "shot_01.mp4", with_sound=False)
    make_clip(inbox / "shot_03.mp4", with_sound=True)
    generate.run(ctx, tts=lambda: tts)
    assert ctx.conn.execute("SELECT status FROM contents").fetchone()[0] == "generated"
    return content_id


def fake_llm() -> GeminiClient:
    """Gemini stand-in that answers the metadata request with a canned, valid answer."""
    return GeminiClient("fake", "gemini-3.5-flash-lite", sdk=FakeGenaiClient(
        fixture_reply("metadata_alternator")), retry_wait_s=0)  # fmt: skip


def add_music(ctx: AppContext, title: str = "Calm Gears") -> None:
    track = ctx.root / f"{title}.mp3"
    run_ffmpeg(["-f", "lavfi", "-i", f"sine=f={MUSIC_HZ}:d=3", str(track)])  # shorter: it loops
    add_track(ctx, track, title=title, artist="Test Artist")


def band_level_db(path: Path, hz: int) -> float:
    """Mean loudness (dB) of a narrow frequency band of a file's audio."""
    report = run_ffmpeg(["-i", str(path), "-af", f"bandpass=f={hz}:width_type=q:w=10,volumedetect",
                         "-f", "null", "-"])  # fmt: skip
    return float(re.search(r"mean_volume: (-?[\d.]+) dB", report).group(1))


@pytest.mark.slow
def test_both_renders_and_preview_are_made_and_tiktok_has_no_music(ctx: AppContext) -> None:
    content_id = generated_item(ctx)
    add_music(ctx)

    result = assemble.run(ctx, llm=fake_llm())

    assert (result.processed, result.failed) == (1, 0)
    folder = generate.asset_dir(ctx, content_id)
    youtube, tiktok = folder / "render_youtube.mp4", folder / "render_tiktok.mp4"
    for render in (youtube, tiktok):
        info = probe(render)
        video = next(s for s in info["streams"] if s["codec_type"] == "video")
        audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
        assert (video["width"], video["height"], video["r_frame_rate"]) == (1080, 1920, "30/1")
        assert (audio["codec_name"], audio["sample_rate"]) == ("aac", "48000")
        assert float(info["format"]["duration"]) == pytest.approx(VOICE_S + 1.0, abs=0.15)
        assert measure_loudness(render).integrated_lufs == pytest.approx(-14, abs=1)
    # Music only on YouTube; the clip's own sound in both (D-042, D-068). TikTok's level in the
    # music band is just the filter's floor (voice spill, AAC noise); the music sits well above it.
    assert band_level_db(youtube, MUSIC_HZ) - band_level_db(tiktok, MUSIC_HZ) > 8
    assert band_level_db(tiktok, SFX_HZ) > -60
    preview = probe(folder / "preview_720p.mp4")
    assert next(s for s in preview["streams"] if s["codec_type"] == "video")["width"] == 720
    kinds = {r[0] for r in ctx.conn.execute("SELECT kind FROM assets WHERE content_id = ?",
                                              (content_id,))}  # fmt: skip
    assert {"render_youtube", "render_tiktok", "preview", "music"} <= kinds
    assert (folder / "music.txt").read_text().startswith("1\tCalm Gears")
    status = ctx.conn.execute("SELECT status FROM contents").fetchone()[0]
    assert status == "assembled"  # every QA check passed
    report = json.loads((folder / "qa_report.json").read_text())
    assert {r["check"] for r in report if r["ok"] is not True} == {"similarity"}
    meta = json.loads((folder / "metadata.json").read_text())
    assert meta["youtube"]["title"].startswith("Alternator in 40 seconds")
    cover = Image.open(folder / "cover.jpg")
    assert cover.size == (1080, 1920)
    [cover_row] = ctx.conn.execute("SELECT shot_n FROM assets WHERE kind = 'cover'").fetchall()
    assert cover_row[0] == 3  # the Cutaway clip
    assert assemble.run(ctx).processed == 0  # nothing left in `generated`


@pytest.mark.slow
def test_blueprint_film_is_the_whole_picture(
    ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from factory import blueprint
    from factory.blueprint.canvas import LINE, Scene

    def draw(c, t, st, t0, t1):
        c.text((540, 400), "a quiet test drawing", "label", LINE)

    roles = ["hook", "body", "body", "body", "note", "close"]
    machine = SimpleNamespace(
        MACHINE="alternator", FACTS={}, LIVE=(), step=lambda *a: None,
        new_state=lambda *a: {}, SCENES=[Scene(r, draw) for r in roles],
    )  # fmt: skip
    monkeypatch.setitem(blueprint.MODULES, "alternator", machine)
    real = generate.render_film
    monkeypatch.setattr(generate, "render_film", lambda *a, **k: real(*a, **k, ss=0.25, fps=10))
    content_id = add_content(ctx.conn, ctx.channel, object_name="alternator", fmt="short",
                             pillar="vehicles", style="blueprint")  # fmt: skip
    narration = ("The alternator charges your battery. A belt turns it. Magnets spin. Coils catch "
                 "it. As an engineer, I like it. It never stops.")  # fmt: skip
    with ctx.conn:
        ctx.conn.execute(
            "INSERT INTO scripts (content_id, version, body_text, engineer_note, est_duration_s, "
            "shot_list_json) VALUES (?, 1, ?, 'As an engineer, I like it.', 4, ?)",
            (content_id, narration, json.dumps(SHOTS)),
        )
        for url in ("https://en.wikipedia.org/wiki/Alternator", "https://example.edu/alternators"):
            ctx.conn.execute(
                "INSERT INTO sources (content_id, url, title, publisher) VALUES (?, ?, 'T', 'P')",
                (content_id, url),
            )
    set_status(ctx.conn, content_id, "scripted")
    set_status(ctx.conn, content_id, "script_approved")
    tts = make_fake_tts(seconds=VOICE_S, amplitude=0.05)
    generate.run(ctx, tts=lambda: tts)
    add_music(ctx)

    result = assemble.run(ctx, llm=fake_llm())

    assert (result.processed, result.failed) == (1, 0)
    folder = generate.asset_dir(ctx, content_id)
    info = probe(folder / "render_youtube.mp4")
    assert float(info["format"]["duration"]) == pytest.approx(VOICE_S + 1.0, abs=0.15)
    status = ctx.conn.execute("SELECT status FROM contents").fetchone()[0]
    assert status == "assembled"  # no diagram cards needed: the film draws its own
    report = json.loads((folder / "qa_report.json").read_text())
    assert {r["check"] for r in report if r["ok"] is not True} == {"similarity"}
    [cover] = ctx.conn.execute("SELECT model, license FROM assets WHERE kind = 'cover'").fetchall()
    assert tuple(cover) == ("frame of blueprint", "own_work")


@pytest.mark.slow
def test_broken_item_stays_generated_with_the_reason(ctx: AppContext) -> None:
    content_id = generated_item(ctx)
    add_music(ctx)
    row = ctx.conn.execute("SELECT * FROM contents WHERE id = ?", (content_id,)).fetchone()
    assemble._assemble(ctx, row)  # render only
    (generate.asset_dir(ctx, content_id) / "subs.ass").unlink()  # ROADMAP 1.10: break something

    result = assemble.run(ctx, llm=fake_llm())  # renders exist, so this runs QA only

    status, reason = ctx.conn.execute("SELECT status, status_reason FROM contents").fetchone()
    assert (result.processed, result.skipped, result.failed) == (0, 1, 0)
    assert status == "generated"
    assert reason.startswith("QA failed: subtitles: subs.ass missing")


@pytest.mark.slow
def test_empty_music_library_stops_before_any_work(ctx: AppContext) -> None:
    content_id = generated_item(ctx)

    with pytest.raises(FactoryError, match="no music in the library"):
        assemble.run(ctx)

    assert not (generate.asset_dir(ctx, content_id) / "render_tiktok.mp4").exists()


def test_least_used_track_is_picked_and_bad_files_are_refused(ctx: AppContext) -> None:
    add_music(ctx, "First")
    add_music(ctx, "Second")
    with ctx.conn:
        ctx.conn.execute(
            "INSERT INTO contents (channel_id, format, object_name) VALUES (1, 'short', 'x')"
        )
        ctx.conn.execute(
            "INSERT INTO assets (content_id, kind, path, music_track_id) "
            "VALUES (1, 'music', 'm', 1)"
        )

    assert pick_track(ctx).title == "Second"
    assert [uses for _, uses in list_tracks(ctx)] == [1, 0]
    not_audio = ctx.root / "notes.txt"
    not_audio.write_text("hello")
    with pytest.raises(FactoryError, match="expected one of"):
        add_track(ctx, not_audio, title="Notes")
    with pytest.raises(FactoryError, match="file not found"):
        add_track(ctx, ctx.root / "missing.mp3", title="Missing")
