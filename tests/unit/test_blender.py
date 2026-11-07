"""Blender films (ROADMAP 2.26, D-123): render job zips, the returned film, migration 009."""

from __future__ import annotations

import ast
import json
import math
import shutil
import sqlite3
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest
from fakes.fake_tts import make_fake_tts

from factory import blender, films
from factory.blender import alternator, jobs
from factory.config import REPO_ROOT, EnvSettings, load_channel, load_settings
from factory.context import AppContext
from factory.db import add_content, connect, migrate, schema_version, set_status, upsert_channel
from factory.errors import FactoryError
from factory.media.ffmpeg import run_ffmpeg
from factory.stages import generate
from factory.stages import script as script_stage
from factory.stages.voice import asset_dir

NARRATION = ("The alternator makes power for the car. A belt turns its pulley. Magnets spin "
             "inside the coils. Six diodes make direct current. As an engineer, I like its "
             "slip rings. It never stops charging.")  # fmt: skip
VOICE_S = 12.0  # six sentences of about 2 s: every scene is long enough


@pytest.fixture
def ctx(tmp_path: Path) -> AppContext:
    conn = connect(tmp_path / "test.db")
    migrate(conn)
    channel = load_channel("coil-and-cog")
    upsert_channel(conn, channel)
    shutil.copytree(REPO_ROOT / "assets_static" / "fonts", tmp_path / "assets_static" / "fonts")
    env = EnvSettings(dry_run=True, factory_channel="coil-and-cog")
    yield AppContext(load_settings(), env, channel, conn, tmp_path)
    conn.close()


def approved_item(ctx: AppContext, object_name: str = "alternator") -> int:
    content_id = add_content(ctx.conn, ctx.channel, object_name=object_name, fmt="short",
                             pillar="vehicles", style="blender")  # fmt: skip
    with ctx.conn:
        ctx.conn.execute(
            "INSERT INTO scripts (content_id, version, body_text, engineer_note, shot_list_json) "
            "VALUES (?, 1, ?, 'As an engineer, I like its slip rings.', '[]')",
            (content_id, NARRATION),
        )
    set_status(ctx.conn, content_id, "scripted")
    set_status(ctx.conn, content_id, "script_approved")
    return content_id


def row(ctx: AppContext, content_id: int) -> sqlite3.Row:
    return ctx.conn.execute("SELECT * FROM contents WHERE id = ?", (content_id,)).fetchone()


def status(ctx: AppContext, content_id: int) -> str:
    return row(ctx, content_id)["status"]


def issue(ctx: AppContext) -> int:
    content_id = approved_item(ctx)
    generate.run(ctx, tts=lambda: make_fake_tts(seconds=VOICE_S))
    return content_id


def make_film(path: Path, frames: int, size: str = "1080x1920") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    source = f"testsrc=size={size}:rate=30"
    run_ffmpeg(["-f", "lavfi", "-i", source, "-frames:v", str(frames), "-c:v", "libx264",
                "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)])  # fmt: skip
    return path


# --- the job zip -------------------------------------------------------------------------------


def test_job_zip_has_everything_blender_needs_and_nothing_else(ctx: AppContext) -> None:
    content_id = issue(ctx)

    assert status(ctx, content_id) == "awaiting_clips"
    assert "job_000001.zip" in row(ctx, content_id)["status_reason"]
    with zipfile.ZipFile(jobs.zip_path(ctx, content_id)) as z:
        names = set(z.namelist())
        spec = json.loads(z.read("job.json"))
        how_to = z.read("HOW_TO_RUN.txt").decode()
    assert names == {"job.json", "render.py", "kit.py", "alternator.py", "Inter-SemiBold.ttf",
                     "OFL-Inter.txt", "HOW_TO_RUN.txt"}  # fmt: skip
    assert [s["role"] for s in spec["scenes"]] == list(alternator.ROLES)
    starts = [s["start"] for s in spec["scenes"]]
    assert starts[0] == 0 and starts == sorted(starts) and starts[-1] < spec["duration_s"]
    assert spec["frames"] == math.ceil(spec["duration_s"] * 30)  # never shorter than the voice
    assert (spec["width"], spec["height"], spec["fps"]) == (1080, 1920, 30)
    assert f"film_{spec['job_id']}.mp4" in how_to and "000001" in how_to
    assert jobs.inbox_dir(ctx, content_id).is_dir()


def test_job_files_run_without_the_factory() -> None:
    """The render PC has only Blender: the job's Python files may import nothing of ours."""
    for path in jobs.JOB_FILES.glob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        imported = {alias.name.split(".")[0] for node in ast.walk(tree)
                    if isinstance(node, ast.Import) for alias in node.names}  # fmt: skip
        imported |= {node.module.split(".")[0] for node in ast.walk(tree)
                     if isinstance(node, ast.ImportFrom) and node.module}  # fmt: skip
        assert "factory" not in imported, path.name
    for model in blender.MODELS.values():
        assert (jobs.JOB_FILES / model.MODEL_FILE).is_file()


def test_every_model_fact_has_two_different_sources() -> None:
    for model in blender.MODELS.values():
        for name, fact in model.FACTS.items():
            assert fact.source.startswith("https://"), name
            assert fact.second_source.startswith("https://"), name
            assert fact.source != fact.second_source, name


def test_the_job_id_changes_when_the_model_changes(
    ctx: AppContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content_id = issue(ctx)
    first = jobs.job_for(ctx, row(ctx, content_id)).job_id
    assert jobs.job_for(ctx, row(ctx, content_id)).job_id == first  # same input, same job
    changed = tmp_path / "job_files"
    shutil.copytree(jobs.JOB_FILES, changed)
    (changed / "kit.py").write_text((changed / "kit.py").read_text() + "\n# a change\n")
    monkeypatch.setattr(jobs, "JOB_FILES", changed)

    assert jobs.job_for(ctx, row(ctx, content_id)).job_id != first
    generate.run(ctx, tts=lambda: make_fake_tts(seconds=VOICE_S))  # the zip is written again
    assert jobs.zip_job_id(ctx, content_id) == jobs.job_for(ctx, row(ctx, content_id)).job_id


def test_a_machine_without_a_model_waits_without_work(ctx: AppContext) -> None:
    content_id = approved_item(ctx, "turbocharger")

    result = generate.run(ctx, tts=lambda: make_fake_tts(seconds=VOICE_S))

    assert status(ctx, content_id) == "script_approved"
    assert result.skipped == 1 and not jobs.zip_path(ctx, content_id).exists()
    assert "no Blender model for 'turbocharger'" in blender.missing_message("turbocharger")


def test_16_9_waits_for_task_2_28(ctx: AppContext) -> None:
    content_id = issue(ctx)
    short = ctx.channel.formats.short.model_copy(update={"aspect": "16:9"})
    channel = ctx.channel.model_copy(
        update={"formats": ctx.channel.formats.model_copy(update={"short": short})}
    )
    with pytest.raises(FactoryError, match="16:9 comes with ROADMAP 2.28"):
        jobs.job_for(replace(ctx, channel=channel), row(ctx, content_id))


def test_too_short_scenes_are_refused(ctx: AppContext) -> None:
    content_id = approved_item(ctx)

    generate.run(ctx, tts=lambda: make_fake_tts(seconds=4.0))  # six sentences in 4 s

    assert status(ctx, content_id) == "failed"
    assert "scenes shorter than 1.5 s" in row(ctx, content_id)["status_reason"]


# --- the film coming back ----------------------------------------------------------------------


def test_only_this_jobs_complete_film_is_taken(ctx: AppContext) -> None:
    content_id = issue(ctx)
    job = jobs.job_for(ctx, row(ctx, content_id))
    inbox = jobs.inbox_dir(ctx, content_id)
    tts = lambda: make_fake_tts(seconds=VOICE_S)  # noqa: E731

    make_film(inbox / "film_0ldj0b00.mp4", job.spec["frames"])  # an older job's film
    generate.run(ctx, tts=tts)
    assert status(ctx, content_id) == "awaiting_clips"

    make_film(inbox / job.film_name, job.spec["frames"], size="1920x1080")  # wrong way round
    assert "is 1920x1080, not 1080x1920" in jobs.find_film(ctx, job)[1][0]
    make_film(inbox / job.film_name, job.spec["frames"] - 30)  # a render that stopped early
    assert "frames, not" in jobs.find_film(ctx, job)[1][0]
    generate.run(ctx, tts=tts)
    assert status(ctx, content_id) == "awaiting_clips"

    make_film(inbox / job.film_name, job.spec["frames"])
    generate.run(ctx, tts=tts)

    assert status(ctx, content_id) == "generated"
    assert (asset_dir(ctx, content_id) / generate.BLENDER_FILE).is_file()
    asset = ctx.conn.execute(
        "SELECT generator, model, license, width, height FROM assets WHERE kind = 'blender'"
    ).fetchone()
    assert tuple(asset) == ("blender", "blender/alternator", "own_work", 1080, 1920)


# --- scripts and styles ------------------------------------------------------------------------


def test_scripts_of_a_blender_item_use_the_models_facts_and_scenes(ctx: AppContext) -> None:
    blender_item = row(ctx, approved_item(ctx))
    flow_item = row(ctx, add_content(ctx.conn, ctx.channel, object_name="alternator",
                                     fmt="short", pillar="vehicles", style="flow"))  # fmt: skip

    assert [f.text for f in script_stage.film_facts(blender_item)] == [
        f.text for f in alternator.FACTS.values()
    ]
    assert films.outline(blender_item) == alternator.OUTLINE
    assert script_stage.film_facts(flow_item) is None and films.outline(flow_item) == ()


# --- migration 009 -----------------------------------------------------------------------------


def _first_migrations(tmp_path: Path, n: int) -> Path:
    folder = tmp_path / f"first_{n}"
    folder.mkdir()
    for path in sorted((REPO_ROOT / "migrations").glob("*.sql"))[:n]:
        shutil.copy(path, folder / path.name)
    return folder


def test_migration_009_keeps_every_row_and_accepts_blender(tmp_path: Path) -> None:
    conn = connect(tmp_path / "old.db")
    migrate(conn, _first_migrations(tmp_path, 8))
    upsert_channel(conn, load_channel("coil-and-cog"))
    with conn:
        conn.execute("INSERT INTO contents (channel_id, format, pillar, object_name, visual_style) "
                     "VALUES (1, 'short', 'vehicles', 'cvt gearbox', 'blueprint')")  # fmt: skip
        conn.execute("INSERT INTO scripts (content_id, version, body_text) VALUES (1, 1, 'x')")
        conn.execute("INSERT INTO assets (content_id, kind, path) VALUES (1, 'blueprint', 'b.mp4')")

    migrate(conn)

    assert schema_version(conn) == 9
    assert conn.execute("SELECT visual_style FROM contents").fetchone()[0] == "blueprint"
    assert conn.execute("SELECT count(*) FROM scripts").fetchone()[0] == 1
    with conn:
        conn.execute("UPDATE contents SET visual_style = 'blender' WHERE id = 1")
        conn.execute("INSERT INTO assets (content_id, kind, path) VALUES (1, 'blender', 'f.mp4')")
    with pytest.raises(sqlite3.IntegrityError):  # references are checked again afterwards
        conn.execute("INSERT INTO scripts (content_id, version, body_text) VALUES (99, 1, 'x')")
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_a_migration_that_breaks_references_is_not_applied(tmp_path: Path) -> None:
    folder = _first_migrations(tmp_path, 9)
    (folder / "010_broken.sql").write_text(
        "INSERT INTO scripts (content_id, version, body_text) VALUES (42, 1, 'orphan');"
    )
    conn = connect(tmp_path / "test.db")

    with pytest.raises(FactoryError, match="010_broken.sql would leave 1 broken references"):
        migrate(conn, folder)

    assert schema_version(conn) == 9
    assert conn.execute("SELECT count(*) FROM scripts").fetchone()[0] == 0
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
