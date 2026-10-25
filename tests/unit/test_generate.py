from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fakes.fake_tts import make_fake_tts
from PIL import Image
from typer.testing import CliRunner

from factory.cli import app
from factory.config import REPO_ROOT, EnvSettings, load_channel, load_settings
from factory.context import AppContext
from factory.db import add_content, connect, migrate, set_status, upsert_channel
from factory.errors import BudgetExceededError
from factory.media.ffmpeg import probe
from factory.stages import generate

pytestmark = pytest.mark.slow  # runs ffmpeg

SHOTS = [
    {"n": 1, "beat": "studio", "kind": "flow_clip", "description": "Alternator on the pedestal",
     "duration_s": 6, "prompt_vars": {"hero_feature": "the pulley and cooling fins"}},
    {"n": 2, "beat": "cutaway", "kind": "flow_clip", "description": "Casing opens",
     "duration_s": 7, "prompt_vars": {"internals": "rotor and stator", "focus_part": "the rotor"}},
    {"n": 3, "beat": "cutaway", "kind": "diagram", "description": "Labels", "duration_s": 4,
     "prompt_vars": {}, "labels": ["rotor", "stator coils"], "energy": "electric"},
    {"n": 4, "beat": "flow", "kind": "flow_clip", "description": "Current flows",
     "duration_s": 8, "prompt_vars": {"energy": "electric", "path": "the stator coils",
                                       "process": "AC becoming DC"}},
    {"n": 5, "beat": "in_situ", "kind": "flow_clip", "description": "Engine bay",
     "duration_s": 6, "prompt_vars": {"context": "a car engine bay"}},
    {"n": 6, "beat": "studio", "kind": "still", "description": "Hero view of the alternator",
     "duration_s": 2, "prompt_vars": {}},
]  # fmt: skip
CLIPS = [1, 2, 4, 5]


fake_tts = make_fake_tts(seconds=1.5, amplitude=0.1)


def make_clip(path: Path, size: str = "108x192", seconds: float = 3.5) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", f"testsrc=size={size}:rate=10:duration={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )  # fmt: skip


@pytest.fixture
def ctx(tmp_path: Path) -> AppContext:
    shutil.copytree(REPO_ROOT / "prompts", tmp_path / "prompts")
    conn = connect(tmp_path / "test.db")
    migrate(conn)
    channel = load_channel("coil-and-cog")
    upsert_channel(conn, channel)
    env = EnvSettings(dry_run=True, factory_channel="coil-and-cog")
    # outputs go under tmp_path; subtitles need the real font files there too
    shutil.copytree(REPO_ROOT / "assets_static" / "fonts", tmp_path / "assets_static" / "fonts")
    yield AppContext(load_settings(), env, channel, conn, tmp_path)
    conn.close()


def approved_item(ctx: AppContext) -> int:
    content_id = add_content(
        ctx.conn, ctx.channel, object_name="alternator", fmt="short", pillar="vehicles"
    )
    with ctx.conn:
        ctx.conn.execute(
            "INSERT INTO scripts (content_id, version, body_text, est_duration_s, shot_list_json) "
            "VALUES (?, 1, 'Hello there, this is the alternator.', 40, ?)",
            (content_id, json.dumps(SHOTS)),
        )
        ctx.conn.execute(
            "UPDATE contents SET working_title = 'Alternator in 40 seconds' WHERE id = ?",
            (content_id,),
        )
    set_status(ctx.conn, content_id, "scripted")
    set_status(ctx.conn, content_id, "script_approved")
    return content_id


def run(ctx: AppContext) -> generate.StageResult:
    return generate.run(ctx, tts=lambda: fake_tts)


def status(ctx: AppContext, content_id: int) -> str:
    return ctx.conn.execute("SELECT status FROM contents WHERE id = ?", (content_id,)).fetchone()[0]


def assets(ctx: AppContext, kind: str) -> list:
    return ctx.conn.execute(
        "SELECT * FROM assets WHERE kind = ? ORDER BY shot_n", (kind,)
    ).fetchall()


def test_approved_script_gets_flow_sheet_inbox_and_voice(ctx: AppContext) -> None:
    content_id = approved_item(ctx)

    result = run(ctx)

    assert (result.processed, status(ctx, content_id)) == (1, "awaiting_clips")
    folder = ctx.root / "data" / "assets" / "coil-and-cog" / f"{content_id:06d}"
    sheet = (folder / "flow_sheet.md").read_text()
    assert "4 × Veo 3.1 - Fast, **80 credits**" in sheet
    assert "you have 50 of 50 left today" in sheet
    assert "make 2 clip(s) today and the rest on the next day(s)" in sheet
    assert "close-up of the alternator rotating steadily on a matte dark pedestal" in sheet
    assert "Save as: `shot_01.mp4`" in sheet and "Save as: `shot_03.mp4`" not in sheet  # diagram
    assert "glowing cyan energy surges through the stator coils" in sheet
    assert "of the alternator, vertical 9:16, with three smooth camera angles" in sheet
    assert "No logos, no text" in sheet and "no extra or distorted components" in sheet
    assert "\\\\wsl$\\" in sheet and "\\data\\inbox\\flow\\000001" in sheet
    prompts = json.loads((folder / "flow_prompts.json").read_text())
    assert "pedestal" in prompts["1"] and "pedestal" not in prompts["5"]  # in situ: no studio set
    assert "installed in a car engine bay" in prompts["5"]
    assert "already moving in the very first frame" in prompts["1"]  # D-090: shot 1 only
    assert "already moving" not in prompts["5"]
    assert (ctx.root / "data" / "inbox" / "flow" / "000001").is_dir()
    assert (folder / "voice.wav").is_file()
    assert len(assets(ctx, "flow_sheet")) == 1 and len(assets(ctx, "voiceover")) == 1
    assert "You don't have to make them" in sheet and "Save as: `still_06.png`" in sheet
    assert "product image of the alternator, vertical 9:16.\nHero view" in prompts["6"]


def test_script_without_diagram_labels_is_refused_before_any_flow_work(ctx: AppContext) -> None:
    content_id = approved_item(ctx)
    old = [{**s, "labels": []} if s["kind"] == "diagram" else s for s in SHOTS]
    with ctx.conn:
        ctx.conn.execute("UPDATE scripts SET shot_list_json = ?", (json.dumps(old),))

    result = run(ctx)

    assert (result.failed, status(ctx, content_id)) == (1, "failed")
    reason = ctx.conn.execute("SELECT status_reason FROM contents").fetchone()[0]
    assert "have no labels" in reason and "redo the script" in reason


def test_missing_or_bad_clips_keep_the_item_waiting(ctx: AppContext) -> None:
    content_id = approved_item(ctx)
    run(ctx)
    inbox = generate.inbox_dir(ctx, content_id)
    make_clip(inbox / "shot_01.mp4")
    make_clip(inbox / "shot_02.mp4", size="192x108")  # landscape
    (inbox / "shot_04.mp4").write_text("not a video")

    result = run(ctx)

    assert (result.processed, result.skipped) == (0, 1)
    assert status(ctx, content_id) == "awaiting_clips"
    assert assets(ctx, "flow_clip") == []


def test_complete_clip_set_is_collected(ctx: AppContext) -> None:
    content_id = approved_item(ctx)
    run(ctx)
    inbox = generate.inbox_dir(ctx, content_id)
    for n in CLIPS:
        make_clip(inbox / f"shot_{n:02d}.mp4")
    (inbox / "notes.txt").write_text("ignored")

    result = run(ctx)

    assert (result.processed, status(ctx, content_id)) == (1, "generated")
    rows = assets(ctx, "flow_clip")
    assert [r["shot_n"] for r in rows] == CLIPS
    first = rows[0]
    assert (first["generator"], first["model"], first["license"]) == (
        "google_flow",
        "Veo 3.1 - Fast",
        "google_flow_output",
    )
    assert (first["width"], first["height"]) == (108, 192)
    assert first["path"].endswith(f"{content_id:06d}/clips/shot_01.mp4")
    assert "on a matte dark pedestal" in first["prompt"]
    assert (inbox / "shot_01.mp4").is_file()  # the original download stays in the inbox
    folder = ctx.root / "data" / "assets" / "coil-and-cog" / f"{content_id:06d}"
    [still] = assets(ctx, "still")
    assert (still["generator"], still["model"]) == ("ffmpeg", "frame of shot_01")  # same beat
    [anim] = assets(ctx, "still_anim")
    info = probe(folder / "stills" / "still_06_anim.mp4")
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"], video["r_frame_rate"]) == (1080, 1920, "30/1")
    assert float(info["format"]["duration"]) == pytest.approx(2.0, abs=0.1)
    [diagram] = assets(ctx, "diagram")
    assert (diagram["shot_n"], diagram["generator"], diagram["license"]) == (
        3,
        "pillow",
        "own_work",
    )
    assert (folder / "diagrams" / "diag_03_preview.jpg").is_file()
    assert run(ctx).processed == 0  # nothing left to do: safe to re-run


def test_owner_still_in_the_inbox_is_used(ctx: AppContext) -> None:
    content_id = approved_item(ctx)
    run(ctx)
    inbox = generate.inbox_dir(ctx, content_id)
    for n in CLIPS:
        make_clip(inbox / f"shot_{n:02d}.mp4")
    Image.new("RGB", (900, 1600), "#334455").save(inbox / "still_06.jpg")

    run(ctx)

    [still] = assets(ctx, "still")
    assert still["generator"] == "google_flow"
    assert "Hero view" in still["prompt"]


def test_flow_log_respects_the_daily_budget(ctx: AppContext) -> None:
    assert generate.flow_log(ctx, 20) == (20, 50)
    assert generate.flow_log(ctx, 20) == (40, 50)

    with pytest.raises(BudgetExceededError):
        generate.flow_log(ctx, 20)


def test_flow_log_command(config_dir: Path) -> None:
    runner = CliRunner()
    runner.invoke(app, ["db", "init", "--config-dir", str(config_dir)])

    ok = runner.invoke(app, ["flow-log", "--credits", "20", "--config-dir", str(config_dir)])
    over = runner.invoke(app, ["flow-log", "--credits", "40", "--config-dir", str(config_dir)])

    assert ok.exit_code == 0 and "Flow credits today: 20 of 50 (30 left)" in ok.output
    assert over.exit_code == 1 and "would exceed today's limit" in over.output
