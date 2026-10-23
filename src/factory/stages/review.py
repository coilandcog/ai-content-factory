"""Gate 1 (script review): approve, reject or redo, each stored as a `reviews` row.

See ARCHITECTURE.md §3. The CLI (and later the Telegram bot, task 2.1) call these functions;
each decision writes its review row and the status change in one transaction.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

import httpx

from factory.context import AppContext
from factory.db import set_status
from factory.errors import FactoryError
from factory.integrations.gemini import GeminiClient
from factory.stages import script as script_stage

log = logging.getLogger("factory.review")


@dataclass(frozen=True)
class Approval:
    word_count: int
    est_duration_s: float
    warning: str | None  # set when the edited narration left the format's word range


def pending(ctx: AppContext) -> list[sqlite3.Row]:
    """The channel's items waiting for Gate 1, oldest first."""
    return ctx.conn.execute(
        """
        SELECT c.* FROM contents c JOIN channels ch ON ch.id = c.channel_id
        WHERE ch.slug = ? AND c.status = 'scripted' ORDER BY c.id
        """,
        (ctx.channel.slug,),
    ).fetchall()


def current_script(conn: sqlite3.Connection, content_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM scripts WHERE content_id = ? AND is_current = 1", (content_id,)
    ).fetchone()
    if row is None:
        raise FactoryError(f"content #{content_id} has no script")
    return row


def approve(ctx: AppContext, content_id: int, note: str) -> Approval:
    """Store the owner's Engineer's Note in the script and narration, then approve.

    The note is our main answer to YouTube's inauthentic-content policy, so the CLI makes sure it
    was written or knowingly kept by the owner before calling this.
    """
    item = _scripted_item(ctx, content_id)
    note = " ".join(note.split())
    if not note:
        raise FactoryError("the Engineer's Note must not be empty")
    script = current_script(ctx.conn, content_id)
    narration = _replace_note(script["body_text"], script["engineer_note"] or "", note)
    words = script_stage.word_count(narration)
    est = round(words / ctx.channel.voice.target_wpm * 60, 1)
    low, high = script_stage.word_range(ctx.channel, item["format"])
    warning = None if low <= words <= high else f"narration has {words} words; target {low}-{high}"
    with ctx.conn:
        ctx.conn.execute(
            """
            UPDATE scripts SET engineer_note = ?, body_text = ?, word_count = ?, est_duration_s = ?
            WHERE id = ?
            """,
            (note, narration, words, est, script["id"]),
        )
        _add_review(ctx.conn, content_id, "approve", None)
        set_status(ctx.conn, content_id, "script_approved")  # last: its commit covers all above
    log.info("#%s approved at Gate 1", content_id, extra=_extra(content_id, "gate1_approved"))
    return Approval(words, est, warning)


def reject(ctx: AppContext, content_id: int, reason: str) -> None:
    """Reject with a reason; the learn stage reads reasons later."""
    _scripted_item(ctx, content_id)
    reason = _required(reason, "reject")
    with ctx.conn:
        _add_review(ctx.conn, content_id, "reject", reason)
        set_status(ctx.conn, content_id, "rejected", reason=reason)
    log.info("#%s rejected: %s", content_id, reason, extra=_extra(content_id, "gate1_rejected"))


def redo(
    ctx: AppContext,
    content_id: int,
    reason: str,
    *,
    llm: GeminiClient | None = None,
    http: httpx.Client | None = None,
) -> int:
    """Record the redo request, then write the next script version from the owner's feedback.

    The review row is kept even if generation fails; the previous version then stays current.
    """
    _scripted_item(ctx, content_id)
    reason = _required(reason, "redo")
    with ctx.conn:
        _add_review(ctx.conn, content_id, "redo", reason)
    log.info("#%s redo: %s", content_id, reason, extra=_extra(content_id, "gate1_redo"))
    try:
        return script_stage.rescript(ctx, content_id, reason, llm=llm, http=http)
    except FactoryError as exc:
        raise FactoryError(f"redo saved, but the new version failed: {exc}") from exc


def _scripted_item(ctx: AppContext, content_id: int) -> sqlite3.Row:
    row = ctx.conn.execute(
        """
        SELECT c.* FROM contents c JOIN channels ch ON ch.id = c.channel_id
        WHERE c.id = ? AND ch.slug = ?
        """,
        (content_id, ctx.channel.slug),
    ).fetchone()
    if row is None:
        raise FactoryError(f"content #{content_id} does not exist for {ctx.channel.slug}")
    if row["status"] != "scripted":
        raise FactoryError(f"content #{content_id} is '{row['status']}', not waiting for Gate 1")
    return row


def _replace_note(narration: str, old: str, new: str) -> str:
    # The narration is hook + body + note + loop line; the note is its last-but-one part, so the
    # last occurrence is the one to replace.
    head, found, tail = narration.rpartition(old) if old else ("", "", "")
    if not found:
        raise FactoryError("could not find the Engineer's Note draft inside the narration")
    return f"{head}{new}{tail}"


def _required(reason: str, decision: str) -> str:
    reason = " ".join(reason.split())
    if not reason:
        raise FactoryError(f"a reason is required to {decision}")
    return reason


def _add_review(
    conn: sqlite3.Connection, content_id: int, decision: str, reason: str | None
) -> None:
    conn.execute(
        "INSERT INTO reviews (content_id, gate, decision, reason, channel) "
        "VALUES (?, 'script', ?, ?, 'cli')",
        (content_id, decision, reason),
    )


def _extra(content_id: int, event: str) -> dict[str, object]:
    return {"content_id": content_id, "event": event}
