"""Assemble stage, part 1 (ROADMAP 1.9): generated items -> render_youtube.mp4, render_tiktok.mp4
and preview_720p.mp4.

The timeline follows the narration: the shot list's planned durations are scaled to fill
voice.wav exactly, then the 1 s logo sting follows. The video is rendered once; the YouTube mix
adds an Audio Library track, the TikTok mix has no music (D-042). Both keep the Flow clips' own
sound under the voice (D-068). Then the cover is picked and automated QA runs (task 1.10, D-070):
a pass moves the item to `assembled`, a failure keeps it `generated` with the reasons.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

from factory import films
from factory.context import AppContext, StageResult
from factory.db import note_reason, set_status
from factory.errors import BudgetExceededError, FactoryError
from factory.integrations.gemini import GeminiClient
from factory.media import compose, qa, stills
from factory.media.audio import measure_loudness, normalize_file
from factory.media.ffmpeg import probe
from factory.music import Track, pick_track
from factory.stages import generate, metadata, voice
from factory.stages.script import MIN_DIAGRAMS, previous_variety
from factory.stages.voice import asset_dir

log = logging.getLogger("factory.assemble")

RENDERS = ("render_youtube.mp4", "render_tiktok.mp4")
PREVIEW = "preview_720p.mp4"
QA_REPORT = "qa_report.json"
SILENT_LUFS = -60.0  # quieter than this counts as "no sound" (e.g. a clip with a silent track)


def run(
    ctx: AppContext,
    *,
    limit: int | None = None,
    force: bool = False,
    llm: GeminiClient | None = None,
) -> StageResult:
    """Render generated items (unless renders exist; `force` re-renders), pick the cover, then
    run QA: a pass moves the item to `assembled`, a failure keeps it `generated` with the reason.

    StageResult: processed = passed QA, skipped = QA failed (fix and run again), failed = error.
    """
    result = StageResult()
    items = generate.items_in_status(ctx, "generated", limit)
    if not items:
        log.info("nothing to assemble", extra={"event": "assemble_nothing"})
        return result
    if any(force or not _rendered(ctx, row["id"]) for row in items):
        pick_track(ctx)  # stop before any work if the music library is empty
    for row in items:
        generate.run_safely(ctx, row, result, lambda r: _assemble_and_check(ctx, r, force, llm))
    return result


def _rendered(ctx: AppContext, content_id: int) -> bool:
    return all((asset_dir(ctx, content_id) / name).is_file() for name in RENDERS)


def _assemble_and_check(
    ctx: AppContext, row: sqlite3.Row, force: bool, llm: GeminiClient | None
) -> bool:
    content_id = row["id"]
    if force or not _rendered(ctx, content_id):
        _assemble(ctx, row)
    folder = asset_dir(ctx, content_id)
    _pick_cover(ctx, content_id, folder)
    metadata_error = None
    try:
        metadata.ensure_metadata(ctx, content_id, llm=llm)
    except BudgetExceededError:
        raise
    except FactoryError as exc:  # e.g. Gemini unavailable: a fixable QA failure, retried next run
        metadata_error = str(exc)
    checks = qa.run_checks(_qa_inputs(ctx, row, folder, metadata_error))
    qa.write_report(checks, folder / QA_REPORT)
    failed = qa.failures(checks)
    extra = {"content_id": content_id}
    if failed:
        reason = "QA failed: " + "; ".join(f"{c.name}: {c.detail}" for c in failed)
        note_reason(ctx.conn, content_id, reason[:1000])
        log.warning("#%s %s", content_id, reason, extra={**extra, "event": "qa_failed"})
        return False
    set_status(ctx.conn, content_id, "assembled")
    log.info("#%s QA passed -> assembled", content_id, extra={**extra, "event": "qa_passed"})
    return True


def _qa_inputs(
    ctx: AppContext, row: sqlite3.Row, folder: Path, metadata_error: str | None
) -> qa.QaInputs:
    script = generate.current_script(ctx, row["id"])
    shots = json.loads(script["shot_list_json"])
    code_film = row["visual_style"] in films.CODE_FILM_STYLES
    video_format = getattr(ctx.channel.formats, row["format"])
    source = ctx.conn.execute(
        """
        SELECT t.source FROM assets a JOIN music_tracks t ON t.id = a.music_track_id
        WHERE a.content_id = ? AND a.kind = 'music'
        """,
        (row["id"],),
    ).fetchone()
    return qa.QaInputs(
        folder=folder,
        aspect=video_format.aspect,
        min_s=video_format.min_s,
        max_s=video_format.max_s,
        # a film made by code shows its labels itself, so there are no separate cards (D-111)
        min_diagrams=0 if code_film else MIN_DIAGRAMS[row["format"]],
        diagram_shots=[] if code_film else [s["n"] for s in shots if s["kind"] == "diagram"],
        subtitle_font=voice.subtitle_font(ctx),
        music_source=source["source"] if source else None,
        variety=(
            (row["hook_type"], row["beat_order"]),
            previous_variety(ctx.conn, row["channel_id"], row["id"]),
        ),
        metadata_problems=_metadata_problems(ctx, row["id"]),
        metadata_error=metadata_error,
        similarity_max=script["similarity_max"],
        similarity_threshold=ctx.channel.scoring.similarity_block_threshold,
    )


def _metadata_problems(ctx: AppContext, content_id: int) -> list[str] | None:
    if not (asset_dir(ctx, content_id) / metadata.METADATA_FILE).is_file():
        return None
    return metadata.validate(metadata.load(ctx, content_id))


def _pick_cover(ctx: AppContext, content_id: int, folder: Path) -> Path:
    """cover.jpg: the sharpest frame of the Cutaway clip (else the first clip), 1080x1920, no
    text (DESIGN_SYSTEM.md §9); for a film made by code (blueprint, Blender) the sharpest frame of
    the film (D-111, D-123). The owner confirms it at Gate 2."""
    style = ctx.conn.execute("SELECT visual_style FROM contents WHERE id = ?", (content_id,))
    visual_style = style.fetchone()[0]
    if visual_style in films.CODE_FILM_STYLES:
        chosen, clip, licence = {"n": None}, folder / films.FILM_FILES[visual_style], "own_work"
    else:
        shots = json.loads(generate.current_script(ctx, content_id)["shot_list_json"])
        clips = [s for s in shots if s["kind"] == "flow_clip"]
        chosen = next((s for s in clips if s["beat"] == "cutaway"), clips[0])
        clip, licence = folder / "clips" / f"shot_{chosen['n']:02d}.mp4", "google_flow_output"
    with tempfile.TemporaryDirectory(prefix=".cover-", dir=folder) as tmp:
        frame = Image.open(stills.sharpest_frame(clip, Path(tmp) / "frame.png")).convert("RGB")
        cover = ImageOps.fit(frame, (1080, 1920), Image.Resampling.LANCZOS)
    path = folder / "cover.jpg"
    cover.save(path, quality=92)
    details = {
        "path": path,
        "shot_n": chosen["n"],
        "generator": "ffmpeg",
        "model": f"frame of {clip.stem}",
        "license": licence,
        "width": 1080,
        "height": 1920,
    }
    generate.replace_assets(ctx, content_id, "cover", [details])
    return path


def _assemble(ctx: AppContext, row: sqlite3.Row) -> bool:
    content_id = row["id"]
    folder = asset_dir(ctx, content_id)
    script = generate.current_script(ctx, content_id)
    shots = json.loads(script["shot_list_json"])
    voice_wav = folder / "voice.wav"
    voice_s = float(probe(voice_wav)["format"]["duration"])
    track = pick_track(ctx)
    with tempfile.TemporaryDirectory(prefix=".assemble-", dir=folder) as tmp:
        work = Path(tmp)
        if row["visual_style"] in films.CODE_FILM_STYLES:  # the film is the whole picture
            film = folder / films.FILM_FILES[row["visual_style"]]
            if not film.is_file():
                raise FactoryError(f"missing {film.name}: run `factory generate` again")
            segments = [compose.Segment(1, "flow_clip", film, _seconds(film), voice_s)]
        else:
            segments = _segments(ctx, folder, shots, voice_s, work)
        total_s = voice_s + _sting_seconds(ctx)
        video = compose.render_video(
            segments,
            folder / voice.SUBS_FILE,
            voice.fonts_dir(ctx),
            _sting(ctx),
            work / "video.mp4",
        )
        mixes = {
            "render_youtube.mp4": (track.path, compose.MUSIC_LUFS - _loudness(track.path)),
            "render_tiktok.mp4": None,  # no music on TikTok: the owner adds a TikTok sound
        }
        for name, music in mixes.items():
            raw = compose.render_mix(voice_wav, segments, total_s, work / f"{name}.raw.wav", music)
            mastered = normalize_file(raw, work / f"{name}.wav")
            compose.mux(video, work / f"{name}.wav", folder / name)
            log.info(
                "#%s %s: %.1f s, %.1f LUFS",
                content_id,
                name,
                total_s,
                mastered.integrated_lufs,
                extra={"content_id": content_id, "event": "rendered"},
            )
    compose.preview(folder / "render_youtube.mp4", folder / PREVIEW)
    _record(ctx, content_id, folder, track, total_s)
    return True


def _segments(
    ctx: AppContext, folder: Path, shots: list[dict], voice_s: float, work: Path
) -> list[compose.Segment]:
    clip_shots = [s for s in shots if s["kind"] == "flow_clip"]
    slots = compose.slots([s["duration_s"] for s in shots], voice_s)
    segments = []
    for shot, slot in zip(shots, slots, strict=True):
        n, kind = shot["n"], shot["kind"]
        if kind == "flow_clip":
            source = folder / "clips" / f"shot_{n:02d}.mp4"
            segments.append(compose.Segment(n, kind, source, _seconds(source), slot,
                                            sfx_gain_db=_sfx_gain(source)))  # fmt: skip
        elif kind == "still":
            source = folder / "stills" / f"still_{n:02d}_anim.mp4"
            segments.append(compose.Segment(n, kind, source, _seconds(source), slot))
        else:  # diagram: a Ken Burns move over a frame of the same beat's clip, card on top
            clip = folder / "clips" / f"shot_{generate.clip_for(shot, clip_shots):02d}.mp4"
            frame = stills.sharpest_frame(clip, work / f"frame_{n:02d}.png", samples=3)
            background = stills.animate_still(frame, work / f"bg_{n:02d}.mp4", slot, variant=n)
            card = folder / "diagrams" / f"diag_{n:02d}.png"
            segments.append(compose.Segment(n, kind, background, slot, slot, card=card))
        missing = [p for p in (segments[-1].source, segments[-1].card) if p and not p.is_file()]
        if missing:
            raise FactoryError(f"missing {missing[0].name}: run `factory generate` again")
    return segments


def _sfx_gain(clip: Path) -> float | None:
    """Gain that brings a clip's own sound to SFX_LUFS, or None if it has no audible sound."""
    if not any(s.get("codec_type") == "audio" for s in probe(clip)["streams"]):
        return None
    loudness = _loudness(clip)
    return None if loudness < SILENT_LUFS else compose.SFX_LUFS - loudness


def _loudness(path: Path) -> float:
    value = measure_loudness(path).integrated_lufs
    return value if value == value else -99.0  # NaN/-inf from silence counts as silent


def _seconds(path: Path) -> float:
    if not path.is_file():
        raise FactoryError(f"missing {path.name}: run `factory generate` again")
    return float(probe(path)["format"]["duration"])


def _sting(ctx: AppContext) -> Path:
    path = ctx.root / ctx.settings.paths.assets_static_dir / "logo" / "sting_1080x1920.mp4"
    if not path.is_file():
        raise FactoryError(f"logo sting missing: {path} (run `factory brand build`)")
    return path


def _sting_seconds(ctx: AppContext) -> float:
    return _seconds(_sting(ctx))


def _record(ctx: AppContext, content_id: int, folder: Path, track: Track, total_s: float) -> None:
    (folder / "music.txt").write_text(f"{track.id}\t{track.title}\n")
    common = {"generator": "ffmpeg", "license": "own_work", "duration_s": round(total_s, 2)}
    full_hd = {**common, "width": 1080, "height": 1920}
    outputs = (
        ("render_youtube", folder / "render_youtube.mp4", full_hd),
        ("render_tiktok", folder / "render_tiktok.mp4", full_hd),
        ("preview", folder / PREVIEW, {**common, "width": 720, "height": 1280}),
    )
    for kind, path, details in outputs:
        generate.replace_assets(ctx, content_id, kind, [{"path": path, **details}])
    music_txt = (folder / "music.txt").relative_to(ctx.root).as_posix()
    with ctx.conn:
        ctx.conn.execute(
            "DELETE FROM assets WHERE content_id = ? AND kind = 'music'", (content_id,)
        )
        ctx.conn.execute(
            """
            INSERT INTO assets (content_id, kind, path, generator, music_track_id, license,
                                license_url)
            VALUES (?, 'music', ?, 'youtube_audio_library', ?, 'youtube_audio_library', ?)
            """,
            (content_id, music_txt, track.id, track.license_url),
        )
