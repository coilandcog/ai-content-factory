"""Gate 1 (script) and Gate 2 (final video) reviews: approve, reject or redo, each stored as a
`reviews` row.

See ARCHITECTURE.md §3. The CLI and the Telegram bot (task 2.1) call these functions;
each decision writes its review row and the status change in one transaction. Gate 2 is never
skipped: approving needs the owner's confirmed checklist (D-047, D-071).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import textwrap
from dataclasses import dataclass

import httpx

from factory.context import AppContext
from factory.db import set_status
from factory.errors import FactoryError
from factory.integrations.gemini import GeminiClient
from factory.stages import episode_script, metadata
from factory.stages import precheck as precheck_stage
from factory.stages import scenes as scenes_stage
from factory.stages import script as script_stage
from factory.stages.voice import asset_dir

log = logging.getLogger("factory.review")


@dataclass(frozen=True)
class Via:
    """Where a decision was made, stored in reviews.channel / telegram_message_id."""

    channel: str = "cli"  # cli | telegram
    message_id: int | None = None  # the Telegram review message the buttons were on


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


def approve(
    ctx: AppContext, content_id: int, note: str, *, via: Via | None = None, anyway: bool = False
) -> Approval:
    """Store the owner's Engineer's Note in the script and narration, then approve.

    The note is our main answer to YouTube's inauthentic-content policy, so the CLI makes sure it
    was written or knowingly kept by the owner before calling this. The final narration is
    pre-checked first (D-118): open problems raise PrecheckError unless `anyway`; then they are
    kept as the review's reason.
    """
    item = _scripted_item(ctx, content_id)
    note = " ".join(note.split())
    if not note:
        raise FactoryError("the Engineer's Note must not be empty")
    script = current_script(ctx.conn, content_id)
    narration = _replace_note(script["body_text"], script["engineer_note"] or "", note)
    plan = json.loads(script["shot_list_json"])
    if episode_script.is_episode_plan(plan):  # the note is also its own scene (D-129)
        plan = [{**s, "narration": note} if s["template"] == "note" else s for s in plan]
    problems = precheck_stage.check(ctx, content_id, narration).problems
    if problems and not anyway:
        raise precheck_stage.PrecheckError(content_id, problems)
    reason = "approved anyway: " + "; ".join(problems) if problems else None
    words = script_stage.word_count(narration)
    est = round(words / ctx.channel.voice.target_wpm * 60, 1)
    low, high = script_stage.word_range(ctx.channel, item["format"])
    warning = None if low <= words <= high else f"narration has {words} words; target {low}-{high}"
    with ctx.conn:
        ctx.conn.execute(
            """
            UPDATE scripts SET engineer_note = ?, body_text = ?, word_count = ?, est_duration_s = ?,
                shot_list_json = ?
            WHERE id = ?
            """,
            (note, narration, words, est, json.dumps(plan, ensure_ascii=False), script["id"]),
        )
        _add_review(ctx.conn, content_id, "approve", reason, via=via)
        set_status(ctx.conn, content_id, "script_approved")  # last: its commit covers all above
    log.info("#%s approved at Gate 1", content_id, extra=_extra(content_id, "gate1_approved"))
    return Approval(words, est, warning)


def precheck_report(ctx: AppContext, content_id: int) -> str:
    """The pre-check of the current script (D-118), shown above it at Gate 1."""
    return precheck_stage.check(ctx, content_id).report()


def reject(ctx: AppContext, content_id: int, reason: str, *, via: Via | None = None) -> None:
    """Reject with a reason; the learn stage reads reasons later."""
    _scripted_item(ctx, content_id)
    reason = _required(reason, "reject")
    with ctx.conn:
        _add_review(ctx.conn, content_id, "reject", reason, via=via)
        set_status(ctx.conn, content_id, "rejected", reason=reason)
    log.info("#%s rejected: %s", content_id, reason, extra=_extra(content_id, "gate1_rejected"))


def redo(
    ctx: AppContext,
    content_id: int,
    reason: str,
    *,
    llm: GeminiClient | None = None,
    http: httpx.Client | None = None,
    via: Via | None = None,
) -> int:
    """Record the redo request, then write the next script version from the owner's feedback.

    The review row is kept even if generation fails; the previous version then stays current.
    """
    _scripted_item(ctx, content_id)
    reason = _required(reason, "redo")
    with ctx.conn:
        _add_review(ctx.conn, content_id, "redo", reason, via=via)
    log.info("#%s redo: %s", content_id, reason, extra=_extra(content_id, "gate1_redo"))
    try:
        return script_stage.rescript(ctx, content_id, reason, llm=llm, http=http)
    except FactoryError as exc:
        raise FactoryError(f"redo saved, but the new version failed: {exc}") from exc


# --- Gate 2: the final video -------------------------------------------------------------------

# TESTING.md §4, human part: what the owner confirms after watching the preview.
FINAL_CHECKLIST = (
    "The hook works with the sound OFF (the first frame is visually interesting).",
    "Facts match the sources; nothing on screen contradicts the narration.",
    "The Engineer's Note is present and sounds like me.",
    "No visual glitches: melting parts, wrong physics, stray text or logos, hands or faces.",
    "The loop line flows back into the hook.",
    "Title and cover are honest.",
    "I will switch on the AI-generated label when posting on TikTok.",
)
# Files a Gate 2 redo removes, so the next `factory assemble` renders and checks again.
REDO_REMOVES = {
    "render_youtube": "render_youtube.mp4",
    "render_tiktok": "render_tiktok.mp4",
    "preview": "preview_720p.mp4",
}


def pending_final(ctx: AppContext) -> list[sqlite3.Row]:
    """The channel's items waiting for Gate 2 (QA passed), oldest first."""
    return ctx.conn.execute(
        """
        SELECT c.* FROM contents c JOIN channels ch ON ch.id = c.channel_id
        WHERE ch.slug = ? AND c.status = 'assembled' ORDER BY c.id
        """,
        (ctx.channel.slug,),
    ).fetchall()


def approve_final(
    ctx: AppContext, content_id: int, *, checklist_ok: bool, via: Via | None = None
) -> None:
    """Approve the final video. Needs the confirmed checklist and complete metadata."""
    _item_in(ctx, content_id, "assembled", "Gate 2")
    if not checklist_ok:
        raise FactoryError("Gate 2 approval needs the checklist confirmed (watch the preview)")
    problems = metadata.validate(metadata.load(ctx, content_id))
    if problems:
        raise FactoryError("metadata.json is not complete: " + "; ".join(problems))
    with ctx.conn:
        _add_review(ctx.conn, content_id, "approve", None, gate="final", via=via)
        set_status(ctx.conn, content_id, "approved")
    log.info("#%s approved at Gate 2", content_id, extra=_extra(content_id, "gate2_approved"))


def reject_final(ctx: AppContext, content_id: int, reason: str, *, via: Via | None = None) -> None:
    _item_in(ctx, content_id, "assembled", "Gate 2")
    reason = _required(reason, "reject")
    with ctx.conn:
        _add_review(ctx.conn, content_id, "reject", reason, gate="final", via=via)
        set_status(ctx.conn, content_id, "rejected", reason=reason)
    log.info("#%s rejected at Gate 2: %s", content_id, reason,
             extra=_extra(content_id, "gate2_rejected"))  # fmt: skip


def redo_final(ctx: AppContext, content_id: int, reason: str, *, via: Via | None = None) -> None:
    """Send the item back to `generated` and remove its renders, so the next `factory assemble`
    renders and checks again once the owner has fixed things (e.g. replaced a clip)."""
    _item_in(ctx, content_id, "assembled", "Gate 2")
    reason = _required(reason, "redo")
    folder = asset_dir(ctx, content_id)
    with ctx.conn:
        _add_review(ctx.conn, content_id, "redo", reason, gate="final", via=via)
        for kind, name in REDO_REMOVES.items():
            ctx.conn.execute(
                "DELETE FROM assets WHERE content_id = ? AND kind = ?", (content_id, kind)
            )
            (folder / name).unlink(missing_ok=True)
        (folder / "qa_report.json").unlink(missing_ok=True)
        set_status(ctx.conn, content_id, "generated", reason=f"Gate 2 redo: {reason}")
    log.info("#%s redo at Gate 2: %s", content_id, reason, extra=_extra(content_id, "gate2_redo"))


# --- plain-text views (terminal and Telegram show the same text) ---------------------------


def content_report(conn: sqlite3.Connection, content_id: int) -> str:
    row = conn.execute(
        """
        SELECT c.*, ch.slug FROM contents c JOIN channels ch ON ch.id = c.channel_id
        WHERE c.id = ?
        """,
        (content_id,),
    ).fetchone()
    if row is None:
        raise FactoryError(f"content #{content_id} does not exist")
    lines = [
        f"Content #{row['id']} ({row['slug']})",
        f"  object: {row['object_name']} | format: {row['format']} | pillar: {row['pillar']}"
        f" | style: {row['visual_style']}"
        + (f" | series #{row['series_id']} part {row['part_n']}" if row["series_id"] else ""),
        f"  status: {row['status']}",
    ]
    if row["status_before_failure"]:
        lines.append(f"  failed while: {row['status_before_failure']}")
    if row["status_reason"]:
        lines.append(f"  reason: {row['status_reason']}")
    if row["virality_score"] is not None:
        lines.append(f"  score: {row['virality_score']} | angle: {row['angle']}")
    if row["planned_publish_at"]:
        lines.append(f"  planned: {row['planned_publish_at']} (UTC)")
    if row["working_title"]:
        lines.append(f"  title: {row['working_title']}")
        lines.append(f"  hook: {row['hook_type']} | beats: {row['beat_order']}")
    lines.append(f"  created: {row['created_at']} | updated: {row['updated_at']}")
    return "\n".join(lines)


def script_report(conn: sqlite3.Connection, content_id: int) -> str:
    """The current script as plain text: narration parts, shot list and sources (for Gate 1)."""
    script = conn.execute(
        "SELECT * FROM scripts WHERE content_id = ? AND is_current = 1", (content_id,)
    ).fetchone()
    if script is None:
        raise FactoryError(f"content #{content_id} has no script yet: run `factory script`")
    shots = json.loads(script["shot_list_json"])
    item_status = conn.execute("SELECT status FROM contents WHERE id = ?", (content_id,)).fetchone()
    note_label = {
        "scripted": "ENGINEER'S NOTE (draft, yours to edit at Gate 1)",
        "rejected": "ENGINEER'S NOTE (draft, not approved)",
        "archived": "ENGINEER'S NOTE (draft, not approved)",
    }.get(item_status["status"], "ENGINEER'S NOTE (approved by you)")
    sources = conn.execute(
        "SELECT url, publisher, claim FROM sources WHERE content_id = ? ORDER BY id", (content_id,)
    ).fetchall()
    similar = script["similarity_max"]
    lines = [
        "",
        f"Script v{script['version']} | {script['word_count']} words | "
        f"~{script['est_duration_s']:.0f} s | {script['llm_model']} | "
        f"prompt {script['prompt_version']} | similarity "
        + ("not checked" if similar is None else f"{similar:.2f}"),
        "",
        f"HOOK:  {script['hook']}",
        "",
        "NARRATION:",
        textwrap.fill(script["body_text"], width=96, initial_indent="  ", subsequent_indent="  "),
        "",
        f"{note_label}:  {script['engineer_note']}",
        f"LOOP LINE:  {script['loop_line']}",
        "",
    ]
    if episode_script.is_episode_plan(shots):  # a long video: chapters of scenes (D-129)
        lines += ["CHAPTERS (each scene's template is the view of the 3D model it plays over):"]
        lines += episode_script.chapters_report(shots)
        lines += ["", "SOURCES:"]
        lines += [f"  - {s['url']} ({s['publisher']}): {s['claim']}" for s in sources]
        return "\n".join(lines)
    # The film's hero_look is the same in every clip (D-092): show it once.
    looks = [s["prompt_vars"]["hero_look"] for s in shots if "hero_look" in s["prompt_vars"]]
    if looks:
        lines += ["LOOK:", textwrap.fill(looks[0], width=96, initial_indent="  ",
                                         subsequent_indent="  "), ""]  # fmt: skip
    lines.append("SHOTS:")
    for shot in shots:
        extra = ", ".join(f"{k}={v}" for k, v in shot["prompt_vars"].items() if k != "hero_look")
        if shot.get("labels"):  # diagram label cards (task 1.8): check them before approving
            extra = f"labels: {', '.join(shot['labels'])}; dots: {shot.get('energy') or 'electric'}"
        lines.append(
            f"  {shot['n']:>2}. {shot['beat']:<8} {shot['kind']:<9} {shot['duration_s']:>4.1f} s  "
            f"{shot['description']}" + (f"  [{extra}]" if extra else "")
        )
    lines += ["", "SOURCES:"]
    lines += [f"  - {s['url']} ({s['publisher']}): {s['claim']}" for s in sources]
    return "\n".join(lines)


def final_report(ctx: AppContext, content_id: int) -> str:
    """The Gate 2 text: item summary, titles, caption, description, music and the QA result."""
    data = metadata.load(ctx, content_id)
    qa_path = asset_dir(ctx, content_id) / "qa_report.json"
    qa_lines = []
    if qa_path.is_file():
        for check in json.loads(qa_path.read_text()):
            mark = {True: "PASS", False: "FAIL", None: "----"}[check["ok"]]
            qa_lines.append(f"  {mark} {check['check']}: {check['detail']}")
    music = data.get("music") or {}
    lines = [
        content_report(ctx.conn, content_id),
        "",
        f"YOUTUBE TITLE:  {data['youtube']['title']}",
        "YOUTUBE DESCRIPTION:",
        textwrap.indent(data["youtube"]["description"], "  "),
        "",
        f"TIKTOK CAPTION:  {data['tiktok']['caption']}",
        f"MUSIC (YouTube only):  {music.get('title', '-')} by {music.get('artist') or '?'}",
        "",
        *scenes_stage.summary(ctx.conn, content_id),  # Blender films only (D-124)
        "AUTOMATED QA:",
        *(qa_lines or ["  (no qa_report.json)"]),
    ]
    return "\n".join(lines)


def _item_in(ctx: AppContext, content_id: int, status: str, gate: str) -> sqlite3.Row:
    row = ctx.conn.execute(
        """
        SELECT c.* FROM contents c JOIN channels ch ON ch.id = c.channel_id
        WHERE c.id = ? AND ch.slug = ?
        """,
        (content_id, ctx.channel.slug),
    ).fetchone()
    if row is None:
        raise FactoryError(f"content #{content_id} does not exist for {ctx.channel.slug}")
    if row["status"] != status:
        raise FactoryError(f"content #{content_id} is '{row['status']}', not waiting for {gate}")
    return row


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
    conn: sqlite3.Connection,
    content_id: int,
    decision: str,
    reason: str | None,
    gate: str = "script",
    via: Via | None = None,
) -> None:
    via = via or Via()
    conn.execute(
        "INSERT INTO reviews (content_id, gate, decision, reason, channel, telegram_message_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (content_id, gate, decision, reason, via.channel, via.message_id),
    )


def _extra(content_id: int, event: str) -> dict[str, object]:
    return {"content_id": content_id, "event": event}
