"""The Telegram review bot: `factory bot` (ROADMAP 2.1, ARCHITECTURE.md §3).

Gate 1 and Gate 2 from the phone. Every button ends in the same `review` functions the terminal
uses, so the rules (reason required, Engineer's Note, Gate 2 checklist) can't drift apart. Only
TELEGRAM_ALLOWED_CHAT_ID is listened to; anything from another chat is ignored without a reply.

What the bot remembers lives in memory only (which items it has shown, which reply it waits for).
After a restart it shows everything still waiting again; a button on an old message still works
as long as its item is still waiting, because each press checks the item's status first.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from factory.context import AppContext
from factory.errors import FactoryError, RetryableError
from factory.integrations.gemini import GeminiClient
from factory.integrations.telegram_bot import MAX_TEXT, Buttons, TelegramClient
from factory.media.qa import PREVIEW_MAX_MB
from factory.stages import metadata, review
from factory.stages.precheck import PrecheckError
from factory.stages.review import Via
from factory.stages.voice import asset_dir

log = logging.getLogger("factory.bot")

POLL_TIMEOUT_S = 30  # how long one getUpdates waits; also how often new items are looked for
RECONNECT_WAIT_S = 60  # after Telegram was unreachable through all retries
ALERT_ERROR_CHARS = 500
WAITING_STATUS = {"script": "scripted", "final": "assembled"}

HELP = (
    "Coil & Cog review bot.\n"
    "Items waiting for Gate 1 (script) or Gate 2 (final video) arrive here with buttons.\n"
    "/pending  show everything waiting again\n"
    "/cancel   stop answering a question (the item keeps waiting)\n"
    "/help     this text"
)


@dataclass(frozen=True)
class Waiting:
    """What the owner's next message (or confirm button) answers."""

    action: str  # note | reject | redo | checklist | anyway
    gate: str  # script | final
    content_id: int
    message_id: int  # the review message whose button started this
    note: str = ""  # anyway: the note the pre-check found problems with (D-118)


class ReviewBot:
    def __init__(
        self,
        ctx: AppContext,
        client: TelegramClient,
        chat_id: int,
        *,
        llm: GeminiClient | None = None,
        http: httpx.Client | None = None,
    ) -> None:
        self.ctx = ctx
        self.client = client
        self.chat_id = chat_id
        self.llm = llm  # None: the review functions make real clients (tests pass fakes)
        self.http = http
        self.offset: int | None = None
        self.waiting: Waiting | None = None
        self.shown: set[tuple[str, int]] = set()
        self.decisions = 0
        self.alerted_runs: set[int] = set()
        self.alerted_items: set[int] = set()
        self.first_run_id = 0

    # --- the loop ---------------------------------------------------------------------------

    def run_forever(self) -> None:
        self.start()
        while True:
            try:
                self.poll_once()
            except RetryableError as exc:  # Telegram unreachable even after the retries
                log.warning("telegram unreachable (%s); trying again in %d s", exc,
                            RECONNECT_WAIT_S, extra={"event": "bot_offline"})  # fmt: skip
                time.sleep(RECONNECT_WAIT_S)

    def start(self) -> None:
        """Alert only about failures from now on (no flood of old ones), then say hello."""
        conn = self.ctx.conn
        self.first_run_id = (
            self.ctx.run_id or conn.execute("SELECT COALESCE(MAX(id), 0) FROM runs").fetchone()[0]
        )
        self.alerted_items = {row["id"] for row in self._failed_items()}
        gate1, gate2 = len(review.pending(self.ctx)), len(review.pending_final(self.ctx))
        self.say(f"Bot running. Waiting for Gate 1: {gate1}, Gate 2: {gate2}. /help for commands.")
        log.info("bot started", extra={"event": "bot_started"})
        self.tick()

    def poll_once(self, timeout_s: int = POLL_TIMEOUT_S) -> None:
        for update in self.client.get_updates(self.offset, timeout_s):
            self.offset = update["update_id"] + 1  # confirms it: Telegram won't send it again
            self.handle(update)
        self.tick()

    def tick(self) -> None:
        self.show_pending()
        self.send_alerts()

    # --- incoming updates -------------------------------------------------------------------

    def handle(self, update: dict[str, Any]) -> None:
        if "callback_query" in update:
            press = update["callback_query"]
            message = press.get("message") or {}
            if (message.get("chat") or {}).get("id") != self.chat_id:
                self._ignore(press.get("from", {}).get("id"))
                return
            self.client.answer_callback(press["id"])
            self._guarded(self.on_button, press.get("data", ""), message["message_id"])
        elif "message" in update:
            message = update["message"]
            if (message.get("chat") or {}).get("id") != self.chat_id:
                self._ignore((message.get("chat") or {}).get("id"))
                return
            self._guarded(self.on_text, (message.get("text") or "").strip())

    def on_button(self, data: str, message_id: int) -> None:
        action, _, rest = data.partition(":")
        gate, _, raw_id = rest.partition(":")
        if gate not in WAITING_STATUS or not raw_id.isdigit():
            self.say("Unknown button.")
            return
        content_id = int(raw_id)
        if action == "cancel":
            self.waiting = None
            self.say(f"Cancelled. #{content_id} is still waiting.")
            return
        if action == "skip":
            self.say(f"Skipped #{content_id}. /pending shows it again.")
            return
        if not self._still_waiting(gate, content_id, message_id):
            return
        origin = self._origin(gate, content_id, message_id)
        if action == "approve" and gate == "script":
            self._ask_note(content_id, message_id)
        elif action == "approve":
            self._ask_checklist(content_id, message_id)
        elif action == "anyway" and gate == "script":
            waiting = self.waiting
            if waiting is None or waiting.action != "anyway" or waiting.content_id != content_id:
                self.say(
                    f"Nothing to approve for #{content_id}: press Approve on its review again."
                )
                return
            self._approve_script(content_id, waiting.note, origin, anyway=True)
        elif action == "keep" and gate == "script":
            draft = review.current_script(self.ctx.conn, content_id)["engineer_note"] or ""
            self._approve_script(content_id, draft, origin)
        elif action == "confirm" and gate == "final":
            via = Via("telegram", origin)
            review.approve_final(self.ctx, content_id, checklist_ok=True, via=via)
            publish = f"On the PC: uv run factory publish --manual --id {content_id}"
            self._decided(origin, f"#{content_id} approved: ready to publish.\n{publish}")
        elif action in ("reject", "redo"):
            self.waiting = Waiting(action, gate, content_id, message_id)
            question = "Why are you rejecting" if action == "reject" else "What must change in"
            self.say(f"{question} #{content_id}? Reply with a short reason, or /cancel.")
        elif action == "edit" and gate == "final":
            self.say(
                "Metadata is edited on the PC, where the rules are checked:\n"
                f"  uv run factory review --gate final --id {content_id}\n"
                "then choose [e]dit. Afterwards /pending shows the item here again."
            )
        else:
            self.say("Unknown button.")

    def on_text(self, text: str) -> None:
        if text.startswith("/"):
            command = text.split()[0].split("@")[0].lower()
            if command == "/cancel":
                self.waiting = None
                self.say("Cancelled. Nothing was changed.")
            elif command == "/pending":
                self.shown.clear()
                if not self.show_pending():
                    self.say("Nothing is waiting for review.")
            else:
                self.say(HELP)
            return
        waiting = self.waiting
        if waiting is None:
            self.say("I'm not waiting for an answer. Use the buttons on a review message.\n" + HELP)
            return
        if waiting.action in ("checklist", "anyway"):
            self.say("Use the buttons above, or /cancel.")
            return
        self.waiting = None
        via = Via("telegram", waiting.message_id)
        content_id = waiting.content_id
        if waiting.action == "note":
            self._approve_script(content_id, text, waiting.message_id)
        elif waiting.action == "reject":
            reject = review.reject if waiting.gate == "script" else review.reject_final
            reject(self.ctx, content_id, text, via=via)
            self._decided(waiting.message_id, f"#{content_id} rejected.")
        elif waiting.gate == "script":  # redo: the next script version, shown as a new message
            self.say(f"Writing the next version of #{content_id} with Gemini ...")
            review.redo(self.ctx, content_id, text, llm=self.llm, http=self.http, via=via)
            self._decided(waiting.message_id, f"#{content_id}: new version below.")
            self.shown.discard(("script", content_id))
        else:
            review.redo_final(self.ctx, content_id, text, via=via)
            self._decided(waiting.message_id, f"#{content_id} is back to 'generated'. Fix it on "
                          "the PC, then: uv run factory assemble")  # fmt: skip

    # --- the two gates ----------------------------------------------------------------------

    def show_pending(self) -> int:
        """Send every waiting item not shown yet in this session; returns how many were sent."""
        sent = 0
        for gate, rows, show in (
            ("script", review.pending(self.ctx), self._show_script),
            ("final", review.pending_final(self.ctx), self._show_final),
        ):
            for row in rows:
                if (gate, row["id"]) in self.shown:
                    continue
                self.shown.add((gate, row["id"]))  # first: a failure is reported once, not looped
                self._guarded(show, row["id"])
                sent += 1
        return sent

    def _show_script(self, content_id: int) -> None:
        text = (
            "GATE 1: script review\n\n"
            + review.content_report(self.ctx.conn, content_id)
            + "\n\n"
            + review.precheck_report(self.ctx, content_id)
            + "\n"
            + review.script_report(self.ctx.conn, content_id)
        )
        self.say_long(text, _gate_buttons("script", content_id))

    def _show_final(self, content_id: int) -> None:
        metadata.ensure_metadata(self.ctx, content_id, llm=self.llm)
        preview = asset_dir(self.ctx, content_id) / "preview_720p.mp4"
        if preview.is_file() and preview.stat().st_size / 1_000_000 < PREVIEW_MAX_MB:
            caption = f"{self._prefix()}#{content_id} preview (both renders share this picture)"
            self.client.send_video(self.chat_id, preview, caption=caption)
        else:
            self.say(f"#{content_id}: no preview_720p.mp4 under {PREVIEW_MAX_MB} MB; watch it on "
                     "the PC before deciding.")  # fmt: skip
        text = (
            "GATE 2: final review\n\n"
            + review.final_report(self.ctx, content_id)
            + "\n\nCHECKLIST (you confirm it when you approve):\n"
            + _checklist()
        )
        self.say_long(text, _gate_buttons("final", content_id))

    def _ask_note(self, content_id: int, message_id: int) -> None:
        draft = review.current_script(self.ctx.conn, content_id)["engineer_note"] or ""
        self.waiting = Waiting("note", "script", content_id, message_id)
        self.say(
            f"Engineer's Note for #{content_id}: reply with your own note (one or two sentences "
            "in your voice), or keep the AI draft below. /cancel to stop."
        )
        self.say(draft, [[("Keep this draft", f"keep:script:{content_id}"),
                          ("Cancel", f"cancel:script:{content_id}")]])  # fmt: skip

    def _ask_checklist(self, content_id: int, message_id: int) -> None:
        self.waiting = Waiting("checklist", "final", content_id, message_id)
        self.say(
            f"Before approving #{content_id}: did you watch the preview with sound, and does "
            f"every item hold?\n{_checklist()}",
            [[("✅ All checked: approve", f"confirm:final:{content_id}")],
             [("Cancel", f"cancel:final:{content_id}")]],
        )  # fmt: skip

    def _approve_script(
        self, content_id: int, note: str, origin: int, *, anyway: bool = False
    ) -> None:
        via = Via("telegram", origin)
        try:
            approval = review.approve(self.ctx, content_id, note, via=via, anyway=anyway)
        except PrecheckError as exc:  # D-118: the text with the note is checked once more
            self.waiting = Waiting("anyway", "script", content_id, origin, note)
            self.say(
                f"Pre-check of #{content_id} with this note found:\n"
                + "\n".join(f"! {p}" for p in exc.problems)
                + "\nApprove anyway, or Cancel and press Approve again to write another note.",
                [[("Approve anyway", f"anyway:script:{content_id}"),
                  ("Cancel", f"cancel:script:{content_id}")]],
            )  # fmt: skip
            return
        self.waiting = None
        text = (
            f"#{content_id} approved: {approval.word_count} words, "
            f"~{approval.est_duration_s:.0f} s.\nOn the PC: uv run factory generate"
        )
        if approval.warning:
            text += f"\nWARNING {approval.warning}"
        self._decided(origin, text)

    def _decided(self, origin: int, text: str) -> None:
        self.decisions += 1
        self.client.remove_buttons(self.chat_id, origin)
        self.say(text)

    def _still_waiting(self, gate: str, content_id: int, message_id: int) -> bool:
        """A button on an old message: refuse it if the item has moved on (e.g. decided in the
        terminal), and take the buttons off that message."""
        row = self.ctx.conn.execute(
            "SELECT status FROM contents WHERE id = ?", (content_id,)
        ).fetchone()
        if row is not None and row["status"] == WAITING_STATUS[gate]:
            return True
        now = row["status"] if row else "gone"
        self.client.remove_buttons(self.chat_id, message_id)
        self.say(f"#{content_id} is '{now}' now, so there is nothing to decide here.")
        return False

    def _origin(self, gate: str, content_id: int, message_id: int) -> int:
        """The review message a confirm button belongs to (the one whose buttons get removed)."""
        w = self.waiting
        if w is not None and w.gate == gate and w.content_id == content_id:
            return w.message_id
        return message_id

    # --- alerts -----------------------------------------------------------------------------

    def send_alerts(self) -> None:
        """Failed or partly failed commands (budget stops included) and newly failed items."""
        runs = self.ctx.conn.execute(
            "SELECT id, command, status, error FROM runs "
            "WHERE id > ? AND status IN ('failed', 'partial') ORDER BY id",
            (self.first_run_id,),
        ).fetchall()
        for run in runs:
            if run["id"] in self.alerted_runs:
                continue
            self.alerted_runs.add(run["id"])
            error = (run["error"] or "details in data/logs/")[:ALERT_ERROR_CHARS]
            self.say(f"⚠️ Run #{run['id']} '{run['command']}' {run['status']}: {error}")
        for item in self._failed_items():
            if item["id"] in self.alerted_items:
                continue
            self.alerted_items.add(item["id"])
            self.say(
                f"⚠️ #{item['id']} {item['object_name']} failed while "
                f"'{item['status_before_failure']}': {item['status_reason'] or '-'}\n"
                f"Details: uv run factory status {item['id']}"
            )

    def _failed_items(self) -> list[Any]:
        return self.ctx.conn.execute(
            """
            SELECT c.* FROM contents c JOIN channels ch ON ch.id = c.channel_id
            WHERE ch.slug = ? AND c.status = 'failed' ORDER BY c.id
            """,
            (self.ctx.channel.slug,),
        ).fetchall()

    # --- sending ----------------------------------------------------------------------------

    def say(self, text: str, buttons: Buttons | None = None) -> int:
        return self.client.send_message(self.chat_id, self._prefix() + text, buttons)

    def say_long(self, text: str, buttons: Buttons | None = None) -> int:
        """Text over Telegram's 4,096-character limit goes out in parts; buttons on the last."""
        parts = split_text(text, MAX_TEXT - len(self._prefix()))
        for part in parts[:-1]:
            self.say(part)
        return self.say(parts[-1], buttons)

    def _prefix(self) -> str:
        return "[DRY RUN] " if self.ctx.env.dry_run else ""

    def _guarded(self, action: Callable[..., None], *args: Any) -> None:
        """Run one handler; a refused decision is told to the owner instead of stopping the bot."""
        try:
            action(*args)
        except RetryableError:
            raise  # Telegram itself is unreachable: the loop waits and reconnects
        except FactoryError as exc:
            self.say(f"Not done: {exc}")
        except Exception as exc:  # a bug: keep the bot alive, keep the details in the log
            log.exception("bot handler crashed", extra={"event": "bot_error"})
            self.say(f"Something went wrong ({type(exc).__name__}). Details are in data/logs/.")

    def _ignore(self, chat_id: object) -> None:
        log.warning("ignored an update from chat %s (not TELEGRAM_ALLOWED_CHAT_ID)", chat_id,
                    extra={"event": "bot_ignored"})  # fmt: skip


def find_chats(client: TelegramClient, timeout_s: int = 30) -> list[tuple[int, str]]:
    """Chats that messaged the bot recently, as (chat id, first name), for `--find-chat-id`.

    Nothing is confirmed, so the bot still sees these messages later.
    """
    chats: dict[int, str] = {}
    for update in client.get_updates(None, timeout_s):
        message = update.get("message") or (update.get("callback_query") or {}).get("message")
        chat = (message or {}).get("chat") or {}
        if "id" in chat:
            chats[chat["id"]] = chat.get("first_name") or chat.get("title") or "?"
    return list(chats.items())


def split_text(text: str, limit: int) -> list[str]:
    """Cut text into parts of at most `limit` characters, at line ends where possible."""
    parts: list[str] = []
    current = ""
    for line in text.splitlines():
        while len(line) > limit:  # a single very long line: hard cut
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            parts.append(current)
            current = line
        else:
            current = candidate
    if current or not parts:
        parts.append(current)
    return parts


def _gate_buttons(gate: str, content_id: int) -> Buttons:
    key = f"{gate}:{content_id}"
    rows = [
        [("✅ Approve", f"approve:{key}"), ("❌ Reject", f"reject:{key}")],
        [("🔁 Redo", f"redo:{key}"), ("⏭ Skip", f"skip:{key}")],
    ]
    if gate == "final":
        rows[1].insert(1, ("✏️ Edit metadata", f"edit:{key}"))
    return rows


def _checklist() -> str:
    return "\n".join(f"• {item}" for item in review.FINAL_CHECKLIST)
