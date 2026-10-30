"""Lessons from failed clip reviews (ROADMAP 2.18, D-103): the factory remembers its mistakes.

`learn` collects every failed review in the items' clip_review.json files that hasn't been learned
from yet, and asks Gemini for short, general lessons (not about one machine). They start as
'pending'; only lessons the owner approves go into the film rules (new prompts) and into the clip
review's check list, so the same mistake is avoided and caught on every later video.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import ClassVar, Literal

from pydantic import BaseModel, Field, create_model, model_validator

from factory.context import AppContext
from factory.errors import FactoryError
from factory.integrations.gemini import GeminiClient
from factory.prompts import render_prompt

log = logging.getLogger("factory.lessons")

MAX_WORDS = 30
PROMPT_LIMIT = 15  # newest approved lessons in the prompts, so they stay short


class Lesson(BaseModel):
    text: str = Field(description=f"one general rule for future prompts, at most {MAX_WORDS} words")
    applies_to: Literal["clip", "frame", "both"] = Field(
        description="clip = motion/camera problems, frame = picture problems, both = either"
    )
    evidence: list[int] = Field(description="numbers of the failures this lesson comes from")


class LessonBatch(BaseModel):
    lessons: list[Lesson]

    failures: ClassVar[int] = 0  # set per call by _batch_schema(): how many failures were shown

    @model_validator(mode="after")
    def _usable(self) -> LessonBatch:
        problems = []
        for i, lesson in enumerate(self.lessons, 1):
            if len(lesson.text.split()) > MAX_WORDS:
                problems.append(f"lesson {i} has more than {MAX_WORDS} words")
            if not lesson.evidence or any(not 1 <= n <= self.failures for n in lesson.evidence):
                problems.append(f"lesson {i}: evidence must be failure numbers 1-{self.failures}")
        if problems:
            raise ValueError("; ".join(problems))
        return self


def _batch_schema(failures: int) -> type[LessonBatch]:
    model = create_model("LessonBatch_n", __base__=LessonBatch)
    model.failures = failures
    return model


def unlearned_failures(ctx: AppContext) -> list[dict]:
    """Every failed review not learned from yet, oldest first, with a stable key."""
    seen = {
        r[0]
        for r in ctx.conn.execute(
            "SELECT review_key FROM lesson_inputs WHERE channel_id = ?", (_channel_id(ctx),)
        )
    }
    # Imported here: script and clip_review import this module for approved_for_prompts().
    from factory.stages.clip_review import REPORT_FILE

    base = ctx.root / ctx.settings.paths.assets_dir / ctx.channel.slug
    failures = []
    for report in sorted(base.glob(f"*/{REPORT_FILE}")):
        for entry in json.loads(report.read_text()):
            key = f"{report.parent.name}/{entry['file']}/{entry['reviewed_at']}"
            if entry["verdict"] == "fail" and key not in seen:
                failures.append({"key": key, "item": int(report.parent.name), **entry})
    return sorted(failures, key=lambda f: f["reviewed_at"])


def learn(ctx: AppContext, *, llm: GeminiClient | None = None) -> list[int]:
    """Propose lessons from the new failures; returns the new lesson ids (pending)."""
    failures = unlearned_failures(ctx)
    if not failures:
        raise FactoryError(
            "no new failed reviews to learn from (`factory clips review` makes them)"
        )
    known = ctx.conn.execute(
        "SELECT text, status FROM lessons WHERE channel_id = ? ORDER BY id", (_channel_id(ctx),)
    ).fetchall()
    prompts_root = ctx.root / ctx.settings.paths.prompts_dir
    system = render_prompt("clip_review_system", ctx.channel, prompts_root=prompts_root).text
    user = render_prompt(
        "lessons_learn", ctx.channel, prompts_root=prompts_root,
        failures=failures, known=known, max_words=MAX_WORDS,
    )  # fmt: skip
    if llm is None:
        from factory.stages.script import gemini_client  # see unlearned_failures()

        llm = gemini_client(ctx)
    batch = llm.generate_json(
        system=system, prompt=user.text, schema=_batch_schema(len(failures)), max_reasks=2
    )
    channel_id = _channel_id(ctx)
    ids = []
    with ctx.conn:
        for lesson in batch.lessons:
            evidence = [failures[n - 1]["key"] for n in sorted(set(lesson.evidence))]
            ids.append(
                ctx.conn.execute(
                    "INSERT INTO lessons (channel_id, text, applies_to, evidence_json, model) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (channel_id, lesson.text.strip(), lesson.applies_to, json.dumps(evidence),
                     llm.model),
                ).lastrowid
            )  # fmt: skip
        ctx.conn.executemany(
            "INSERT OR IGNORE INTO lesson_inputs (channel_id, review_key) VALUES (?, ?)",
            [(channel_id, f["key"]) for f in failures],
        )
    log.info("%d lesson(s) proposed from %d failure(s)", len(ids), len(failures),
             extra={"event": "lessons_proposed"})  # fmt: skip
    return ids


def listing(ctx: AppContext, *, include_removed: bool = False) -> list[sqlite3.Row]:
    return ctx.conn.execute(
        f"""
        SELECT * FROM lessons WHERE channel_id = ?
        {"" if include_removed else "AND status <> 'removed'"} ORDER BY id
        """,
        (_channel_id(ctx),),
    ).fetchall()


def decide(ctx: AppContext, lesson_id: int, status: str, text: str | None = None) -> None:
    """Approve (optionally with your own wording) or remove a lesson."""
    row = ctx.conn.execute(
        "SELECT id FROM lessons WHERE id = ? AND channel_id = ?", (lesson_id, _channel_id(ctx))
    ).fetchone()
    if row is None:
        raise FactoryError(f"lesson #{lesson_id} does not exist")
    with ctx.conn:
        ctx.conn.execute(
            """
            UPDATE lessons SET status = ?, text = COALESCE(?, text),
                decided_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
            WHERE id = ?
            """,
            (status, text.strip() if text and text.strip() else None, lesson_id),
        )


def approved_for_prompts(ctx: AppContext, applies_to: str | None = None) -> list[str]:
    """The newest approved lessons, oldest first; `applies_to` keeps those for a clip or frame."""
    try:
        rows = ctx.conn.execute(
            """
            SELECT text, applies_to FROM lessons WHERE status = 'approved' AND channel_id =
                (SELECT id FROM channels WHERE slug = ?)
            ORDER BY id DESC LIMIT ?
            """,
            (ctx.channel.slug, PROMPT_LIMIT),
        ).fetchall()
    except sqlite3.OperationalError as exc:  # database from before migration 004
        raise FactoryError("run `uv run factory db init` to add the lessons table") from exc
    wanted = {"clip", "frame", "both"} if applies_to is None else {applies_to, "both"}
    return [r["text"] for r in reversed(rows) if r["applies_to"] in wanted]


def _channel_id(ctx: AppContext) -> int:
    row = ctx.conn.execute("SELECT id FROM channels WHERE slug = ?", (ctx.channel.slug,)).fetchone()
    if row is None:
        raise FactoryError(
            f"channel {ctx.channel.slug} is not in the database: run factory db init"
        )
    return row[0]
