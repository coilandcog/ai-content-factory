"""Assemble stage, part 1 (ROADMAP 1.9): generated items -> render_youtube.mp4, render_tiktok.mp4
and preview_720p.mp4.

The timeline follows the narration: the shot list's planned durations are scaled to fill
voice.wav exactly, then the 1 s logo sting follows. The video is rendered once; the YouTube mix
adds an Audio Library track, the TikTok mix has no music (D-042). Both keep the Flow clips' own
sound under the voice (D-068). Automated QA and the move to `assembled` come in task 1.10, so the
status stays `generated` here.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import tempfile
from pathlib import Path

from factory.context import AppContext, StageResult
from factory.errors import FactoryError
from factory.media import compose, stills
from factory.media.audio import measure_loudness, normalize_file
from factory.media.ffmpeg import probe
from factory.music import Track, pick_track
from factory.stages import generate, voice
from factory.stages.voice import asset_dir

log = logging.getLogger("factory.assemble")

RENDERS = ("render_youtube.mp4", "render_tiktok.mp4")
PREVIEW = "preview_720p.mp4"
SILENT_LUFS = -60.0  # quieter than this counts as "no sound" (e.g. a clip with a silent track)


def run(ctx: AppContext, *, limit: int | None = None, force: bool = False) -> StageResult:
    """Render every generated item that has no renders yet (`force` re-renders them)."""
    result = StageResult()
    items = [
        row
        for row in generate.items_in_status(ctx, "generated", limit)
        if force or not all((asset_dir(ctx, row["id"]) / name).is_file() for name in RENDERS)
    ]
    if not items:
        log.info("nothing to assemble", extra={"event": "assemble_nothing"})
        return result
    pick_track(ctx)  # stop before any work if the music library is empty
    for row in items:
        generate.run_safely(ctx, row, result, lambda r: _assemble(ctx, r))
    return result


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
