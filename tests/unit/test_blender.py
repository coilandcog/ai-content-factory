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
from factory.stages import generate, scenes
from factory.stages import script as script_stage

NARRATION = ("The alternator makes power for the car. A belt turns its rotor. The stator coils "
             "catch the current. Six diodes make direct current. As an engineer, I like its "
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
    # the scenes' frames follow each other with no gap or overlap and fill the whole film
    ranges = [(s["first_frame"], s["last_frame"]) for s in spec["scenes"]]
    assert ranges[0][0] == 1 and ranges[-1][1] == spec["frames"]
    assert all(b[0] == a[1] + 1 for a, b in zip(ranges, ranges[1:], strict=False))
    assert spec["render"] == [1, 2, 3, 4, 5, 6]  # a first job renders every scene
    assert f"scene_{spec['job_id']}_06.mp4" in how_to and "000001" in how_to
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
    assert jobs.zip_job(ctx, content_id) == (
        jobs.job_for(ctx, row(ctx, content_id)).job_id,
        [1, 2, 3, 4, 5, 6],
    )


def test_a_machine_without_a_model_waits_without_work(ctx: AppContext) -> None:
    content_id = approved_item(ctx, "turbocharger")

    result = generate.run(ctx, tts=lambda: make_fake_tts(seconds=VOICE_S))

    assert status(ctx, content_id) == "script_approved"
    assert result.skipped == 1 and not jobs.zip_path(ctx, content_id).exists()
    assert "no Blender model for 'turbocharger'" in blender.missing_message("turbocharger")


def test_jobs_are_9_16_or_16_9(ctx: AppContext) -> None:
    content_id = issue(ctx)

    def with_aspect(aspect: str) -> AppContext:
        short = ctx.channel.formats.short.model_copy(update={"aspect": aspect})
        formats = ctx.channel.formats.model_copy(update={"short": short})
        return replace(ctx, channel=ctx.channel.model_copy(update={"formats": formats}))

    wide = jobs.job_for(with_aspect("16:9"), row(ctx, content_id)).spec
    assert (wide["width"], wide["height"]) == (1920, 1080)  # long videos (D-128)
    with pytest.raises(FactoryError, match="Blender films are 9:16 or 16:9"):
        jobs.job_for(with_aspect("1:1"), row(ctx, content_id))


def test_too_short_scenes_are_refused(ctx: AppContext) -> None:
    content_id = approved_item(ctx)

    generate.run(ctx, tts=lambda: make_fake_tts(seconds=4.0))  # six sentences in 4 s

    assert status(ctx, content_id) == "failed"
    assert "scenes shorter than 1.5 s" in row(ctx, content_id)["status_reason"]


# --- the film coming back ----------------------------------------------------------------------


def deliver(ctx: AppContext, job: jobs.Job, numbers: list[int], **kw) -> None:
    """What the render PC sends back: one clip per scene, named for this job."""
    for scene in job.spec["scenes"]:
        if scene["n"] in numbers:
            frames = scene["last_frame"] - scene["first_frame"] + 1
            make_film(jobs.inbox_dir(ctx, job.spec["content_id"]) / job.clip_name(scene["n"]),
                      kw.get("frames", frames), kw.get("size", "1080x1920"))  # fmt: skip


def run_generate(ctx: AppContext) -> None:
    generate.run(ctx, tts=lambda: make_fake_tts(seconds=VOICE_S))


def scene_status(ctx: AppContext, content_id: int) -> dict[int, str]:
    return {r["n"]: r["status"] for r in scenes.rows(ctx.conn, content_id)}


def test_each_scene_clip_is_checked_on_its_own(ctx: AppContext) -> None:
    content_id = issue(ctx)
    job = jobs.job_for(ctx, row(ctx, content_id))
    assert [r["narration"] for r in scenes.rows(ctx.conn, content_id)][0].startswith("The")
    inbox = jobs.inbox_dir(ctx, content_id)

    deliver(ctx, job, [1, 2, 3, 4, 5])  # scene 6 not rendered yet
    make_film(inbox / "scene_0ldj0b00_06.mp4", 30)  # an older job's clip is never taken
    deliver(ctx, job, [3], size="1920x1080")  # scene 3 the wrong way round
    run_generate(ctx)

    assert status(ctx, content_id) == "awaiting_clips"
    expected = {1: "rendered", 2: "rendered", 3: "waiting", 4: "rendered", 5: "rendered"}
    assert scene_status(ctx, content_id) == {**expected, 6: "waiting"}
    assert "is 1920x1080, not 1080x1920" in jobs.find_clip(ctx, job, 3)[1]

    deliver(ctx, job, [3, 6])
    run_generate(ctx)

    assert status(ctx, content_id) == "generated"
    assert set(scene_status(ctx, content_id).values()) == {"rendered"}
    shots = [r[0] for r in ctx.conn.execute(
        "SELECT shot_n FROM assets WHERE kind = 'blender' ORDER BY shot_n")]  # fmt: skip
    assert shots == [1, 2, 3, 4, 5, 6]
    assert scenes.clip_path(ctx, content_id, 6).is_file()


def generated(ctx: AppContext) -> tuple[int, jobs.Job]:
    content_id = issue(ctx)
    job = jobs.job_for(ctx, row(ctx, content_id))
    deliver(ctx, job, [1, 2, 3, 4, 5, 6])
    run_generate(ctx)
    assert status(ctx, content_id) == "generated"
    return content_id, job


def test_an_approval_holds_the_file_it_was_given_for(ctx: AppContext) -> None:
    content_id, job = generated(ctx)

    assert scenes.approve(ctx, content_id, [2]) == [2]
    assert "scene 1 is rendered" in scenes.not_ready(ctx, content_id)
    assert scenes.approve(ctx, content_id) == [1, 2, 3, 4, 5, 6]
    assert scenes.not_ready(ctx, content_id) == []

    clip = scenes.clip_path(ctx, content_id, 4)
    clip.write_bytes(clip.read_bytes() + b"changed")  # someone replaced the file by hand

    assert scenes.not_ready(ctx, content_id) == ["scene 4 changed since it was approved"]
    assert "CHANGED since approval" in scenes.report(ctx, content_id)
    history = ctx.conn.execute("SELECT count(*) FROM scene_reviews WHERE decision = 'approve'")
    assert history.fetchone()[0] == 7  # scene 2 twice: every decision is kept


def test_a_redo_renders_only_its_scene_and_keeps_the_others(ctx: AppContext) -> None:
    content_id, job = generated(ctx)
    scenes.approve(ctx, content_id)
    kept = scenes.clip_path(ctx, content_id, 2).read_bytes()

    out = scenes.redo(ctx, content_id, [3], "the stator glow starts too late")

    assert status(ctx, content_id) == "awaiting_clips"
    assert scene_status(ctx, content_id)[3] == "redo"
    assert [s for n, s in scene_status(ctx, content_id).items() if n != 3] == ["approved"] * 5
    assert jobs.zip_job(ctx, content_id) == (job.job_id, [3])
    with zipfile.ZipFile(out) as z:
        how_to = z.read("HOW_TO_RUN.txt").decode()
    assert "only scene 3 (a redo)" in how_to and job.clip_name(2) not in how_to
    inbox = jobs.inbox_dir(ctx, content_id)
    assert (inbox / f"old_{job.clip_name(3)}").is_file()  # the old clip is never taken again
    run_generate(ctx)
    assert status(ctx, content_id) == "awaiting_clips"  # still waiting for the new scene 3

    deliver(ctx, job, [3])
    run_generate(ctx)

    assert status(ctx, content_id) == "generated"
    assert scene_status(ctx, content_id)[3] == "rendered"
    assert scenes.clip_path(ctx, content_id, 2).read_bytes() == kept
    assert scenes.not_ready(ctx, content_id) == ["scene 3 is rendered"]
    with pytest.raises(FactoryError, match="needs a reason"):
        scenes.redo(ctx, content_id, [1], "  ")
    with pytest.raises(FactoryError, match="has no scene 9"):
        scenes.redo(ctx, content_id, [9], "no such scene")


def test_gate_2_lists_the_scene_approvals(ctx: AppContext) -> None:
    content_id, _ = generated(ctx)
    scenes.approve(ctx, content_id, [1, 2])

    lines = scenes.summary(ctx.conn, content_id)

    assert lines[0] == "SCENES: 2/6 approved"
    assert "  scene 3 (body): rendered" in lines


def test_an_item_issued_before_scenes_existed_gets_them(ctx: AppContext) -> None:
    content_id = issue(ctx)
    with ctx.conn:  # as a 2.26 job left it: no scene rows
        ctx.conn.execute("DELETE FROM scenes WHERE content_id = ?", (content_id,))
    job = jobs.job_for(ctx, row(ctx, content_id))
    deliver(ctx, job, [1, 2, 3, 4, 5, 6])

    run_generate(ctx)

    assert status(ctx, content_id) == "generated"
    assert len(scenes.rows(ctx.conn, content_id)) == 6


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


def _first_migrations(tmp_path: Path, n: int, name: str | None = None) -> Path:
    folder = tmp_path / (name or f"first_{n}")
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

    migrate(conn, _first_migrations(tmp_path, 9, "first_9"))

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
    folder = _first_migrations(tmp_path, 10)
    (folder / "011_broken.sql").write_text(
        "INSERT INTO scripts (content_id, version, body_text) VALUES (42, 1, 'orphan');"
    )
    conn = connect(tmp_path / "test.db")

    with pytest.raises(FactoryError, match="011_broken.sql would leave 1 broken references"):
        migrate(conn, folder)

    assert schema_version(conn) == 10
    assert conn.execute("SELECT count(*) FROM scripts").fetchone()[0] == 0
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_scenes_commands_explain_an_item_without_scenes(config_dir: Path) -> None:
    from typer.testing import CliRunner

    from factory.cli import app

    runner, opts = CliRunner(), ["--config-dir", str(config_dir)]
    runner.invoke(app, ["db", "init", *opts])
    runner.invoke(app, ["new", "--object", "alternator", "--format", "short", "--pillar",
                        "vehicles", *opts])  # fmt: skip

    shown = runner.invoke(app, ["scenes", "show", "1", *opts])
    approve = runner.invoke(app, ["scenes", "approve", "1", "--scene", "x", *opts])
    redo = runner.invoke(app, ["scenes", "redo", "1", "--scene", "2", "--reason", "x", *opts])

    assert shown.exit_code == 0 and "has no scenes yet" in shown.output
    assert approve.exit_code == 1 and "--scene must be numbers" in approve.output
    assert redo.exit_code == 1 and "is idea: scenes can be redone" in redo.output


def scripted_item(ctx: AppContext, narration: str) -> int:
    content_id = add_content(ctx.conn, ctx.channel, object_name="alternator", fmt="short",
                             pillar="vehicles", style="blender")  # fmt: skip
    with ctx.conn:
        ctx.conn.execute(
            "INSERT INTO scripts (content_id, version, body_text, engineer_note, shot_list_json) "
            "VALUES (?, 1, ?, 'As an engineer, I like its slip rings.', '[]')",
            (content_id, narration),
        )
    set_status(ctx.conn, content_id, "scripted")
    set_status(ctx.conn, content_id, "script_approved")
    generate.run(ctx, tts=lambda: make_fake_tts(seconds=20.0))  # every sentence over 1.5 s
    return content_id


def test_each_part_scene_starts_where_the_narration_names_that_part(ctx: AppContext) -> None:
    """Two rotor sentences: counting sentences would put the stator on screen during the second
    one (#20, 2026-11-07); the stator scene must wait for the stator sentence (D-126)."""
    content_id = scripted_item(ctx, (
        "Why does the battery stay full? A belt turns the rotor. Its field coil makes a magnet. "
        "The stator windings catch the current. Six diodes make direct current. As an engineer, "
        "I like its slip rings. It never stops charging."
    ))  # fmt: skip

    said = {r["n"]: r["narration"] for r in scenes.rows(ctx.conn, content_id)}

    assert said[2] == "A belt turns the rotor. Its field coil makes a magnet."
    assert said[3] == "The stator windings catch the current."
    assert said[4] == "Six diodes make direct current."


def test_a_script_that_skips_a_part_is_refused_before_any_render(ctx: AppContext) -> None:
    content_id = scripted_item(ctx, (
        "Why does the battery stay full? A belt turns the rotor. Its field coil makes a magnet. "
        "Six diodes make direct current. It is clever. As an engineer, I like its slip rings. "
        "It never stops charging."
    ))  # fmt: skip

    assert status(ctx, content_id) == "failed"
    assert "never names scene 3's part" in row(ctx, content_id)["status_reason"]
    assert not jobs.zip_path(ctx, content_id).exists()


def test_scene_rows_follow_new_timings_until_a_clip_arrives(ctx: AppContext) -> None:
    content_id = issue(ctx)
    with ctx.conn:  # rows from an older timing (as #20's were before D-126)
        ctx.conn.execute("UPDATE scenes SET last_frame = last_frame - 10 WHERE n = 2 AND "
                         "content_id = ?", (content_id,))  # fmt: skip
        ctx.conn.execute("UPDATE scenes SET first_frame = first_frame - 10 WHERE n = 3 AND "
                         "content_id = ?", (content_id,))  # fmt: skip

    run_generate(ctx)  # no clip yet: the rows are simply made again

    job = jobs.job_for(ctx, row(ctx, content_id))
    want = [(s["first_frame"], s["last_frame"]) for s in job.spec["scenes"]]
    assert [(r["first_frame"], r["last_frame"]) for r in scenes.rows(ctx.conn, content_id)] == want

    deliver(ctx, job, [1])
    run_generate(ctx)
    with ctx.conn:
        ctx.conn.execute("UPDATE scenes SET last_frame = last_frame - 10 WHERE n = 2 AND "
                         "content_id = ?", (content_id,))  # fmt: skip
    run_generate(ctx)  # a clip exists: never mix two cuts of the film

    assert status(ctx, content_id) == "failed"
    assert "timings changed after clips arrived" in row(ctx, content_id)["status_reason"]


def test_variety_compares_only_the_hook_for_films_made_by_code(ctx: AppContext) -> None:
    from factory.media import qa
    from factory.stages import assemble

    first, second = approved_item(ctx), approved_item(ctx)
    with ctx.conn:  # same beat order on both: a Blender film can't change its scene order
        ctx.conn.execute("UPDATE contents SET hook_type = 'reveal', beat_order = 'classic' "
                         "WHERE id = ?", (first,))  # fmt: skip
        ctx.conn.execute("UPDATE contents SET hook_type = 'why_question', beat_order = 'classic' "
                         "WHERE id = ?", (second,))  # fmt: skip

    current, previous = assemble._variety(ctx, row(ctx, second), code_film=True)

    assert qa.variety(current, previous)[0] is True
    with ctx.conn:
        ctx.conn.execute("UPDATE contents SET hook_type = 'reveal' WHERE id = ?", (second,))
    ok, detail = qa.variety(*assemble._variety(ctx, row(ctx, second), code_film=True))
    assert not ok and detail == "same hook_type as the previous video"  # the hook still counts
