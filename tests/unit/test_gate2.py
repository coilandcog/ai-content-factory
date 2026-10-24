from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from fakes.fake_gemini import LLM_FIXTURES, FakeGenaiClient, fixture_reply, reply
from typer.testing import CliRunner

from factory import cli
from factory.config import REPO_ROOT, EnvSettings, load_channel, load_settings
from factory.context import AppContext
from factory.db import add_content, connect, migrate, set_status, upsert_channel
from factory.errors import FactoryError
from factory.integrations.gemini import GeminiClient
from factory.stages import metadata, review
from factory.stages.voice import asset_dir

SOURCES = ["https://en.wikipedia.org/wiki/Alternator", "https://example.edu/alternators"]


def llm(*outcomes: object) -> GeminiClient:
    sdk = FakeGenaiClient(*(outcomes or (fixture_reply("metadata_alternator"),)))
    return GeminiClient("fake", "gemini-3.5-flash-lite", sdk=sdk, retry_wait_s=0)


def make_ctx(root: Path) -> AppContext:
    if not (root / "prompts").exists():
        shutil.copytree(REPO_ROOT / "prompts", root / "prompts")  # metadata prompt templates
    conn = connect(root / "data" / "factory.db")
    migrate(conn)
    channel = load_channel("coil-and-cog")
    upsert_channel(conn, channel)
    env = EnvSettings(dry_run=True, factory_channel="coil-and-cog")
    return AppContext(load_settings(), env, channel, conn, root)


@pytest.fixture
def ctx(tmp_path: Path) -> AppContext:
    context = make_ctx(tmp_path)
    yield context
    context.conn.close()


def assembled_item(ctx: AppContext, *, with_metadata: bool = True) -> int:
    """An item walked through the real status path to `assembled`, with stand-in render files."""
    content_id = add_content(ctx.conn, ctx.channel, object_name="alternator", fmt="short",
                             pillar="vehicles")  # fmt: skip
    with ctx.conn:
        ctx.conn.execute(
            "INSERT INTO scripts (content_id, version, body_text, shot_list_json) "
            "VALUES (?, 1, 'The alternator charges your battery.', '[]')",
            (content_id,),
        )
        for url in SOURCES:
            ctx.conn.execute(
                "INSERT INTO sources (content_id, url, title, publisher) VALUES (?, ?, 'T', 'P')",
                (content_id, url),
            )
    for status in ("scripted", "script_approved", "awaiting_clips", "generated", "assembled"):
        set_status(ctx.conn, content_id, status)
    folder = asset_dir(ctx, content_id)
    folder.mkdir(parents=True)
    for name in ("render_youtube.mp4", "render_tiktok.mp4", "preview_720p.mp4", "qa_report.json"):
        (folder / name).write_text("stand-in")
    if with_metadata:
        metadata.ensure_metadata(ctx, content_id, llm=llm())
    return content_id


def status(ctx: AppContext, content_id: int) -> str:
    return ctx.conn.execute("SELECT status FROM contents WHERE id = ?", (content_id,)).fetchone()[0]


# --- metadata.json -----------------------------------------------------------------------------


def test_metadata_has_creative_parts_and_exact_code_parts(ctx: AppContext) -> None:
    content_id = assembled_item(ctx)

    data = metadata.load(ctx, content_id)

    assert data["youtube"]["title"].startswith("Alternator in 40 seconds")
    lines = data["youtube"]["description"].splitlines()
    assert lines[0].startswith("See how a spinning rotor")  # summary first
    assert lines[2] == "Sources:" and SOURCES[0] in lines[3] and SOURCES[1] in lines[4]
    assert not any("AI-generated" in line for line in lines)  # no disclosure line (D-075)
    assert lines[-1] == "#HowItWorks #Engineering #Cars"
    assert data["youtube"]["contains_synthetic_media"] is True
    assert data["youtube"]["category_id"] == 28 and data["youtube"]["made_for_kids"] is False
    assert data["tiktok"]["caption"].endswith("#HowItWorks #Engineering #CarTok #Alternator")
    assert data["tiktok"]["ai_generated_label"] is True and data["tiktok"]["add_tiktok_sound"]
    assert metadata.validate(data) == []
    kinds = [r[0] for r in ctx.conn.execute("SELECT kind FROM assets WHERE kind = 'metadata'")]
    assert kinds == ["metadata"]


def test_music_attribution_goes_into_the_description(ctx: AppContext) -> None:
    content_id = assembled_item(ctx, with_metadata=False)
    with ctx.conn:
        track = ctx.conn.execute(
            "INSERT INTO music_tracks (title, artist, source, platform_scope, attribution_required,"
            " attribution_text, path) VALUES ('Gears', 'Ann', 'youtube_audio_library', 'youtube',"
            " 1, 'Music: Gears by Ann (YouTube Audio Library)', 'x.mp3')"
        ).lastrowid
        ctx.conn.execute(
            "INSERT INTO assets (content_id, kind, path, music_track_id) "
            "VALUES (?, 'music', 'm', ?)",
            (content_id, track),
        )

    metadata.ensure_metadata(ctx, content_id, llm=llm())

    description = metadata.load(ctx, content_id)["youtube"]["description"]
    assert "Music: Gears by Ann (YouTube Audio Library)" in description


def test_rule_breaks_are_reasked_and_existing_metadata_is_kept(ctx: AppContext) -> None:
    content_id = assembled_item(ctx, with_metadata=False)
    bad = json.loads((LLM_FIXTURES / "metadata_alternator.json").read_text())
    bad["youtube_title"] = "Alternator " + "x" * 80
    bad["youtube_hashtags"] = ["#One", "two words"]
    client = llm(reply(json.dumps(bad)), fixture_reply("metadata_alternator"))

    metadata.ensure_metadata(ctx, content_id, llm=client)
    metadata.ensure_metadata(ctx, content_id, llm=client)  # file exists: no new call

    assert len(client._sdk.calls) == 2  # 1 bad answer + 1 re-ask
    assert "title has" in client._sdk.calls[1]["contents"]


def synthetic_off(d: dict) -> None:
    d["youtube"]["contains_synthetic_media"] = False


def long_title(d: dict) -> None:
    d["youtube"]["title"] = "x" * 71


def one_source(d: dict) -> None:
    d["sources"] = d["sources"][:1]


def label_off(d: dict) -> None:
    d["tiktok"]["ai_generated_label"] = False


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (synthetic_off, "contains_synthetic_media must be true"),
        (long_title, "title has 71 characters"),
        (one_source, "lists 1 source(s)"),
        (label_off, "ai_generated_label must be true"),
    ],
)
def test_validate_catches_broken_metadata(
    ctx: AppContext, change: Callable[[dict], None], problem: str
) -> None:
    data = metadata.load(ctx, assembled_item(ctx))
    change(data)

    assert any(problem in p for p in metadata.validate(data))


def test_metadata_schema_converts_to_a_gemini_schema() -> None:
    from google import genai
    from google.genai import _transformers

    client = genai.Client(api_key="offline-check")._api_client  # no network call is made
    assert "youtube_title" in _transformers.t_schema(client, metadata.PostMetadata).properties


# --- Gate 2 ------------------------------------------------------------------------------------


def test_approve_needs_the_checklist(ctx: AppContext) -> None:
    content_id = assembled_item(ctx)

    with pytest.raises(FactoryError, match="needs the checklist confirmed"):
        review.approve_final(ctx, content_id, checklist_ok=False)
    review.approve_final(ctx, content_id, checklist_ok=True)

    assert status(ctx, content_id) == "approved"
    gate, decision = ctx.conn.execute("SELECT gate, decision FROM reviews").fetchone()
    assert (gate, decision) == ("final", "approve")


def test_approve_refuses_incomplete_metadata(ctx: AppContext) -> None:
    content_id = assembled_item(ctx)
    data = metadata.load(ctx, content_id)
    data["youtube"]["description"] = "no sources"
    metadata.write(ctx, content_id, data)

    with pytest.raises(FactoryError, match="metadata.json is not complete"):
        review.approve_final(ctx, content_id, checklist_ok=True)
    assert status(ctx, content_id) == "assembled"


def test_reject_and_redo_need_reasons(ctx: AppContext) -> None:
    content_id = assembled_item(ctx)

    with pytest.raises(FactoryError, match="reason is required"):
        review.reject_final(ctx, content_id, "  ")
    with pytest.raises(FactoryError, match="reason is required"):
        review.redo_final(ctx, content_id, "")
    review.reject_final(ctx, content_id, "Cover shows a melted part")

    assert status(ctx, content_id) == "rejected"


def test_redo_sends_back_to_generated_and_removes_renders(ctx: AppContext) -> None:
    content_id = assembled_item(ctx)
    folder = asset_dir(ctx, content_id)

    review.redo_final(ctx, content_id, "Shot 2 has a glitch; I replaced the clip")

    reason = ctx.conn.execute("SELECT status_reason FROM contents").fetchone()[0]
    assert status(ctx, content_id) == "generated"
    assert reason == "Gate 2 redo: Shot 2 has a glitch; I replaced the clip"
    for name in ("render_youtube.mp4", "render_tiktok.mp4", "preview_720p.mp4", "qa_report.json"):
        assert not (folder / name).exists()
    assert (folder / "metadata.json").exists()  # kept; edit it at Gate 2 if needed


def test_only_assembled_items_reach_gate_2(ctx: AppContext) -> None:
    content_id = add_content(ctx.conn, ctx.channel, object_name="zipper", fmt="short",
                             pillar="air_and_sea")  # fmt: skip

    with pytest.raises(FactoryError, match="not waiting for Gate 2"):
        review.approve_final(ctx, content_id, checklist_ok=True)


def test_cli_gate_2_flags(config_dir: Path) -> None:
    root = config_dir.parent
    context = make_ctx(root)
    content_id = assembled_item(context)
    context.conn.close()
    runner = CliRunner()
    opts = ["--config-dir", str(config_dir)]

    approve = ["review", "--gate", "final", "--id", str(content_id), "--decision", "approve"]

    refused = runner.invoke(cli.app, [*approve, *opts])
    approved = runner.invoke(cli.app, [*approve, "--checklist-ok", *opts])

    assert refused.exit_code == 1 and "checklist" in refused.output
    assert approved.exit_code == 0, approved.output
    assert "approved: ready to publish" in approved.output
