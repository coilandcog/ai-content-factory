"""metadata.json: titles, captions, description, hashtags and platform flags (ROADMAP 1.11, D-071).

Gemini writes only the creative parts (title, summary, caption, hashtags) under schema rules.
Everything that must be exactly right is assembled by code: the music attribution and the AI
platform flags (CONTENT_STRATEGY.md §4 and §11). The description has no AI disclosure line (the
platform AI labels do that job, D-075) and no source links (D-077); the checked sources stay in
metadata.json and the database for fact-checking.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from factory.context import AppContext
from factory.errors import FactoryError
from factory.integrations.gemini import GeminiClient
from factory.prompts import render_prompt
from factory.stages import generate
from factory.stages import series as series_stage
from factory.stages.script import gemini_client
from factory.stages.voice import asset_dir

log = logging.getLogger("factory.metadata")

METADATA_FILE = "metadata.json"
TITLE_MAX = 70
MIN_SOURCES = 2
HASHTAG = re.compile(r"^#[A-Za-z][A-Za-z0-9_]{1,29}$")


class PostMetadata(BaseModel):
    """What Gemini writes. Every field is required (Gemini leaves out optional ones)."""

    youtube_title: str = Field(description=f"at most {TITLE_MAX} characters, object named early")
    youtube_summary: str = Field(description="1-2 plain sentences about what the viewer learns")
    youtube_hashtags: list[str] = Field(description="exactly 3, like #Engineering")
    tiktok_caption: str = Field(description="one hook line, no hashtags")
    tiktok_hashtags: list[str] = Field(description="3 to 5, like #HowItWorks")

    @field_validator("youtube_title")
    @classmethod
    def _title(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value or len(value) > TITLE_MAX:
            raise ValueError(f"title has {len(value)} characters; it must have 1-{TITLE_MAX}")
        return value

    @field_validator("youtube_summary", "tiktok_caption")
    @classmethod
    def _text(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("must not be empty")
        if "#" in value:
            raise ValueError("no hashtags here; they go in the hashtag lists")
        return value

    @field_validator("youtube_hashtags")
    @classmethod
    def _youtube_tags(cls, value: list[str]) -> list[str]:
        return _tags(value, 3, 3)

    @field_validator("tiktok_hashtags")
    @classmethod
    def _tiktok_tags(cls, value: list[str]) -> list[str]:
        return _tags(value, 3, 5)


def _tags(value: list[str], low: int, high: int) -> list[str]:
    tags = [tag.strip() if tag.strip().startswith("#") else f"#{tag.strip()}" for tag in value]
    bad = [tag for tag in tags if not HASHTAG.match(tag)]
    if bad:
        raise ValueError(f"hashtags must look like #OneWord: {bad}")
    if len(set(t.lower() for t in tags)) != len(tags) or not low <= len(tags) <= high:
        raise ValueError(f"needs {low}-{high} different hashtags, got {len(tags)}")
    return tags


# --- creating metadata.json --------------------------------------------------------------------


def ensure_metadata(ctx: AppContext, content_id: int, *, llm: GeminiClient | None = None) -> Path:
    """Create metadata.json if it doesn't exist yet (one Gemini call), and return its path."""
    path = asset_dir(ctx, content_id) / METADATA_FILE
    if path.is_file():
        return path
    item = _item(ctx, content_id)
    script = generate.current_script(ctx, content_id)
    sources = _sources(ctx.conn, content_id)
    if len(sources) < MIN_SOURCES:
        raise FactoryError(f"content #{content_id} has only {len(sources)} source(s); needs 2")
    llm = llm or gemini_client(ctx)
    root = ctx.root / ctx.settings.paths.prompts_dir
    system = render_prompt("metadata_system", ctx.channel, prompts_root=root)
    series = getattr(ctx.channel.formats, item["format"]).series
    user = render_prompt("metadata", ctx.channel, prompts_root=root, item=item, script=script,
                         title_max=TITLE_MAX, series=series)  # fmt: skip
    draft = llm.generate_json(system=system.text, prompt=user.text, schema=PostMetadata)
    data = build(ctx, item, draft, sources, _music(ctx.conn, content_id))
    data["generated_by"] = {
        "model": llm.model,
        "prompt_version": f"{system.version}+{user.version}",
    }
    write(ctx, content_id, data)
    log.info("#%s metadata written: %s", content_id, draft.youtube_title,
             extra={"content_id": content_id, "event": "metadata_written"})  # fmt: skip
    return path


def build(
    ctx: AppContext,
    item: sqlite3.Row,
    draft: PostMetadata,
    sources: list[sqlite3.Row],
    music: sqlite3.Row | None,
) -> dict[str, Any]:
    """The full metadata: Gemini's creative parts plus everything code must get exactly right."""
    channel = ctx.channel
    title, caption = draft.youtube_title, draft.tiktok_caption
    description = [draft.youtube_summary]
    lines, problem = chapters(ctx.conn, item["id"])
    if lines and problem is None:  # YouTube turns these lines into chapters (D-128)
        description += ["", *lines]
    part = series_stage.context(ctx.conn, item["id"])
    if part is not None:  # a series part: exact title, part number and links (D-112)
        title = part.title if len(part.title) <= TITLE_MAX else title
        caption = f"Part {part.part_n}/{part.parts}: {caption}"
        description += ["", f"Part {part.part_n} of {part.parts}.",
                        *series_stage.links(ctx.conn, item["id"])]  # fmt: skip
    if music is not None and music["attribution_text"]:
        description += ["", music["attribution_text"]]
    description += ["", " ".join(draft.youtube_hashtags)]
    return {
        "content_id": item["id"],
        "object": item["object_name"],
        "format": item["format"],
        "youtube": {
            "title": title,
            "description": "\n".join(description),
            "hashtags": draft.youtube_hashtags,
            "category_id": channel.platforms.youtube.category_id,
            "contains_synthetic_media": True,  # always disclose AI (D-039)
            "made_for_kids": False,
            "render": "render_youtube.mp4",
        },
        "tiktok": {
            "caption": f"{caption} {' '.join(draft.tiktok_hashtags)}",
            "hashtags": draft.tiktok_hashtags,
            "ai_generated_label": True,  # switch it on when posting (D-039)
            "add_tiktok_sound": True,  # the render has no music (D-042)
            "render": "render_tiktok.mp4",
        },
        "sources": [
            {"url": s["url"], "title": s["title"], "publisher": s["publisher"]} for s in sources
        ],  # fmt: skip
        "music": None
        if music is None
        else {
            "title": music["title"],
            "artist": music["artist"],
            "attribution": music["attribution_text"],
            "license_url": music["license_url"],
        },  # fmt: skip
        "cover": "cover.jpg",
    }


CHAPTERS_MIN = 3  # YouTube's rules for chapters in a description
CHAPTER_MIN_S = 10


def chapters(conn: sqlite3.Connection, content_id: int) -> tuple[list[str], str | None]:
    """YouTube chapter lines ("0:00 Intro", "2:05 The rotor") from the item's chapter titles
    and each chapter's first scene, plus why they break YouTube's rules (first at 0:00, at least
    3, each at least 10 s), or None. No chapters at all: ([], None)."""
    rows = conn.execute(
        """
        SELECT c.n, c.title, MIN(s.start_s) AS start_s, MAX(s.end_s) AS end_s
        FROM chapters c LEFT JOIN scenes s ON s.content_id = c.content_id AND s.chapter = c.n
        WHERE c.content_id = ? GROUP BY c.n, c.title ORDER BY c.n
        """,
        (content_id,),
    ).fetchall()
    if not rows:
        return [], None
    empty = [r["n"] for r in rows if r["start_s"] is None]
    if empty:
        return [], f"chapter {', '.join(map(str, empty))} has no scenes"
    starts = [int(r["start_s"]) for r in rows]
    ends = [*starts[1:], int(rows[-1]["end_s"])]
    lines = [f"{_timestamp(s)} {r['title'].strip()}" for s, r in zip(starts, rows, strict=True)]
    problems = []
    if starts[0] != 0:
        problems.append(f"the first chapter starts at {_timestamp(starts[0])}, not 0:00")
    if len(rows) < CHAPTERS_MIN:
        problems.append(f"{len(rows)} chapters (YouTube needs at least {CHAPTERS_MIN})")
    short = [r["n"] for r, a, b in zip(rows, starts, ends, strict=True) if b - a < CHAPTER_MIN_S]
    if short:
        problems.append(f"chapter {', '.join(map(str, short))} shorter than {CHAPTER_MIN_S} s")
    return lines, "; ".join(problems) or None


def _timestamp(seconds: int) -> str:
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def load(ctx: AppContext, content_id: int) -> dict[str, Any]:
    path = asset_dir(ctx, content_id) / METADATA_FILE
    if not path.is_file():
        raise FactoryError(f"content #{content_id} has no {METADATA_FILE}")
    return json.loads(path.read_text())


def write(ctx: AppContext, content_id: int, data: dict[str, Any]) -> Path:
    path = asset_dir(ctx, content_id) / METADATA_FILE
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    row = {"path": path, "generator": "factory", "license": "own_work"}
    generate.replace_assets(ctx, content_id, "metadata", [row])
    return path


def validate(data: dict[str, Any]) -> list[str]:
    """Problems with a (possibly hand-edited) metadata.json; [] = complete and within the rules."""
    problems: list[str] = []
    try:
        PostMetadata(
            youtube_title=data["youtube"]["title"],
            youtube_summary=data["youtube"]["description"].split("\n", 1)[0],
            youtube_hashtags=data["youtube"]["hashtags"],
            tiktok_caption=data["tiktok"]["caption"].split("#", 1)[0].strip() or "-",
            tiktok_hashtags=data["tiktok"]["hashtags"],
        )
    except (KeyError, TypeError, AttributeError) as exc:
        return [f"missing field: {exc}"]
    except ValidationError as exc:
        problems += [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
    recorded = [s for s in data.get("sources", []) if s.get("url")]
    if len(recorded) < MIN_SOURCES:  # kept for fact-checking, not shown in the description
        problems.append(f"metadata.json records {len(recorded)} source(s); needs {MIN_SOURCES}")
    if data["youtube"].get("contains_synthetic_media") is not True:
        problems.append("contains_synthetic_media must be true")
    if data["tiktok"].get("ai_generated_label") is not True:
        problems.append("ai_generated_label must be true")
    return problems


# --- helpers -----------------------------------------------------------------------------------


def _item(ctx: AppContext, content_id: int) -> sqlite3.Row:
    row = ctx.conn.execute("SELECT * FROM contents WHERE id = ?", (content_id,)).fetchone()
    if row is None:
        raise FactoryError(f"content #{content_id} does not exist")
    return row


def _sources(conn: sqlite3.Connection, content_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT url, title, publisher FROM sources WHERE content_id = ? ORDER BY id",
        (content_id,),
    ).fetchall()


def _music(conn: sqlite3.Connection, content_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT t.* FROM assets a JOIN music_tracks t ON t.id = a.music_track_id
        WHERE a.content_id = ? AND a.kind = 'music'
        """,
        (content_id,),
    ).fetchone()
