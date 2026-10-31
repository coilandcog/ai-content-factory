"""ideate: scored video ideas, and the weekly plan (ROADMAP 1.15, CONTENT_STRATEGY.md §8-9, D-080).

`run` asks Gemini once for about 10 ideas and scores them out of 100: Gemini judges hook,
familiarity, visual potential, evergreen demand and novelty (85 points); the code turns the real
discover signals into the trend score (15 points), because language models are unreliable with
numbers. Ideas at or above `scoring.min_virality_score` become `idea` items. Machines the channel
already has an active item for are never proposed again.

`plan_week` fills the week's slots from `cadence.weekly_plan` with the best unplanned ideas: no
pillar three days in a row, and a different hook type and beat order from the day before. The
script stage then writes exactly that hook and beat order.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, ClassVar, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, create_model, model_validator

from factory.context import AppContext
from factory.integrations.gemini import GeminiClient
from factory.prompts import render_prompt
from factory.stages import references as references_stage
from factory.stages.discover import TopicSignals, latest_signals
from factory.stages.script import BEAT_ORDERS, HOOK_TYPES, gemini_client

log = logging.getLogger("factory.ideate")

IDEAS_ASKED = 10
MIN_SHORT, MIN_DEEP = 3, 2
MAX_NEW_OBJECTS = 3  # machines outside the seed list per run
MAX_REASKS = 2
INACTIVE = ("rejected", "archived")  # these don't count as "covered"
# CONTENT_STRATEGY.md §9: Gemini's criteria and their maximum points; the trend part is ours.
WEIGHTS = {"hook_strength": 25, "familiarity": 20, "visual_potential": 20, "evergreen": 10,
           "novelty": 10}  # fmt: skip
TREND_MAX, RISING_MAX, DEMAND_MAX = 15, 9, 6
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


# --- the schema Gemini answers in --------------------------------------------------------------


class Policy(BaseModel):
    allowed: bool
    reason: str


class Scores(BaseModel):
    hook_strength: int
    familiarity: int
    visual_potential: int
    evergreen: int
    novelty: int
    reason: str

    @model_validator(mode="after")
    def _within_weights(self) -> Scores:
        # Ranges are checked here, not in the JSON schema (Gemini rejects min/max keywords).
        bad = [f"{k} {getattr(self, k)} (0-{w})" for k, w in WEIGHTS.items()
               if not 0 <= getattr(self, k) <= w]  # fmt: skip
        if bad:
            raise ValueError("scores out of range: " + ", ".join(bad))
        return self


class IdeaBase(BaseModel):
    object_name: str
    angle: str
    policy: Policy
    scores: Scores


class IdeaBatchBase(BaseModel):
    min_short: ClassVar[int] = MIN_SHORT
    min_deep: ClassVar[int] = MIN_DEEP

    @model_validator(mode="after")
    def _mix_and_unique(self) -> IdeaBatchBase:
        ideas = self.ideas  # type: ignore[attr-defined]
        shorts = sum(1 for i in ideas if i.format == "short")
        deeps = sum(1 for i in ideas if i.format == "deep_dive")
        names = [" ".join(i.object_name.split()).lower() for i in ideas]
        problems = []
        if shorts < self.min_short or deeps < self.min_deep:
            problems.append(f"{shorts} short and {deeps} deep_dive ideas; need at least "
                            f"{self.min_short} and {self.min_deep}")  # fmt: skip
        if len(set(names)) != len(names):
            problems.append("every object_name must be different")
        if problems:
            raise ValueError("; ".join(problems))
        return self


def idea_schema(ctx: AppContext) -> type[IdeaBatchBase]:
    """The answer model for this channel: pillars, enabled formats and hook types as fixed lists."""
    pillars = tuple(p.id for p in ctx.channel.niche.pillars)
    formats = tuple(f for f in ("short", "deep_dive") if getattr(ctx.channel.formats, f).enabled)
    idea = create_model(
        "IdeaDraft",
        __base__=IdeaBase,
        pillar=(Literal[pillars], ...),
        format=(Literal[formats], ...),
        hook_type=(Literal[HOOK_TYPES], ...),
    )
    return create_model("IdeaBatch", __base__=IdeaBatchBase, ideas=(list[idea], ...))


# --- trend points from the discover signals ----------------------------------------------------


def trend_points(signals: TopicSignals | None) -> tuple[int, dict[str, Any]]:
    """Up to 9 points for rising interest (the higher of the Wikipedia and Google Trends ratios:
    0 at 0.8x, full at 1.3x) and up to 6 for YouTube demand (median views: 0 at 10k, full at 1M).
    A machine without data gets 0."""
    ratios = [r for r in (signals.trend_ratio, signals.trends_ratio) if r is not None] if signals \
        else []  # fmt: skip
    rising = RISING_MAX * _clamp((max(ratios) - 0.8) / 0.5) if ratios else 0.0
    median = signals.youtube_median if signals else None
    demand = DEMAND_MAX * _clamp((math.log10(median) - 4) / 2) if median and median > 0 else 0.0
    detail = {"rising_ratio": max(ratios) if ratios else None, "youtube_median": median,
              "rising_points": round(rising, 1), "demand_points": round(demand, 1)}  # fmt: skip
    return round(rising + demand), detail


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


# --- ideate ------------------------------------------------------------------------------------


@dataclass
class IdeateResult:
    saved: list[int] = field(default_factory=list)
    below_threshold: int = 0
    skipped: list[str] = field(default_factory=list)  # "object: why"


def run(
    ctx: AppContext, *, count: int = IDEAS_ASKED, llm: GeminiClient | None = None
) -> IdeateResult:
    """One Gemini call; ideas scoring at least min_virality_score become `idea` items."""
    conn, channel = ctx.conn, ctx.channel
    channel_id = _channel_id(conn, channel.slug)
    covered = covered_objects(conn, channel_id)
    signals = {s.topic: s for s in latest_signals(conn, channel_id)}
    seeds = list(dict.fromkeys(channel.niche.seed_topics))
    schema = idea_schema(ctx)
    root = ctx.root / ctx.settings.paths.prompts_dir
    system = render_prompt("ideate_system", channel, prompts_root=root)
    user = render_prompt(
        "ideate", channel, prompts_root=root, count=count, min_short=MIN_SHORT, min_deep=MIN_DEEP,
        max_new=MAX_NEW_OBJECTS, covered=sorted(covered), hook_types=list(HOOK_TYPES),
        topics=[_topic_line(t, signals.get(t)) for t in seeds if t not in covered],
        reference_ideas=_reference_ideas(ctx),
    )  # fmt: skip
    llm = llm or gemini_client(ctx)
    batch = llm.generate_json(system=system.text, prompt=user.text, schema=schema,
                              max_reasks=MAX_REASKS)  # fmt: skip
    result = IdeateResult()
    new_objects = 0
    threshold = channel.scoring.min_virality_score
    for idea in batch.ideas:
        name = " ".join(idea.object_name.split()).lower()
        if not idea.policy.allowed:
            result.skipped.append(f"{name}: blocked ({idea.policy.reason})")
            continue
        if name in covered:
            result.skipped.append(f"{name}: already covered")
            continue
        if name not in seeds:
            new_objects += 1
            if new_objects > MAX_NEW_OBJECTS:
                result.skipped.append(f"{name}: more than {MAX_NEW_OBJECTS} new machines")
                continue
        trend, trend_detail = trend_points(signals.get(name))
        llm_points = sum(getattr(idea.scores, k) for k in WEIGHTS)
        total = llm_points + trend
        if total < threshold:
            result.below_threshold += 1
            log.info("idea %s scored %d (< %d)", name, total, threshold,
                     extra={"event": "idea_below"})  # fmt: skip
            continue
        breakdown = {
            **{k: getattr(idea.scores, k) for k in WEIGHTS},
            "trend_signal": trend,
            "trend_detail": trend_detail,
            "reason": idea.scores.reason,
            "suggested_hook": idea.hook_type,
            "model": llm.model,
            "prompt_version": f"{system.version}+{user.version}",
        }
        result.saved.append(_save(conn, channel, channel_id, idea, name, total, breakdown))
        covered.add(name)
    log.info("ideate: %d saved, %d below %d, %d skipped", len(result.saved),
             result.below_threshold, threshold, len(result.skipped),
             extra={"event": "ideate_done"})  # fmt: skip
    return result


def covered_objects(conn: sqlite3.Connection, channel_id: int) -> set[str]:
    rows = conn.execute(
        f"""
        SELECT DISTINCT object_name FROM contents
        WHERE channel_id = ? AND status NOT IN ({",".join("?" * len(INACTIVE))})
        """,
        (channel_id, *INACTIVE),
    ).fetchall()
    return {r[0] for r in rows}


def _topic_line(topic: str, s: TopicSignals | None) -> dict[str, str]:
    def ratio(value: float | None) -> str:
        return "-" if value is None else f"{value:.2f}"

    return {
        "topic": topic,
        "trend": ratio(s.trend_ratio if s else None),
        "youtube": "-" if not s or s.youtube_median is None else f"{s.youtube_median:,.0f} views",
        "google": "" if not s or s.trends_ratio is None else ratio(s.trends_ratio),
    }


def _save(
    conn: sqlite3.Connection,
    channel: Any,
    channel_id: int,
    idea: Any,
    name: str,
    total: int,
    breakdown: dict[str, Any],
) -> int:
    with conn:
        content_id = conn.execute(
            """
            INSERT INTO contents (channel_id, format, pillar, object_name, angle, virality_score,
                score_breakdown_json, status, visual_style)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'idea', ?)
            """,
            (channel_id, idea.format, idea.pillar, name, idea.angle, total,
             json.dumps(breakdown, ensure_ascii=False), channel.video_style),
        ).lastrowid  # fmt: skip
        # Link the newest signals of this machine, so the learn stage can see what inspired it.
        conn.execute(
            """
            INSERT OR IGNORE INTO content_signals (content_id, signal_id)
            SELECT ?, t.id FROM trend_signals t
            WHERE t.channel_id = ? AND t.query = ? AND t.observed_at = (
                SELECT MAX(observed_at) FROM trend_signals
                WHERE channel_id = t.channel_id AND query = t.query AND source = t.source)
            """,
            (content_id, channel_id, name),
        )
    log.info("#%s idea: %s (%s, %s) score %d", content_id, name, idea.format, idea.pillar, total,
             extra={"content_id": content_id, "event": "idea_saved"})  # fmt: skip
    return content_id


# --- the weekly plan ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Slot:
    day: date
    fmt: str
    publish_at: str  # UTC, as stored in contents.planned_publish_at
    content_id: int | None = None
    object_name: str | None = None
    pillar: str | None = None
    score: int | None = None
    hook_type: str | None = None
    beat_order: str | None = None
    note: str = ""  # why a slot is empty, or "kept" for one planned earlier


def week_start(ctx: AppContext, which: str, now: datetime | None = None) -> date:
    """Monday of this or next week in the channel's time zone."""
    today = (now or datetime.now(UTC)).astimezone(ZoneInfo(ctx.channel.timezone)).date()
    monday = today - timedelta(days=today.weekday())
    return monday + timedelta(days=7) if which == "next" else monday


def plan_week(ctx: AppContext, monday: date) -> list[Slot]:
    """Fill the week's empty slots with the best fitting ideas; planned slots are kept."""
    conn, channel = ctx.conn, ctx.channel
    channel_id = _channel_id(conn, channel.slug)
    zone = ZoneInfo(channel.timezone)
    hour, minute = (int(x) for x in channel.cadence.publish_time.split(":"))
    plan = channel.cadence.weekly_plan
    chain = _history(conn, channel_id, _utc(monday, hour, minute, zone))  # (pillar, hook, beat)
    slots: list[Slot] = []
    threshold = channel.scoring.min_virality_score
    for offset, weekday in enumerate(WEEKDAYS):
        fmt = plan.get(weekday)
        if fmt is None:
            continue
        day = monday + timedelta(days=offset)
        publish_at = _utc(day, hour, minute, zone)
        kept = conn.execute(
            "SELECT * FROM contents WHERE channel_id = ? AND planned_publish_at = ?",
            (channel_id, publish_at),
        ).fetchone()
        if kept is not None:
            slots.append(_slot(day, fmt, publish_at, kept, "kept"))
            chain.append((kept["pillar"], kept["hook_type"], kept["beat_order"]))
            continue
        pick = _pick(conn, channel_id, fmt, threshold, chain)
        if pick is None:
            slots.append(Slot(day, fmt, publish_at,
                              note=f"no unplanned {fmt} idea scoring {threshold}+ fits; run "
                                   "`factory ideate`"))  # fmt: skip
            continue
        prev_hook = chain[-1][1] if chain else None
        prev_beat = chain[-1][2] if chain else None
        suggested = json.loads(pick["score_breakdown_json"] or "{}").get("suggested_hook")
        hook = suggested if suggested in HOOK_TYPES and suggested != prev_hook \
            else _next_in(HOOK_TYPES, prev_hook)  # fmt: skip
        beat = _next_in(tuple(BEAT_ORDERS), prev_beat)
        with conn:
            conn.execute(
                """
                UPDATE contents SET planned_publish_at = ?, hook_type = ?, beat_order = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                WHERE id = ?
                """,
                (publish_at, hook, beat, pick["id"]),
            )
        row = conn.execute("SELECT * FROM contents WHERE id = ?", (pick["id"],)).fetchone()
        slots.append(_slot(day, fmt, publish_at, row, "planned now"))
        chain.append((row["pillar"], hook, beat))
        log.info("#%s planned for %s (%s)", row["id"], day.isoformat(), fmt,
                 extra={"content_id": row["id"], "event": "idea_planned"})  # fmt: skip
    return slots


def _pick(
    conn: sqlite3.Connection,
    channel_id: int,
    fmt: str,
    threshold: int,
    chain: list[tuple[str | None, str | None, str | None]],
) -> sqlite3.Row | None:
    """The best unplanned idea of this format that doesn't make a pillar run 3 days in a row."""
    candidates = conn.execute(
        """
        SELECT * FROM contents
        WHERE channel_id = ? AND status = 'idea' AND planned_publish_at IS NULL
          AND format = ? AND virality_score >= ?
        ORDER BY virality_score DESC, id
        """,
        (channel_id, fmt, threshold),
    ).fetchall()
    blocked = chain[-1][0] if len(chain) >= 2 and chain[-1][0] == chain[-2][0] else None
    return next((c for c in candidates if c["pillar"] != blocked), None)


def _history(
    conn: sqlite3.Connection, channel_id: int, before: str
) -> list[tuple[str | None, str | None, str | None]]:
    """The two videos just before the week (planned ones first, else the latest scripted)."""
    rows = conn.execute(
        """
        SELECT pillar, hook_type, beat_order FROM contents
        WHERE channel_id = ? AND (planned_publish_at < ?
            OR (planned_publish_at IS NULL AND status NOT IN ('idea', 'rejected', 'archived')
                AND hook_type IS NOT NULL))
        ORDER BY COALESCE(planned_publish_at, updated_at) DESC, id DESC LIMIT 2
        """,
        (channel_id, before),
    ).fetchall()
    return [(r["pillar"], r["hook_type"], r["beat_order"]) for r in reversed(rows)]


def _next_in(options: tuple[str, ...], previous: str | None) -> str:
    """The option after `previous` in a fixed rotation (the first one if there is none)."""
    if previous not in options:
        return options[0]
    return options[(options.index(previous) + 1) % len(options)]


def _slot(day: date, fmt: str, publish_at: str, row: sqlite3.Row, note: str) -> Slot:
    return Slot(day, fmt, publish_at, row["id"], row["object_name"], row["pillar"],
                row["virality_score"], row["hook_type"], row["beat_order"], note)  # fmt: skip


def _utc(day: date, hour: int, minute: int, zone: ZoneInfo) -> str:
    local = datetime.combine(day, time(hour, minute), zone)
    return local.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _channel_id(conn: sqlite3.Connection, slug: str) -> int:
    return conn.execute("SELECT id FROM channels WHERE slug = ?", (slug,)).fetchone()[0]


def _reference_ideas(ctx: AppContext) -> list[str]:
    """Topic ideas from reference videos the owner approved (D-109), each once, newest first."""
    seen: dict[str, None] = {}
    for note in reversed(references_stage.approved_for_prompts(ctx)):
        for idea in note["topic_ideas"]:
            seen.setdefault(" ".join(idea.split()), None)
    return list(seen)[:10]
