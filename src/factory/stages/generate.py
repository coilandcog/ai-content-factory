"""Generate stage, part 1 (ROADMAP 1.6).

Pass 1, script_approved -> awaiting_clips: write flow_sheet.md (one ready prompt per Flow clip),
create the Flow inbox folder and render voice.wav.
Pass 2, awaiting_clips -> generated: once every shot_NN.mp4 is in the inbox and plays, copy the
clips into the asset folder and record them, then make the animated stills and diagram cards
(1.8). Pass 1 also writes the subtitles (subs.ass, 1.7) with the voice; "generated" needs clips,
voice, subtitles, stills and diagrams.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
from collections.abc import Callable
from functools import partial
from pathlib import Path

from PIL import Image

from factory.budgets import check_and_record, daily_limits, usage_today
from factory.context import AppContext, StageResult
from factory.db import set_status
from factory.errors import BudgetExceededError, FactoryError
from factory.integrations.gemini import GeminiClient
from factory.media import diagrams, stills
from factory.media.ffmpeg import probe
from factory.prompts import render_prompt
from factory.stages import script as script_stage
from factory.stages import voice
from factory.stages.voice import Synthesizer, asset_dir

log = logging.getLogger("factory.generate")

# Flow is used by hand in the browser, so these describe what the owner picks there (D-063).
FLOW_MODEL = "Veo 3.1 - Fast"  # final clips; Lite is for prompt tests only (D-043)
FLOW_CREDITS_PER_CLIP = 20  # conservative until confirmed in Flow's model menu (Ultra pays 10)
FLOW_CLIP_SECONDS = 8
MIN_CLIP_SECONDS = 3.0  # shorter files are almost certainly failed downloads
ENERGY_COLORS = {"electric": "cyan", "heat": "amber", "fluid": "blue"}  # DESIGN_SYSTEM.md §3
ENERGY_WORDS = {"electric": "electric current", "heat": "heat", "fluid": "fluid and air"}
PROMPTS_FILE = script_stage.FLOW_PROMPTS_FILE  # the prompts issued, read back when clips arrive
STILL_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")  # optional owner images: still_NN.<ext>
STILL_SAFE_AREA = "upper third and the lower quarter"  # where labels and subtitles go


def run(
    ctx: AppContext, *, limit: int | None = None, tts: Callable[[], Synthesizer] | None = None
) -> StageResult:
    """Issue Flow sheets for approved scripts, then pick up finished clip sets.

    `tts` builds the speech function only when a voiceover is needed (loading Kokoro is slow).
    """
    result = StageResult()
    approved = items_in_status(ctx, "script_approved", limit)
    speech = (tts or _kokoro)() if approved else None
    for row in approved:
        run_safely(ctx, row, result, partial(_issue, ctx, speech=speech))
    for row in items_in_status(ctx, "awaiting_clips", limit):
        run_safely(ctx, row, result, partial(_collect, ctx))
    return result


def flow_log(ctx: AppContext, credits: int) -> tuple[int, int]:
    """Record Flow credits spent today; returns (used today, daily budget)."""
    used = check_and_record(ctx.conn, ctx.channel, "flow", credits)
    return used, daily_limits(ctx.channel)["flow"]


def inbox_dir(ctx: AppContext, content_id: int) -> Path:
    """data/inbox/flow/<id as 6 digits>/, where the owner saves Flow downloads."""
    return ctx.root / ctx.settings.paths.inbox_dir / "flow" / f"{content_id:06d}"


# --- pass 1: Flow sheet + voice ----------------------------------------------------------------


def _issue(ctx: AppContext, row: sqlite3.Row, speech: Synthesizer) -> bool:
    content_id = row["id"]
    sheet = _write_sheet(ctx, row)
    voice.render(ctx, content_id, speech)
    set_status(ctx.conn, content_id, "awaiting_clips")
    log.info(
        "#%s: Flow sheet ready: %s",
        content_id,
        sheet.relative_to(ctx.root),
        extra={"content_id": content_id, "event": "flow_sheet_issued"},
    )
    return True


def _write_sheet(ctx: AppContext, row: sqlite3.Row) -> Path:
    """flow_prompts.json + flow_sheet.md from the current script, and the inbox folder."""
    content_id = row["id"]
    script = current_script(ctx, content_id)
    shots = _flow_shots(script)
    if not shots:
        raise FactoryError("the script has no flow_clip shots")
    unlabelled = [s["n"] for s in _shots(script, "diagram") if not s.get("labels")]
    if unlabelled:
        raise FactoryError(
            f"diagram shots {unlabelled} have no labels (script made before task 1.8): redo the "
            "script at Gate 1"
        )
    if any("scene" not in s["prompt_vars"] for s in shots):
        raise FactoryError(
            f"the script's clips have no film shots (made before D-092): run "
            f"`factory generate --restage {content_id}`"
        )
    aspect = getattr(ctx.channel.formats, row["format"]).aspect
    # Each clip continues from the previous clip's last frame, like a cut in a film (D-092).
    ends = [None] + [s["prompt_vars"]["end_frame"] for s in shots[:-1]]
    prompts = {
        s["n"]: _shot_prompt(ctx, row, s, aspect, end) for s, end in zip(shots, ends, strict=True)
    }
    frame_prompts = {s["n"]: _frame_prompt(ctx, row, s, aspect) for s in shots}
    still_prompts = {s["n"]: _still_prompt(ctx, row, s, aspect) for s in _shots(script, "still")}

    folder = asset_dir(ctx, content_id)
    folder.mkdir(parents=True, exist_ok=True)
    all_prompts = {**prompts, **still_prompts}
    (folder / PROMPTS_FILE).write_text(json.dumps(all_prompts, indent=2, ensure_ascii=False))
    inbox = inbox_dir(ctx, content_id)
    inbox.mkdir(parents=True, exist_ok=True)
    sheet = folder / "flow_sheet.md"
    sheet.write_text(
        _sheet_text(ctx, row, script, shots, prompts, frame_prompts, inbox, still_prompts)
    )
    replace_assets(ctx, content_id, "flow_sheet", [{"path": sheet, "generator": "factory"}])
    return sheet


def _shot_prompt(
    ctx: AppContext, row: sqlite3.Row, shot: dict, aspect: str, previous_end: str | None
) -> str:
    """One film shot (D-092): the shared hero_look, a continuation of the previous clip's last
    frame, the scene, one camera move and this clip's last frame."""
    variables = {"energy_color_name": None, "energy_word": None, **shot["prompt_vars"]}
    energy = variables.get("energy")
    if energy and shot["beat"] == "flow":
        variables["energy_color_name"] = ENERGY_COLORS[energy]
        variables["energy_word"] = ENERGY_WORDS[energy]
    rendered = render_prompt(
        "flow_shot",
        ctx.channel,
        prompts_root=ctx.root / ctx.settings.paths.prompts_dir,
        object=row["object_name"],
        beat=shot["beat"],
        aspect=aspect,
        clip_seconds=FLOW_CLIP_SECONDS,
        first=shot["n"] == 1,  # D-090: shot 1 moves from its first frame
        previous_end=previous_end,
        **variables,
    )
    return rendered.text.strip()


# --- restage: new film shots for an approved script (D-092) ------------------------------------

RESTAGE_STATUSES = ("script_approved", "awaiting_clips")


def restage(
    ctx: AppContext,
    content_id: int,
    llm: GeminiClient | None = None,
    only: tuple[int, ...] | None = None,
) -> Path | None:
    """Ask Gemini for new film shots (hero_look + scene/camera/end_frame per flow_clip) for an
    approved script whose narration stays as approved. An item waiting for clips gets a new Flow
    sheet at once (returned); a script_approved item gets it from the next `factory generate`.

    `only` rewrites just those clips and keeps the hero_look and the other clips (D-096), so
    clips the owner already made keep their prompts."""
    row = ctx.conn.execute("SELECT * FROM contents WHERE id = ?", (content_id,)).fetchone()
    if row is None or row["status"] not in RESTAGE_STATUSES:
        raise FactoryError(
            f"content #{content_id} is not {' or '.join(RESTAGE_STATUSES)}; only approved "
            "scripts that have no clips yet can get new film shots"
        )
    script = current_script(ctx, content_id)
    shots = json.loads(script["shot_list_json"])
    clips = tuple(s["n"] for s in shots if s["kind"] == "flow_clip")
    hero_look = None
    if only is not None:
        if not only or set(only) - set(clips):
            raise FactoryError(f"--shots must name flow_clips of #{content_id}: {list(clips)}")
        looks = {s["prompt_vars"].get("hero_look") for s in shots if s["kind"] == "flow_clip"}
        if None in looks:
            raise FactoryError(
                f"#{content_id} has no film shots yet: run `factory generate --restage "
                f"{content_id}` without --shots first"
            )
        hero_look = looks.pop()
    redo = tuple(sorted(only)) if only is not None else clips
    schema = script_stage.visual_plan_schema(redo, keep_look=only is not None)
    prompts_root = ctx.root / ctx.settings.paths.prompts_dir
    system = render_prompt("script_system", ctx.channel, prompts_root=prompts_root)
    user = render_prompt(
        "restage",
        ctx.channel,
        prompts_root=prompts_root,
        format=row["format"],
        object=row["object_name"],
        narration=script["body_text"],
        shots=shots,
        clip_numbers=redo,
        hero_look=hero_look,
        hero_look_words=script_stage.HERO_LOOK_WORDS,
        benchmarks=script_stage.benchmark_examples(ctx),
    )
    llm = llm or script_stage.gemini_client(ctx)
    plan = llm.generate_json(
        system=system.text, prompt=user.text, schema=schema, max_reasks=script_stage.MAX_REASKS
    )
    visuals = {v.n: v for v in plan.shots}
    look = hero_look or plan.hero_look.strip()
    for shot in shots:
        if shot["kind"] != "flow_clip" or shot["n"] not in visuals:
            continue
        v = visuals[shot["n"]]
        energy = shot["prompt_vars"].get("energy")
        shot["prompt_vars"] = {
            "hero_look": look,
            "scene": v.scene.strip(),
            "camera": v.camera.strip(),
            "end_frame": v.end_frame.strip(),
            **({"energy": energy} if energy and shot["beat"] == "flow" else {}),
        }
    with ctx.conn:
        ctx.conn.execute(
            "UPDATE scripts SET shot_list_json = ? WHERE id = ?",
            (json.dumps(shots, ensure_ascii=False), script["id"]),
        )
    log.info("#%s: new film shots for clips %s", content_id, list(redo),
             extra={"content_id": content_id, "event": "restaged"})  # fmt: skip
    return _write_sheet(ctx, row) if row["status"] == "awaiting_clips" else None


def _frame_prompt(ctx: AppContext, row: sqlite3.Row, shot: dict, aspect: str) -> str:
    """The clip's opening frame as a Flow image (0 credits): the owner checks the composition
    before spending 20 credits on the video (D-095)."""
    v = shot["prompt_vars"]
    return render_prompt(
        "flow_frame",
        ctx.channel,
        prompts_root=ctx.root / ctx.settings.paths.prompts_dir,
        object=row["object_name"],
        aspect=aspect,
        hero_look=v["hero_look"],
        scene=v["scene"],
        camera=v["camera"],
    ).text.strip()


def _still_prompt(ctx: AppContext, row: sqlite3.Row, shot: dict, aspect: str) -> str:
    return render_prompt(
        "still_image",
        ctx.channel,
        prompts_root=ctx.root / ctx.settings.paths.prompts_dir,
        object=row["object_name"],
        description=shot["description"],
        aspect=aspect,
        safe_area_hint=STILL_SAFE_AREA,
    ).text.strip()


def _sheet_text(
    ctx: AppContext,
    row: sqlite3.Row,
    script: sqlite3.Row,
    shots: list[dict],
    prompts: dict[int, str],
    frame_prompts: dict[int, str],
    inbox: Path,
    still_prompts: dict[int, str],
) -> str:
    used, budget = usage_today(ctx.conn, ctx.channel).get("flow", (0, None))
    budget = budget or 0
    return render_prompt(
        "flow_sheet",
        ctx.channel,
        prompts_root=ctx.root / ctx.settings.paths.prompts_dir,
        item=row,
        narration_s=round(script["est_duration_s"] or 0),
        shots=[
            {**s, "prompt": prompts[s["n"]], "frame_prompt": frame_prompts[s["n"]]} for s in shots
        ],
        flow_model=FLOW_MODEL,
        credits_needed=len(shots) * FLOW_CREDITS_PER_CLIP,
        credits_left=max(budget - used, 0),
        clips_today=max(budget - used, 0) // FLOW_CREDITS_PER_CLIP,
        credits_budget=budget,
        clip_seconds=FLOW_CLIP_SECONDS,
        inbox_windows=windows_path(inbox),
        stills=[{**s, "prompt": still_prompts[s["n"]]} for s in _shots(script, "still")],
    ).text


def windows_path(path: Path) -> str:
    r"""How Windows Explorer reaches a WSL folder: \\wsl$\<distro>\home\..."""
    distro = os.environ.get("WSL_DISTRO_NAME", "Ubuntu")
    return f"\\\\wsl$\\{distro}" + str(path.resolve()).replace("/", "\\")


# --- pass 2: collect clips -------------------------------------------------------------------


def _collect(ctx: AppContext, row: sqlite3.Row) -> bool:
    """Move a complete, playable clip set into the project. False = still waiting."""
    content_id = row["id"]
    script = current_script(ctx, content_id)
    shots = _flow_shots(script)
    inbox = inbox_dir(ctx, content_id)
    problems: list[str] = []
    found: dict[int, tuple[Path, dict]] = {}
    for shot in shots:
        path = inbox / f"shot_{shot['n']:02d}.mp4"
        if not path.is_file():
            problems.append(f"{path.name} missing")
            continue
        info, problem = _check_clip(path)
        if problem:
            problems.append(f"{path.name} {problem}")
        else:
            found[shot["n"]] = (path, info)
    expected = {f"shot_{s['n']:02d}.mp4" for s in shots} | {
        f"still_{s['n']:02d}{ext}" for s in _shots(script, "still") for ext in STILL_EXTENSIONS
    }
    extras = sorted(p.name for p in inbox.glob("*") if p.is_file() and p.name not in expected)
    if extras:
        log.warning(
            "#%s: ignoring unexpected files in the inbox: %s", content_id, ", ".join(extras)
        )
    folder = asset_dir(ctx, content_id)
    for needed in ("voice.wav", voice.SUBS_FILE):
        if not (folder / needed).is_file():
            problems.append(f"{needed} missing: run `factory voice render {content_id}`")
    if problems:
        log.info(
            "#%s waiting for clips: %s",
            content_id,
            "; ".join(problems),
            extra={"content_id": content_id, "event": "clips_waiting"},
        )
        return False
    clips = _store_clips(ctx, content_id, found)
    still_shots, diagram_shots = _shots(script, "still"), _shots(script, "diagram")
    _make_stills(ctx, content_id, still_shots, clips, shots)
    _make_diagrams(ctx, content_id, diagram_shots, clips, shots)
    set_status(ctx.conn, content_id, "generated")
    log.info(
        "#%s: %d clips, %d stills, %d diagrams in place -> generated",
        content_id,
        len(found),
        len(still_shots),
        len(diagram_shots),
        extra={"content_id": content_id, "event": "clips_collected"},
    )
    return True


def _check_clip(path: Path) -> tuple[dict, str | None]:
    """ffprobe facts about a clip, or a short reason why it can't be used."""
    try:
        info = probe(path)
    except FactoryError:
        return {}, "is not a readable video"
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        return info, "has no video stream"
    if int(video["height"]) <= int(video["width"]):
        return info, f"is {video['width']}x{video['height']}, not portrait 9:16 (set 9:16 in Flow)"
    if float(info["format"].get("duration", 0)) < MIN_CLIP_SECONDS:
        return info, "is shorter than 3 s (incomplete download?)"
    return info, None


def _store_clips(
    ctx: AppContext, content_id: int, found: dict[int, tuple[Path, dict]]
) -> dict[int, Path]:
    """Copy the clips into clips/, record them, and return shot number -> copied clip."""
    folder = asset_dir(ctx, content_id) / "clips"
    folder.mkdir(parents=True, exist_ok=True)
    prompts = json.loads((asset_dir(ctx, content_id) / PROMPTS_FILE).read_text())
    rows = []
    for n, (source, info) in sorted(found.items()):
        target = folder / source.name
        shutil.copy2(source, target)  # copy: the inbox keeps the owner's original download
        video = next(s for s in info["streams"] if s["codec_type"] == "video")
        rows.append(
            {
                "path": target,
                "shot_n": n,
                "generator": "google_flow",
                "model": FLOW_MODEL,
                "prompt": prompts.get(str(n)),
                "license": "google_flow_output",
                "duration_s": round(float(info["format"]["duration"]), 2),
                "width": int(video["width"]),
                "height": int(video["height"]),
            }
        )
    replace_assets(ctx, content_id, "flow_clip", rows)
    return {r["shot_n"]: r["path"] for r in rows}


# --- pass 2: stills and diagrams (task 1.8) ---------------------------------------------------


def _make_stills(
    ctx: AppContext,
    content_id: int,
    shots: list[dict],
    clips: dict[int, Path],
    clip_shots: list[dict],
) -> None:
    """still_NN.png (the owner's Flow image if in the inbox, else the sharpest frame of a clip of
    the same beat) plus its Ken Burns animation still_NN_anim.mp4 (D-067)."""
    folder = asset_dir(ctx, content_id) / "stills"
    folder.mkdir(parents=True, exist_ok=True)
    prompts = json.loads((asset_dir(ctx, content_id) / PROMPTS_FILE).read_text())
    images, animations = [], []
    for shot in shots:
        n = shot["n"]
        image = folder / f"still_{n:02d}.png"
        provided = _owner_still(inbox_dir(ctx, content_id), n)
        if provided:
            Image.open(provided).convert("RGB").save(image)
            origin = {"generator": "google_flow", "prompt": prompts.get(str(n))}
        else:
            clip_n = clip_for(shot, clip_shots)
            stills.sharpest_frame(clips[clip_n], image)
            origin = {"generator": "ffmpeg", "model": f"frame of shot_{clip_n:02d}"}
        common = {"shot_n": n, "license": "google_flow_output"}
        images.append({"path": image, **common, **origin})
        anim = stills.animate_still(
            image, folder / f"still_{n:02d}_anim.mp4", shot["duration_s"], variant=n
        )
        animations.append(
            {"path": anim, **common, "generator": "factory", "model": "ken_burns",
             "duration_s": shot["duration_s"], "width": stills.FRAME[0], "height": stills.FRAME[1]}
        )  # fmt: skip
    replace_assets(ctx, content_id, "still", images)
    replace_assets(ctx, content_id, "still_anim", animations)


def _make_diagrams(
    ctx: AppContext,
    content_id: int,
    shots: list[dict],
    clips: dict[int, Path],
    clip_shots: list[dict],
) -> None:
    """diag_NN.png label cards, plus diag_NN_preview.jpg (the card over a frame) to check by eye."""
    folder = asset_dir(ctx, content_id) / "diagrams"
    folder.mkdir(parents=True, exist_ok=True)
    font = voice.fonts_dir(ctx) / f"{ctx.channel.brand.fonts.label}.ttf"
    if not font.is_file():
        raise FactoryError(f"label font missing: {font}")
    rows = []
    for shot in shots:
        n = shot["n"]
        card = diagrams.render_diagram(
            shot["labels"], shot.get("energy"), ctx.channel, font, folder / f"diag_{n:02d}.png"
        )
        background = folder / f".frame_{n:02d}.png"
        stills.sharpest_frame(clips[clip_for(shot, clip_shots)], background, samples=3)
        diagrams.preview_diagram(card, background, folder / f"diag_{n:02d}_preview.jpg")
        background.unlink()
        rows.append({"path": card, "shot_n": n, "generator": "pillow", "license": "own_work",
                     "width": diagrams.FRAME[0], "height": diagrams.FRAME[1]})  # fmt: skip
    replace_assets(ctx, content_id, "diagram", rows)


def _owner_still(inbox: Path, n: int) -> Path | None:
    for ext in STILL_EXTENSIONS:
        path = inbox / f"still_{n:02d}{ext}"
        if path.is_file():
            return path
    return None


def clip_for(shot: dict, clip_shots: list[dict]) -> int:
    """The clip to borrow a frame from: same beat if possible, then the nearest shot number."""
    same_beat = [s for s in clip_shots if s["beat"] == shot["beat"]] or clip_shots
    return min(same_beat, key=lambda s: abs(s["n"] - shot["n"]))["n"]


# --- helpers ---------------------------------------------------------------------------------


def items_in_status(ctx: AppContext, status: str, limit: int | None) -> list[sqlite3.Row]:
    return ctx.conn.execute(
        """
        SELECT c.* FROM contents c JOIN channels ch ON ch.id = c.channel_id
        WHERE ch.slug = ? AND c.status = ? ORDER BY c.id LIMIT ?
        """,
        (ctx.channel.slug, status, limit or -1),
    ).fetchall()


def run_safely(
    ctx: AppContext, row: sqlite3.Row, result: StageResult, step: Callable[[sqlite3.Row], bool]
) -> None:
    """Run one item's step; a broken item becomes `failed` without stopping the others."""
    try:
        done = step(row)
    except BudgetExceededError:
        raise
    except Exception as exc:
        set_status(ctx.conn, row["id"], "failed", reason=str(exc)[:500])
        log.exception(
            "#%s failed", row["id"], extra={"content_id": row["id"], "event": "generate_failed"}
        )
        result.failed += 1
        return
    if done:
        result.processed += 1
    else:
        result.skipped += 1


def current_script(ctx: AppContext, content_id: int) -> sqlite3.Row:
    script = ctx.conn.execute(
        "SELECT * FROM scripts WHERE content_id = ? AND is_current = 1", (content_id,)
    ).fetchone()
    if script is None:
        raise FactoryError(f"content #{content_id} has no current script")
    return script


def _flow_shots(script: sqlite3.Row) -> list[dict]:
    return _shots(script, "flow_clip")


def _shots(script: sqlite3.Row, kind: str) -> list[dict]:
    return [s for s in json.loads(script["shot_list_json"]) if s["kind"] == kind]


def replace_assets(ctx: AppContext, content_id: int, kind: str, rows: list[dict]) -> None:
    """Replace all assets of one kind for an item (re-running never duplicates rows)."""
    with ctx.conn:
        ctx.conn.execute("DELETE FROM assets WHERE content_id = ? AND kind = ?", (content_id, kind))
        for r in rows:
            path: Path = r["path"]
            ctx.conn.execute(
                """
                INSERT INTO assets (content_id, kind, shot_n, path, sha256, generator, model,
                                    prompt, license, duration_s, width, height)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    content_id,
                    kind,
                    r.get("shot_n"),
                    path.relative_to(ctx.root).as_posix(),
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                    r.get("generator"),
                    r.get("model"),
                    r.get("prompt"),
                    r.get("license"),
                    r.get("duration_s"),
                    r.get("width"),
                    r.get("height"),
                ),
            )


def _kokoro() -> Synthesizer:
    from factory.integrations.kokoro_tts import KokoroTTS

    return KokoroTTS().synthesize
