"""Script stage: idea -> scripted (ARCHITECTURE.md §2, CONTENT_STRATEGY.md §5-7).

For each idea: optional Wikipedia reference -> one Gemini call returning a ScriptDraft whose rules
(word count, shot mix, beat order, sources, no repeated hook/beat) are part of the schema, so the
client's automatic re-ask fixes rule breaks -> source URL check -> scripts + sources rows.
"""

from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, ClassVar, Literal
from urllib.parse import quote, unquote

import httpx
from pydantic import BaseModel, Field, create_model, model_validator

from factory import blueprint
from factory.blueprint.canvas import NUMBER
from factory.budgets import check_and_record
from factory.config import ChannelConfig
from factory.context import AppContext, StageResult
from factory.db import set_status
from factory.errors import (
    BudgetExceededError,
    ConfigError,
    FactoryError,
    IntegrationError,
    PolicyBlockedError,
)
from factory.integrations.gemini import GeminiClient
from factory.integrations.wikimedia import WikiSummary, article_title, page_summary
from factory.prompts import render_prompt
from factory.similarity import closest
from factory.stages import lessons as lessons_stage
from factory.stages import references as references_stage
from factory.stages import series as series_stage
from factory.stages.voice import asset_dir

log = logging.getLogger("factory.script")

HOOK_TYPES = (
    "why_question",
    "hidden_inside",
    "surprising_number",
    "myth_bust",
    "what_if",
    "reveal",
)
BEAT_ORDERS: dict[str, tuple[str, ...]] = {
    "classic": ("studio", "cutaway", "flow", "in_situ"),
    "context_first": ("in_situ", "studio", "cutaway", "flow"),
    "action_first": ("flow", "cutaway", "studio", "in_situ"),
    "cold_open": ("cutaway", "flow", "in_situ", "studio"),
}
# Every flow_clip is one shot of a short film: what happens, one camera move and the last frame,
# which the next clip continues from (D-092). The flow beat also names its energy colour.
# Story shots (D-104): every clip has a style (its camera rule), a first and a last picture, and
# may start on the previous clip's last picture, so clips chain through images the owner approved.
STYLES = ("product", "open", "explain", "close", "installed", "moving")
SCENE_FIELDS = ("scene", "camera", "start_picture", "end_frame", "style")
CLIP_FIELDS: dict[str, tuple[str, ...]] = {
    "studio": SCENE_FIELDS,
    "cutaway": SCENE_FIELDS,
    "flow": (*SCENE_FIELDS, "energy"),
    "in_situ": SCENE_FIELDS,
}
# The hero_look is repeated word for word in every clip prompt so Veo draws the same machine.
HERO_LOOK_WORDS = (20, 80)
FLOW_PROMPTS_FILE = "flow_prompts.json"  # the exact Flow prompts of an item (generate stage)
MIN_DIAGRAMS = {"short": 2, "deep_dive": 4, "episode": 4}  # TESTING.md §4 QA rule
MIN_CLIPS, MAX_CLIPS = 3, 5  # DESIGN_SYSTEM.md §10.5
# Ask for 3 sources, keep the item if at least 2 open: one invented URL then costs nothing.
SOURCES_ASKED, SOURCES_NEEDED = 3, 2
# Fewer than 2 open: ask Gemini for replacements at most this many times (D-086).
SOURCE_ROUNDS = 2
# flash-lite fixes a named rule break when asked again; at most 5 Gemini calls per idea (D-083).
MAX_REASKS = 4
CLIP_MAX_S = 8.0
# Planned shots must cover this share of the narration; assembly stretches the rest (D-058).
COVERAGE = 0.85
MAX_LABELS, MAX_LABEL_WORDS = 4, 4  # DESIGN_SYSTEM.md §7: labels of 4 words or fewer
SOURCE_TIMEOUT_S = 10.0
# A hook formula that is a whole sentence (script_rules.j2) must not be used word for word (D-088).
FIXED_HOOK_EXAMPLES = ("You've seen this a thousand times, but never inside it.",)
REPEAT_SHARE = 0.5
# The hook is said in the first 2-3 seconds, before viewers swipe away (D-090).
HOOK_MAX_WORDS = 12
# The Engineer's Note opens with this lead-in (D-090); the fact after it is checked like the body.
NOTE_LEAD_IN = "As an engineer, what surprises me is that"
# Body and Engineer's Note state plain facts; they never claim what viewers know or miss
# (D-084, body since D-088). The hook is not checked: the myth_bust formula is "Most people think".
CLAIM_PHRASES = re.compile(
    r"most people|surpris|as an engineer|you (?:might|may|probably) (?:not|never|n't)|"
    r"nobody (?:tells|knows|realises|realizes)|few people (?:know|realise|realize)|"
    r"(?:didn't|don't|did not|do not) know",
    re.IGNORECASE,
)


# --- the ScriptDraft schema ----------------------------------------------------------------


class PolicyCheck(BaseModel):
    allowed: bool
    reason: str


class Shot(BaseModel):
    n: int = Field(ge=1)
    beat: Literal["studio", "cutaway", "flow", "in_situ"]
    kind: Literal["flow_clip", "still", "diagram"]
    description: str = Field(min_length=5)
    # Gemini schemas support ge/le, not gt/lt. Descriptions are part of the schema Gemini reads.
    duration_s: float = Field(
        ge=0.5, le=12, description="Seconds on screen; a flow_clip is at most 8 seconds"
    )
    # Required (Gemini leaves out optional fields); flow_clip only, "" for other kinds (D-092).
    scene: str = Field(description="flow_clip only: what happens in this film shot; else ''")
    camera: str = Field(description="flow_clip only: one smooth camera move and lens; else ''")
    end_frame: str = Field(description="flow_clip only: what the last frame shows; else ''")
    start_picture: str = Field(description="flow_clip only: what the first frame shows; else ''")
    style: str = Field(description=f"flow_clip only: one of {', '.join(STYLES)}; else ''")
    continues: bool = Field(
        description="flow_clip only: true if it starts on the previous flow_clip's last picture"
    )
    energy: Literal["electric", "heat", "fluid"] | None = None
    # Required (Gemini leaves out optional fields): the diagram's part labels, [] for other kinds.
    labels: list[str] = Field(description="diagram only: 1-4 labels of at most 4 words; else []")

    @model_validator(mode="after")
    def _clip_has_template_fields(self) -> Shot:
        if self.kind == "diagram":
            labels = [label.strip() for label in self.labels if label.strip()]
            if not 1 <= len(labels) <= MAX_LABELS:
                raise ValueError(f"shot {self.n} (diagram) needs 1-{MAX_LABELS} labels")
            long = [label for label in labels if len(label.split()) > MAX_LABEL_WORDS]
            if long:
                raise ValueError(f"shot {self.n}: labels have at most 4 words: {long}")
            self.labels = labels
        else:
            self.labels = []
        if self.kind == "flow_clip":
            missing = [f for f in CLIP_FIELDS[self.beat] if not str(getattr(self, f) or "").strip()]
            if missing:
                raise ValueError(f"shot {self.n} ({self.beat} flow_clip) needs {missing}")
            if self.style not in STYLES:
                raise ValueError(f"shot {self.n}: style must be one of {', '.join(STYLES)}")
            # A Veo clip is at most 8 s long, so cap the plan instead of rejecting the script;
            # the coverage rule in ScriptDraft still catches a resulting gap.
            self.duration_s = min(self.duration_s, CLIP_MAX_S)
        return self

    def prompt_vars(self) -> dict[str, Any]:
        fields = {f: getattr(self, f).strip() for f in CLIP_FIELDS[self.beat] if getattr(self, f)}
        return {**fields, "continues": self.continues}


class SourceDraft(BaseModel):
    url: str = Field(pattern=r"^https?://\S+$")
    title: str
    publisher: str
    claim: str


class SourceReplacements(BaseModel):
    """Gemini's answer when cited pages could not be opened (D-086)."""

    sources: list[SourceDraft]


class ShotVisual(BaseModel):
    n: int = Field(ge=1)
    style: Literal["product", "open", "explain", "close", "installed", "moving"]
    start_picture: str = Field(min_length=10)
    scene: str = Field(min_length=20)
    camera: str = Field(min_length=5)
    end_frame: str = Field(min_length=5)
    continues: bool


class ShotsPlan(BaseModel):
    """New film shots for some flow_clips of an approved script; the narration and the
    hero_look stay (D-096: `--restage N --shots 3`)."""

    shots: list[ShotVisual]

    clip_numbers: ClassVar[tuple[int, ...]] = ()  # set per call by visual_plan_schema()

    @model_validator(mode="after")
    def _matches_shot_list(self) -> ShotsPlan:
        if problems := self._problems():
            raise ValueError("; ".join(problems))
        return self

    def _problems(self) -> list[str]:
        if (got := tuple(s.n for s in self.shots)) != self.clip_numbers:
            return [f"shots are {list(got)}; give exactly the flow_clips {list(self.clip_numbers)}"]
        return []


class VisualPlan(ShotsPlan):
    """New film shots for all flow_clips of an approved script; the narration stays (D-092)."""

    hero_look: str

    def _problems(self) -> list[str]:
        return hero_look_problems(self.hero_look) + super()._problems()


def visual_plan_schema(
    clip_numbers: tuple[int, ...], *, keep_look: bool = False
) -> type[ShotsPlan]:
    base = ShotsPlan if keep_look else VisualPlan
    model = create_model(f"{base.__name__}_clips", __base__=base)
    model.clip_numbers = clip_numbers
    return model


class ScriptDraftBase(BaseModel):
    """Fields shared by every format; script_schema() adds hook_type/beat_order and limits."""

    # No defaults on purpose: a field with a default becomes optional in the JSON schema, and
    # Gemini then leaves it out. A refused topic sends them empty instead.
    policy: PolicyCheck
    working_title: str
    hook: str
    body: str
    engineer_note_draft: str
    loop_line: str
    hero_look: str
    shots: list[Shot]
    sources: list[SourceDraft]

    # Set per call by script_schema(); not part of the JSON.
    min_words: ClassVar[int] = 0
    max_words: ClassVar[int] = 10_000
    min_diagrams: ClassVar[int] = 0
    wpm: ClassVar[int] = 142  # overwritten from voice.target_wpm by script_schema()
    reference_url: ClassVar[str | None] = None
    allowed_hooks: ClassVar[tuple[str, ...]] = HOOK_TYPES
    allowed_beats: ClassVar[tuple[str, ...]] = tuple(BEAT_ORDERS)
    # Blueprint items (D-111/D-112): the only numbers the narration may say, so the narration and
    # the film can never disagree; None = no limit. A series part ends on a statement.
    verified_facts: ClassVar[tuple[str, ...] | None] = None
    in_series: ClassVar[bool] = False

    def narration(self) -> str:
        """Everything the narrator says, in order (stored as scripts.body_text)."""
        parts = (self.hook, self.body, self.engineer_note_draft, self.loop_line)
        return " ".join(p.strip() for p in parts if p.strip())

    @model_validator(mode="after")
    def _channel_rules(self) -> ScriptDraftBase:
        if not self.policy.allowed:
            return self  # a refused topic needs no script
        problems = _rule_problems(self)
        if problems:
            raise ValueError("; ".join(problems))
        return self


def _rule_problems(draft: Any) -> list[str]:
    problems: list[str] = []
    for name in ("working_title", "hook", "body", "engineer_note_draft", "loop_line"):
        if not getattr(draft, name).strip():
            problems.append(f"{name} must not be empty")
    loop = draft.loop_line.strip()
    # The owner rejected a trailing "because..." at Gate 1 as an incomplete sentence (D-062).
    if loop and (loop.endswith(("...", "…")) or loop[-1] not in ".?!"):
        problems.append("loop_line must be a complete sentence ending in . ? or ! (no '...')")
    note_fact = re.sub(rf"^\s*{re.escape(NOTE_LEAD_IN)}", "", draft.engineer_note_draft, flags=re.I)
    for name, text in (("body", draft.body), ("engineer_note_draft", note_fact)):
        if claim := CLAIM_PHRASES.search(text):
            problems.append(
                f"{name} says '{claim.group(0)}': state the fact plainly, "
                "without saying what people know, miss or find surprising"
            )
    if _repeats(draft.engineer_note_draft, draft.body):
        problems.append("engineer_note_draft repeats the body: give a fact the body doesn't say")
    if draft.in_series and loop.endswith("?"):  # owner: a part never ends on an open question
        problems.append("loop_line must be a closing statement in a series, not a question")
    if draft.verified_facts is not None:
        allowed = " ".join(draft.verified_facts)
        unverified = [n for n in NUMBER.findall(draft.narration()) if n.rstrip(".,") not in allowed]
        if unverified:
            problems.append(
                f"the narration says {', '.join(unverified)}, which is not in the verified facts "
                f"({'; '.join(draft.verified_facts)}): use only those numbers, or none"
            )
    if _repeats(draft.loop_line, draft.hook):  # D-089: it leads back to the hook, not repeats it
        problems.append(
            "loop_line repeats the hook: write a new last sentence that leads back into the hook"
        )
    if _repeats(draft.hook, " ".join(FIXED_HOOK_EXAMPLES)):
        problems.append("hook copies the formula's example: write your own hook for this machine")
    if (hook_words := word_count(draft.hook)) > HOOK_MAX_WORDS:
        problems.append(f"hook has {hook_words} words: keep it to {HOOK_MAX_WORDS} or fewer")
    words = word_count(draft.narration())
    if not draft.min_words <= words <= draft.max_words:
        # Per-part counts tell the model which part to shorten when it is asked again.
        parts = ", ".join(
            f"{name} {word_count(getattr(draft, name))}"
            for name in ("hook", "body", "engineer_note_draft", "loop_line")
        )
        problems.append(
            f"narration has {words} words ({parts}); it must have "
            f"{draft.min_words}-{draft.max_words}"
        )
    problems += hero_look_problems(draft.hero_look)
    shots = draft.shots
    if [s.n for s in shots] != list(range(1, len(shots) + 1)):
        problems.append("shots must be numbered 1, 2, 3, ... in order")
    if shots and shots[0].kind != "flow_clip":  # D-090: motion from frame 1
        problems.append(f"shot 1 is a {shots[0].kind}: the first shot must be a flow_clip")
    clips = sum(s.kind == "flow_clip" for s in shots)
    if not MIN_CLIPS <= clips <= MAX_CLIPS:
        problems.append(f"{clips} flow_clip shots; use {MIN_CLIPS}-{MAX_CLIPS}")
    diagrams = sum(s.kind == "diagram" for s in shots)
    if diagrams < draft.min_diagrams:
        problems.append(f"{diagrams} diagram shots; use at least {draft.min_diagrams}")
    first_seen = list(dict.fromkeys(s.beat for s in shots))
    expected = list(BEAT_ORDERS[draft.beat_order])
    if first_seen != expected:
        problems.append(f"beats first appear as {first_seen}; beat_order needs {expected}")
    needed_s = COVERAGE * words / draft.wpm * 60
    total_s = sum(s.duration_s for s in shots)
    if total_s < needed_s:
        problems.append(f"shots last {total_s:.0f} s; they must cover at least {needed_s:.0f} s")
    if len(draft.sources) < SOURCES_ASKED:
        problems.append(f"{len(draft.sources)} sources; give {SOURCES_ASKED} different ones")
    if draft.reference_url and draft.reference_url not in [s.url for s in draft.sources]:
        problems.append(f"list {draft.reference_url} as a source")
    return problems


def hero_look_problems(hero_look: str) -> list[str]:
    low, high = HERO_LOOK_WORDS
    if not low <= (words := word_count(hero_look)) <= high:
        return [f"hero_look has {words} words: describe the machine in {low}-{high} words"]
    return []


def _trigrams(text: str) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9']+", text.lower())
    return {tuple(words[i : i + 3]) for i in range(len(words) - 2)}


def _repeats(part: str, other: str) -> bool:
    """Does `part` mostly repeat `other`? Half or more of its three-word runs appear there
    (D-088): catches a copied sentence, not a shared technical term."""
    own = _trigrams(part)
    return bool(own) and len(own & _trigrams(other)) / len(own) >= REPEAT_SHARE


def script_schema(
    channel: ChannelConfig,
    fmt: str,
    *,
    previous: tuple[str | None, str | None],
    reference_url: str | None,
    planned: tuple[str, str] | None = None,
    verified_facts: list[str] | None = None,
    in_series: bool = False,
) -> type[ScriptDraftBase]:
    """Build the ScriptDraft model for one item.

    The previous video's hook_type and beat_order are left out of the allowed values, so Gemini
    cannot repeat them (CONTENT_STRATEGY.md §6-7). An item from the weekly plan must use the
    hook_type and beat_order the plan gave it (D-080). Word limits come from the format's seconds
    and the narrator's words per minute in the YAML.
    """
    prev_hook, prev_beat = previous
    hooks = tuple(h for h in HOOK_TYPES if h != prev_hook)
    beats = tuple(b for b in BEAT_ORDERS if b != prev_beat)
    if planned is not None:
        hooks, beats = (planned[0],), (planned[1],)
    model = create_model(
        f"ScriptDraft_{fmt}",
        __base__=ScriptDraftBase,
        hook_type=(Literal[hooks], ...),
        beat_order=(Literal[beats], ...),
    )
    model.min_words, model.max_words = word_range(channel, fmt)
    wpm = channel.voice.target_wpm
    model.min_diagrams = MIN_DIAGRAMS[fmt]
    model.wpm = wpm
    model.reference_url = reference_url
    model.allowed_hooks = hooks
    model.allowed_beats = beats
    model.verified_facts = tuple(verified_facts) if verified_facts is not None else None
    model.in_series = in_series
    return model


def part_words(target_words: int) -> dict[str, int]:
    """A word budget per narration part that adds up to the target (D-083).

    gemini-3.5-flash-lite overshot a single total by about 35%; small models keep short parts
    to a length much better than one long total, so the prompt asks for each part separately.
    """
    hook, loop = 10, 10
    note = min(25, round(target_words * 0.15)) + word_count(NOTE_LEAD_IN)
    return {"hook": hook, "body": target_words - hook - note - loop, "note": note, "loop": loop}


def shot_seconds(target_s: int) -> dict[str, int]:
    """The shot-list sum spelled out for the prompt (D-083): the model adds badly, so the code
    does the arithmetic. Shorts get the fewest clips (Flow credits), longer videos the most."""
    clips = MIN_CLIPS if target_s <= 60 else MAX_CLIPS
    return {
        "clips": clips,
        "clip_s": round(clips * CLIP_MAX_S),
        "rest_s": max(target_s - round(clips * CLIP_MAX_S), 0),
        "min_total_s": math.ceil(COVERAGE * target_s),
    }


def word_range(channel: ChannelConfig, fmt: str) -> tuple[int, int]:
    """Allowed narration words for a format: its seconds x the narrator's words per minute."""
    video_format = getattr(channel.formats, fmt)
    wpm = channel.voice.target_wpm
    return round(video_format.min_s * wpm / 60), round(video_format.max_s * wpm / 60)


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]+(?:['’][A-Za-z]+)?", text))


# --- the stage -------------------------------------------------------------------------------


def run(
    ctx: AppContext,
    *,
    limit: int | None = None,
    llm: GeminiClient | None = None,
    http: httpx.Client | None = None,
) -> StageResult:
    """Script every eligible idea of the channel. One failing item never stops the others;
    going over the daily Gemini budget stops the whole stage (BudgetExceededError)."""
    result = StageResult()
    ideas = _eligible_ideas(ctx, limit)
    if not ideas:
        log.info("no ideas to script", extra={"event": "script_nothing"})
        return result
    llm = llm or gemini_client(ctx)
    own_http = http is None
    http = http or http_client(ctx)
    try:
        for row in ideas:
            _script_one_safely(ctx, row, llm, http, result)
    finally:
        if own_http:
            http.close()
    return result


def _script_one_safely(
    ctx: AppContext, row: sqlite3.Row, llm: GeminiClient, http: httpx.Client, result: StageResult
) -> None:
    content_id = row["id"]
    extra = {"content_id": content_id}
    try:
        script_id = _script_one(ctx, row, llm, http)
    except BudgetExceededError:
        raise  # stop the stage; the remaining ideas wait for tomorrow
    except PolicyBlockedError as exc:
        set_status(ctx.conn, content_id, "archived", reason=f"blocked: {exc}")
        log.warning("#%s archived: %s", content_id, exc, extra={**extra, "event": "script_blocked"})
        result.processed += 1
    except Exception as exc:  # one bad item must not stop the batch (CONVENTIONS §4)
        set_status(ctx.conn, content_id, "failed", reason=str(exc)[:500])
        log.exception("#%s failed", content_id, extra={**extra, "event": "script_failed"})
        result.failed += 1
    else:
        log.info(
            "#%s scripted (script %s)", content_id, script_id, extra={**extra, "event": "scripted"}
        )
        result.processed += 1


@dataclass(frozen=True)
class Feedback:
    """What the owner asked to change at Gate 1, plus the narration they reviewed."""

    text: str
    previous: str


def rescript(
    ctx: AppContext,
    content_id: int,
    feedback: str,
    *,
    llm: GeminiClient | None = None,
    http: httpx.Client | None = None,
) -> int:
    """Write the next script version of a `scripted` item using the owner's feedback (Gate 1
    redo). Returns the new script id; the previous version stays as history."""
    row = ctx.conn.execute("SELECT * FROM contents WHERE id = ?", (content_id,)).fetchone()
    current = ctx.conn.execute(
        "SELECT body_text FROM scripts WHERE content_id = ? AND is_current = 1", (content_id,)
    ).fetchone()
    if row is None or row["status"] != "scripted" or current is None:
        raise FactoryError(f"content #{content_id} is not a scripted item")
    llm = llm or gemini_client(ctx)
    own_http = http is None
    http = http or http_client(ctx)
    try:
        return _script_one(ctx, row, llm, http, Feedback(feedback, current["body_text"]))
    finally:
        if own_http:
            http.close()


def _script_one(
    ctx: AppContext,
    row: sqlite3.Row,
    llm: GeminiClient,
    http: httpx.Client,
    feedback: Feedback | None = None,
) -> int:
    channel, conn = ctx.channel, ctx.conn
    reference = page_summary(
        http, row["object_name"], before_call=lambda: check_and_record(conn, channel, "wikimedia")
    )
    previous = previous_variety(conn, row["channel_id"], row["id"])
    schema = script_schema(
        channel,
        row["format"],
        previous=previous,
        reference_url=reference.url if reference else None,
        planned=(row["hook_type"], row["beat_order"])
        if row["planned_publish_at"] and row["hook_type"] and row["beat_order"]
        else None,
        verified_facts=(facts := blueprint_facts(row)),
        in_series=(part := series_stage.context(conn, row["id"])) is not None,
    )
    system, user = _prompts(ctx, row, schema, reference, feedback, part, facts)
    draft = llm.generate_json(
        system=system.text, prompt=user.text, schema=schema, max_reasks=MAX_REASKS
    )
    if not draft.policy.allowed:
        raise PolicyBlockedError(draft.policy.reason)
    sources = _open_sources(ctx, row, llm, http, draft, system.text)
    # Parts of one series (and its teaser) share a topic on purpose: they are not compared with
    # each other, only with every other video (D-112).
    match = closest(conn, row["channel_id"], row["id"], draft.narration(),
                    window=channel.scoring.similarity_window,
                    exclude=part.sibling_ids if part else ())  # fmt: skip
    threshold = channel.scoring.similarity_block_threshold
    if match.score > threshold:  # D-041/D-076: a near-repeat of a recent script is not made
        raise PolicyBlockedError(
            f"too similar to #{match.content_id} ({match.score:.2f} > {threshold:.2f})"
        )
    return _save(conn, row["id"], draft, sources, llm.model,
                 f"{system.version}+{user.version}", match.score)  # fmt: skip


def blueprint_facts(row: sqlite3.Row) -> list[str] | None:
    """The drawing's verified facts for a blueprint item, None for a Flow item (D-111)."""
    if row["visual_style"] != "blueprint" or row["object_name"] not in blueprint.MODULES:
        return None
    return [f.text for f in blueprint.MODULES[row["object_name"]].FACTS.values()]


def visual_rules_template(ctx: AppContext) -> str:
    """The film rules Gemini follows: short product-film rules (default, D-105) or story shots."""
    return "visual_rules.j2" if ctx.channel.video_prompts == "story" else "visual_rules_brief.j2"


def benchmark_examples(ctx: AppContext) -> list[dict[str, Any]]:
    """The channel's benchmark videos with the exact Flow prompts of their good shots, for the
    film rules (D-094). A benchmark whose prompts file is missing keeps only the owner's notes."""
    examples = []
    for bench in ctx.channel.benchmarks:
        path = asset_dir(ctx, bench.content_id) / FLOW_PROMPTS_FILE
        try:
            issued = json.loads(path.read_text())
        except (OSError, ValueError):
            log.warning("benchmark #%s has no readable %s; using the notes only",
                        bench.content_id, FLOW_PROMPTS_FILE,
                        extra={"event": "benchmark_missing"})  # fmt: skip
            issued = {}
        examples.append({
            "name": bench.name,
            "liked": bench.liked,
            "improve": bench.improve,
            "prompts": [issued[str(n)] for n in bench.good_shots if str(n) in issued],
        })  # fmt: skip
    return examples


def _eligible_ideas(ctx: AppContext, limit: int | None) -> list[sqlite3.Row]:
    # Items created by hand (factory new) have no score; the owner chose them, so they count.
    # Scored ideas from `factory ideate` wait until the weekly plan picks them (D-080).
    return ctx.conn.execute(
        """
        SELECT c.* FROM contents c JOIN channels ch ON ch.id = c.channel_id
        WHERE ch.slug = ? AND c.status = 'idea'
          AND (c.virality_score IS NULL OR c.planned_publish_at IS NOT NULL)
        ORDER BY COALESCE(c.planned_publish_at, c.created_at), c.id LIMIT ?
        """,
        (ctx.channel.slug, limit or -1),
    ).fetchall()


def previous_variety(
    conn: sqlite3.Connection, channel_id: int, content_id: int
) -> tuple[str | None, str | None]:
    """hook_type and beat_order of the channel's most recent other scripted item (planned ideas
    already carry the plan's values but are not scripted yet, so they don't count). Rejected,
    dropped and failed items never reach viewers, so they don't count either: dropping #4 made it
    the "previous video" of #8 (D-106)."""
    row = conn.execute(
        """
        SELECT hook_type, beat_order FROM contents
        WHERE channel_id = ? AND id <> ? AND hook_type IS NOT NULL
          AND status NOT IN ('idea', 'rejected', 'archived', 'failed')
        ORDER BY updated_at DESC, id DESC LIMIT 1
        """,
        (channel_id, content_id),
    ).fetchone()
    return (row["hook_type"], row["beat_order"]) if row else (None, None)


def _prompts(
    ctx: AppContext,
    row: sqlite3.Row,
    schema: type[ScriptDraftBase],
    reference: WikiSummary | None,
    feedback: Feedback | None,
    series_part: series_stage.SeriesContext | None = None,
    verified_facts: list[str] | None = None,
) -> tuple[Any, Any]:
    channel = ctx.channel
    video_format = getattr(channel.formats, row["format"])
    prompts_root = ctx.root / ctx.settings.paths.prompts_dir
    system = render_prompt("script_system", channel, prompts_root=prompts_root)
    user = render_prompt(
        f"script_{row['format']}",
        channel,
        prompts_root=prompts_root,
        item=row,
        series=video_format.series,
        target_s=round((video_format.min_s + video_format.max_s) / 2),
        allowed_hooks=list(schema.allowed_hooks),
        allowed_beats=list(schema.allowed_beats),
        min_words=schema.min_words,
        max_words=schema.max_words,
        # A target in the middle of the range keeps the model away from the edges.
        target_words=(target_words := round((schema.min_words + schema.max_words) / 2)),
        target_total_s=(target_s := round(target_words / schema.wpm * 60)),
        part_words=part_words(target_words),
        hook_max_words=HOOK_MAX_WORDS,
        note_lead_in=NOTE_LEAD_IN,
        shot_seconds=shot_seconds(target_s),
        min_clips=MIN_CLIPS,
        max_clips=MAX_CLIPS,
        min_diagrams=schema.min_diagrams,
        hero_look_words=HERO_LOOK_WORDS,
        visual_rules=visual_rules_template(ctx),
        benchmarks=benchmark_examples(ctx),
        references=references_stage.approved_for_prompts(ctx, row["id"]),
        lessons=lessons_stage.approved_for_prompts(ctx),
        reference=reference,
        feedback=feedback,
        series_part=series_part,
        verified_facts=verified_facts,
    )
    return system, user


def _open_sources(
    ctx: AppContext,
    row: sqlite3.Row,
    llm: GeminiClient,
    http: httpx.Client,
    draft: ScriptDraftBase,
    system: str,
) -> list[SourceDraft]:
    """The cited pages that open. With fewer than SOURCES_NEEDED, Gemini replaces the dead ones,
    at most SOURCE_ROUNDS times; still too few raises FactoryError (D-086)."""
    candidates = list(draft.sources)
    sources: list[SourceDraft] = []
    dead: list[str] = []
    for round_n in range(SOURCE_ROUNDS + 1):
        for source in candidates:
            if source.url in dead or source.url in [s.url for s in sources]:
                continue  # Gemini repeated a URL it was told about
            if _citable(source.url) and _reachable(http, source.url):
                sources.append(source)
                continue
            dead.append(source.url)
            fixed = _wikipedia_fix(ctx, row, http, source)
            if fixed and fixed.url not in [s.url for s in sources]:
                sources.append(fixed)
        if dead:
            log.warning(
                "#%s: %d source(s) could not be opened or are not citable: %s",
                row["id"], len(dead), ", ".join(dead),
                extra={"content_id": row["id"], "event": "sources_dead"},
            )  # fmt: skip
        if len(sources) >= SOURCES_NEEDED or round_n == SOURCE_ROUNDS:
            break
        prompt = render_prompt(
            "script_sources",
            ctx.channel,
            prompts_root=ctx.root / ctx.settings.paths.prompts_dir,
            object_name=row["object_name"],
            dead=dead,
            working=[s.url for s in sources],
            needed=SOURCES_ASKED - len(sources),
            narration=draft.narration(),
        )
        candidates = llm.generate_json(
            system=system, prompt=prompt.text, schema=SourceReplacements
        ).sources
    if len(sources) < SOURCES_NEEDED:
        raise FactoryError(
            f"only {len(sources)} of {SOURCES_NEEDED} needed sources could be opened after "
            f"{SOURCE_ROUNDS} requests for replacements; not found: {', '.join(dead)}"
        )
    return sources


# Wikimedia pages other than Wikipedia articles (a Wikidata item, a Commons file, Wikipedia's own
# File: or Special: pages) open fine but don't support a claim: Gemini cited Wikidata's
# "phosphorescence" item for a tunnel boring machine (D-088).
WIKIMEDIA_HOSTS = (
    "wikipedia.org", "wikidata.org", "wikimedia.org", "wiktionary.org", "wikibooks.org",
    "wikiquote.org", "wikisource.org", "wikiversity.org", "wikivoyage.org", "wikinews.org",
)  # fmt: skip
ANY_WIKI_ARTICLE = re.compile(r"^https?://[a-z-]+\.wikipedia\.org/wiki/([^?#]+)$")
NON_ARTICLE = re.compile(
    r"^(?:File|Image|Media|Special|Category|Talk|User|Wikipedia|Help|Template|Portal|Draft|"
    r"Module|MediaWiki)(?:[ _]talk)?:",
    re.IGNORECASE,
)


def _citable(url: str) -> bool:
    """Any page outside Wikimedia; inside it, only Wikipedia articles (D-088)."""
    host = httpx.URL(url).host
    if not any(host == h or host.endswith(f".{h}") for h in WIKIMEDIA_HOSTS):
        return True
    match = ANY_WIKI_ARTICLE.match(url)
    return bool(match) and not NON_ARTICLE.match(unquote(match.group(1)))


WIKI_ARTICLE = re.compile(r"^https?://en\.wikipedia\.org/wiki/([^?#]+)$")


def _wikipedia_fix(
    ctx: AppContext, row: sqlite3.Row, http: httpx.Client, source: SourceDraft
) -> SourceDraft | None:
    """A dead English Wikipedia link whose title Gemini guessed ("Shield_tunneling") becomes
    the real article that Wikipedia's search finds ("Tunnelling shield"), or None (D-087)."""
    match = WIKI_ARTICLE.match(source.url)
    if not match:
        return None
    guess = unquote(match.group(1)).replace("_", " ")
    try:
        title = article_title(
            http, guess, before_call=lambda: check_and_record(ctx.conn, ctx.channel, "wikimedia")
        )
    except IntegrationError as exc:  # best effort: the Gemini replacement step still follows
        log.warning("Wikipedia search for %r failed: %s", guess, exc,
                    extra={"content_id": row["id"], "event": "wiki_fix_error"})  # fmt: skip
        return None
    if not title or title == guess:
        return None
    url = f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"
    if not _reachable(http, url):
        return None
    log.info("#%s: Wikipedia has no %r; citing %r instead", row["id"], guess, title,
             extra={"content_id": row["id"], "event": "wiki_fix"})  # fmt: skip
    return source.model_copy(update={"url": url, "title": title, "publisher": "Wikipedia"})


# Only Wikipedia answers honestly when it refuses a script: other sites (Britannica,
# ice.org.uk) send 403 even for pages that don't exist, so there it counts as missing (D-086).
EXISTS_BUT_BLOCKS_BOTS = {401, 403, 429}
HONEST_HOSTS = ("wikipedia.org",)


def _reachable(http: httpx.Client, url: str) -> bool:
    """Does the cited page exist? One request per source, following redirects.

    2xx = yes. 401/403/429 = yes only on HONEST_HOSTS. 404/410, server errors, dead domains
    and bot walls elsewhere = no.
    """
    try:
        status = http.get(url).status_code
    except httpx.HTTPError:
        return False
    if 200 <= status < 300:
        return True
    host = httpx.URL(url).host
    honest = any(host == h or host.endswith(f".{h}") for h in HONEST_HOSTS)
    return honest and status in EXISTS_BUT_BLOCKS_BOTS


def _save(
    conn: sqlite3.Connection,
    content_id: int,
    draft: ScriptDraftBase,
    sources: list[SourceDraft],
    model: str,
    prompt_version: str,
    similarity_max: float,
) -> int:
    narration = draft.narration()
    words = word_count(narration)
    shot_list = [
        {
            "n": s.n,
            "beat": s.beat,
            "kind": s.kind,
            "description": s.description,
            "duration_s": s.duration_s,
            "prompt_vars": (
                {"hero_look": draft.hero_look.strip(), **s.prompt_vars()}
                if s.kind == "flow_clip"
                else {}
            ),
            **({"labels": s.labels, "energy": s.energy} if s.kind == "diagram" else {}),
        }
        for s in draft.shots
    ]
    # One transaction: set_status runs last, and its commit also commits the rows above. If the
    # status change is refused, nothing has been committed and everything is rolled back.
    with conn:
        conn.execute("UPDATE scripts SET is_current = 0 WHERE content_id = ?", (content_id,))
        version = conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM scripts WHERE content_id = ?", (content_id,)
        ).fetchone()[0]
        script_id = conn.execute(
            """
            INSERT INTO scripts (content_id, version, hook, body_text, engineer_note, loop_line,
                word_count, est_duration_s, shot_list_json, similarity_max, llm_provider,
                llm_model, prompt_version, is_current)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'gemini', ?, ?, 1)
            """,
            (
                content_id,
                version,
                draft.hook,
                narration,
                draft.engineer_note_draft,
                draft.loop_line,
                words,
                round(words / draft.wpm * 60, 1),
                json.dumps(shot_list, ensure_ascii=False),
                similarity_max,
                model,
                prompt_version,
            ),
        ).lastrowid
        conn.execute("DELETE FROM sources WHERE content_id = ?", (content_id,))
        conn.executemany(
            """
            INSERT INTO sources (content_id, url, title, publisher, claim, accessed_at)
            VALUES (?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            """,
            [(content_id, s.url, s.title, s.publisher, s.claim) for s in sources],
        )
        conn.execute(
            "UPDATE contents SET working_title = ?, hook_type = ?, beat_order = ? WHERE id = ?",
            (draft.working_title, draft.hook_type, draft.beat_order, content_id),
        )
        set_status(conn, content_id, "scripted")
    return script_id


# --- clients ---------------------------------------------------------------------------------


def gemini_client(ctx: AppContext) -> GeminiClient:
    if ctx.env.gemini_api_key is None:
        raise ConfigError("GEMINI_API_KEY is missing in .env")
    return GeminiClient(
        ctx.env.gemini_api_key.get_secret_value(),
        ctx.channel.llm.model,
        before_call=lambda: check_and_record(ctx.conn, ctx.channel, "gemini"),
    )


def http_client(ctx: AppContext) -> httpx.Client:
    agent = ctx.env.wikimedia_user_agent
    if not agent or "CHANNEL_EMAIL_HERE" in agent:
        raise ConfigError(
            "WIKIMEDIA_USER_AGENT is missing in .env; Wikimedia requires e.g. "
            "'CoilAndCogFactory/0.1 (https://coilandcog.github.io)'"
        )
    # Wikimedia blocks agents without contact details more often (robot policy, D-064).
    if not re.search(r"\(.*(https?://|@).*\)", agent):
        raise ConfigError(
            f"WIKIMEDIA_USER_AGENT '{agent}' has no contact details; use e.g. "
            "'CoilAndCogFactory/0.1 (https://coilandcog.github.io)'"
        )
    return httpx.Client(
        timeout=SOURCE_TIMEOUT_S, follow_redirects=True, headers={"User-Agent": agent}
    )
