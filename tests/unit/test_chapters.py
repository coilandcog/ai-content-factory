"""YouTube chapter lists for long videos (D-128): titles from `chapters`, times from `scenes`."""

from __future__ import annotations

import sqlite3

import pytest

from factory.config import load_channel
from factory.db import add_content, connect, migrate, upsert_channel
from factory.stages import metadata


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:  # noqa: ANN001
    conn = connect(tmp_path / "test.db")
    migrate(conn)
    upsert_channel(conn, load_channel("coil-and-cog"))
    yield conn
    conn.close()


def item_with(conn: sqlite3.Connection, chapters: list[tuple[str, float, float]]) -> int:
    """One scene per chapter: (title, start s, end s)."""
    content_id = add_content(conn, load_channel("coil-and-cog"), object_name="alternator",
                             fmt="episode", pillar="vehicles", style="blender")  # fmt: skip
    with conn:
        for n, (title, start, end) in enumerate(chapters, 1):
            conn.execute("INSERT INTO chapters (content_id, n, title) VALUES (?, ?, ?)",
                         (content_id, n, title))  # fmt: skip
            conn.execute(
                "INSERT INTO scenes (content_id, chapter, n, role, start_s, end_s, first_frame, "
                "last_frame) VALUES (?, ?, ?, 'body', ?, ?, ?, ?)",
                (content_id, n, n, start, end, int(start * 30) + 1, int(end * 30)),
            )
    return content_id


def test_chapters_become_youtube_timestamps(conn: sqlite3.Connection) -> None:
    content_id = item_with(conn, [("Intro", 0.0, 125.4), ("The rotor", 125.4, 250.0),
                                  ("The stator", 250.0, 3700.0)])  # fmt: skip

    lines, problem = metadata.chapters(conn, content_id)

    assert problem is None
    assert lines == ["0:00 Intro", "2:05 The rotor", "4:10 The stator"]


def test_an_item_without_chapters_has_none(conn: sqlite3.Connection) -> None:
    content_id = add_content(conn, load_channel("coil-and-cog"), object_name="alternator",
                             fmt="short", pillar="vehicles")  # fmt: skip

    assert metadata.chapters(conn, content_id) == ([], None)


@pytest.mark.parametrize(
    ("chapters", "problem"),
    [
        ([("Intro", 0, 60), ("Rotor", 60, 120)], "2 chapters (YouTube needs at least 3)"),
        ([("Intro", 0, 60), ("Rotor", 60, 65), ("Stator", 65, 120)], "chapter 2 shorter than 10 s"),
        ([("Intro", 2, 60), ("Rotor", 60, 120), ("Stator", 120, 180)], "starts at 0:02, not 0:00"),
    ],
)
def test_youtubes_chapter_rules_are_checked(
    conn: sqlite3.Connection, chapters: list[tuple[str, float, float]], problem: str
) -> None:
    content_id = item_with(conn, chapters)

    _, found = metadata.chapters(conn, content_id)

    assert found is not None and problem in found


def test_long_subtitles_use_the_16_9_layout(tmp_path) -> None:  # noqa: ANN001
    from factory.config import REPO_ROOT
    from factory.media import qa, subtitles

    font = REPO_ROOT / "assets_static" / "fonts" / "Montserrat-ExtraBold.ttf"
    text = ["The", "rotor", "spins", "inside", "the", "stator", "and", "makes", "a", "current",
            "flow."]  # fmt: skip
    words = [subtitles.Word(w, i * 0.4, i * 0.4 + 0.35) for i, w in enumerate(text)]
    out = subtitles.write_ass(words, load_channel("coil-and-cog"), font, tmp_path / "s.ass", "16:9")

    text = out.read_text()
    assert "PlayResX: 1920" in text and "PlayResY: 1080" in text
    assert ",58," in text.split("Style: Default,", 1)[1]  # the 16:9 font size
    pages = subtitles.build_pages(words, font, subtitles.LAYOUTS["16:9"])
    assert max(len(line) for page in pages for line in page) > 4  # wider lines than Shorts
    assert qa.safe_zones(out, font, [], "16:9")[0] is True
