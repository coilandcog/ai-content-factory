from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fakes.fake_gemini import FakeGenaiClient, reply
from typer.testing import CliRunner

from factory.budgets import check_and_record
from factory.cli import app
from factory.config import REPO_ROOT, EnvSettings, load_channel, load_settings
from factory.context import AppContext
from factory.db import add_content, connect, migrate, set_status, upsert_channel
from factory.errors import FactoryError
from factory.integrations.gemini import GeminiClient
from factory.stages import generate, references
from factory.stages.voice import asset_dir

STORY = {"style": "product", "start_picture": "the machine at rest", "continues": False}

NOTES = {
    "summary": "A polished engine block on a dark set",
    "lighting": "cyan rim left, warm key right",
    "camera": "low three-quarter angle, slow orbit",
    "composition": "machine fills two thirds of the frame",
    "motion": "none",
    "materials_colours": "brushed steel, deep graphite",
    "prompt_phrases": ["crisp cyan rim light", "slow low orbit", "brushed steel highlights"],
    "has_people": False,
}


@pytest.fixture
def ctx(tmp_path: Path) -> AppContext:
    shutil.copytree(REPO_ROOT / "prompts", tmp_path / "prompts")
    conn = connect(tmp_path / "test.db")
    migrate(conn)
    channel = load_channel("coil-and-cog").model_copy(update={"benchmarks": []})
    upsert_channel(conn, channel)
    env = EnvSettings(dry_run=True, factory_channel="coil-and-cog")
    yield AppContext(load_settings(), env, channel, conn, tmp_path)
    conn.close()


def drop(ctx: AppContext, name: str, data: bytes = b"image bytes") -> Path:
    folder = references.inbox(ctx)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(data)
    return path


def fake_llm(ctx: AppContext, *answers: dict | str) -> tuple[GeminiClient, FakeGenaiClient]:
    texts = [a if isinstance(a, str) else json.dumps(a) for a in answers]
    sdk = FakeGenaiClient(*(reply(t) for t in texts))
    llm = GeminiClient(
        "fake", "gemini-3.5-flash-lite", sdk=sdk, retry_wait_s=0,
        before_call=lambda: check_and_record(ctx.conn, ctx.channel, "gemini"),
    )  # fmt: skip
    return llm, sdk


def test_new_files_become_pending_notes_and_move_out_of_the_inbox(ctx: AppContext) -> None:
    drop(ctx, "engine.png")
    drop(ctx, "readme.txt")
    llm, sdk = fake_llm(ctx, {**NOTES, "prompt_phrases": ["only one"]}, NOTES)

    results = references.add(ctx, llm=llm)

    assert [r["file"] for r in results] == ["engine.png", "readme.txt"]
    assert results[0]["id"] == 1 and "not an image or video" in results[1]["skipped"]
    assert "give 3-6 prompt_phrases, not 1" in sdk.calls[1]["contents"][1]  # asked again
    part, text = sdk.calls[0]["contents"]
    assert part.inline_data.mime_type == "image/png"
    assert '"none" for a still image' in text and "never names, brands, logos" in text
    row = references.listing(ctx)[0]
    assert (row["status"], row["kind"], row["original_name"]) == ("pending", "image", "engine.png")
    assert row["path"] == "data/references/0001_engine.png"
    assert (ctx.root / row["path"]).is_file() and not (
        references.inbox(ctx) / "engine.png"
    ).exists()
    assert references.approved_for_prompts(ctx) == []  # pending notes never reach prompts


def test_the_same_file_is_not_studied_twice(ctx: AppContext) -> None:
    drop(ctx, "engine.png")
    llm, _ = fake_llm(ctx, NOTES)
    references.add(ctx, llm=llm)
    drop(ctx, "copy.png")  # same bytes, other name
    llm, sdk = fake_llm(ctx)

    results = references.add(ctx, llm=llm)

    assert results == [{"file": "copy.png", "skipped": "already in the library as #1"}]
    assert sdk.calls == []


def test_empty_inbox_says_where_to_save(ctx: AppContext) -> None:
    llm, _ = fake_llm(ctx)

    with pytest.raises(FactoryError, match="no files in data/inbox/references"):
        references.add(ctx, llm=llm)
    assert references.inbox(ctx).is_dir()  # created so the owner can find it


def test_too_large_clip_is_shrunk_before_sending(
    ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = drop(ctx, "clip.mp4", b"")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "testsrc=size=640x360:rate=10:duration=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )  # fmt: skip
    monkeypatch.setattr(references, "MAX_INLINE_BYTES", 10)
    llm, sdk = fake_llm(ctx, {**NOTES, "motion": "test pattern scrolls"})

    references.add(ctx, llm=llm)

    part, _ = sdk.calls[0]["contents"]
    assert part.inline_data.mime_type == "video/mp4"
    assert part.inline_data.data != (ctx.root / references.listing(ctx)[0]["path"]).read_bytes()


def test_only_approved_notes_reach_the_film_rules(ctx: AppContext) -> None:
    for n in range(10):
        drop(ctx, f"ref{n}.png", f"image {n}".encode())
    llm, _ = fake_llm(ctx, *[{**NOTES, "summary": f"Reference {n}"} for n in range(10)])
    references.add(ctx, llm=llm)
    for ref_id in range(1, 11):
        references.decide(ctx, ref_id, "approved")
    references.decide(ctx, 10, "removed")
    references.decide(ctx, 9, "approved", "  I love this rim light  ")

    notes = references.approved_for_prompts(ctx)

    assert [n["summary"] for n in notes] == [f"Reference {n}" for n in range(1, 9)]  # newest 8
    assert notes[-1]["owner_note"] == "I love this rim light"
    with pytest.raises(FactoryError, match="reference #99 does not exist"):
        references.decide(ctx, 99, "approved")


def test_restage_prompt_carries_the_approved_notes(ctx: AppContext) -> None:
    drop(ctx, "engine.png")
    llm, _ = fake_llm(ctx, NOTES)
    references.add(ctx, llm=llm)
    references.decide(ctx, 1, "approved", "copy this low orbit")
    content_id = add_content(ctx.conn, ctx.channel, object_name="alternator", fmt="short",
                             pillar="vehicles")  # fmt: skip
    shots = [{"n": 1, "beat": "studio", "kind": "flow_clip", "description": "On a dark set",
              "duration_s": 8, "prompt_vars": {"context": "old"}}]  # fmt: skip
    with ctx.conn:
        ctx.conn.execute(
            "INSERT INTO scripts (content_id, version, body_text, shot_list_json) "
            "VALUES (?, 1, 'Narration.', ?)",
            (content_id, json.dumps(shots)),
        )
    set_status(ctx.conn, content_id, "scripted")
    set_status(ctx.conn, content_id, "script_approved")
    plan = {"hero_look": " ".join(["polished alternator"] * 12),
            "shots": [{"n": 1, "scene": "It turns slowly on a dark set", "camera": "low orbit",
                       **STORY, "end_frame": "the pulley"}]}  # fmt: skip
    llm, sdk = fake_llm(ctx, plan)

    generate.restage(ctx, content_id, llm=llm)

    prompt = sdk.calls[0]["contents"]
    assert "Reference notes the owner approved" in prompt
    assert 'Owner: "copy this low orbit"' in prompt and "crisp cyan rim light" in prompt
    assert not (asset_dir(ctx, content_id) / generate.PROMPTS_FILE).exists()  # no sheet yet


def test_list_command_before_any_reference(config_dir: Path) -> None:
    opts = ["--config-dir", str(config_dir)]
    CliRunner().invoke(app, ["db", "init", *opts])

    result = CliRunner().invoke(app, ["references", "list", *opts])

    assert result.exit_code == 0, result.output
    assert "No references yet" in result.output


def test_references_for_one_item_only_shape_that_item(ctx: AppContext) -> None:
    drop(ctx, "general.png", b"general")
    references.add(ctx, llm=fake_llm(ctx, {**NOTES, "summary": "General look"})[0])
    content_id = add_content(ctx.conn, ctx.channel, object_name="cvt gearbox", fmt="short",
                             pillar="vehicles")  # fmt: skip
    drop(ctx, "cvt.png", b"cvt")
    references.add(ctx, for_item=content_id, llm=fake_llm(ctx, {**NOTES, "summary": "CVT belt"})[0])
    references.decide(ctx, 1, "approved")
    references.decide(ctx, 2, "approved")

    assert [n["summary"] for n in references.approved_for_prompts(ctx)] == ["General look"]
    for_cvt = references.approved_for_prompts(ctx, content_id)
    assert [(n["summary"], n["for_item"]) for n in for_cvt] == [
        ("General look", None), ("CVT belt", content_id),
    ]  # fmt: skip
    with pytest.raises(FactoryError, match="content #99 does not exist"):
        references.add(ctx, for_item=99, llm=fake_llm(ctx)[0])
