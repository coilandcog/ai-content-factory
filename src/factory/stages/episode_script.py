"""Long-video scripts in chapters (ROADMAP 2.29, D-129).

A long video (format `episode`, 8-9 min, D-128) is written in two steps, because the free model
breaks rules far more often in one long answer:
1. the outline: 4 chapter titles and, per chapter, 3-6 scenes, each naming one of the Blender
   model's scene templates and what it explains, plus the hook type and 3 sources;
2. one call per chapter: the narration of each of its scenes, given the outline, the earlier
   chapters and the verified facts.
Code checks every step and sends a broken one back: only the model's templates, a sentence never
names a model part its scene doesn't show, only verified numbers as digits, no picture-talk, the
Engineer's Note as its own scene in the last chapter, and each chapter's length.
The script is stored like any other (`body_text` = the whole narration), with the chapter and
scene plan in `shot_list_json` and the chapter titles in `chapters`.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from types import ModuleType
from typing import Any, ClassVar, Literal

import httpx
from pydantic import BaseModel, create_model, model_validator

from factory import blender
from factory.budgets import check_and_record
from factory.context import AppContext
from factory.db import set_status
from factory.errors import FactoryError, PolicyBlockedError
from factory.integrations.gemini import GeminiClient
from factory.integrations.wikimedia import page_summary
from factory.prompts import render_prompt
from factory.similarity import closest
from factory.stages import script as script_stage
from factory.stages.script import (
    CLAIM_PHRASES,
    NOTE_LEAD_IN,
    NUMBER_WORDS,
    PICTURE_TALK,
    PolicyCheck,
    SourceDraft,
    word_count,
    word_range,
)

CHAPTERS = 4
SCENES_PER_CHAPTER = (4, 6)  # short scenes: flash-lite writes 60-80 words per scene
CHAPTER_TITLE_MAX = 40
CHAPTER_SLACK = 0.20  # each chapter within ±20% of its share; later ones make up the total
HOOK_MAX = 16  # a long video may open a little slower than a Short (12 words, D-083)
SENTENCE = re.compile(r"[^.!?]+[.!?]*")
LONG_TIMEOUT_S = 120  # the long-script model thinks 20-60 s per answer
CHAPTER_REASKS = 6  # length is the rule flash-lite misses most; each chapter is a short answer
NUMBER = re.compile(r"\d[\d,.]*")


# --- step 1: the outline -------------------------------------------------------------------------


class OutlineScene(BaseModel):
    template: str
    explains: str


class OutlineChapter(BaseModel):
    title: str
    scenes: list[OutlineScene]


class OutlineBase(BaseModel):
    """4 chapters of scenes; hook_type is added per item (the previous video's is left out)."""

    policy: PolicyCheck
    working_title: str
    chapters: list[OutlineChapter]
    sources: list[SourceDraft]

    templates: ClassVar[tuple[str, ...]] = ()
    reference_url: ClassVar[str | None] = None

    @model_validator(mode="after")
    def _rules(self) -> OutlineBase:
        if self.policy.allowed and (problems := outline_problems(self)):
            raise ValueError("; ".join(problems))
        return self


def outline_problems(outline: Any) -> list[str]:
    problems = []
    if not outline.working_title.strip():
        problems.append("working_title must not be empty")
    if len(outline.chapters) != CHAPTERS:
        problems.append(f"{len(outline.chapters)} chapters; give exactly {CHAPTERS}")
    low, high = SCENES_PER_CHAPTER
    for n, chapter in enumerate(outline.chapters, 1):
        title = chapter.title.strip()
        if not title or len(title) > CHAPTER_TITLE_MAX:
            problems.append(f"chapter {n}'s title must be 1-{CHAPTER_TITLE_MAX} characters")
        if not low <= len(chapter.scenes) <= high:
            problems.append(f"chapter {n} has {len(chapter.scenes)} scenes; give {low}-{high}")
        for k, scene in enumerate(chapter.scenes, 1):
            if scene.template not in outline.templates:
                known = ", ".join(outline.templates)
                problems.append(f"chapter {n} scene {k} uses template '{scene.template}', which "
                                f"the model can't show; use one of {known}")  # fmt: skip
    scenes = [s for c in outline.chapters for s in c.scenes]
    if scenes and scenes[0].template != "whole":
        problems.append("the first scene (the hook) uses template 'whole'")
    notes = [(n, s) for n, c in enumerate(outline.chapters, 1) for s in c.scenes
             if s.template == "note"]  # fmt: skip
    if len(notes) != 1 or (notes and notes[0][0] != len(outline.chapters)):
        problems.append(f"exactly one scene uses template 'note' (the Engineer's Note), in "
                        f"chapter {CHAPTERS}")  # fmt: skip
    if scenes and scenes[-1].template not in ("whole", "exploded"):
        problems.append("the last scene (the closing line) uses template 'whole' or 'exploded'")
    problems += script_stage._source_problems(outline)
    return problems


def outline_schema(model: ModuleType, hooks: tuple[str, ...], reference_url: str | None):
    schema = create_model("EpisodeOutline", __base__=OutlineBase,
                          hook_type=(Literal[hooks], ...))  # fmt: skip
    schema.templates = tuple(model.TEMPLATES)
    schema.reference_url = reference_url
    return schema


# --- step 2: one chapter's narration -------------------------------------------------------------


class SceneText(BaseModel):
    narration: str


class ChapterBase(BaseModel):
    scenes: list[SceneText]

    plan: ClassVar[OutlineChapter]
    model: ClassVar[ModuleType]
    introduced: ClassVar[frozenset[str]] = frozenset()  # parts earlier chapters explained
    facts: ClassVar[tuple[str, ...]] = ()
    words: ClassVar[tuple[int, int]] = (0, 10_000)
    first: ClassVar[bool] = False
    last: ClassVar[bool] = False

    @model_validator(mode="after")
    def _rules(self) -> ChapterBase:
        if problems := chapter_problems(self):
            raise ValueError("; ".join(problems))
        return self


def chapter_words(low: int, high: int, share: float, n: int, so_far: int) -> tuple[int, int]:
    """The words chapter n may have: about its share (±CHAPTER_SLACK) while the chapters still to
    come can make the whole video land in low-high; the last chapter takes what is left."""
    after = CHAPTERS - n  # chapters still to come after this one
    least = max(round(share * (1 - CHAPTER_SLACK)), low - so_far - round(after * share * 1.2))
    most = min(round(share * (1 + CHAPTER_SLACK)), high - so_far - round(after * share * 0.8))
    if after == 0:
        least, most = low - so_far, high - so_far
    return max(least, 1), max(most, least)


def chapter_problems(chapter: Any) -> list[str]:
    plan, model = chapter.plan, chapter.model
    if len(chapter.scenes) != len(plan.scenes):
        return [f"{len(chapter.scenes)} scenes; the outline has {len(plan.scenes)}"]
    problems = []
    introduced = set(chapter.introduced)
    for k, (text, scene) in enumerate(zip(chapter.scenes, plan.scenes, strict=True), 1):
        narration = " ".join(text.narration.split())
        label = f"scene {k} [{scene.template}]"
        if not narration:
            problems.append(f"{label} has no narration")
            continue
        found = part_problems(model, scene.template, narration, frozenset(introduced))
        problems += [f"{label}: {p}" for p in found]
        if scene.template not in OPEN_TEMPLATES and not shown_parts(model, scene.template,
                                                                      narration):  # fmt: skip
            shows = ", ".join(model.TEMPLATES[scene.template].shows)
            problems.append(f"{label} never names what it shows ({shows}): explain that part")
        introduced |= shown_parts(model, scene.template, narration)
        if scene.template == "note":
            if not narration.startswith(NOTE_LEAD_IN):
                problems.append(f'{label} starts with "{NOTE_LEAD_IN}"')
        elif claim := CLAIM_PHRASES.search(narration):
            problems.append(f"{label} says '{claim.group(0)}': state the fact plainly")
        if talk := PICTURE_TALK.search(narration):
            problems.append(f"{label} says '{talk.group(0)}': explain, don't narrate the picture")
    hook = SENTENCE.findall(chapter.scenes[0].narration.strip()) if chapter.first else []
    if hook and word_count(hook[0]) > HOOK_MAX:
        problems.append(f"the hook (scene 1's first sentence) has {word_count(hook[0])} "
                        f"words; at most {HOOK_MAX}")  # fmt: skip
    if chapter.last and chapter.scenes[-1].narration.strip().endswith("?"):
        problems.append("the closing line (the last scene) ends on a statement, not a question")
    text = " ".join(s.narration for s in chapter.scenes)
    allowed = " ".join(chapter.facts)
    unverified = [n for n in NUMBER.findall(text) if n.rstrip(".,") not in allowed]
    if unverified:
        problems.append(f"numbers {', '.join(unverified)} are not verified facts; "
                        "describe without them")  # fmt: skip
    if spelled := NUMBER_WORDS.findall(text):
        problems.append(f"write numbers as digits ({', '.join(sorted(set(spelled)))})")
    low, high = chapter.words
    if not low <= (words := word_count(text)) <= high:
        aim = round((low + high) / 2 / len(chapter.scenes))
        each = ", ".join(f"scene {k}: {word_count(s.narration)}"
                         for k, s in enumerate(chapter.scenes, 1))  # fmt: skip
        problems.append(f"the chapter has {words} words; it must have {low}-{high}, about {aim} "
                        f"per scene ({each}): {'add to' if words < low else 'shorten'} the "
                        "scenes furthest from that")  # fmt: skip
    return problems


OPEN_TEMPLATES = ("whole", "exploded", "note")  # views of the whole machine: no part required


def named_parts(model: ModuleType, text: str) -> set[str]:
    return {part for part, pattern in model.PARTS.items() if re.search(pattern, text, re.I)}


def shown_parts(model: ModuleType, template: str, narration: str) -> set[str]:
    """The parts this narration names that its template shows ("*" shows every part)."""
    shows = model.TEMPLATES[template].shows
    named = named_parts(model, narration)
    return named if "*" in shows else named & set(shows)


def part_problems(
    model: ModuleType, template: str, narration: str, introduced: frozenset[str] = frozenset()
) -> list[str]:
    """Sentences that name a model part the template doesn't show and no earlier scene explained
    (D-122/D-129: the narration never explains a part the viewer can't see; a short reference
    back to a part already explained, like "the rotor's field current" over the regulator, is
    fine)."""
    shows = model.TEMPLATES[template].shows
    if "*" in shows:
        return []
    problems = []
    for sentence in SENTENCE.findall(narration):
        for part in sorted(named_parts(model, sentence) - set(shows) - introduced):
            problems.append(f'"{sentence.strip()}" names the {part}, which this scene doesn\'t '
                            f"show ({', '.join(shows)}) and no earlier scene explained; explain "
                            "it in a scene that shows it, or leave it out")  # fmt: skip
    return problems


def chapter_schema(model: ModuleType, plan: OutlineChapter, facts: list[str], words: tuple,
                   first: bool, last: bool, introduced: frozenset[str] = frozenset()):  # fmt: skip
    schema = create_model("EpisodeChapter", __base__=ChapterBase)
    schema.plan, schema.model, schema.facts = plan, model, tuple(facts)
    schema.introduced = introduced
    schema.words, schema.first, schema.last = words, first, last
    return schema


# --- the whole script ----------------------------------------------------------------------------


@dataclass
class EpisodeScript:
    """What the two steps produced; `sources` and `narration()` as the script stage expects."""

    outline: Any
    chapters: list[list[str]]  # narration per scene, per chapter

    @property
    def sources(self) -> list[SourceDraft]:
        return self.outline.sources

    def scenes(self) -> list[dict[str, Any]]:
        out, n = [], 0
        for c, (plan, texts) in enumerate(
            zip(self.outline.chapters, self.chapters, strict=True), 1
        ):
            for scene, text in zip(plan.scenes, texts, strict=True):
                n += 1
                out.append({
                    "n": n, "chapter": c, "chapter_title": plan.title.strip(),
                    "template": scene.template, "narration": " ".join(text.split()),
                })  # fmt: skip
        return out

    def narration(self) -> str:
        return " ".join(s["narration"] for s in self.scenes())


def check_item(row: sqlite3.Row) -> ModuleType:
    """A long video needs the Blender style and a Blender model of its machine (no Gemini call
    is made otherwise)."""
    if row["visual_style"] != "blender":
        content_id, style = row["id"], row["visual_style"]
        raise FactoryError(f"long videos are made with Blender (D-128); #{content_id} is "
                           f"{style}: `factory style {content_id} blender`")  # fmt: skip
    return blender.model_for(row["object_name"])


def write(
    ctx: AppContext,
    row: sqlite3.Row,
    llm: GeminiClient,
    http: httpx.Client,
    feedback: script_stage.Feedback | None = None,
) -> int:
    """Outline, then each chapter; checks, sources and similarity as for any script; saved."""
    model = check_item(row)
    channel, conn = ctx.channel, ctx.conn
    if channel.llm.long_script_model:  # flash-lite can't hold a 1,200-word structure (D-129)
        llm = llm.with_model(channel.llm.long_script_model, timeout_s=LONG_TIMEOUT_S)
    reference = page_summary(
        http, row["object_name"], before_call=lambda: check_and_record(conn, channel, "wikimedia")
    )
    previous = script_stage.previous_variety(conn, row["channel_id"], row["id"])
    hooks = tuple(h for h in script_stage.HOOK_TYPES if h != previous[0])
    facts = [f.text for f in model.FACTS.values()]
    system = _render(ctx, "script_system")
    context = {"item": row, "model": model, "facts": list(model.FACTS.values()),
               "feedback": feedback, "reference": reference}  # fmt: skip
    outline_prompt = _render(ctx, "script_episode_outline", allowed_hooks=hooks,
                             series=getattr(channel.formats, row["format"]).series,
                             chapters=CHAPTERS, scenes=SCENES_PER_CHAPTER, **context)  # fmt: skip
    outline = llm.generate_json(
        system=system.text, prompt=outline_prompt.text,
        schema=outline_schema(model, hooks, reference.url if reference else None),
        max_reasks=script_stage.MAX_REASKS,
    )  # fmt: skip
    if not outline.policy.allowed:
        raise PolicyBlockedError(outline.policy.reason)
    low, high = word_range(channel, row["format"])
    share = (low + high) / 2 / CHAPTERS
    chapters: list[list[str]] = []
    introduced: set[str] = set()
    for n, plan in enumerate(outline.chapters, 1):
        words = chapter_words(low, high, share, n, sum(word_count(" ".join(c)) for c in chapters))
        # flash-lite writes about 15% under the words it is asked for, so it is asked for more
        scene_words = round(sum(words) / 2 / len(plan.scenes) * 1.15)
        prompt = _render(ctx, "script_episode_chapter", outline=outline, chapter_n=n,
                         earlier=chapters, words=words, note_lead_in=NOTE_LEAD_IN,
                         scene_words=scene_words, introduced=sorted(introduced),
                         **context)  # fmt: skip
        draft = llm.generate_json(
            system=system.text, prompt=prompt.text,
            schema=chapter_schema(model, plan, facts, words, first=n == 1, last=n == CHAPTERS,
                                  introduced=frozenset(introduced)),
            max_reasks=CHAPTER_REASKS,
        )  # fmt: skip
        chapters.append([s.narration for s in draft.scenes])
        for scene, text in zip(plan.scenes, chapters[-1], strict=True):
            introduced |= shown_parts(model, scene.template, text)
    script = EpisodeScript(outline, chapters)
    sources = script_stage._open_sources(ctx, row, llm, http, script, system.text)
    match = closest(conn, row["channel_id"], row["id"], script.narration(),
                    window=channel.scoring.similarity_window)  # fmt: skip
    threshold = channel.scoring.similarity_block_threshold
    if match.score > threshold:
        raise PolicyBlockedError(
            f"too similar to #{match.content_id} ({match.score:.2f} > {threshold:.2f})"
        )
    version = f"{system.version}+{outline_prompt.version}"
    return save(ctx, row["id"], script, sources, llm.model, version, match.score)


def save(
    ctx: AppContext,
    content_id: int,
    script: EpisodeScript,
    sources: list[SourceDraft],
    llm_model: str,
    prompt_version: str,
    similarity_max: float,
) -> int:
    """Store it as the item's current script; the chapter titles go into `chapters`."""
    conn, wpm = ctx.conn, ctx.channel.voice.target_wpm
    scenes = script.scenes()
    narration = script.narration()
    words = word_count(narration)
    note = next(s["narration"] for s in scenes if s["template"] == "note")
    plan = [
        {
            **s,
            "kind": "scene",
            "beat": s["template"],
            "description": s["narration"][:80],
            "duration_s": round(word_count(s["narration"]) / wpm * 60, 1),
            "prompt_vars": {},
        }
        for s in scenes
    ]  # fmt: skip  (list-shaped like a shot list, D-129)
    outline = script.outline
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
            (content_id, version, scenes[0]["narration"], narration, note,
             scenes[-1]["narration"], words, round(words / wpm * 60, 1),
             json.dumps(plan, ensure_ascii=False), similarity_max, llm_model, prompt_version),
        ).lastrowid  # fmt: skip
        conn.execute("DELETE FROM sources WHERE content_id = ?", (content_id,))
        conn.executemany(
            """
            INSERT INTO sources (content_id, url, title, publisher, claim, accessed_at)
            VALUES (?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            """,
            [(content_id, s.url, s.title, s.publisher, s.claim) for s in sources],
        )
        conn.execute("DELETE FROM chapters WHERE content_id = ?", (content_id,))
        conn.executemany(
            "INSERT INTO chapters (content_id, n, title) VALUES (?, ?, ?)",
            [(content_id, n, c.title.strip()) for n, c in enumerate(outline.chapters, 1)],
        )
        conn.execute(
            "UPDATE contents SET working_title = ?, hook_type = ?, beat_order = NULL WHERE id = ?",
            (outline.working_title, outline.hook_type, content_id),
        )
        set_status(conn, content_id, "scripted")
    return script_id


def chapters_report(plan: list[dict[str, Any]]) -> list[str]:
    """Gate 1 lines: each chapter with its scenes' templates and narration."""
    import textwrap

    lines, current = [], None
    for scene in plan:
        if scene["chapter"] != current:
            current = scene["chapter"]
            lines += ["", f"CHAPTER {current}: {scene['chapter_title']}"]
        lines.append(textwrap.fill(scene["narration"], width=96,
                                   initial_indent=f"  [{scene['template']}] ",
                                   subsequent_indent="    "))  # fmt: skip
    return lines


def is_episode_plan(plan: list[dict[str, Any]]) -> bool:
    return bool(plan) and plan[0].get("kind") == "scene"


def _render(ctx: AppContext, name: str, **variables: Any):  # noqa: ANN202
    return render_prompt(name, ctx.channel, prompts_root=ctx.root / ctx.settings.paths.prompts_dir,
                         **variables)  # fmt: skip
