"""Still images for "still" shots: pick a frame from a clip, and animate a still (Ken Burns).

Ken Burns = a slow zoom and pan across a still photo, so it feels like video. Frames are drawn
in Pillow with sub-pixel crop boxes and piped into FFmpeg, which avoids the jitter of FFmpeg's
zoompan filter. Parallax would need a depth-estimation model, so it isn't done (D-067).
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps, ImageStat

from factory.media.ffmpeg import encode_rgb_frames, probe, run_ffmpeg

FRAME = (1080, 1920)
FPS = 30
ZOOM = 0.08  # 1.00 -> 1.08 over the shot: slow enough to hide the softness of upscaled frames
PAN = 0.03  # sideways drift, as a share of the image width
FRAME_SAMPLES = 8


def sharpest_frame(clip: Path, out_png: Path, samples: int = FRAME_SAMPLES) -> Path:
    """Save the sharpest of `samples` evenly spaced frames of a clip (skipping its first and last
    half second, where Veo clips often fade or blur)."""
    duration = float(probe(clip)["format"]["duration"])
    start, end = min(0.5, duration / 4), max(duration - 0.5, duration * 3 / 4)
    best: tuple[float, Image.Image] | None = None
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(samples):
            t = start + (end - start) * i / max(samples - 1, 1)
            frame_path = Path(tmp) / f"f{i}.png"
            run_ffmpeg(["-ss", f"{t:.3f}", "-i", str(clip), "-frames:v", "1", str(frame_path)])
            frame = Image.open(frame_path).convert("RGB")
            score = _sharpness(frame)
            if best is None or score > best[0]:
                best = (score, frame)
    assert best is not None
    out_png.parent.mkdir(parents=True, exist_ok=True)
    best[1].save(out_png)
    return out_png


def animate_still(image: Path, out_mp4: Path, duration_s: float, *, variant: int) -> Path:
    """A 1080x1920, 30 fps MP4 of the image with a slow Ken Burns move.

    `variant` (e.g. the shot number) alternates zoom in/out and pan left/right between shots.
    """
    source = ImageOps.fit(
        Image.open(image).convert("RGB"), _cover_size(image), method=Image.Resampling.LANCZOS
    )
    zoom_in, pan_right = variant % 2 == 1, (variant // 2) % 2 == 0
    encode_rgb_frames(
        _ken_burns_frames(source, round(duration_s * FPS), zoom_in, pan_right),
        width=FRAME[0],
        height=FRAME[1],
        fps=FPS,
        duration_s=duration_s,
        out_path=out_mp4,
    )
    return out_mp4


def _ken_burns_frames(
    source: Image.Image, count: int, zoom_in: bool, pan_right: bool
) -> Iterator[bytes]:
    width, height = source.size
    for i in range(count):
        t = i / max(count - 1, 1)
        ease = t * t * (3 - 2 * t)  # smoothstep: starts and ends gently
        zoom = 1 + ZOOM * (ease if zoom_in else 1 - ease)
        box_w, box_h = width / zoom, height / zoom
        drift = (PAN * width) * (ease - 0.5) * (1 if pan_right else -1)
        cx = min(max(width / 2 + drift, box_w / 2), width - box_w / 2)
        box = (cx - box_w / 2, (height - box_h) / 2, cx + box_w / 2, (height + box_h) / 2)
        frame = source.resize(FRAME, Image.Resampling.BICUBIC, box=box)  # float box = sub-pixel
        yield frame.tobytes()


def _cover_size(image: Path) -> tuple[int, int]:
    """A 9:16 working size at least as big as the frame, so zooming never upscales twice."""
    with Image.open(image) as img:
        scale = max(FRAME[0] / img.width, FRAME[1] / img.height, 1.0)
        w = max(FRAME[0], round(min(img.width, img.height * 9 / 16) * scale))
    return w, round(w * 16 / 9)


def _sharpness(frame: Image.Image) -> float:
    """Variance of the edge image: higher means more crisp detail (blurred frames score low)."""
    edges = frame.convert("L").resize((360, 640)).filter(ImageFilter.FIND_EDGES)
    return ImageStat.Stat(edges).var[0]
