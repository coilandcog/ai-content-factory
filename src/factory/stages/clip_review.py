"""Clip review (D-097): Gemini watches each Flow clip and frame image and checks it against its
prompt and the film rules (DESIGN_SYSTEM.md §10), before the owner spends more credits.

Advice only: no status changes. Every review is appended to clip_review.json in the item's
asset folder, so the history can feed later learning.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from factory.context import AppContext
from factory.errors import FactoryError
from factory.integrations.gemini import GeminiClient, Media
from factory.prompts import render_prompt
from factory.stages import generate
from factory.stages import lessons as lessons_stage
from factory.stages import script as script_stage
from factory.stages.voice import asset_dir

log = logging.getLogger("factory.clip_review")

REPORT_FILE = "clip_review.json"
VIDEO_TYPES = {".mp4": "video/mp4"}
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".webp": "image/webp"}  # fmt: skip


class ClipVerdict(BaseModel):
    verdict: Literal["pass", "fail"]
    score: int = Field(ge=1, le=10, description="1 = unusable, 10 = as good as the benchmarks")
    problems: list[str] = Field(description="each problem in one short sentence; [] if none")
    fix: str = Field(description="what to change in the prompt or in Flow; '' if nothing")


def review(
    ctx: AppContext,
    content_id: int,
    *,
    only: tuple[int, ...] | None = None,
    llm: GeminiClient | None = None,
) -> list[dict]:
    """Review the item's clips (shot_NN.mp4) and the images they start and end on
    (image_NN.png/.jpg/.webp, D-104) that exist, in shot order: images first, then the clip.
    Returns the new report entries."""
    row = ctx.conn.execute("SELECT * FROM contents WHERE id = ?", (content_id,)).fetchone()
    if row is None:
        raise FactoryError(f"content #{content_id} does not exist")
    script = generate.current_script(ctx, content_id)
    shots = [s for s in json.loads(script["shot_list_json"]) if s["kind"] == "flow_clip"]
    if any("style" not in s["prompt_vars"] for s in shots):
        raise FactoryError(
            f"#{content_id} has no story shots (made before D-104): run "
            f"`factory generate --restage {content_id}` first"
        )
    _, frames = generate.images_for(ctx, shots)
    if only is not None:
        unknown = set(only) - {s["n"] for s in shots}
        if unknown:
            raise FactoryError(f"--shots {sorted(unknown)} are not flow_clips of #{content_id}")
        shots = [s for s in shots if s["n"] in only]
    issued = _issued_prompts(ctx, content_id)
    versions = generate.issued_prompts(ctx, content_id)
    todo, queued = [], set()
    for shot in shots:
        n = shot["n"]
        for image in frames[n]:  # its start and end image, each reviewed once
            path = _find(_folders(ctx, content_id), f"image_{image:02d}", IMAGE_TYPES)
            if path and image not in queued:
                queued.add(image)
                todo.append((shot, "frame", path, issued.get(f"image_{image}", ""),
                             f"image_{image}"))  # fmt: skip
        clip = _find(_folders(ctx, content_id), f"shot_{n:02d}", VIDEO_TYPES)
        if clip:
            todo.append((shot, "clip", clip, issued.get(str(n), ""), str(n)))
    if not todo:
        names = ", ".join(
            f"image_{frames[s['n']][0]:02d}.png / image_{frames[s['n']][1]:02d}.png / "
            f"shot_{s['n']:02d}.mp4"
            for s in shots
        )
        raise FactoryError(
            f"nothing to review for #{content_id}: save {names} in "
            f"{generate.windows_path(generate.inbox_dir(ctx, content_id))}"
        )
    llm = llm or script_stage.gemini_client(ctx)
    prompts_root = ctx.root / ctx.settings.paths.prompts_dir
    system = render_prompt("clip_review_system", ctx.channel, prompts_root=prompts_root).text
    entries = []
    for shot, kind, path, shot_prompt, key in todo:
        version = versions.get(key, {})
        made = datetime.fromtimestamp(path.stat().st_mtime, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        user = render_prompt(
            "clip_review",
            ctx.channel,
            prompts_root=prompts_root,
            kind=kind,
            object=row["object_name"],
            beat=shot["beat"],
            shot_prompt=shot_prompt,
            lessons=lessons_stage.approved_for_prompts(ctx, kind),
        )
        data = path.read_bytes()
        types = VIDEO_TYPES if kind == "clip" else IMAGE_TYPES
        verdict = llm.generate_json(
            system=system,
            prompt=user.text,
            schema=ClipVerdict,
            media=Media(data=data, mime_type=types[path.suffix.lower()]),
        )
        entries.append({
            "reviewed_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "shot": shot["n"], "kind": kind, "file": path.name,
            "sha256": hashlib.sha256(data).hexdigest(), "model": llm.model,
            "prompt_sha256": version.get("sha256"), "prompt_issued_at": version.get("issued_at"),
            "file_saved_at": made,
            # D-098: saved before its prompt was (re)issued, so it was made from an older prompt
            "older_than_prompt": bool(version) and made < version["issued_at"],
            **verdict.model_dump(),
        })  # fmt: skip
        log.info("#%s %s: %s %d/10", content_id, path.name, verdict.verdict, verdict.score,
                 extra={"content_id": content_id, "event": "clip_reviewed"})  # fmt: skip
    report = asset_dir(ctx, content_id) / REPORT_FILE
    report.parent.mkdir(parents=True, exist_ok=True)
    history = json.loads(report.read_text()) if report.is_file() else []
    report.write_text(json.dumps(history + entries, indent=2, ensure_ascii=False))
    return entries


def _issued_prompts(ctx: AppContext, content_id: int) -> dict[str, str]:
    path = asset_dir(ctx, content_id) / generate.PROMPTS_FILE
    if not path.is_file():
        raise FactoryError(f"#{content_id} has no Flow sheet yet: run `factory generate` first")
    return json.loads(path.read_text())


def _folders(ctx: AppContext, content_id: int) -> list[Path]:
    """The Flow inbox first, then the project's clips folder (clips move there once complete)."""
    return [generate.inbox_dir(ctx, content_id), asset_dir(ctx, content_id) / "clips"]


def _find(folders: list[Path], stem: str, types: dict[str, str]) -> Path | None:
    for folder in folders:
        for ext in types:
            path = folder / f"{stem}{ext}"
            if path.is_file():
                return path
    return None
