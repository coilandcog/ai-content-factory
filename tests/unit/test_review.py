from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import httpx
import pytest
from fakes.fake_gemini import LLM_FIXTURES, FakeGenaiClient, fixture_reply, reply
from typer.testing import CliRunner

from factory import cli
from factory.config import REPO_ROOT, EnvSettings, load_channel, load_settings
from factory.context import AppContext
from factory.db import add_content, connect, migrate, upsert_channel
from factory.errors import FactoryError
from factory.integrations.gemini import GeminiClient
from factory.stages import review
from factory.stages import script as script_stage

DRAFT_NOTE = (
    "As an engineer, what surprises me is how much power such a small part makes "
    "from a rubber belt."
)
MY_NOTE = "As an engineer, I like that the regulator holds 14 volts whether you idle or cruise."
WIKI = "https://en.wikipedia.org/wiki/Alternator"


def web(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "w/api.php" in url:
        return httpx.Response(200, json=["alternator", ["Alternator"], [""], [WIKI]])
    if "page/summary" in url:
        body = {"type": "standard", "title": "Alternator", "extract": "An alternator ...",
                "content_urls": {"desktop": {"page": WIKI}}}  # fmt: skip
        return httpx.Response(200, json=body)
    return httpx.Response(200, text="ok")


def http() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(web))


def llm(*outcomes: object) -> tuple[GeminiClient, FakeGenaiClient]:
    sdk = FakeGenaiClient(*outcomes)
    return GeminiClient("fake", "gemini-3.5-flash-lite", sdk=sdk, retry_wait_s=0), sdk


def scripted_item(ctx: AppContext) -> int:
    content_id = add_content(
        ctx.conn, ctx.channel, object_name="alternator", fmt="short", pillar="machines"
    )
    client, _ = llm(fixture_reply("script_short_alternator"))
    script_stage.run(ctx, llm=client, http=http())
    return content_id


def make_ctx(db_path: Path) -> AppContext:
    conn = connect(db_path)
    migrate(conn)
    channel = load_channel("coil-and-cog")
    upsert_channel(conn, channel)
    env = EnvSettings(dry_run=True, factory_channel="coil-and-cog", wikimedia_user_agent="Test/0.1")
    return AppContext(load_settings(), env, channel, conn, REPO_ROOT)


@pytest.fixture
def ctx(tmp_path: Path) -> AppContext:
    context = make_ctx(tmp_path / "test.db")
    yield context
    context.conn.close()


def status(ctx: AppContext, content_id: int) -> sqlite3.Row:
    return ctx.conn.execute("SELECT * FROM contents WHERE id = ?", (content_id,)).fetchone()


def reviews(ctx: AppContext) -> list[tuple[str, str | None]]:
    rows = ctx.conn.execute("SELECT decision, reason FROM reviews ORDER BY id").fetchall()
    return [(r["decision"], r["reason"]) for r in rows]


# --- the decisions -------------------------------------------------------------------------


def test_approve_puts_the_owners_note_into_the_narration(ctx: AppContext) -> None:
    content_id = scripted_item(ctx)

    approval = review.approve(ctx, content_id, MY_NOTE)

    script = review.current_script(ctx.conn, content_id)
    assert script["engineer_note"] == MY_NOTE
    assert MY_NOTE in script["body_text"] and DRAFT_NOTE not in script["body_text"]
    assert script["body_text"].endswith("why your battery stays alive.")  # loop line kept
    assert (
        script["word_count"] == approval.word_count == script_stage.word_count(script["body_text"])
    )
    assert approval.warning is None
    assert status(ctx, content_id)["status"] == "script_approved"
    assert reviews(ctx) == [("approve", None)]


def test_approve_warns_when_the_note_breaks_the_word_range(ctx: AppContext) -> None:
    content_id = scripted_item(ctx)

    approval = review.approve(ctx, content_id, "As an engineer, " + "really " * 40 + "neat.")

    assert approval.warning is not None and "target 80-133" in approval.warning
    assert status(ctx, content_id)["status"] == "script_approved"  # the owner decides


def test_empty_note_or_reason_is_refused(ctx: AppContext) -> None:
    content_id = scripted_item(ctx)

    with pytest.raises(FactoryError, match="must not be empty"):
        review.approve(ctx, content_id, "   ")
    with pytest.raises(FactoryError, match="reason is required to reject"):
        review.reject(ctx, content_id, " ")
    with pytest.raises(FactoryError, match="reason is required to redo"):
        review.redo(ctx, content_id, "")
    assert status(ctx, content_id)["status"] == "scripted"
    assert reviews(ctx) == []


def test_reject_stores_the_reason(ctx: AppContext) -> None:
    content_id = scripted_item(ctx)

    review.reject(ctx, content_id, "duplicate topic")

    row = status(ctx, content_id)
    assert (row["status"], row["status_reason"]) == ("rejected", "duplicate topic")
    assert reviews(ctx) == [("reject", "duplicate topic")]


def test_redo_writes_version_2_and_keeps_version_1(ctx: AppContext) -> None:
    content_id = scripted_item(ctx)
    v1_text = review.current_script(ctx.conn, content_id)["body_text"]
    answer = json.loads((LLM_FIXTURES / "script_short_alternator.json").read_text())
    answer["hook"] = "What happens if your alternator stops spinning?"
    answer["hook_type"] = "what_if"
    answer["sources"][2]["url"] = "https://www.example.org/regulators"
    client, sdk = llm(reply(json.dumps(answer)))

    review.redo(ctx, content_id, "mention the voltage regulator", llm=client, http=http())

    versions = ctx.conn.execute(
        "SELECT version, is_current, hook FROM scripts WHERE content_id = ? ORDER BY version",
        (content_id,),
    ).fetchall()
    assert [(v["version"], v["is_current"]) for v in versions] == [(1, 0), (2, 1)]
    assert versions[1]["hook"].startswith("What happens if")
    assert status(ctx, content_id)["status"] == "scripted"  # back to Gate 1 with v2
    assert reviews(ctx) == [("redo", "mention the voltage regulator")]
    prompt = sdk.calls[0]["contents"]
    assert "mention the voltage regulator" in prompt and v1_text in prompt
    urls = [
        r["url"]
        for r in ctx.conn.execute("SELECT url FROM sources WHERE content_id = ?", (content_id,))
    ]
    assert "https://www.example.org/regulators" in urls and len(urls) == 3  # v1's set replaced


def test_failed_redo_keeps_version_1_current(ctx: AppContext) -> None:
    content_id = scripted_item(ctx)
    client, _ = llm(reply("{}"), reply("{}"), reply("{}"))

    with pytest.raises(FactoryError, match="redo saved, but the new version failed"):
        review.redo(ctx, content_id, "shorter hook", llm=client, http=http())

    assert review.current_script(ctx.conn, content_id)["version"] == 1
    assert status(ctx, content_id)["status"] == "scripted"
    assert reviews(ctx) == [("redo", "shorter hook")]


def test_only_scripted_items_can_be_reviewed(ctx: AppContext) -> None:
    content_id = add_content(
        ctx.conn, ctx.channel, object_name="zipper", fmt="short", pillar="everyday_things"
    )

    with pytest.raises(FactoryError, match="is 'idea', not waiting for Gate 1"):
        review.approve(ctx, content_id, MY_NOTE)


# --- the CLI -------------------------------------------------------------------------------


@pytest.fixture
def cli_db(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A temp repo whose database holds one scripted item (#1); real Gemini/web are faked."""
    runner = CliRunner()
    runner.invoke(cli.app, ["db", "init", "--config-dir", str(config_dir)])
    shutil.copytree(REPO_ROOT / "prompts", config_dir.parent / "prompts")  # redo renders prompts
    tmp_ctx = make_ctx(config_dir.parent / "data" / "factory.db")
    scripted_item(tmp_ctx)
    tmp_ctx.conn.close()
    monkeypatch.setattr(script_stage, "_http_client", lambda ctx: http())
    return config_dir


def run_cli(config_dir: Path, *args: str, input: str | None = None):  # noqa: ANN201
    return CliRunner().invoke(
        cli.app, ["review", "--gate", "script", *args, "--config-dir", str(config_dir)], input=input
    )


def db_status(config_dir: Path) -> str:
    conn = connect(config_dir.parent / "data" / "factory.db")
    value = conn.execute("SELECT status FROM contents WHERE id = 1").fetchone()[0]
    conn.close()
    return value


def test_cli_flags_approve_and_reject(cli_db: Path) -> None:
    missing = run_cli(cli_db, "--id", "1", "--decision", "approve")
    assert missing.exit_code == 1 and "--note" in missing.output

    done = run_cli(cli_db, "--id", "1", "--decision", "approve", "--note", MY_NOTE)
    assert done.exit_code == 0, done.output
    assert "#1 approved" in done.output
    assert db_status(cli_db) == "script_approved"

    again = run_cli(cli_db, "--id", "1", "--decision", "reject", "--reason", "x")
    assert again.exit_code == 1 and "not waiting for Gate 1" in again.output


def test_cli_interactive_approve_uses_the_editor(
    cli_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_edit_note", lambda draft: MY_NOTE)

    result = run_cli(cli_db, input="a\n")

    assert result.exit_code == 0, result.output
    assert "HOOK:" in result.output and "#1 approved" in result.output
    assert db_status(cli_db) == "script_approved"


def test_cli_interactive_unchanged_note_needs_confirmation(
    cli_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_edit_note", lambda draft: draft)

    result = run_cli(cli_db, input="a\nn\nq\n")  # approve, refuse the draft, then quit

    assert "Keep the AI draft as your Engineer's Note?" in result.output
    assert db_status(cli_db) == "scripted"


def test_cli_interactive_reject_and_redo(cli_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    answer = json.loads((LLM_FIXTURES / "script_short_alternator.json").read_text())
    answer["hook_type"] = "what_if"
    client, _ = llm(reply(json.dumps(answer)))
    monkeypatch.setattr(script_stage, "_gemini", lambda ctx: client)

    result = run_cli(cli_db, input="d\nmention the regulator\nr\nduplicate topic\n")

    assert result.exit_code == 0, result.output
    assert "Script v2" in result.output  # the new version is shown before the next choice
    assert "#1 rejected." in result.output
    assert db_status(cli_db) == "rejected"


def test_cli_gate_final_is_not_built_yet(cli_db: Path) -> None:
    result = CliRunner().invoke(cli.app, ["review", "--gate", "final", "--config-dir", str(cli_db)])

    assert result.exit_code == 1
    assert "ROADMAP task 1.11" in result.output
