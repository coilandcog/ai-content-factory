from __future__ import annotations

import json
import shutil
from pathlib import Path

import httpx
import pytest
from fakes.fake_gemini import LLM_FIXTURES, FakeGenaiClient, fixture_reply, reply
from fakes.fake_telegram import OWNER, STRANGER, TOKEN, FakeTelegram
from typer.testing import CliRunner

from factory import cli
from factory.config import REPO_ROOT, EnvSettings, load_channel, load_settings
from factory.context import AppContext
from factory.db import (
    add_content,
    connect,
    finish_run,
    migrate,
    set_status,
    start_run,
    upsert_channel,
)
from factory.errors import RetryableError
from factory.integrations.gemini import GeminiClient
from factory.integrations.http_json import HttpStatusError
from factory.integrations.telegram_bot import TelegramClient
from factory.stages import bot as bot_stage
from factory.stages import metadata, review
from factory.stages import script as script_stage
from factory.stages.voice import asset_dir

WIKI = "https://en.wikipedia.org/wiki/Alternator"
MY_NOTE = "I like that the regulator holds 14 volts whether you idle or cruise."


def web(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "w/api.php" in url:
        return httpx.Response(200, json=["alternator", ["Alternator"], [""], [WIKI]])
    if "page/summary" in url:
        body = {"type": "standard", "title": "Alternator", "extract": "An alternator ...",
                "content_urls": {"desktop": {"page": WIKI}}}  # fmt: skip
        return httpx.Response(200, json=body)
    return httpx.Response(200, text="ok")


def web_client() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(web))


def llm(*outcomes: object) -> GeminiClient:
    return GeminiClient("fake", "gemini-3.5-flash-lite", sdk=FakeGenaiClient(*outcomes),
                        retry_wait_s=0)  # fmt: skip


@pytest.fixture
def ctx(tmp_path: Path) -> AppContext:
    shutil.copytree(REPO_ROOT / "prompts", tmp_path / "prompts")
    conn = connect(tmp_path / "data" / "factory.db")
    migrate(conn)
    channel = load_channel("coil-and-cog").model_copy(
        update={"video_style": "flow"}
    )  # Flow scripts
    upsert_channel(conn, channel)
    env = EnvSettings(dry_run=True, factory_channel="coil-and-cog",
                      wikimedia_user_agent="Test/0.1 (https://example.org)")  # fmt: skip
    context = AppContext(load_settings(), env, channel, conn, tmp_path)
    yield context
    conn.close()


@pytest.fixture
def tg() -> FakeTelegram:
    return FakeTelegram()


def make_bot(ctx: AppContext, tg: FakeTelegram, *outcomes: object) -> bot_stage.ReviewBot:
    return bot_stage.ReviewBot(ctx, tg.client(), OWNER, llm=llm(*outcomes), http=web_client())


def scripted_item(ctx: AppContext) -> int:
    content_id = add_content(ctx.conn, ctx.channel, object_name="alternator", fmt="short",
                             pillar="vehicles")  # fmt: skip
    script_stage.run(ctx, llm=llm(fixture_reply("script_short_alternator")), http=web_client())
    return content_id


def assembled_item(ctx: AppContext) -> int:
    content_id = add_content(ctx.conn, ctx.channel, object_name="alternator", fmt="short",
                             pillar="vehicles")  # fmt: skip
    with ctx.conn:
        ctx.conn.execute(
            "INSERT INTO scripts (content_id, version, body_text, shot_list_json) "
            "VALUES (?, 1, 'The alternator charges your battery.', '[]')",
            (content_id,),
        )
        for url in (WIKI, "https://example.edu/alternators"):
            ctx.conn.execute(
                "INSERT INTO sources (content_id, url, title, publisher) VALUES (?, ?, 'T', 'P')",
                (content_id, url),
            )
    for status in ("scripted", "script_approved", "awaiting_clips", "generated", "assembled"):
        set_status(ctx.conn, content_id, status)
    folder = asset_dir(ctx, content_id)
    folder.mkdir(parents=True)
    for name in ("render_youtube.mp4", "render_tiktok.mp4", "preview_720p.mp4", "qa_report.json"):
        (folder / name).write_text("[]" if name.endswith(".json") else "stand-in")
    metadata.ensure_metadata(ctx, content_id, llm=llm(fixture_reply("metadata_alternator")))
    return content_id


def status(ctx: AppContext, content_id: int) -> str:
    return ctx.conn.execute("SELECT status FROM contents WHERE id = ?", (content_id,)).fetchone()[0]


def reviews(ctx: AppContext) -> list[tuple]:
    rows = ctx.conn.execute(
        "SELECT gate, decision, reason, channel, telegram_message_id FROM reviews ORDER BY id"
    ).fetchall()
    return [tuple(r) for r in rows]


# --- the client ---------------------------------------------------------------------------------


def test_send_message_with_buttons_returns_the_message_id(tg: FakeTelegram) -> None:
    message_id = tg.client().send_message(OWNER, "hello", [[("Yes", "approve:script:1")]])

    assert message_id == 100
    body = tg.sent()[0]
    assert body["chat_id"] == OWNER and body["text"] == "hello"
    assert body["reply_markup"] == {
        "inline_keyboard": [[{"text": "Yes", "callback_data": "approve:script:1"}]]
    }


def test_429_waits_as_told_then_succeeds(tg: FakeTelegram) -> None:
    tg.failures.append(httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 0}}))

    assert tg.client().send_message(OWNER, "hi") == 100


def test_server_errors_give_up_after_four_attempts(tg: FakeTelegram) -> None:
    tg.failures.extend(httpx.Response(502) for _ in range(4))

    with pytest.raises(RetryableError, match="sendMessage: HTTP 502"):
        tg.client().send_message(OWNER, "hi")


def test_errors_show_telegrams_reason_but_never_the_token(tg: FakeTelegram) -> None:
    tg.failures.append(
        httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"})
    )

    with pytest.raises(HttpStatusError) as caught:
        tg.client().get_updates(None, 0)

    assert "Unauthorized: check TELEGRAM_BOT_TOKEN" in str(caught.value)
    assert TOKEN not in str(caught.value) and "api.telegram.org" not in str(caught.value)


def test_network_errors_never_show_the_url() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}")

    client = TelegramClient(
        TOKEN, http=httpx.Client(transport=httpx.MockTransport(down)), retry_wait_s=0
    )
    with pytest.raises(RetryableError) as caught:
        client.send_message(OWNER, "hi")
    assert str(caught.value) == "telegram sendMessage: ConnectError"


def test_split_text_keeps_parts_under_the_limit_and_cuts_at_line_ends() -> None:
    text = "\n".join(f"line {n:02d} " + "x" * 30 for n in range(10))

    parts = bot_stage.split_text(text, 100)

    assert all(len(p) <= 100 for p in parts)
    assert "\n".join(parts) == text
    assert bot_stage.split_text("y" * 250, 100) == ["y" * 100, "y" * 100, "y" * 50]


# --- who may talk to the bot ---------------------------------------------------------------------


def test_other_chats_are_ignored(ctx: AppContext, tg: FakeTelegram) -> None:
    content_id = scripted_item(ctx)
    bot = make_bot(ctx, tg)
    bot.start()
    tg.clear()

    tg.type("hello", chat_id=STRANGER)
    tg.press(f"approve:script:{content_id}", 100, chat_id=STRANGER)
    tg.press(f"reject:script:{content_id}", 100, chat_id=STRANGER)
    tg.type("spam", chat_id=STRANGER)
    bot.poll_once(0)

    assert [name for name, _ in tg.calls] == ["getUpdates"]  # no reply, no button answer
    assert status(ctx, content_id) == "scripted" and reviews(ctx) == []


# --- Gate 1 -------------------------------------------------------------------------------------


def test_start_shows_waiting_scripts_with_buttons_in_dry_run(
    ctx: AppContext, tg: FakeTelegram
) -> None:
    content_id = scripted_item(ctx)

    make_bot(ctx, tg).start()

    assert tg.texts[0].startswith("[DRY RUN] Bot running. Waiting for Gate 1: 1, Gate 2: 0.")
    shown = tg.message_with(f"approve:script:{content_id}")
    assert shown["text"].startswith("[DRY RUN] GATE 1: script review")
    assert "NARRATION:" in shown["text"] and "SOURCES:" in shown["text"]


def test_approve_asks_for_the_note_and_stores_mine(ctx: AppContext, tg: FakeTelegram) -> None:
    content_id = scripted_item(ctx)
    bot = make_bot(ctx, tg)
    bot.start()
    review_message = tg.message_with(f"approve:script:{content_id}")["message_id"]

    tg.press(f"approve:script:{content_id}", review_message)
    bot.poll_once(0)
    draft = review.current_script(ctx.conn, content_id)["engineer_note"]
    assert tg.message_with(f"keep:script:{content_id}")["text"] == f"[DRY RUN] {draft}"
    assert status(ctx, content_id) == "scripted"  # nothing decided yet

    tg.type(MY_NOTE)
    bot.poll_once(0)

    assert status(ctx, content_id) == "script_approved"
    assert review.current_script(ctx.conn, content_id)["engineer_note"] == MY_NOTE
    assert reviews(ctx) == [("script", "approve", None, "telegram", review_message)]
    removed = tg.sent("editMessageReplyMarkup")
    assert removed[-1]["message_id"] == review_message
    assert "approved" in tg.texts[-1]
    assert bot.decisions == 1


def test_keep_draft_button_approves_with_the_ai_draft(ctx: AppContext, tg: FakeTelegram) -> None:
    content_id = scripted_item(ctx)
    draft = review.current_script(ctx.conn, content_id)["engineer_note"]
    bot = make_bot(ctx, tg)
    bot.start()
    review_message = tg.message_with(f"approve:script:{content_id}")["message_id"]
    tg.press(f"approve:script:{content_id}", review_message)
    bot.poll_once(0)

    keep = tg.message_with(f"keep:script:{content_id}")
    tg.press(f"keep:script:{content_id}", keep["message_id"])
    bot.poll_once(0)

    assert status(ctx, content_id) == "script_approved"
    assert review.current_script(ctx.conn, content_id)["engineer_note"] == draft
    assert reviews(ctx)[0][4] == review_message  # stored against the review message


def test_reject_needs_a_reason_and_cancel_changes_nothing(
    ctx: AppContext, tg: FakeTelegram
) -> None:
    content_id = scripted_item(ctx)
    bot = make_bot(ctx, tg)
    bot.start()

    tg.press(f"reject:script:{content_id}", 101)
    tg.type("/cancel")
    bot.poll_once(0)
    assert status(ctx, content_id) == "scripted"
    tg.type("too long")  # no question open any more: not taken as a reason
    bot.poll_once(0)
    assert status(ctx, content_id) == "scripted"

    tg.press(f"reject:script:{content_id}", 101)
    tg.type("the hook repeats last week's video")
    bot.poll_once(0)

    assert status(ctx, content_id) == "rejected"
    assert reviews(ctx) == [
        ("script", "reject", "the hook repeats last week's video", "telegram", 101)
    ]


def test_redo_writes_version_2_and_shows_it(ctx: AppContext, tg: FakeTelegram) -> None:
    content_id = scripted_item(ctx)
    answer = json.loads((LLM_FIXTURES / "script_short_alternator.json").read_text())
    answer["hook"] = "What happens if your alternator stops spinning?"
    answer["hook_type"] = "what_if"
    bot = make_bot(ctx, tg, reply(json.dumps(answer)))
    bot.start()
    tg.clear()

    tg.press(f"redo:script:{content_id}", 101)
    tg.type("mention the voltage regulator")
    bot.poll_once(0)

    assert review.current_script(ctx.conn, content_id)["version"] == 2
    assert reviews(ctx) == [("script", "redo", "mention the voltage regulator", "telegram", 101)]
    new = tg.message_with(f"approve:script:{content_id}")
    assert "What happens if your alternator stops spinning?" in new["text"]


def test_an_old_button_on_a_decided_item_does_nothing(ctx: AppContext, tg: FakeTelegram) -> None:
    content_id = scripted_item(ctx)
    bot = make_bot(ctx, tg)
    bot.start()
    review.reject(ctx, content_id, "decided in the terminal")

    tg.press(f"approve:script:{content_id}", 101)
    bot.poll_once(0)

    assert "is 'rejected' now" in tg.texts[-1]
    assert tg.sent("editMessageReplyMarkup")[-1]["message_id"] == 101
    assert len(reviews(ctx)) == 1


# --- Gate 2 -------------------------------------------------------------------------------------


def test_gate2_sends_the_preview_then_approves_only_after_the_checklist(
    ctx: AppContext, tg: FakeTelegram
) -> None:
    content_id = assembled_item(ctx)
    bot = make_bot(ctx, tg)
    bot.start()

    assert len(tg.sent("sendVideo")) == 1
    shown = tg.message_with(f"approve:final:{content_id}")
    assert "GATE 2: final review" in shown["text"] and "CHECKLIST" in shown["text"]
    assert "YOUTUBE TITLE:" in shown["text"]

    tg.press(f"approve:final:{content_id}", shown["message_id"])
    bot.poll_once(0)
    assert status(ctx, content_id) == "assembled"  # one tap never approves Gate 2
    confirm = tg.message_with(f"confirm:final:{content_id}")
    assert review.FINAL_CHECKLIST[0] in confirm["text"]

    tg.press(f"confirm:final:{content_id}", confirm["message_id"])
    bot.poll_once(0)

    assert status(ctx, content_id) == "approved"
    assert reviews(ctx) == [("final", "approve", None, "telegram", shown["message_id"])]


def test_gate2_redo_sends_the_item_back_to_generated(ctx: AppContext, tg: FakeTelegram) -> None:
    content_id = assembled_item(ctx)
    bot = make_bot(ctx, tg)
    bot.start()

    tg.press(f"redo:final:{content_id}", 105)
    tg.type("shot 3 has a melting pulley")
    bot.poll_once(0)

    assert status(ctx, content_id) == "generated"
    assert not (asset_dir(ctx, content_id) / "preview_720p.mp4").exists()
    assert "uv run factory assemble" in tg.texts[-1]


def test_pending_command_shows_items_again(ctx: AppContext, tg: FakeTelegram) -> None:
    content_id = scripted_item(ctx)
    bot = make_bot(ctx, tg)
    bot.start()
    tg.clear()

    bot.poll_once(0)
    assert tg.sent() == []  # shown once per session, not every loop

    tg.type("/pending")
    bot.poll_once(0)
    assert tg.message_with(f"approve:script:{content_id}")


# --- alerts -------------------------------------------------------------------------------------


def test_alerts_only_for_failures_after_the_start_and_only_once(
    ctx: AppContext, tg: FakeTelegram
) -> None:
    old = add_content(ctx.conn, ctx.channel, object_name="zipper", fmt="short", pillar="vehicles")
    set_status(ctx.conn, old, "failed", reason="old failure")
    bot = make_bot(ctx, tg)
    bot.start()
    assert not any("⚠️" in t for t in tg.texts)

    run_id = start_run(ctx.conn, "script", "coil-and-cog", True)
    finish_run(ctx.conn, run_id, "failed", error="gemini: 60 used + 1 requested would exceed")
    new = add_content(ctx.conn, ctx.channel, object_name="crane", fmt="short", pillar="vehicles")
    set_status(ctx.conn, new, "failed", reason="Gemini down")
    bot.poll_once(0)
    bot.poll_once(0)

    alerts = [t for t in tg.texts if "⚠️" in t]
    assert len(alerts) == 2
    assert f"Run #{run_id} 'script' failed: gemini: 60 used" in alerts[0]
    assert f"#{new} crane failed while 'idea': Gemini down" in alerts[1]


# --- the command ---------------------------------------------------------------------------------


def test_bot_command_needs_the_token(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    assert runner.invoke(cli.app, ["db", "init", "--config-dir", str(config_dir)]).exit_code == 0
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    result = runner.invoke(cli.app, ["bot", "--config-dir", str(config_dir)])

    assert result.exit_code == 1
    assert "TELEGRAM_BOT_TOKEN is missing" in result.output
