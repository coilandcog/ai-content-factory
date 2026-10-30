"""Reference library (ROADMAP 2.20, D-100): images and clips the owner likes become style notes.

`add` lets Gemini study each file in data/inbox/references/ and stores concrete notes as
'pending'; only notes the owner approves reach the film rules (next to the benchmarks). The
files move to data/references/ and are never used in a video: they inspire, they aren't copied.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sqlite3
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from factory.context import AppContext
from factory.errors import FactoryError
from factory.integrations.gemini import MAX_INLINE_BYTES, GeminiClient, Media
from factory.media.ffmpeg import run_ffmpeg
from factory.prompts import render_prompt
from factory.stages import script as script_stage

log = logging.getLogger("factory.references")

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".webp": "image/webp"}  # fmt: skip
VIDEO_TYPES = {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}
PHRASES = (3, 6)
PROMPT_LIMIT = 8  # newest approved notes in the film rules, so prompts stay short
SHRINK_SECONDS = 20  # a too-large clip is cut to its first 20 s at 720p before sending


class ReferenceNotes(BaseModel):
    summary: str = Field(description="one sentence: what this reference shows")
    lighting: str
    camera: str = Field(description="angle, lens feel and camera movement")
    composition: str
    motion: str = Field(description="what moves and how; 'none' for a still image")
    materials_colours: str
    prompt_phrases: list[str] = Field(description="3-6 short phrases to reuse in video prompts")
    has_people: bool = Field(description="true if a person, face or hands are visible")

    @model_validator(mode="after")
    def _enough_phrases(self) -> ReferenceNotes:
        low, high = PHRASES
        if not low <= len(self.prompt_phrases) <= high:
            raise ValueError(f"give {low}-{high} prompt_phrases, not {len(self.prompt_phrases)}")
        return self


def inbox(ctx: AppContext) -> Path:
    return ctx.root / ctx.settings.paths.inbox_dir / "references"


def store(ctx: AppContext) -> Path:
    return ctx.root / ctx.settings.paths.data_dir / "references"


def add(
    ctx: AppContext, *, for_item: int | None = None, llm: GeminiClient | None = None
) -> list[dict]:
    """Study every new image or clip in the inbox; returns one result per file. With
    `for_item`, the notes only shape that item's prompts (D-104)."""
    if (
        for_item is not None
        and not ctx.conn.execute("SELECT 1 FROM contents WHERE id = ?", (for_item,)).fetchone()
    ):
        raise FactoryError(f"content #{for_item} does not exist")
    folder = inbox(ctx)
    files = sorted(p for p in folder.glob("*") if p.is_file()) if folder.is_dir() else []
    if not files:
        folder.mkdir(parents=True, exist_ok=True)
        raise FactoryError(
            f"no files in {folder.relative_to(ctx.root)}: save images or clips there"
        )
    channel_id = _channel_id(ctx)
    prompts_root = ctx.root / ctx.settings.paths.prompts_dir
    system = render_prompt("script_system", ctx.channel, prompts_root=prompts_root).text
    results = []
    for path in files:
        mime = {**IMAGE_TYPES, **VIDEO_TYPES}.get(path.suffix.lower())
        if mime is None:
            results.append({"file": path.name, "skipped": "not an image or video (.png .jpg "
                            ".jpeg .webp .mp4 .mov .webm)"})  # fmt: skip
            continue
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        known = ctx.conn.execute(
            "SELECT id FROM reference_notes WHERE channel_id = ? AND sha256 = ?", (channel_id, sha)
        ).fetchone()
        if known:
            results.append({"file": path.name, "skipped": f"already in the library as #{known[0]}"})
            continue
        kind = "image" if mime.startswith("image/") else "video"
        llm = llm or script_stage.gemini_client(ctx)
        user = render_prompt("reference_notes", ctx.channel, prompts_root=prompts_root, kind=kind)
        notes = llm.generate_json(
            system=system,
            prompt=user.text,
            schema=ReferenceNotes,
            media=_media(path, mime, kind),
        )
        ref_id = _save(ctx, channel_id, path, kind, sha, notes, llm.model, for_item)
        results.append({"file": path.name, "id": ref_id, **notes.model_dump()})
        log.info("reference #%s from %s (pending)", ref_id, path.name,
                 extra={"event": "reference_added"})  # fmt: skip
    return results


def _media(path: Path, mime: str, kind: str) -> Media:
    data = path.read_bytes()
    if kind == "video" and len(data) > MAX_INLINE_BYTES:
        with tempfile.TemporaryDirectory() as tmp:
            small = Path(tmp) / "small.mp4"
            run_ffmpeg(["-i", str(path), "-t", str(SHRINK_SECONDS), "-vf", "scale=-2:720",
                        "-c:v", "libx264", "-crf", "30", "-an", str(small)])  # fmt: skip
            data, mime = small.read_bytes(), "video/mp4"
    return Media(data=data, mime_type=mime)


def _save(
    ctx: AppContext,
    channel_id: int,
    path: Path,
    kind: str,
    sha: str,
    notes: ReferenceNotes,
    model: str,
    for_item: int | None = None,
) -> int:
    with ctx.conn:
        ref_id = ctx.conn.execute(
            """
            INSERT INTO reference_notes (channel_id, kind, original_name, path, sha256, summary,
                lighting, camera, composition, motion, materials_colours, prompt_phrases_json,
                has_people, model, content_id)
            VALUES (?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (channel_id, kind, path.name, sha, notes.summary, notes.lighting, notes.camera,
             notes.composition, notes.motion, notes.materials_colours,
             json.dumps(notes.prompt_phrases), int(notes.has_people), model, for_item),
        ).lastrowid  # fmt: skip
        target = store(ctx) / f"{ref_id:04d}_{path.name}"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(path, target)
        ctx.conn.execute(
            "UPDATE reference_notes SET path = ? WHERE id = ?",
            (target.relative_to(ctx.root).as_posix(), ref_id),
        )
    return ref_id


def listing(ctx: AppContext, *, include_removed: bool = False) -> list[sqlite3.Row]:
    return ctx.conn.execute(
        f"""
        SELECT * FROM reference_notes WHERE channel_id = ?
        {"" if include_removed else "AND status <> 'removed'"} ORDER BY id
        """,
        (_channel_id(ctx),),
    ).fetchall()


def decide(ctx: AppContext, ref_id: int, status: str, owner_note: str | None = None) -> None:
    """Approve (optionally with your own note, which then counts more than Gemini's) or remove."""
    row = ctx.conn.execute(
        "SELECT status FROM reference_notes WHERE id = ? AND channel_id = ?",
        (ref_id, _channel_id(ctx)),
    ).fetchone()
    if row is None:
        raise FactoryError(f"reference #{ref_id} does not exist")
    with ctx.conn:
        ctx.conn.execute(
            """
            UPDATE reference_notes SET status = ?, owner_note = COALESCE(?, owner_note),
                decided_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
            WHERE id = ?
            """,
            (status, owner_note.strip() if owner_note and owner_note.strip() else None, ref_id),
        )


def approved_for_prompts(ctx: AppContext, content_id: int | None = None) -> list[dict]:
    """The newest PROMPT_LIMIT approved notes, oldest first, for the film rules: general ones
    plus, for `content_id`, the ones made for that item (D-104), which come first in the limit."""
    try:
        rows = ctx.conn.execute(
            """
            SELECT * FROM reference_notes WHERE status = 'approved' AND channel_id =
                (SELECT id FROM channels WHERE slug = ?)
                AND (content_id IS NULL OR content_id = ?)
            ORDER BY content_id IS NULL, id DESC LIMIT ?
            """,
            (ctx.channel.slug, content_id, PROMPT_LIMIT),
        ).fetchall()
    except sqlite3.OperationalError as exc:  # database from before migration 002 or 005
        raise FactoryError("run `uv run factory db init` to update the reference library") from exc
    return [
        {"summary": r["summary"], "owner_note": r["owner_note"], "lighting": r["lighting"],
         "for_item": r["content_id"],
         "camera": r["camera"], "motion": r["motion"],
         "phrases": json.loads(r["prompt_phrases_json"])}
        for r in reversed(rows)
    ]  # fmt: skip


def _channel_id(ctx: AppContext) -> int:
    row = ctx.conn.execute("SELECT id FROM channels WHERE slug = ?", (ctx.channel.slug,)).fetchone()
    if row is None:
        raise FactoryError(
            f"channel {ctx.channel.slug} is not in the database: run factory db init"
        )
    return row[0]
