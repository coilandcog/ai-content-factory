"""Diagram overlays: a "label card" drawn by code (DESIGN_SYSTEM.md §7, D-067).

AI footage doesn't tell us where each part sits in the frame, so arrows would point at the wrong
place. Instead a semi-transparent card in the upper safe area lists 1-4 part labels, each with a
dot in the energy colour. Assembly (task 1.9) slides and fades it in over the clip or still.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from factory.config import ChannelConfig

FRAME = (1080, 1920)
# Labels stay inside x 90-920 (safe zones) and above the subtitles (which start near y 1180).
SAFE_BOX = (90, 220, 920, 1100)
CARD_LEFT, CARD_TOP = 90, 260
FONT_SIZE, MIN_FONT_SIZE = 44, 30  # DESIGN_SYSTEM.md §4: diagram labels in Inter SemiBold 44 px
PADDING, ROW_GAP, DOT_R = 36, 22, 11
CARD_ALPHA = 205  # ~80 % opaque graphite: readable over busy footage
SMALL_WORDS = {"a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to"}


def title_case(label: str) -> str:
    """'diode bridge to battery' -> 'Diode Bridge to Battery'; words with capitals ('AC') stay."""
    words = label.split()
    out = []
    for i, word in enumerate(words):
        keep = any(c.isupper() for c in word) or (i > 0 and word in SMALL_WORDS)
        out.append(word if keep else word[:1].upper() + word[1:])
    return " ".join(out)


def render_diagram(
    labels: Sequence[str],
    energy: str | None,
    channel: ChannelConfig,
    font_path: Path,
    out_path: Path,
) -> Path:
    """Write a transparent 1080x1920 PNG with the label card in the upper safe area."""
    palette = channel.brand.palette
    dot_color = _rgb(getattr(palette, energy or "electric"))
    texts = [title_case(label) for label in labels]
    font = _fitting_font(texts, font_path)
    ascent, descent = font.getmetrics()
    row_h = ascent + descent
    text_x = CARD_LEFT + PADDING + 2 * DOT_R + 20
    text_w = max(font.getlength(t) for t in texts)
    right = min(round(text_x + text_w + PADDING), SAFE_BOX[2])
    bottom = CARD_TOP + 2 * PADDING + len(texts) * row_h + (len(texts) - 1) * ROW_GAP

    layer = Image.new("RGBA", FRAME, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.rounded_rectangle(
        (CARD_LEFT, CARD_TOP, right, bottom),
        radius=28,
        fill=(*_rgb(palette.bg), CARD_ALPHA),
        outline=(*dot_color, 150),
        width=3,
    )
    y = CARD_TOP + PADDING
    for text in texts:
        cy = y + row_h // 2
        cx = CARD_LEFT + PADDING + DOT_R
        draw.ellipse((cx - DOT_R, cy - DOT_R, cx + DOT_R, cy + DOT_R), fill=(*dot_color, 255))
        draw.text((text_x, y), text, font=font, fill=(*_rgb(palette.text), 255))
        y += row_h + ROW_GAP
    out_path.parent.mkdir(parents=True, exist_ok=True)
    layer.save(out_path)
    return out_path


def preview_diagram(diagram_png: Path, background: Path, out_path: Path) -> Path:
    """The card over a background frame (scaled to cover 1080x1920), to check readability."""
    base = ImageOps.fit(Image.open(background).convert("RGB"), FRAME, Image.Resampling.LANCZOS)
    base = base.convert("RGBA")
    base.alpha_composite(Image.open(diagram_png).convert("RGBA"))
    base.convert("RGB").save(out_path, quality=90)
    return out_path


def _fitting_font(texts: Sequence[str], font_path: Path) -> ImageFont.FreeTypeFont:
    """44 px, or smaller (down to 30 px) if the longest label wouldn't fit inside the safe box."""
    room = SAFE_BOX[2] - (CARD_LEFT + PADDING + 2 * DOT_R + 20) - PADDING
    for size in range(FONT_SIZE, MIN_FONT_SIZE - 1, -2):
        font = ImageFont.truetype(str(font_path), size)
        if max(font.getlength(t) for t in texts) <= room:
            return font
    return ImageFont.truetype(str(font_path), MIN_FONT_SIZE)


def _rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
