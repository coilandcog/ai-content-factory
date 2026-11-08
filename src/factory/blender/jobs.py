"""Blender render jobs (D-123, D-124): the zip the render PC renders, and the check of the scene
clips it sends back.

A job zip holds everything Blender needs and nothing else (no project code, no keys): render.py,
the shared kit, the machine's model, job.json (size, frame rate, each scene's start and frames,
from the narration's word timings, and which scenes to render), the label font and HOW_TO_RUN.txt.
Each scene comes back as scene_<job id>_NN.mp4, so a clip from an older job (e.g. before the
model changed) is never taken.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import sqlite3
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factory import blender
from factory.blueprint.canvas import LEAD_S, count_sentences, scene_starts, sentences
from factory.context import AppContext
from factory.errors import FactoryError
from factory.media.ffmpeg import probe
from factory.stages import voice

JOB_FILES = Path(__file__).resolve().parent / "job_files"
FPS = 30  # the same as the assembly (media/compose.py)
SIZES = {"9:16": (1080, 1920), "16:9": (1920, 1080)}  # Shorts; long videos (D-128)
MIN_SCENE_S = 1.5  # shorter scenes can't hold their camera moves
FONT_FILES = ("Inter-SemiBold.ttf", "OFL-Inter.txt")


@dataclass(frozen=True)
class Job:
    spec: dict  # job.json
    job_id: str  # first 8 characters of the spec's fingerprint

    def clip_name(self, n: int) -> str:
        return clip_name(self.job_id, n)

    @property
    def chapter(self) -> int | None:
        """The chapter a long video's job renders (D-130); None for a Short's single job."""
        return self.spec.get("chapter")

    @property
    def numbers(self) -> list[int]:
        return [s["n"] for s in self.spec["scenes"]]


def clip_name(job_id: str, n: int) -> str:
    """What the render PC calls scene n's clip (render.py uses the same pattern)."""
    return f"scene_{job_id}_{n:02d}.mp4"


def outbox_dir(ctx: AppContext) -> Path:
    """data/outbox/blender/: finished job zips, waiting to go to the render PC."""
    return ctx.root / ctx.settings.paths.data_dir / "outbox" / "blender"


def inbox_dir(ctx: AppContext, content_id: int) -> Path:
    """data/inbox/blender/<id as 6 digits>/: where the rendered scene clips go."""
    return ctx.root / ctx.settings.paths.inbox_dir / "blender" / f"{content_id:06d}"


def zip_path(ctx: AppContext, content_id: int, chapter: int | None = None) -> Path:
    """job_000NNN.zip, or job_000NNN_chK.zip for chapter K of a long video (D-130)."""
    suffix = f"_ch{chapter}" if chapter else ""
    return outbox_dir(ctx) / f"job_{content_id:06d}{suffix}.zip"


def jobs_for(ctx: AppContext, row: sqlite3.Row) -> list[Job]:
    """An item's render jobs: one for a Short, one per chapter for a long video (D-130)."""
    return episode_jobs(ctx, row) if row["format"] == "episode" else [job_for(ctx, row)]


@dataclass(frozen=True)
class _Basis:
    """What every job of an item shares: its model, picture size and voice."""

    model: Any
    size: tuple[int, int]
    words: list[dict]
    duration: float
    voice_sha: str


def _basis(ctx: AppContext, row: sqlite3.Row) -> _Basis:
    content_id = row["id"]
    model = blender.model_for(row["object_name"])
    aspect = getattr(ctx.channel.formats, row["format"]).aspect
    if aspect not in SIZES:
        raise FactoryError(f"Blender films are {' or '.join(SIZES)}; #{content_id} is {aspect}")
    problems = [
        f"fact '{k}' needs two source pages (D-122)"
        for k, f in model.FACTS.items()
        if not all(u.startswith("https://") for u in (f.source, f.second_source))
    ]
    if problems:
        raise FactoryError(f"Blender model '{model.MACHINE}': {'; '.join(problems)}")
    folder = voice.asset_dir(ctx, content_id)
    words_file, voice_wav = folder / voice.WORDS_FILE, folder / "voice.wav"
    if not voice_wav.is_file() or not words_file.is_file():
        raise FactoryError(f"voice missing: run `factory voice render {content_id}`")
    return _Basis(model, SIZES[aspect], json.loads(words_file.read_text()),
                  float(probe(voice_wav)["format"]["duration"]),
                  hashlib.sha256(voice_wav.read_bytes()).hexdigest())  # fmt: skip


def _finish(spec: dict, model: Any) -> Job:
    """The job id: a fingerprint of job.json and the Blender files, so a changed model or new
    timings make a new job whose clips can't be mixed up with older ones."""
    files_sha = hashlib.sha256()
    for name in ("render.py", "kit.py", model.MODEL_FILE):
        files_sha.update((JOB_FILES / name).read_bytes())
    fingerprint = hashlib.sha256(
        (json.dumps(spec, sort_keys=True) + files_sha.hexdigest()).encode()
    ).hexdigest()
    spec["job_id"] = fingerprint[:8]
    return Job(spec, fingerprint[:8])


def _common(row: sqlite3.Row, b: _Basis) -> dict:
    return {
        "content_id": row["id"],
        "machine": b.model.MACHINE,
        "model_file": b.model.MODEL_FILE,
        "fps": FPS,
        "width": b.size[0],
        "height": b.size[1],
        "voice_sha256": b.voice_sha,
    }


def episode_jobs(ctx: AppContext, row: sqlite3.Row) -> list[Job]:
    """A long video's jobs, one per chapter (D-130). Each scene uses its template from the
    script and starts on its own first word (exact: the voice's words are the script's words)."""
    b = _basis(ctx, row)
    script = ctx.conn.execute(
        "SELECT shot_list_json FROM scripts WHERE content_id = ? AND is_current = 1", (row["id"],)
    ).fetchone()
    plan = json.loads(script["shot_list_json"]) if script else []
    if not plan or plan[0].get("kind") != "scene":
        raise FactoryError(f"#{row['id']} has no long-video script in chapters (D-129)")
    unknown = sorted({s["template"] for s in plan} - set(b.model.TEMPLATES))
    if unknown:
        raise FactoryError(f"the {b.model.MACHINE} model has no template {', '.join(unknown)}")
    counts = [len(s["narration"].split()) for s in plan]
    if sum(counts) != len(b.words):
        raise FactoryError(f"the voice has {len(b.words)} words but the scenes {sum(counts)}: "
                           f"run `factory voice render {row['id']}` again")  # fmt: skip
    firsts_word = [sum(counts[:k]) for k in range(len(plan))]
    starts = [0.0] + [max(0.0, b.words[i]["start_s"] - LEAD_S) for i in firsts_word[1:]]
    frames = math.ceil(b.duration * FPS)
    firsts = [1] + [round(t * FPS) + 1 for t in starts[1:]]
    lasts = [f - 1 for f in firsts[1:]] + [frames]
    short = [f"scene {s['n']} {(c - a + 1) / FPS:.1f} s"
             for s, a, c in zip(plan, firsts, lasts, strict=True)
             if (c - a + 1) / FPS < MIN_SCENE_S]  # fmt: skip
    if short:
        raise FactoryError(f"scenes shorter than {MIN_SCENE_S} s: {', '.join(short)}")
    scenes = [{"n": s["n"], "role": s["template"], "template": s["template"],
               "chapter": s["chapter"], "start": round(t, 3), "first_frame": a, "last_frame": c,
               "frames": c - a + 1}
              for s, t, a, c in zip(plan, starts, firsts, lasts, strict=True)]  # fmt: skip
    chapters = sorted({s["chapter"] for s in scenes})
    jobs = []
    for chapter in chapters:
        mine = [s for s in scenes if s["chapter"] == chapter]
        spec = {**_common(row, b), "mode": "templates", "chapter": chapter,
                "chapters": len(chapters), "duration_s": round(b.duration, 3),
                "frames": sum(s["frames"] for s in mine), "scenes": mine}  # fmt: skip
        jobs.append(_finish(spec, b.model))
    return jobs


def job_for(ctx: AppContext, row: sqlite3.Row) -> Job:
    """A Short's single render job: scene times from the narration (D-123, D-126)."""
    if row["format"] == "episode":
        raise FactoryError("a long video has one job per chapter: use jobs_for (D-130)")
    b = _basis(ctx, row)
    model, words, duration = b.model, b.words, b.duration
    content_id = row["id"]
    note = ctx.conn.execute(
        "SELECT engineer_note FROM scripts WHERE content_id = ? AND is_current = 1", (content_id,)
    ).fetchone()
    note_n = count_sentences(note[0] or "")
    starts = scene_starts(model.ROLES, sentences(words), note_n, duration)
    starts = _body_starts_by_topic(model, words, note_n, starts)
    lengths = [b - a for a, b in zip(starts, [*starts[1:], duration], strict=True)]
    short = [f"{role} {s:.1f} s" for role, s in zip(model.ROLES, lengths, strict=True)
             if s < MIN_SCENE_S]  # fmt: skip
    if short:
        raise FactoryError(f"scenes shorter than {MIN_SCENE_S} s: {', '.join(short)}; "
                           "the narration needs more sentences per scene")  # fmt: skip
    frames = math.ceil(duration * FPS)  # the film is never shorter than the voice
    firsts = [1] + [round(s * FPS) + 1 for s in starts[1:]]
    lasts = [f - 1 for f in firsts[1:]] + [frames]
    rows = zip(model.ROLES, starts, firsts, lasts, strict=True)
    spec = {
        **_common(row, b),
        "duration_s": round(duration, 3),
        "frames": frames,
        "scenes": [
            {"n": n, "role": r, "start": round(s, 3), "first_frame": a, "last_frame": c}
            for n, (r, s, a, c) in enumerate(rows, 1)
        ],
    }  # fmt: skip
    return _finish(spec, model)


def _body_starts_by_topic(
    model, words: list[dict], note_n: int, starts: list[float]
) -> list[float]:
    """Each body scene after the first starts on the first body sentence that names its part
    (the model's SCENE_KEYWORDS), so the picture changes when the narration does. Counting
    sentences alone put the stator on screen while the narration still explained the rotor
    (#20, 2026-11-07). A script that never names a part in order is refused (D-126)."""
    keywords = getattr(model, "SCENE_KEYWORDS", None)
    if keywords is None:
        return starts
    texts = _sentence_texts(words)
    spoken = sentences(words)
    body = list(range(1, len(spoken) - 1 - note_n))  # sentence numbers between hook and note
    out, after = list(starts), body[0] if body else 1
    for k, (role, pattern) in enumerate(zip(model.ROLES, keywords, strict=True)):
        if role != "body" or pattern is None:
            continue
        if k == model.ROLES.index("body"):  # the first body scene starts the body
            continue
        found = next((i for i in body if i > after and re.search(pattern, texts[i], re.I)), None)
        if found is None:
            raise FactoryError(
                f"the narration never names scene {k + 1}'s part ({pattern}) after the earlier "
                "parts, so the film would show the wrong part: redo the script so the body "
                "explains the parts in the film's order"
            )
        out[k], after = max(0.0, spoken[found][0] - LEAD_S), found
    return out


def _sentence_texts(words: list[dict]) -> list[str]:
    """The text of each spoken sentence, split like canvas.sentences."""
    out, current = [], []
    for w in words:
        current.append(w["text"])
        if w["text"].rstrip().endswith((".", "?", "!")):
            out.append(" ".join(current))
            current = []
    if current:
        out.append(" ".join(current))
    return out


def zip_job(
    ctx: AppContext, content_id: int, chapter: int | None = None
) -> tuple[str, list[int]] | None:
    """(job id, scenes to render) of the outbox zip, None when there is no readable zip."""
    try:
        with zipfile.ZipFile(zip_path(ctx, content_id, chapter)) as z:
            spec = json.loads(z.read("job.json"))
        return spec["job_id"], spec["render"]
    except (OSError, KeyError, zipfile.BadZipFile, json.JSONDecodeError):
        return None


def write_zip(ctx: AppContext, job: Job, render: list[int], inbox_windows: str) -> Path:
    """data/outbox/blender/job_<id>.zip rendering the given scenes; replaced in one step, never
    half-written."""
    spec = {**job.spec, "render": sorted(render)}
    out = zip_path(ctx, spec["content_id"], job.chapter)
    out.parent.mkdir(parents=True, exist_ok=True)
    fonts = ctx.root / ctx.settings.paths.assets_static_dir / "fonts"
    tmp = out.with_suffix(".zip.part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("job.json", json.dumps(spec, indent=2) + "\n")
        for name in ("render.py", "kit.py", spec["model_file"]):
            z.write(JOB_FILES / name, name)
        for name in FONT_FILES:
            z.write(fonts / name, name)
        z.writestr("HOW_TO_RUN.txt", how_to_run(job, spec["render"], inbox_windows))
    tmp.replace(out)
    return out


def how_to_run(job: Job, render: list[int], inbox_windows: str) -> str:
    spec = job.spec
    scenes = [s for s in spec["scenes"] if s["n"] in render]
    frames = sum(s["last_frame"] - s["first_frame"] + 1 for s in scenes)
    minutes = frames * 3.2 / 60  # EEVEE on the GTX 750 Ti (D-121)
    folder = f"job_{spec['content_id']:06d}" + (f"_ch{job.chapter}" if job.chapter else "")
    part = f", chapter {job.chapter} of {spec['chapters']}" if job.chapter else ""
    which = (
        "all scenes"
        if len(scenes) == len(spec["scenes"])
        else (f"only scene {', '.join(str(s['n']) for s in scenes)} (a redo)")
    )
    clips = "\n".join(f"       out\\{job.clip_name(s['n'])}" for s in scenes)
    title = f"#{spec['content_id']} ({spec['machine']}{part}, job {job.job_id})"
    return f"""COIL & COG RENDER JOB {title}
{"=" * 60}
Renders {which}: {frames} frames, {spec["width"]}x{spec["height"]} at {spec["fps"]} fps
(about {minutes:.0f} min in EEVEE on the GTX 750 Ti).

On the render PC (Windows, PowerShell)
1. Unzip this file to D:\\coilcog\\{folder}\\ and go there:
       cd D:\\coilcog\\{folder}
2. Optional check of the look first (one picture per scene, a few minutes):
       & "<your blender.exe>" -b -P render.py -- --sample
   then open the pictures in out\\sample_eevee\\
3. The scenes:
       & "<your blender.exe>" -b -P render.py
   <your blender.exe> is the full path in quotes, e.g.
       "D:\\blender 4.5 lts\\blender-4.5.15-windows-x64\\blender.exe"
   If it stops (sleep, power cut, Ctrl+C), run the same line again: finished frames are kept.
4. When it says "done", copy ONLY these clips (the frames are big):
{clips}
   into the factory inbox:
       {inbox_windows}
   (USB, email or Drive is fine: the clips hold no secrets. Download from Drive, don't save
   from its video player.)

On the laptop
5. uv run factory generate          (checks each clip; a bad one is refused by name)
6. uv run factory scenes show {spec["content_id"]}   (watch each scene, then approve or redo it)
"""


def find_clip(ctx: AppContext, job: Job, n: int) -> tuple[Path | None, str | None]:
    """Scene n's clip for this job in the inbox, or None plus what is wrong or missing."""
    clip = inbox_dir(ctx, job.spec["content_id"]) / job.clip_name(n)
    if not clip.is_file():
        return None, f"{clip.name} not in the inbox yet"
    scene = next(s for s in job.spec["scenes"] if s["n"] == n)
    problem = check_clip(clip, job.spec, scene["last_frame"] - scene["first_frame"] + 1)
    return (None, f"{clip.name} {problem}") if problem else (clip, None)


def check_clip(path: Path, spec: dict, frames: int) -> str | None:
    """A short reason why a scene clip can't be used, or None."""
    try:
        info = probe(path)
    except FactoryError:
        return "is not a readable video"
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        return "has no video stream"
    size = (int(video["width"]), int(video["height"]))
    if size != (spec["width"], spec["height"]):
        return f"is {size[0]}x{size[1]}, not {spec['width']}x{spec['height']}"
    if video.get("r_frame_rate") != f"{spec['fps']}/1":
        return f"runs at {video.get('r_frame_rate')} fps, not {spec['fps']}"
    counted = video.get("nb_frames")
    if counted is not None and int(counted) != frames:
        return f"has {counted} frames, not {frames} (render not finished?)"
    duration = float(info["format"].get("duration", 0))
    if abs(duration - frames / spec["fps"]) > 1.5 / spec["fps"]:
        return f"lasts {duration:.2f} s, not {frames / spec['fps']:.2f} s"
    return None


def copy_clip(clip: Path, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(clip, target)  # copy: the inbox keeps the render PC's original
    return target
