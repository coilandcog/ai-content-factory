from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from fakes.fake_gemini import LLM_FIXTURES, FakeGenaiClient, fixture_reply, reply
from typer.testing import CliRunner

from factory.budgets import check_and_record, usage_today
from factory.cli import app
from factory.config import REPO_ROOT, EnvSettings, load_channel, load_settings
from factory.context import AppContext
from factory.db import add_content, connect, migrate, upsert_channel
from factory.errors import BudgetExceededError, ConfigError
from factory.integrations.gemini import GeminiClient
from factory.stages import script as stage

WIKI_URL = "https://en.wikipedia.org/wiki/Alternator"
SECOND_SOURCE = "https://www.example-university.edu/ee/alternator-basics"
THIRD_SOURCE = "https://www.example-standards.org/charging-systems"
EXTRACT = "An alternator is an electrical generator that converts mechanical energy to AC."


def wiki_and_sources(dead: set[str] = frozenset(), found: bool = True) -> Callable:
    """httpx MockTransport handler: Wikipedia search + summary, and the cited pages."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        assert request.headers["User-Agent"].startswith("CoilAndCogTest")
        if "w/api.php" in url:
            titles = ["Alternator"] if found else []
            return httpx.Response(200, json=["alternator", titles, [""], [WIKI_URL]])
        if "rest_v1/page/summary" in url:
            body = {
                "type": "standard",
                "title": "Alternator",
                "extract": EXTRACT,
                "content_urls": {"desktop": {"page": WIKI_URL}},
            }
            return httpx.Response(200, json=body)
        return httpx.Response(404 if url in dead else 200, text="<html>ok</html>")

    return handler


@pytest.fixture
def ctx(tmp_path: Path) -> AppContext:
    conn = connect(tmp_path / "test.db")
    migrate(conn)
    channel = load_channel("coil-and-cog")
    upsert_channel(conn, channel)
    env = EnvSettings(
        dry_run=True, factory_channel="coil-and-cog", wikimedia_user_agent="CoilAndCogTest/0.1"
    )
    context = AppContext(load_settings(), env, channel, conn, REPO_ROOT)  # real prompt templates
    yield context
    conn.close()


def llm_for(ctx: AppContext, sdk: FakeGenaiClient) -> GeminiClient:
    return GeminiClient(
        "fake-key",
        "gemini-3.5-flash-lite",
        sdk=sdk,
        retry_wait_s=0,
        before_call=lambda: check_and_record(ctx.conn, ctx.channel, "gemini"),
    )


def http_for(handler: Callable) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(handler), headers={"User-Agent": "CoilAndCogTest/0.1"}
    )


def new_idea(ctx: AppContext, name: str = "alternator") -> int:
    return add_content(ctx.conn, ctx.channel, object_name=name, fmt="short", pillar="machines")


def content(ctx: AppContext, content_id: int) -> sqlite3.Row:
    return ctx.conn.execute("SELECT * FROM contents WHERE id = ?", (content_id,)).fetchone()


def run_stage(ctx: AppContext, sdk: FakeGenaiClient, handler: Callable | None = None):  # noqa: ANN201
    return stage.run(ctx, llm=llm_for(ctx, sdk), http=http_for(handler or wiki_and_sources()))


def test_idea_becomes_scripted_with_script_sources_and_variety_fields(ctx: AppContext) -> None:
    content_id = new_idea(ctx)
    sdk = FakeGenaiClient(fixture_reply("script_short_alternator"))

    result = run_stage(ctx, sdk)

    row = content(ctx, content_id)
    assert (result.processed, result.failed) == (1, 0)
    assert row["status"] == "scripted"
    assert (row["hook_type"], row["beat_order"]) == ("why_question", "classic")
    assert row["working_title"].startswith("Why your battery")
    script = ctx.conn.execute(
        "SELECT * FROM scripts WHERE content_id = ?", (content_id,)
    ).fetchone()
    assert (script["version"], script["is_current"]) == (1, 1)
    assert 80 <= script["word_count"] <= 133
    assert script["body_text"].startswith("Why doesn't your car battery")
    assert script["body_text"].endswith("why your battery stays alive.")
    assert "+" in script["prompt_version"]
    shots = json.loads(script["shot_list_json"])
    assert shots[3]["prompt_vars"] == {
        "energy": "electric",
        "path": "the stator coils into the diode plate",
        "process": "alternating current becoming direct current",
    }
    assert shots[2]["prompt_vars"] == {}  # diagrams carry no Flow prompt fields
    urls = [r["url"] for r in ctx.conn.execute("SELECT url FROM sources ORDER BY id")]
    assert urls == [WIKI_URL, SECOND_SOURCE, THIRD_SOURCE]
    usage = usage_today(ctx.conn, ctx.channel)
    assert usage["gemini"][0] == 1 and usage["wikimedia"][0] == 2


def test_prompt_contains_reference_rules_and_blocklist(ctx: AppContext) -> None:
    new_idea(ctx)
    sdk = FakeGenaiClient(fixture_reply("script_short_alternator"))

    run_stage(ctx, sdk)

    [call] = sdk.calls
    assert EXTRACT in call["contents"]
    assert f"list {WIKI_URL} as the first source" in call["contents"]
    assert "between 80 and 133 words" in call["contents"]
    assert "defeating safety or emissions devices" in call["config"].system_instruction


def test_next_script_cannot_repeat_hook_or_beat_order(ctx: AppContext) -> None:
    new_idea(ctx)
    new_idea(ctx, "zipper")
    sdk = FakeGenaiClient(
        fixture_reply("script_short_alternator"), fixture_reply("script_short_alternator")
    )

    result = run_stage(ctx, sdk)

    second_schema = sdk.calls[1]["config"].response_schema
    assert "why_question" not in second_schema.allowed_hooks
    assert "classic" not in second_schema.allowed_beats
    # The canned answer repeats them, so the second item can't validate even after the re-ask.
    assert (result.processed, result.failed) == (1, 1)
    assert content(ctx, 2)["status"] == "failed"


def test_blocked_topic_is_archived(ctx: AppContext) -> None:
    content_id = new_idea(ctx, "emissions defeat device")

    result = run_stage(ctx, FakeGenaiClient(fixture_reply("script_blocked")))

    row = content(ctx, content_id)
    assert result.processed == 1
    assert row["status"] == "archived"
    assert row["status_reason"].startswith("blocked: Explains how to bypass")
    assert ctx.conn.execute("SELECT COUNT(*) FROM scripts").fetchone()[0] == 0


def test_rule_break_is_fixed_by_the_reask(ctx: AppContext) -> None:
    content_id = new_idea(ctx)
    too_short = json.loads((LLM_FIXTURES / "script_short_alternator.json").read_text())
    too_short["body"] = "It charges the battery."
    sdk = FakeGenaiClient(reply(json.dumps(too_short)), fixture_reply("script_short_alternator"))

    run_stage(ctx, sdk)

    assert content(ctx, content_id)["status"] == "scripted"
    assert "narration has" in sdk.calls[1]["contents"]  # the re-ask names the broken rule


def test_unreachable_sources_fail_the_item_but_not_the_batch(ctx: AppContext) -> None:
    first, second = new_idea(ctx), new_idea(ctx, "turbocharger")
    answer = json.loads((LLM_FIXTURES / "script_short_alternator.json").read_text())
    dead_a, dead_b = "https://invented.example/a", "https://invented.example/b"
    answer_a = json.loads(json.dumps(answer))  # deep copies; both are changed below
    answer_a["sources"][1]["url"], answer_a["sources"][2]["url"] = dead_a, dead_b
    answer_b = json.loads(json.dumps(answer))
    answer_b |= {"hook_type": "what_if", "beat_order": "context_first"}
    shots = answer_b["shots"]
    answer_b["shots"] = [shots[5], *shots[:5], shots[6]]  # in_situ first for context_first
    for i, shot in enumerate(answer_b["shots"], start=1):
        shot["n"] = i
    sdk = FakeGenaiClient(reply(json.dumps(answer_a)), reply(json.dumps(answer_b)))

    result = run_stage(ctx, sdk, wiki_and_sources(dead={dead_a, dead_b}))

    assert (result.processed, result.failed) == (1, 1)
    assert content(ctx, first)["status"] == "failed"
    assert "only 1 of 3 sources could be opened" in content(ctx, first)["status_reason"]
    assert content(ctx, second)["status"] == "scripted"


def test_one_dead_source_is_dropped_and_the_rest_kept(ctx: AppContext) -> None:
    content_id = new_idea(ctx)
    sdk = FakeGenaiClient(fixture_reply("script_short_alternator"))

    run_stage(ctx, sdk, wiki_and_sources(dead={THIRD_SOURCE}))

    assert content(ctx, content_id)["status"] == "scripted"
    urls = [r["url"] for r in ctx.conn.execute("SELECT url FROM sources ORDER BY id")]
    assert urls == [WIKI_URL, SECOND_SOURCE]


def test_works_without_a_wikipedia_article(ctx: AppContext) -> None:
    content_id = new_idea(ctx)
    sdk = FakeGenaiClient(fixture_reply("script_short_alternator"))

    run_stage(ctx, sdk, wiki_and_sources(found=False))

    assert content(ctx, content_id)["status"] == "scripted"
    assert "No reference text is available" in sdk.calls[0]["contents"]


def test_budget_exhaustion_stops_the_stage_before_calling(ctx: AppContext) -> None:
    content_id = new_idea(ctx)
    check_and_record(ctx.conn, ctx.channel, "gemini", 60)  # today's whole budget
    sdk = FakeGenaiClient(fixture_reply("script_short_alternator"))

    with pytest.raises(BudgetExceededError):
        run_stage(ctx, sdk)

    assert sdk.calls == []
    assert content(ctx, content_id)["status"] == "idea"  # untouched, retried tomorrow


def test_missing_settings_are_clear_errors(ctx: AppContext) -> None:
    new_idea(ctx)
    no_agent = AppContext(
        ctx.settings,
        EnvSettings(dry_run=True, factory_channel="coil-and-cog"),
        ctx.channel,
        ctx.conn,
        ctx.root,
    )
    with pytest.raises(ConfigError, match="GEMINI_API_KEY is missing"):
        stage.run(no_agent)
    with pytest.raises(ConfigError, match="WIKIMEDIA_USER_AGENT is missing"):
        stage.run(no_agent, llm=llm_for(ctx, FakeGenaiClient()))


def test_show_script_prints_every_part(config_dir: Path) -> None:
    runner = CliRunner()
    opts = ["--config-dir", str(config_dir)]
    runner.invoke(app, ["db", "init", *opts])
    runner.invoke(
        app, ["new", "--object", "alternator", "--format", "short", "--pillar", "machines", *opts]
    )
    conn = connect(config_dir.parent / "data" / "factory.db")
    env = EnvSettings(
        dry_run=True, factory_channel="coil-and-cog", wikimedia_user_agent="CoilAndCogTest/0.1"
    )
    tmp_ctx = AppContext(load_settings(), env, load_channel("coil-and-cog"), conn, REPO_ROOT)
    run_stage(tmp_ctx, FakeGenaiClient(fixture_reply("script_short_alternator")))
    conn.close()

    shown = runner.invoke(app, ["status", "1", "--show", "script", *opts])

    assert shown.exit_code == 0, shown.output
    for text in [
        "status: scripted",
        "hook: why_question | beats: classic",
        "HOOK:  Why doesn't your car battery go flat",
        "ENGINEER'S NOTE (draft, yours to edit at Gate 1):  As an engineer",
        " 4. flow     flow_clip  8.0 s",
        "energy=electric",
        f"- {WIKI_URL} (Wikipedia)",
    ]:
        assert text in shown.output


@pytest.mark.parametrize("fmt", ["short", "deep_dive"])
def test_schema_converts_to_a_gemini_schema(fmt: str) -> None:
    """The fake skips the SDK's schema conversion, so check it offline here: Gemini rejects some
    JSON-schema rules (e.g. exclusiveMinimum from Field(gt=...)) before a request is even sent."""
    from google import genai
    from google.genai import _transformers

    model = stage.script_schema(
        load_channel("coil-and-cog"), fmt, previous=(None, None), reference_url=None
    )
    sdk_client = genai.Client(api_key="offline-check")._api_client  # no network call is made

    converted = _transformers.t_schema(sdk_client, model)

    assert "shots" in converted.properties


def test_flow_clip_longer_than_8_s_is_capped() -> None:
    shot = stage.Shot(
        n=1, beat="in_situ", kind="flow_clip", description="Engine bay, belt spinning",
        duration_s=10, context="a car engine bay",
    )  # fmt: skip

    assert shot.duration_s == 8.0


def test_trailing_ellipsis_loop_line_is_corrected(ctx: AppContext) -> None:
    content_id = new_idea(ctx)
    dangling = json.loads((LLM_FIXTURES / "script_short_alternator.json").read_text())
    dangling["loop_line"] = "That is why your battery stays charged, because..."
    sdk = FakeGenaiClient(reply(json.dumps(dangling)), fixture_reply("script_short_alternator"))

    run_stage(ctx, sdk)

    assert content(ctx, content_id)["status"] == "scripted"
    assert "loop_line must be a complete sentence" in sdk.calls[1]["contents"]
