"""Blender render jobs (D-123): the zip the render PC renders, and the check of the film it sends
back.

A job zip holds everything Blender needs and nothing else (no project code, no keys): render.py,
the shared kit, the machine's model, job.json (size, frame rate and when each scene starts, from
the narration's word timings), the label font and HOW_TO_RUN.txt. The film comes back as
film_<job id>.mp4, so a film from an older job (e.g. before a script redo) is never taken.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import sqlite3
import zipfile
from dataclasses import dataclass
from pathlib import Path

from factory import blender
from factory.blueprint.canvas import count_sentences, scene_starts, sentences
from factory.context import AppContext
from factory.errors import FactoryError
from factory.media.ffmpeg import probe
from factory.stages import voice

JOB_FILES = Path(__file__).resolve().parent / "job_files"
FPS = 30  # the same as the assembly (media/compose.py)
SIZES = {"9:16": (1080, 1920)}  # 16:9 long videos come with ROADMAP 2.28
MIN_SCENE_S = 1.5  # shorter scenes can't hold their camera moves
FONT_FILES = ("Inter-SemiBold.ttf", "OFL-Inter.txt")


@dataclass(frozen=True)
class Job:
    spec: dict  # job.json
    job_id: str  # first 8 characters of the spec's fingerprint

    @property
    def film_name(self) -> str:
        return f"film_{self.job_id}.mp4"


def outbox_dir(ctx: AppContext) -> Path:
    """data/outbox/blender/: finished job zips, waiting to go to the render PC."""
    return ctx.root / ctx.settings.paths.data_dir / "outbox" / "blender"


def inbox_dir(ctx: AppContext, content_id: int) -> Path:
    """data/inbox/blender/<id as 6 digits>/: where the rendered film goes."""
    return ctx.root / ctx.settings.paths.inbox_dir / "blender" / f"{content_id:06d}"


def zip_path(ctx: AppContext, content_id: int) -> Path:
    return outbox_dir(ctx) / f"job_{content_id:06d}.zip"


def job_for(ctx: AppContext, row: sqlite3.Row) -> Job:
    """The render job for an item whose voice exists: scene times from the narration."""
    content_id = row["id"]
    model = blender.model_for(row["object_name"])
    aspect = getattr(ctx.channel.formats, row["format"]).aspect
    if aspect not in SIZES:
        raise FactoryError(f"Blender films are {', '.join(SIZES)} for now; #{content_id} is "
                           f"{aspect} (16:9 comes with ROADMAP 2.28)")  # fmt: skip
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
    words = json.loads(words_file.read_text())
    duration = float(probe(voice_wav)["format"]["duration"])
    note = ctx.conn.execute(
        "SELECT engineer_note FROM scripts WHERE content_id = ? AND is_current = 1", (content_id,)
    ).fetchone()
    starts = scene_starts(model.ROLES, sentences(words), count_sentences(note[0] or ""), duration)
    lengths = [b - a for a, b in zip(starts, [*starts[1:], duration], strict=True)]
    short = [f"{role} {s:.1f} s" for role, s in zip(model.ROLES, lengths, strict=True)
             if s < MIN_SCENE_S]  # fmt: skip
    if short:
        raise FactoryError(f"scenes shorter than {MIN_SCENE_S} s: {', '.join(short)}; "
                           "the narration needs more sentences per scene")  # fmt: skip
    width, height = SIZES[aspect]
    spec = {
        "content_id": content_id,
        "machine": model.MACHINE,
        "model_file": model.MODEL_FILE,
        "fps": FPS,
        "width": width,
        "height": height,
        "duration_s": round(duration, 3),
        "frames": math.ceil(duration * FPS),  # the film is never shorter than the voice
        "scenes": [{"role": r, "start": round(s, 3)}
                   for r, s in zip(model.ROLES, starts, strict=True)],
        "voice_sha256": hashlib.sha256(voice_wav.read_bytes()).hexdigest(),
    }  # fmt: skip
    files_sha = hashlib.sha256()
    for name in ("render.py", "kit.py", model.MODEL_FILE):  # a changed model is a new job too
        files_sha.update((JOB_FILES / name).read_bytes())
    fingerprint = hashlib.sha256(
        (json.dumps(spec, sort_keys=True) + files_sha.hexdigest()).encode()
    ).hexdigest()
    spec["job_id"] = fingerprint[:8]
    return Job(spec, fingerprint[:8])


def zip_job_id(ctx: AppContext, content_id: int) -> str | None:
    """The job id inside the outbox zip, None when there is no readable zip."""
    try:
        with zipfile.ZipFile(zip_path(ctx, content_id)) as z:
            return json.loads(z.read("job.json"))["job_id"]
    except (OSError, KeyError, zipfile.BadZipFile, json.JSONDecodeError):
        return None


def write_zip(ctx: AppContext, job: Job, inbox_windows: str) -> Path:
    """data/outbox/blender/job_<id>.zip; replaced in one step, never half-written."""
    spec = job.spec
    out = zip_path(ctx, spec["content_id"])
    out.parent.mkdir(parents=True, exist_ok=True)
    fonts = ctx.root / ctx.settings.paths.assets_static_dir / "fonts"
    tmp = out.with_suffix(".zip.part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("job.json", json.dumps(spec, indent=2) + "\n")
        for name in ("render.py", "kit.py", spec["model_file"]):
            z.write(JOB_FILES / name, name)
        for name in FONT_FILES:
            z.write(fonts / name, name)
        z.writestr("HOW_TO_RUN.txt", how_to_run(job, inbox_windows))
    tmp.replace(out)
    return out


def how_to_run(job: Job, inbox_windows: str) -> str:
    spec = job.spec
    minutes = spec["frames"] * 3.2 / 60  # EEVEE on the GTX 750 Ti (D-121)
    folder = f"job_{spec['content_id']:06d}"
    return f"""COIL & COG RENDER JOB #{spec["content_id"]} ({spec["machine"]}, job {job.job_id})
{"=" * 60}
{spec["frames"]} frames, {spec["width"]}x{spec["height"]} at {spec["fps"]} fps
({spec["duration_s"]:.1f} s; about {minutes:.0f} min in EEVEE on the GTX 750 Ti).

On the render PC (Windows, PowerShell)
1. Unzip this file to D:\\coilcog\\{folder}\\ and go there:
       cd D:\\coilcog\\{folder}
2. Optional check of the look first (one picture per scene, a few minutes):
       & "<your blender.exe>" -b -P render.py -- --sample
   then open the pictures in out\\sample_eevee\\
3. The film:
       & "<your blender.exe>" -b -P render.py
   <your blender.exe> is the full path in quotes, e.g.
       "D:\\blender 4.5 lts\\blender-4.5.15-windows-x64\\blender.exe"
   If it stops (sleep, power cut, Ctrl+C), run the same line again: finished frames are kept.
4. When it says "done", copy ONLY out\\{job.film_name} (the frames are big)
   into the factory inbox:
       {inbox_windows}
   (USB, email or Drive is fine: the film holds no secrets.)

On the laptop
5. uv run factory generate      (checks the film and moves the item on)
"""


def find_film(ctx: AppContext, job: Job) -> tuple[Path | None, list[str]]:
    """The film for this job in the inbox, or None plus what is wrong or missing."""
    inbox = inbox_dir(ctx, job.spec["content_id"])
    film = inbox / job.film_name
    if film.is_file():
        problem = check_film(film, job.spec)
        return (None, [f"{film.name} {problem}"]) if problem else (film, [])
    others = sorted(p.name for p in inbox.glob("*.mp4"))
    if others:
        return None, [f"{', '.join(others)} in the inbox, but this job's film is {job.film_name} "
                      "(an older job's film, or not renamed)"]  # fmt: skip
    return None, [f"{job.film_name} not in the inbox yet"]


def check_film(path: Path, spec: dict) -> str | None:
    """A short reason why the film can't be used, or None."""
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
    frames = video.get("nb_frames")
    if frames is not None and int(frames) != spec["frames"]:
        return f"has {frames} frames, not {spec['frames']} (render not finished?)"
    duration = float(info["format"].get("duration", 0))
    if abs(duration - spec["frames"] / spec["fps"]) > 1.5 / spec["fps"]:
        return f"lasts {duration:.2f} s, not {spec['frames'] / spec['fps']:.2f} s"
    return None


def copy_film(film: Path, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(film, target)  # copy: the inbox keeps the render PC's original
    return target
