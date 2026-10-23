"""FFmpeg building blocks for assembly (ROADMAP 1.9, D-068).

The video is rendered once; the audio is mixed separately per platform (YouTube with music, TikTok
without) and muxed onto the same video, which halves the rendering time.

Beginner note: an FFmpeg "filter graph" is a recipe of named streams. `[0:v]` is the video of
input 0; `scale=...,fps=30[v0]` processes it and names the result `[v0]`; later filters use
`[v0]` as their input. One graph can combine many inputs into one output.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from factory.media.ffmpeg import run_ffmpeg
from factory.media.subtitles import filter_arg

FPS = 30
WIDTH, HEIGHT = 1080, 1920
MAX_SLOWDOWN = 1.25  # a clip may play at most 25% slower before it holds its last frame
CARD_SLIDE_S = 8 / FPS  # DESIGN_SYSTEM.md §7: slides and fades of 6-10 frames
CARD_SLIDE_PX = 140
SAMPLE_RATE = 48_000
# Levels before the final -14 LUFS master. These are the levels in pauses (and under the sting);
# while the narrator speaks, ducking pulls them down several dB more. Music -20 was the owner's
# pick by ear (version "B"): about 15 dB under the voice on average (D-069). The Veo sound ends up
# ~18 dB under the voice while it speaks (D-068).
SFX_LUFS = -24.0
MUSIC_LUFS = -20.0
MUSIC_FADE_S = 1.5
# Ducking (sidechain compression): the voice controls the volume of SFX/music. Speech sits
# roughly 15-25 dB above the threshold, so ratio 2 removes about 7-12 dB while it talks.
DUCK = "threshold=0.02:ratio=2:attack=15:release=350"


@dataclass(frozen=True)
class Segment:
    """One shot on the timeline."""

    n: int
    kind: str  # flow_clip | still | diagram
    source: Path  # clip, Ken Burns still, or the diagram's Ken Burns background
    source_s: float  # how long the source is
    slot_s: float  # how long it is on screen
    card: Path | None = None  # diagram label card (transparent PNG)
    sfx_gain_db: float | None = None  # gain for the clip's own sound; None = no usable sound


# --- timeline maths ----------------------------------------------------------------------------


def slots(planned_s: Sequence[float], total_s: float) -> list[float]:
    """Scale the shot list's planned durations so they fill exactly `total_s` (the narration).

    Rounded to whole frames with a running total, so the slots add up to the exact frame count.
    """
    scale = total_s / sum(planned_s)
    out: list[float] = []
    running, previous_frame = 0.0, 0
    for planned in planned_s:
        running += planned * scale
        frame = round(running * FPS)
        out.append((frame - previous_frame) / FPS)
        previous_frame = frame
    return out


def clip_fit(source_s: float, slot_s: float) -> tuple[float, float]:
    """How a clip fills its slot: (slow-down factor, seconds to hold the last frame).

    Long enough: play normally and cut. Too short: slow down up to MAX_SLOWDOWN, then hold.
    """
    if source_s >= slot_s:
        return 1.0, 0.0
    factor = min(slot_s / source_s, MAX_SLOWDOWN)
    return factor, max(slot_s - source_s * factor, 0.0)


# --- video -------------------------------------------------------------------------------------


def render_video(
    segments: Sequence[Segment], subs: Path, fonts_dir: Path, sting: Path, out_path: Path
) -> Path:
    """All segments in order, subtitles burned in, then the logo sting; video only (no audio)."""
    inputs: list[str] = []
    graph: list[str] = []
    for i, seg in enumerate(segments):
        inputs += ["-i", str(seg.source)]
        graph.append(f"[{i}:v]{_fit_chain(seg)}[seg{i}]")
    next_input = len(segments)
    labels = []
    for i, seg in enumerate(segments):
        if seg.card is None:
            labels.append(f"[seg{i}]")
            continue
        inputs += [
            "-loop",
            "1",
            "-framerate",
            str(FPS),
            "-t",
            f"{seg.slot_s:.3f}",
            "-i",
            str(seg.card),
        ]
        fade = min(CARD_SLIDE_S, seg.slot_s / 3)
        graph.append(
            f"[{next_input}:v]format=rgba,fade=t=in:st=0:d={fade:.3f}:alpha=1,"
            f"fade=t=out:st={seg.slot_s - fade:.3f}:d={fade:.3f}:alpha=1[card{i}]"
        )
        # The card slides in from the left while it fades in (x goes from -140 px to 0).
        graph.append(
            f"[seg{i}][card{i}]overlay=x='if(lt(t,{fade:.3f}),-{CARD_SLIDE_PX}*(1-t/{fade:.3f}),0)'"
            f":y=0:eof_action=pass,format=yuv420p[dia{i}]"
        )
        labels.append(f"[dia{i}]")
        next_input += 1
    inputs += ["-i", str(sting)]
    graph.append(f"[{next_input}:v]{_normalise_chain()},setpts=PTS-STARTPTS[sting]")
    graph.append(f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[body]")
    graph.append(f"[body]ass={filter_arg(subs)}:fontsdir={filter_arg(fonts_dir)}[subbed]")
    graph.append("[subbed][sting]concat=n=2:v=1:a=0[vout]")
    run_ffmpeg(
        [*inputs, "-filter_complex", ";".join(graph), "-map", "[vout]", "-an",
         "-c:v", "libx264", "-profile:v", "high", "-preset", "medium", "-crf", "18",
         "-pix_fmt", "yuv420p", "-r", str(FPS), str(out_path)]
    )  # fmt: skip
    return out_path


def _normalise_chain() -> str:
    """Any input to 1080x1920 (cover + centre crop, sharp Lanczos upscaling), square pixels."""
    return (
        f"scale={WIDTH}:{HEIGHT}:flags=lanczos:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},setsar=1"
    )


def _fit_chain(seg: Segment) -> str:
    if seg.kind == "flow_clip":
        factor, hold = clip_fit(seg.source_s, seg.slot_s)
        timing = f"setpts={factor:.4f}*(PTS-STARTPTS)"
        if hold > 0:
            timing += f",tpad=stop_mode=clone:stop_duration={hold:.3f}"
    else:  # Ken Burns clips are stretched or squeezed slightly to fit exactly
        timing = f"setpts={seg.slot_s / seg.source_s:.4f}*(PTS-STARTPTS)"
    return (
        f"{_normalise_chain()},{timing},fps={FPS},format=yuv420p,"
        f"trim=duration={seg.slot_s:.3f},setpts=PTS-STARTPTS"
    )


# --- audio -------------------------------------------------------------------------------------


def render_mix(
    voice: Path,
    segments: Sequence[Segment],
    total_s: float,
    out_wav: Path,
    music: tuple[Path, float] | None = None,
) -> Path:
    """Voice + the clips' own sound (ducked) [+ music, ducked, faded out] as a 48 kHz stereo WAV.

    `music` is (track file, gain in dB). Without it (the TikTok mix) there is no music at all.
    """
    stereo = f"aformat=sample_rates={SAMPLE_RATE}:channel_layouts=stereo"
    keys = 2 if music else 1
    inputs = ["-i", str(voice)]
    count_in = 1  # FFmpeg numbers inputs 0, 1, 2, ... in the order they are given
    graph = [
        f"[0:a]{stereo},apad=whole_dur={total_s:.3f},atrim=0:{total_s:.3f},"
        f"asplit={keys + 1}[voice]" + "".join(f"[key{k}]" for k in range(keys))
    ]
    pieces = []
    for i, seg in enumerate(segments):
        if seg.kind == "flow_clip" and seg.sfx_gain_db is not None:
            inputs += ["-i", str(seg.source)]
            index, count_in = count_in, count_in + 1
            factor, _ = clip_fit(seg.source_s, seg.slot_s)
            tempo = f",atempo={1 / factor:.4f}" if factor > 1 else ""
            graph.append(
                f"[{index}:a]{stereo},volume={seg.sfx_gain_db:.2f}dB{tempo},"
                f"atrim=0:{seg.slot_s:.3f},apad=whole_dur={seg.slot_s:.3f},asetpts=PTS-STARTPTS[s{i}]"
            )
        else:
            graph.append(f"aevalsrc=0:c=stereo:s={SAMPLE_RATE}:d={seg.slot_s:.3f}[s{i}]")
        pieces.append(f"[s{i}]")
    graph.append(
        f"{''.join(pieces)}concat=n={len(pieces)}:v=0:a=1,apad=whole_dur={total_s:.3f},"
        f"atrim=0:{total_s:.3f}[sfx]"
    )
    graph.append(f"[sfx][key0]sidechaincompress={DUCK}[sfxd]")
    mix_inputs = "[voice][sfxd]"
    if music:
        track, gain_db = music
        inputs += ["-stream_loop", "-1", "-i", str(track)]  # loop the track if it is too short
        graph.append(
            f"[{count_in}:a]{stereo},volume={gain_db:.2f}dB,atrim=0:{total_s:.3f},"
            f"afade=t=out:st={max(total_s - MUSIC_FADE_S, 0):.3f}:d={MUSIC_FADE_S},"
            f"asetpts=PTS-STARTPTS[music]"
        )
        graph.append(f"[music][key1]sidechaincompress={DUCK}[musicd]")
        mix_inputs += "[musicd]"
    count = 3 if music else 2
    graph.append(f"{mix_inputs}amix=inputs={count}:normalize=0:duration=first[mix]")
    run_ffmpeg(
        [*inputs, "-filter_complex", ";".join(graph), "-map", "[mix]",
         "-c:a", "pcm_s16le", "-ar", str(SAMPLE_RATE), str(out_wav)]
    )  # fmt: skip
    return out_wav


# --- output files ------------------------------------------------------------------------------


def mux(video: Path, audio: Path, out_path: Path) -> Path:
    """Combine the rendered video (copied, not re-encoded) with an audio mix as AAC 192 kbps."""
    run_ffmpeg(
        ["-i", str(video), "-i", str(audio), "-map", "0:v", "-map", "1:a", "-c:v", "copy",
         "-c:a", "aac", "-b:a", "192k", "-ar", str(SAMPLE_RATE), "-shortest",
         "-movflags", "+faststart", str(out_path)]
    )  # fmt: skip
    return out_path


def preview(render: Path, out_path: Path) -> Path:
    """A 720x1280 copy for review on a phone (kept small for Telegram's 50 MB limit later)."""
    run_ffmpeg(
        ["-i", str(render), "-vf", "scale=720:1280:flags=lanczos", "-c:v", "libx264",
         "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
         "-movflags", "+faststart", str(out_path)]
    )  # fmt: skip
    return out_path
