"""Brand files built from the channel YAML: logo, avatar and the 1-second sting.

Everything comes from `display_name` and `brand.palette`, so a new channel gets its own brand
files without code changes (DESIGN_SYSTEM.md §1 and §9).
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from factory.config import ChannelConfig
from factory.media.ffmpeg import encode_rgb_frames

LOGO_FONT = "Montserrat-ExtraBold.ttf"
ACCENT_CHARS = "&"  # drawn in the 'electric' color, everything else in 'text'
STING_FPS = 30
STING_SECONDS = 1.0
STING_SIZES = ((1080, 1920), (1920, 1080))
AVATAR_SIZE = 800

RGB = tuple[int, int, int]


def build_brand_assets(channel: ChannelConfig, fonts_dir: Path, out_dir: Path) -> list[Path]:
    """Write logo.png, logo_on_bg.png, avatar_800.png and both stings; return their paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    font_path = fonts_dir / LOGO_FONT
    bg = _rgb(channel.brand.palette.bg)
    written: list[Path] = []

    logo = render_logo(channel, font_path, width_px=2000)
    written.append(_save(logo, out_dir / "logo.png"))

    on_bg = Image.new("RGBA", (1600, 900), (*bg, 255))
    _paste_centered(on_bg, render_logo(channel, font_path, width_px=1300))
    written.append(_save(on_bg.convert("RGB"), out_dir / "logo_on_bg.png"))

    written.append(_save(render_avatar(channel, font_path), out_dir / "avatar_800.png"))

    for width, height in STING_SIZES:
        path = out_dir / f"sting_{width}x{height}.mp4"
        encode_rgb_frames(
            sting_frames(channel, font_path, width, height),
            width=width,
            height=height,
            fps=STING_FPS,
            duration_s=STING_SECONDS,
            out_path=path,
        )
        written.append(path)
    return written


def render_logo(channel: ChannelConfig, font_path: Path, *, width_px: int) -> Image.Image:
    """The display name in capitals on a transparent background, scaled to width_px wide."""
    return _render_text(channel.display_name.upper(), channel, font_path, width_px)


def render_avatar(channel: ChannelConfig, font_path: Path) -> Image.Image:
    """Square profile picture: a monogram ("C&C") that stays readable at 48 px and inside the
    circle crop YouTube and TikTok apply."""
    bg = _rgb(channel.brand.palette.bg)
    avatar = Image.new("RGBA", (AVATAR_SIZE, AVATAR_SIZE), (*bg, 255))
    # 0.62 of the width keeps the text inside the inscribed circle with a safe margin.
    _paste_centered(avatar, _render_text(_monogram(channel.display_name), channel, font_path, 496))
    return avatar.convert("RGB")


def sting_frames(
    channel: ChannelConfig, font_path: Path, width: int, height: int
) -> Iterator[bytes]:
    """Yield raw RGB frames: the logo fades in while a cyan spark sweeps across the letters."""
    palette = channel.brand.palette
    bg = Image.new("RGBA", (width, height), (*_rgb(palette.bg), 255))
    logo = render_logo(channel, font_path, width_px=int(min(width * 0.78, height * 0.75)))
    left, top = (width - logo.width) // 2, (height - logo.height) // 2
    letters = logo.getchannel("A")  # the letter shapes, used as a mask for the spark
    band = _spark_band(logo.height, band_width=max(logo.width // 6, 8))
    spark_color = _mix(_rgb(palette.electric), (255, 255, 255), 0.35)

    total = round(STING_FPS * STING_SECONDS)
    for i in range(total):
        t = i / STING_FPS
        frame = bg.copy()
        fade = _ease_out(min(t / 0.45, 1.0))
        faded = logo.copy()
        faded.putalpha(letters.point(lambda v, f=fade: round(v * f)))
        frame.alpha_composite(faded, (left, top))

        # The spark crosses from just left of the logo to just right of it between 0.1 s and 0.75 s.
        progress = (t - 0.10) / 0.65
        if 0.0 <= progress <= 1.0:
            center = round(-band.width / 2 + progress * (logo.width + band.width))
            mask = Image.new("L", logo.size, 0)
            mask.paste(band, (center - band.width // 2, 0))
            core = ImageChops.multiply(mask, letters)
            glow = core.filter(ImageFilter.GaussianBlur(radius=max(logo.height // 10, 2)))
            for layer_mask in (glow, core):
                layer = Image.new("RGBA", logo.size, (*spark_color, 255))
                layer.putalpha(layer_mask)
                frame.alpha_composite(layer, (left, top))
        yield frame.convert("RGB").tobytes()


# --- helpers -------------------------------------------------------------------------------


def _render_text(text: str, channel: ChannelConfig, font_path: Path, width_px: int) -> Image.Image:
    """Draw text with accent characters in 'electric', cropped tight, scaled to width_px."""
    palette = channel.brand.palette
    font = ImageFont.truetype(str(font_path), 400)  # draw large, then scale down for sharp edges
    runs = _color_runs(text, _rgb(palette.text), _rgb(palette.electric))
    total = sum(font.getlength(run) for run, _ in runs)
    ascent, descent = font.getmetrics()
    pad = 40
    canvas = Image.new("RGBA", (math.ceil(total) + 2 * pad, ascent + descent + 2 * pad))
    draw = ImageDraw.Draw(canvas)
    x = float(pad)
    for run, color in runs:
        draw.text((x, pad + ascent), run, font=font, fill=(*color, 255), anchor="ls")
        x += font.getlength(run)
    cropped = canvas.crop(canvas.getbbox())
    height = round(cropped.height * width_px / cropped.width)
    return cropped.resize((width_px, height), Image.Resampling.LANCZOS)


def _color_runs(text: str, base: RGB, accent: RGB) -> list[tuple[str, RGB]]:
    """Split text into runs of one color, e.g. 'COIL ', '&', ' COG' (keeps kerning per run)."""
    runs: list[tuple[str, RGB]] = []
    for char in text:
        color = accent if char in ACCENT_CHARS else base
        if runs and runs[-1][1] == color:
            runs[-1] = (runs[-1][0] + char, color)
        else:
            runs.append((char, color))
    return runs


def _monogram(name: str) -> str:
    """'Coil & Cog' -> 'C&C': first letter of each word; accent characters are kept."""
    return "".join(w if w in ACCENT_CHARS else w[0] for w in name.upper().split())


def _spark_band(height: int, band_width: int) -> Image.Image:
    """A vertical band that is brightest in the middle and fades out to both sides."""
    sigma = band_width / 5
    row = [
        round(255 * math.exp(-(((x - band_width / 2) / sigma) ** 2) / 2)) for x in range(band_width)
    ]
    band = Image.new("L", (band_width, 1))
    band.putdata(row)
    return band.resize((band_width, height))


def _ease_out(x: float) -> float:
    return 1 - (1 - x) ** 3


def _mix(a: RGB, b: RGB, amount: float) -> RGB:
    """Blend color a towards color b (amount 0 = a, 1 = b)."""
    red, green, blue = (round(ca + (cb - ca) * amount) for ca, cb in zip(a, b, strict=True))
    return red, green, blue


def _rgb(hex_color: str) -> RGB:
    value = hex_color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _paste_centered(base: Image.Image, overlay: Image.Image) -> None:
    left = (base.width - overlay.width) // 2
    top = (base.height - overlay.height) // 2
    base.alpha_composite(overlay, (left, top))


def _save(image: Image.Image, path: Path) -> Path:
    image.save(path, optimize=True)
    return path
