from __future__ import annotations

import json
import math
import shutil
import subprocess
from array import array
from pathlib import Path

import pytest
from typer.testing import CliRunner

from factory.cli import app
from factory.config import REPO_ROOT, EnvSettings, load_channel, load_settings
from factory.context import AppContext
from factory.db import add_content, connect, migrate, set_status, upsert_channel
from factory.errors import BudgetExceededError
from factory.integrations.kokoro_tts import SAMPLE_RATE
from factory.stages import generate

pytestmark = pytest.mark.slow  # runs ffmpeg

SHOTS = [
    {"n": 1, "beat": "studio", "kind": "flow_clip", "description": "Alternator on the pedestal",
     "duration_s": 6, "prompt_vars": {"hero_feature": "the pulley and cooling fins"}},
    {"n": 2, "beat": "cutaway", "kind": "flow_clip", "description": "Casing opens",
     "duration_s": 7, "prompt_vars": {"internals": "rotor and stator", "focus_part": "the rotor"}},
    {"n": 3, "beat": "cutaway", "kind": "diagram", "description": "Labels", "duration_s": 4,
     "prompt_vars": {}},
    {"n": 4, "beat": "flow", "kind": "flow_clip", "description": "Current flows",
     "duration_s": 8, "prompt_vars": {"energy": "electric", "path": "the stator coils",
                                       "process": "AC becoming DC"}},
    {"n": 5, "beat": "in_situ", "kind": "flow_clip", "description": "Engine bay",
     "duration_s": 6, "prompt_vars": {"context": "a car engine bay"}},
]  # fmt: skip
CLIPS = [1, 2, 4, 5]


def fake_tts(text: str, voice_id: str, speed: float) -> bytes:
    n = int(1.5 * SAMPLE_RATE)
    return array(
        "f", (0.1 * math.sin(2 * math.pi * 220 * i / SAMPLE_RATE) for i in range(n))
    ).tobytes()


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
    yield AppContext(load_settings(), env, channel, conn, tmp_path)
    conn.close()


def approved_item(ctx: AppContext) -> int:
    content_id = add_content(
        ctx.conn, ctx.channel, object_name="alternator", fmt="short", pillar="machines"
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
    assert "The alternator rests on the pedestal" in sheet
    assert "Save as: `shot_01.mp4`" in sheet and "Save as: `shot_03.mp4`" not in sheet  # diagram
    assert "glowing cyan energy (#00D1FF) flows through the stator coils" in sheet
    assert "9:16 vertical frame" in sheet and "No text, no logos" in sheet
    assert "\\\\wsl$\\" in sheet and "\\data\\inbox\\flow\\000001" in sheet
    prompts = json.loads((folder / "flow_prompts.json").read_text())
    assert "pedestal" in prompts["1"] and "pedestal" not in prompts["5"]  # in situ: no studio set
    assert "installed in a car engine bay" in prompts["5"]
    assert (ctx.root / "data" / "inbox" / "flow" / "000001").is_dir()
    assert (folder / "voice.wav").is_file()
    assert len(assets(ctx, "flow_sheet")) == 1 and len(assets(ctx, "voiceover")) == 1


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
    assert "rests on the pedestal" in first["prompt"]
    assert (inbox / "shot_01.mp4").is_file()  # the original download stays in the inbox
    assert run(ctx).processed == 0  # nothing left to do: safe to re-run


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
