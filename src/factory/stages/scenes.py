"""Scenes as units (ROADMAP 2.27, D-124): a Blender film is made of scene clips, each rendered,
checked, approved or redone on its own.

The rows come from the render job (scene times from the narration). `factory generate` takes each
scene's clip from the inbox once it checks out; the item is `generated` when every scene has a
clip. The owner watches each one (`factory scenes show`) and approves it or asks for a redo of
just that scene. An approval holds the clip's fingerprint (SHA-256), so a changed file is no
longer approved, and `assemble` only joins scenes that are approved and unchanged.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from pathlib import Path

from factory.blender import jobs
from factory.context import AppContext
from factory.db import set_status
from factory.errors import FactoryError
from factory.media import compose
from factory.stages import voice

log = logging.getLogger("factory.scenes")

SCENES_DIR = "scenes"  # in the item's asset folder: scene_NN.mp4
PENDING = ("waiting", "redo")  # scenes that still need a clip from the render PC
REDO_STATUSES = ("awaiting_clips", "generated", "assembled")  # before Gate 2 approval


def clip_path(ctx: AppContext, content_id: int, n: int) -> Path:
    return voice.asset_dir(ctx, content_id) / SCENES_DIR / f"scene_{n:02d}.mp4"


def rows(conn: sqlite3.Connection, content_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM scenes WHERE content_id = ? ORDER BY chapter, n", (content_id,)
    ).fetchall()


def pending(conn: sqlite3.Connection, content_id: int) -> list[int]:
    return [r["n"] for r in rows(conn, content_id) if r["status"] in PENDING]


def create(ctx: AppContext, content_id: int, job: jobs.Job) -> None:
    """One row per scene of the job, with the words spoken while it is on screen. Any older rows
    (and their history) are replaced: this runs when the item's render job is first issued."""
    words = json.loads((voice.asset_dir(ctx, content_id) / voice.WORDS_FILE).read_text())
    spec = job.spec
    ends = [s["start"] for s in spec["scenes"][1:]] + [spec["duration_s"]]
    with ctx.conn:
        old = [r["id"] for r in rows(ctx.conn, content_id)]
        ctx.conn.executemany("DELETE FROM scene_reviews WHERE scene_id = ?", [(i,) for i in old])
        ctx.conn.execute("DELETE FROM scenes WHERE content_id = ?", (content_id,))
        for scene, end in zip(spec["scenes"], ends, strict=True):
            spoken = " ".join(w["text"] for w in words if scene["start"] <= w["start_s"] < end)
            ctx.conn.execute(
                """
                INSERT INTO scenes (content_id, n, role, start_s, end_s, first_frame,
                                    last_frame, narration)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (content_id, scene["n"], scene["role"], scene["start"], end,
                 scene["first_frame"], scene["last_frame"], spoken),
            )  # fmt: skip


def matches(conn: sqlite3.Connection, content_id: int, job: jobs.Job) -> bool:
    """True when the rows have the job's scenes and frames. False when there are none or the
    timings changed (e.g. D-126) before any clip arrived, so they can be made again; changed
    timings after clips arrived would mix two cuts of the film, so that stops with a reason."""
    scene_rows = rows(conn, content_id)
    have = [(r["n"], r["role"], r["first_frame"], r["last_frame"]) for r in scene_rows]
    want = [(s["n"], s["role"], s["first_frame"], s["last_frame"]) for s in job.spec["scenes"]]
    if have == want:
        return True
    if any(r["path"] for r in scene_rows):
        raise FactoryError(
            f"#{content_id}'s scene timings changed after clips arrived; to start over, redo "
            f"every scene: factory scenes redo {content_id} --scene "
            f"{','.join(str(n) for n, *_ in want)} --reason ..."
        )
    return False


def collect(ctx: AppContext, content_id: int, job: jobs.Job) -> list[str]:
    """Take every waiting scene's clip that checks out; returns what is still missing or wrong."""
    problems: list[str] = []
    for n in pending(ctx.conn, content_id):
        clip, problem = jobs.find_clip(ctx, job, n)
        if clip is None:
            problems.append(problem or f"scene {n} missing")
            continue
        target = jobs.copy_clip(clip, clip_path(ctx, content_id, n))
        with ctx.conn:
            ctx.conn.execute(
                """
                UPDATE scenes SET status = 'rendered', path = ?, sha256 = ?,
                    approved_sha256 = NULL, reason = NULL,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                WHERE content_id = ? AND n = ?
                """,
                (target.relative_to(ctx.root).as_posix(), _sha(target), content_id, n),
            )
        log.info("#%s scene %d: %s in place, waiting for approval", content_id, n, clip.name,
                 extra={"content_id": content_id, "event": "scene_collected"})  # fmt: skip
    return problems


def asset_rows(ctx: AppContext, content_id: int, job: jobs.Job) -> list[dict]:
    """The scene clips as `assets` rows (kind blender, shot_n = scene number)."""
    spec = job.spec
    out = []
    for r in rows(ctx.conn, content_id):
        if r["path"]:
            out.append({
                "path": ctx.root / r["path"], "shot_n": r["n"], "generator": "blender",
                "model": f"blender/{spec['machine']}", "prompt": f"job {job.job_id}",
                "license": "own_work", "width": spec["width"], "height": spec["height"],
                "duration_s": round((r["last_frame"] - r["first_frame"] + 1) / spec["fps"], 3),
            })  # fmt: skip
    return out


def approve(ctx: AppContext, content_id: int, numbers: list[int] | None = None) -> list[int]:
    """Approve the given rendered scenes (all rendered ones when None); returns those approved.
    The approval holds the clip's fingerprint as it is now."""
    scenes = _scenes(ctx, content_id, numbers)
    not_ready = [r["n"] for r in scenes if r["status"] in PENDING]
    if numbers and not_ready:
        raise FactoryError(f"scene {', '.join(map(str, not_ready))} has no clip yet")
    approved = []
    with ctx.conn:
        for r in scenes:
            if r["status"] in PENDING:
                continue
            sha = _sha(ctx.root / r["path"])
            ctx.conn.execute(
                "UPDATE scenes SET status = 'approved', sha256 = ?, approved_sha256 = ?, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
                (sha, sha, r["id"]),
            )
            ctx.conn.execute(
                "INSERT INTO scene_reviews (scene_id, decision, sha256) VALUES (?, 'approve', ?)",
                (r["id"], sha),
            )
            approved.append(r["n"])
    log.info("#%s scenes approved: %s", content_id, approved,
             extra={"content_id": content_id, "event": "scenes_approved"})  # fmt: skip
    return approved


def redo(ctx: AppContext, content_id: int, numbers: list[int], reason: str) -> Path:
    """Only these scenes get rendered again: their clips are set aside (old_*.mp4 in the inbox),
    their approvals end, any renders of the whole video are removed, the item waits for clips,
    and a job zip that renders just these scenes is written. Returns the zip."""
    from factory.stages.generate import windows_path  # here: generate imports this module
    from factory.stages.review import REDO_REMOVES

    if not reason.strip():
        raise FactoryError("a redo needs a reason (--reason)")
    item = ctx.conn.execute("SELECT * FROM contents WHERE id = ?", (content_id,)).fetchone()
    if item is None:
        raise FactoryError(f"content #{content_id} does not exist")
    if item["status"] not in REDO_STATUSES:
        raise FactoryError(f"content #{content_id} is {item['status']}: scenes can be redone "
                           f"while it is {', '.join(REDO_STATUSES)}")  # fmt: skip
    scenes = _scenes(ctx, content_id, numbers)
    job = jobs.job_for(ctx, item)
    inbox, folder = jobs.inbox_dir(ctx, content_id), voice.asset_dir(ctx, content_id)
    with ctx.conn:
        for r in scenes:
            for old in inbox.glob(f"scene_*_{r['n']:02d}.mp4"):  # never taken again by mistake
                old.replace(inbox / f"old_{old.name}")
            clip_path(ctx, content_id, r["n"]).unlink(missing_ok=True)
            ctx.conn.execute(
                "UPDATE scenes SET status = 'redo', path = NULL, sha256 = NULL, "
                "approved_sha256 = NULL, reason = ?, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
                (reason.strip(), r["id"]),
            )
            ctx.conn.execute(
                "INSERT INTO scene_reviews (scene_id, decision, sha256, reason) "
                "VALUES (?, 'redo', ?, ?)",
                (r["id"], r["sha256"], reason.strip()),
            )
        for kind, name in REDO_REMOVES.items():
            ctx.conn.execute(
                "DELETE FROM assets WHERE content_id = ? AND kind = ?", (content_id, kind)
            )
            (folder / name).unlink(missing_ok=True)
        (folder / "qa_report.json").unlink(missing_ok=True)
        ctx.conn.executemany(
            "DELETE FROM assets WHERE content_id = ? AND kind = 'blender' AND shot_n = ?",
            [(content_id, r["n"]) for r in scenes],
        )
        which = ", ".join(str(r["n"]) for r in scenes)
        if item["status"] != "awaiting_clips":
            set_status(ctx.conn, content_id, "awaiting_clips", reason=f"redo of scene {which}")
    out = jobs.write_zip(ctx, job, pending(ctx.conn, content_id), windows_path(inbox))
    log.info("#%s redo of scene %s (%s): render %s", content_id, which, reason.strip(),
             windows_path(out),
             extra={"content_id": content_id, "event": "scenes_redo"})  # fmt: skip
    return out


def not_ready(ctx: AppContext, content_id: int) -> list[str]:
    """Why the scenes can't be assembled yet; empty when every scene is approved and unchanged."""
    problems = []
    scene_rows = rows(ctx.conn, content_id)
    if not scene_rows:
        return ["no scenes yet (run `factory generate`)"]
    for r in scene_rows:
        if r["status"] != "approved":
            problems.append(f"scene {r['n']} is {r['status']}")
        elif not (ctx.root / r["path"]).is_file():
            problems.append(f"scene {r['n']}'s clip is missing")
        elif _sha(ctx.root / r["path"]) != r["approved_sha256"]:
            problems.append(f"scene {r['n']} changed since it was approved")
    return problems


def segments(ctx: AppContext, content_id: int, voice_s: float) -> list[compose.Segment]:
    """The approved scene clips in order on the timeline; the last one ends with the voice."""
    out, used = [], 0.0
    scene_rows = rows(ctx.conn, content_id)
    for i, r in enumerate(scene_rows):
        length = (r["last_frame"] - r["first_frame"] + 1) / compose.FPS
        slot = voice_s - used if i == len(scene_rows) - 1 else length
        out.append(compose.Segment(r["n"], "flow_clip", ctx.root / r["path"], length, slot))
        used += slot
    return out


def summary(conn: sqlite3.Connection, content_id: int) -> list[str]:
    """Gate 2 lines: how many scenes are approved, and any that aren't."""
    scene_rows = rows(conn, content_id)
    if not scene_rows:
        return []
    approved = [r for r in scene_rows if r["status"] == "approved"]
    lines = [f"SCENES: {len(approved)}/{len(scene_rows)} approved"]
    lines += [f"  scene {r['n']} ({r['role']}): {r['status']}" for r in scene_rows
              if r["status"] != "approved"]  # fmt: skip
    return lines


def report(ctx: AppContext, content_id: int) -> str:
    """`factory scenes show`: every scene with its words, status and the clip to open."""
    from factory.stages.generate import windows_path

    scene_rows = rows(ctx.conn, content_id)
    if not scene_rows:
        return f"#{content_id} has no scenes yet (only Blender items have scenes, after generate)"
    lines = [f"#{content_id} scenes:"]
    for r in scene_rows:
        mark = {"approved": "✓", "rendered": "?", "waiting": "…", "redo": "↻"}[r["status"]]
        state = r["status"]
        if r["status"] == "approved" and r["path"] and (ctx.root / r["path"]).is_file() \
                and _sha(ctx.root / r["path"]) != r["approved_sha256"]:  # fmt: skip
            mark, state = "!", "CHANGED since approval"
        lines.append(f"{mark} {r['n']}. {r['role']:<5} {r['start_s']:5.1f}-{r['end_s']:5.1f} s"
                     f"  frames {r['first_frame']}-{r['last_frame']}  {state}")  # fmt: skip
        lines.append(f'     "{r["narration"]}"')
        if r["reason"]:
            lines.append(f"     redo: {r['reason']}")
        if r["path"]:
            lines.append(f"     {windows_path(ctx.root / r['path'])}")
    left = [r["n"] for r in scene_rows if r["status"] == "rendered"]
    if left:
        lines.append(f"Next: watch them, then `factory scenes approve {content_id}` "
                     f"(or --scene N), or `factory scenes redo {content_id} --scene N "
                     "--reason ...`")  # fmt: skip
    return "\n".join(lines)


def _scenes(ctx: AppContext, content_id: int, numbers: list[int] | None) -> list[sqlite3.Row]:
    scene_rows = rows(ctx.conn, content_id)
    if not scene_rows:
        raise FactoryError(f"content #{content_id} has no scenes")
    if numbers is None:
        return scene_rows
    known = {r["n"] for r in scene_rows}
    unknown = sorted(set(numbers) - known)
    if unknown:
        raise FactoryError(f"#{content_id} has no scene {', '.join(map(str, unknown))} "
                           f"(scenes: 1-{max(known)})")  # fmt: skip
    return [r for r in scene_rows if r["n"] in numbers]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
