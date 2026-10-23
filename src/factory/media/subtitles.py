"""Word-highlight subtitles as an ASS file (DESIGN_SYSTEM.md §6).

Words and spelling come from the approved script; times come from Kokoro's token timestamps
(D-065). Each "page" shows at most 2 lines of at most 4 words; the word being spoken is cyan.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import ImageFont

from factory.config import ChannelConfig
from factory.integrations.kokoro_tts import TimedToken
from factory.media.ffmpeg import run_ffmpeg

FONT_SIZE = 78
OUTLINE_PX = 6
WORDS_PER_LINE = 4
LINES_PER_PAGE = 2
# DESIGN_SYSTEM §6 allows 820 px, but a centered 820 px block would reach x = 950 and the right
# 160 px (x > 920) must stay free for the like/share buttons; 760 px keeps it inside (D-065).
MAX_LINE_PX = 760
BASELINE_Y = 1344  # about 30% from the bottom of a 1920 px frame
NEW_PAGE_PAUSE_S = 0.6  # a longer pause starts a fresh page
HOLD_S = 0.3  # how long the last page stays after its last word
SENTENCE_END = re.compile(r"[.!?…]+[\"')\]]*$")


@dataclass(frozen=True)
class Word:
    text: str  # exactly as written in the script, punctuation included
    start_s: float
    end_s: float


# --- aligning script words to spoken tokens ----------------------------------------------------


def align_words(script_text: str, tokens: Sequence[TimedToken]) -> list[Word]:
    """Give every script word a start and end time from Kokoro's tokens.

    Matching uses lowercase letters and digits only, so punctuation and split contractions
    ("does" + "n't") still line up. Script words with no matching token get times shared out
    between their neighbours.
    """
    words = script_text.split()
    if not words:
        return []
    spoken = [t for t in tokens if _key(t.text)]
    timed: list[tuple[float, float] | None] = [None] * len(words)
    matcher = difflib.SequenceMatcher(
        a=[_key(w) for w in words], b=[_key(t.text) for t in spoken], autojunk=False
    )
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if tag == "equal":
            for i in range(a1 - a0):
                timed[a0 + i] = (spoken[b0 + i].start_s, spoken[b0 + i].end_s)
        elif tag == "replace":
            _share(timed, a0, a1, spoken[b0:b1])
    _fill_gaps(timed, end_s=spoken[-1].end_s if spoken else 0.0)
    return [Word(w, s, e) for w, (s, e) in zip(words, timed, strict=True)]


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _share(timed: list, a0: int, a1: int, tokens: Sequence[TimedToken]) -> None:
    """n script words ↔ m tokens that differ in spelling: split the tokens evenly by position."""
    n, m = a1 - a0, len(tokens)
    for i in range(n):
        first, last = i * m // n, max(i * m // n, (i + 1) * m // n - 1)
        timed[a0 + i] = (tokens[first].start_s, tokens[last].end_s)


def _fill_gaps(timed: list, *, end_s: float) -> None:
    """Untimed words get an equal share of the time between the timed words around them."""
    i = 0
    while i < len(timed):
        if timed[i] is not None:
            i += 1
            continue
        j = i
        while j < len(timed) and timed[j] is None:
            j += 1
        start = timed[i - 1][1] if i > 0 else 0.0
        stop = timed[j][0] if j < len(timed) else max(end_s, start)
        step = (stop - start) / (j - i)
        for k in range(i, j):
            timed[k] = (start + (k - i) * step, start + (k - i + 1) * step)
        i = j


# --- pages and lines ---------------------------------------------------------------------------


def build_pages(words: Sequence[Word], font_path: Path) -> list[list[list[Word]]]:
    """Group words into pages of up to 2 lines, each line at most 4 words and MAX_LINE_PX wide.

    A sentence end or a long pause closes the page, so a page never mixes two sentences.
    """
    font = ImageFont.truetype(str(font_path), FONT_SIZE)
    pages: list[list[list[Word]]] = []
    page: list[list[Word]] = []
    line: list[Word] = []
    previous: Word | None = None

    def close_line() -> None:
        nonlocal line
        if line:
            page.append(line)
            line = []

    def close_page() -> None:
        nonlocal page
        close_line()
        if page:
            pages.append(page)
            page = []

    for word in words:
        if previous and word.start_s - previous.end_s > NEW_PAGE_PAUSE_S:
            close_page()
        candidate = " ".join(w.text for w in [*line, word])
        if line and (len(line) >= WORDS_PER_LINE or font.getlength(candidate) > MAX_LINE_PX):
            close_line()
            if len(page) >= LINES_PER_PAGE:
                close_page()
        line.append(word)
        if SENTENCE_END.search(word.text):
            close_page()
        previous = word
    close_page()
    return pages


# --- the ASS file ------------------------------------------------------------------------------


def write_ass(
    words: Sequence[Word], channel: ChannelConfig, font_path: Path, out_path: Path, aspect: str
) -> Path:
    """Write the subtitle file: one event per spoken word, showing its page with that word cyan."""
    width, height = (1080, 1920) if aspect == "9:16" else (1920, 1080)
    palette = channel.brand.palette
    font_name = " ".join(ImageFont.truetype(str(font_path), FONT_SIZE).getname())
    pages = build_pages(words, font_path)
    lines = [
        "[Script Info]",
        "; Coil & Cog word-highlight subtitles (DESIGN_SYSTEM.md §6)",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 2",  # never wrap by itself: build_pages already chose the line breaks
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{font_name},{FONT_SIZE},{ass_color(palette.text)},"
        f"{ass_color(palette.electric)},{ass_color(palette.bg)},&H00000000,"
        f"0,0,0,0,100,100,0,0,1,{OUTLINE_PX},0,2,0,0,{height - BASELINE_Y * height // 1920},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    highlight = ass_inline_color(palette.electric)
    for index, page in enumerate(pages):
        flat = [w for line in page for w in line]
        next_start = pages[index + 1][0][0].start_s if index + 1 < len(pages) else None
        page_end = flat[-1].end_s + HOLD_S
        if next_start is not None:
            page_end = min(page_end, next_start)
        for i, word in enumerate(flat):
            start = word.start_s
            end = flat[i + 1].start_s if i + 1 < len(flat) else page_end
            text = r"\N".join(
                " ".join(
                    f"{{\\c{highlight}}}{w.text}{{\\r}}" if w is word else w.text for w in line
                )
                for line in page
            )
            lines.append(
                f"Dialogue: 0,{ass_time(start)},{ass_time(max(end, start + 0.01))},"
                f"Default,,0,0,0,,{text}"
            )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def ass_color(hex_color: str) -> str:
    """'#00D1FF' -> '&H00FFD100': ASS writes colours as alpha, blue, green, red."""
    value = hex_color.lstrip("#")
    red, green, blue = value[0:2], value[2:4], value[4:6]
    return f"&H00{blue}{green}{red}".upper()


def ass_inline_color(hex_color: str) -> str:
    """'#00D1FF' -> '&HFFD100&': inside event text a colour has no alpha and ends with '&'."""
    value = hex_color.lstrip("#")
    return f"&H{value[4:6]}{value[2:4]}{value[0:2]}&".upper()


def ass_time(seconds: float) -> str:
    """3.456 -> '0:00:03.46' (hours:minutes:seconds.centiseconds)."""
    centis = round(max(seconds, 0.0) * 100)
    hours, centis = divmod(centis, 360_000)
    minutes, centis = divmod(centis, 6_000)
    secs, centis = divmod(centis, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


# --- preview -----------------------------------------------------------------------------------


def render_preview(
    voice_wav: Path, ass_path: Path, fonts_dir: Path, out_path: Path, background: str
) -> Path:
    """A small MP4 for checking subtitles on a phone: plain background + voice + burned subtitles.

    The subtitles are drawn at full 1080x1920 size (where their positions are defined), then the
    picture is scaled to 540x960 to keep the file small.
    """
    subtitles = f"ass={filter_arg(ass_path)}:fontsdir={filter_arg(fonts_dir)}"
    run_ffmpeg(
        [
            "-f", "lavfi", "-i", f"color=c=0x{background.lstrip('#')}:s=1080x1920:r=30",
            "-i", str(voice_wav),
            "-vf", f"{subtitles},scale=540:960",
            "-shortest", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
            str(out_path),
        ]
    )  # fmt: skip
    return out_path


def filter_arg(path: Path) -> str:
    """Escape a path for use inside an FFmpeg filter argument (':' and '\\' and quotes)."""
    escaped = str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    return f"'{escaped}'"
