"""Coil & Cog render job (D-123): run it with Blender 4.5 LTS on the render PC.

    blender.exe -b -P render.py                  # the scenes -> out/scene_<job id>_NN.mp4
    blender.exe -b -P render.py -- --sample      # one still per scene, to check the look first

-b = no window; -P = run this file; options go after "--":
    --engine cycles   Cycles instead of EEVEE (photoreal, about 15x slower on a GTX 750 Ti)
    --frames 1-300    only these frames (no MP4s)
    --scale 50        half size (quick checks, no MP4s)
    --samples 16      fewer samples: faster, noisier
    --save-blend      also save out/film.blend to open in Blender

job.json lists the scenes to render ("render"): all of them, or only the ones being redone.
Frames already rendered are kept and skipped, so after a stop or a power cut run the same command
again. Times go to out/render_log.txt. Everything this needs is in this folder.
"""

import importlib
import json
import sys
import time
from pathlib import Path

import bpy

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
OUT = HERE / "out"

import kit  # noqa: E402  (this folder must be on the path first)


def log(line: str) -> None:
    print(line, flush=True)
    OUT.mkdir(exist_ok=True)
    with open(OUT / "render_log.txt", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def options() -> dict:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    opts = {"sample": False, "engine": "eevee", "frames": None, "scale": 100, "samples": None,
            "save": False}  # fmt: skip
    it = iter(argv)
    for a in it:
        if a == "--sample":
            opts["sample"] = True
        elif a == "--engine":
            opts["engine"] = next(it).lower()
        elif a == "--frames":
            lo, hi = next(it).split("-")
            opts["frames"] = (int(lo), int(hi))
        elif a == "--scale":
            opts["scale"] = int(next(it))
        elif a == "--samples":
            opts["samples"] = int(next(it))
        elif a == "--save-blend":
            opts["save"] = True
        else:
            sys.exit(f"unknown option {a} (see the top of render.py)")
    return opts


def main() -> None:
    job = json.loads((HERE / "job.json").read_text(encoding="utf-8"))
    opts = options()
    kit.setup(job["fps"], HERE)
    model = importlib.import_module(Path(job["model_file"]).stem)
    size = (job["width"], job["height"])
    log(f"--- {time.strftime('%Y-%m-%d %H:%M')} Blender {bpy.app.version_string}: "
        f"#{job['content_id']} {job['machine']}, {job['frames']} frames")  # fmt: skip
    if job.get("mode") == "templates":  # a long video's chapter: one view per scene (D-130)
        render_templates(job, model, opts, size)
        return
    kit.clear_scene()
    scene = bpy.context.scene
    model.film(scene, job)
    used = kit.setup_render(scene, opts["engine"], size, job["frames"], opts["scale"],
                            opts["samples"])  # fmt: skip
    log(f"engine: {used}")
    if opts["save"]:
        bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "film.blend"))
    if opts["sample"]:  # the middle of every scene
        ends = [s["start"] for s in job["scenes"][1:]] + [job["duration_s"]]
        mids = [(s["start"] + e) / 2 for s, e in zip(job["scenes"], ends, strict=True)]
        frames = [kit.frame_at(t) for t in mids]
        kit.render_frames(scene, frames, OUT / f"sample_{opts['engine']}", "sample", log)
        return
    chosen = [s for s in job["scenes"] if s["n"] in job["render"]]
    if opts["frames"]:
        lo, hi = opts["frames"]
        frames = list(range(lo, hi + 1))
    else:
        frames = [f for s in chosen for f in range(s["first_frame"], s["last_frame"] + 1)]
    folder = OUT / f"frames_{opts['engine']}"
    kit.render_frames(scene, frames, folder, "scenes", log)
    if opts["frames"] or opts["scale"] != 100:
        return
    names = []
    for s in chosen:  # one clip per scene, named like the factory expects (jobs.clip_name)
        name = f"scene_{job['job_id']}_{s['n']:02d}.mp4"
        if kit.encode(folder, OUT / name, s["first_frame"], s["last_frame"], size, log):
            names.append(name)
    if len(names) == len(chosen):
        log(f"done: copy these {len(names)} clips from out/ to the factory's inbox "
            f"(see HOW_TO_RUN.txt): {', '.join(names)}")  # fmt: skip


def render_templates(job: dict, model, opts: dict, size) -> None:
    """Each scene of the chapter is built and animated on its own (model.template), rendered to
    its own frame folder (kept after a stop) and encoded as scene_<job id>_NN.mp4."""
    chosen = [s for s in job["scenes"] if s["n"] in job["render"]]
    names = []
    for s in chosen:
        kit.clear_scene()
        scene = bpy.context.scene
        model.template(scene, s, job)
        used = kit.setup_render(scene, opts["engine"], size, s["frames"], opts["scale"],
                                opts["samples"])  # fmt: skip
        log(f"scene {s['n']} ({s['template']}, {s['frames']} frames): {used}")
        if opts["sample"]:  # the middle of the scene
            middle, folder = [s["frames"] // 2 + 1], OUT / f"sample_{opts['engine']}"
            kit.render_frames(scene, middle, folder, f"sample scene {s['n']}", log,
                              prefix=f"scene_{s['n']:02d}_")  # fmt: skip
            continue
        folder = OUT / f"frames_{opts['engine']}" / f"scene_{s['n']:02d}"
        kit.render_frames(scene, list(range(1, s["frames"] + 1)), folder, f"scene {s['n']}", log)
        if opts["scale"] != 100 or opts["frames"]:
            continue
        name = f"scene_{job['job_id']}_{s['n']:02d}.mp4"
        if kit.encode(folder, OUT / name, 1, s["frames"], size, log):
            names.append(name)
    if names and len(names) == len(chosen):
        log(f"done: copy these {len(names)} clips from out/ to the factory's inbox "
            f"(see HOW_TO_RUN.txt): {', '.join(names)}")  # fmt: skip


main()
