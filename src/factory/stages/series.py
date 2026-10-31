"""Topic series (ROADMAP 2.24, D-112): one topic as several linked videos.

The parts come from the machine's blueprint drawing (its PARTS), not from Gemini, so the film of
a part always shows what its script explains and the order is right by construction. Each part is
a normal content item (blueprint style) with `series_id` and `part_n`; scripts, titles and
descriptions use the series context below.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from factory import blueprint
from factory.context import AppContext
from factory.db import add_content
from factory.errors import FactoryError

log = logging.getLogger("factory.series")
MIN_PARTS, MAX_PARTS = 2, 5


@dataclass(frozen=True)
class PartInfo:
    n: int
    title: str
    focus: str


@dataclass(frozen=True)
class SeriesContext:
    """What one part's script and metadata need to know about its series."""

    series_id: int
    topic: str
    part_n: int
    parts: int
    outline: list[PartInfo]
    earlier: list[str]  # narration of the earlier parts' current scripts, in order
    sibling_ids: list[int]  # the other parts and the teaser: never similarity-blocked
    facts: list[str]  # the drawing's verified facts, the only numbers the script may use

    @property
    def this(self) -> PartInfo:
        return self.outline[self.part_n - 1]

    @property
    def next(self) -> PartInfo | None:
        return self.outline[self.part_n] if self.part_n < self.parts else None

    @property
    def title(self) -> str:
        """e.g. "Jet Engine, Part 2/3: Compressor and Combustion"."""
        return f"{self.topic.title()}, Part {self.part_n}/{self.parts}: {self.this.title}"


def create(ctx: AppContext, object_name: str, *, parts: int, fmt: str, pillar: str,
           teaser: int | None = None) -> tuple[int, list[int]]:  # fmt: skip
    """Create a series and its parts as `idea` items, in part order. Returns (series id, ids)."""
    name = " ".join(object_name.split()).lower()
    module = blueprint.module_for(name)
    drawn = getattr(module, "PARTS", [])
    if not MIN_PARTS <= parts <= MAX_PARTS:
        raise FactoryError(f"--parts must be {MIN_PARTS}-{MAX_PARTS}")
    if len(drawn) != parts:
        have = f"is drawn in {len(drawn)} parts" if drawn else "has no parts drawn yet"
        raise FactoryError(f"the {name} {have}; ask Claude to draw it in {parts} parts")
    if teaser is not None:
        row = ctx.conn.execute(
            "SELECT object_name FROM contents WHERE id = ?", (teaser,)
        ).fetchone()
        if row is None:
            raise FactoryError(f"teaser #{teaser} does not exist")
    channel_id = ctx.conn.execute(
        "SELECT id FROM channels WHERE slug = ?", (ctx.channel.slug,)
    ).fetchone()[0]
    with ctx.conn:
        series_id = ctx.conn.execute(
            "INSERT INTO series (channel_id, topic, parts, teaser_content_id) VALUES (?, ?, ?, ?)",
            (channel_id, name, parts, teaser),
        ).lastrowid
    ids = []
    for n, part in enumerate(drawn, 1):
        content_id = add_content(ctx.conn, ctx.channel, object_name=name, fmt=fmt, pillar=pillar,
                                 style="blueprint")  # fmt: skip
        with ctx.conn:
            ctx.conn.execute(
                "UPDATE contents SET series_id = ?, part_n = ?, angle = ? WHERE id = ?",
                (series_id, n, f"Part {n}/{parts}: {part.focus}", content_id),
            )
        ids.append(content_id)
    log.info("series #%s: %s in %d parts (#%s)", series_id, name, parts,
             ", #".join(map(str, ids)), extra={"event": "series_created"})  # fmt: skip
    return series_id, ids


def context(conn: sqlite3.Connection, content_id: int) -> SeriesContext | None:
    """The series context of a part, or None for a normal video."""
    row = conn.execute(
        """
        SELECT c.series_id, c.part_n, s.topic, s.parts, s.teaser_content_id
        FROM contents c JOIN series s ON s.id = c.series_id WHERE c.id = ?
        """,
        (content_id,),
    ).fetchone()
    if row is None:
        return None
    module = blueprint.module_for(row["topic"])
    outline = [PartInfo(n, p.title, p.focus) for n, p in enumerate(module.PARTS, 1)]
    others = conn.execute(
        """
        SELECT c.id, c.part_n, s.body_text FROM contents c
        LEFT JOIN scripts s ON s.content_id = c.id AND s.is_current = 1
        WHERE c.series_id = ? AND c.id <> ? ORDER BY c.part_n
        """,
        (row["series_id"], content_id),
    ).fetchall()
    earlier = [r["body_text"] for r in others if r["part_n"] < row["part_n"] and r["body_text"]]
    siblings = [r["id"] for r in others]
    if row["teaser_content_id"]:
        siblings.append(row["teaser_content_id"])
    return SeriesContext(
        series_id=row["series_id"], topic=row["topic"], part_n=row["part_n"], parts=row["parts"],
        outline=outline, earlier=earlier, sibling_ids=siblings,
        facts=[f.text for f in module.FACTS.values()],
    )  # fmt: skip


def links(conn: sqlite3.Connection, content_id: int) -> list[str]:
    """Description lines linking the teaser and the earlier parts that are already on YouTube."""
    ctx = context(conn, content_id)
    if ctx is None:
        return []
    teaser = conn.execute(
        "SELECT teaser_content_id FROM series WHERE id = ?", (ctx.series_id,)
    ).fetchone()[0]
    targets = [("Overview", teaser)] if teaser else []
    earlier = conn.execute(
        "SELECT id, part_n FROM contents WHERE series_id = ? AND part_n < ? ORDER BY part_n",
        (ctx.series_id, ctx.part_n),
    )
    targets += [(f"Part {r['part_n']}", r["id"]) for r in earlier]
    lines = []
    for label, cid in targets:
        post = conn.execute(
            "SELECT url FROM posts WHERE content_id = ? AND platform = 'youtube' AND dry_run = 0",
            (cid,),
        ).fetchone()
        if post and post["url"]:
            lines.append(f"{label}: {post['url']}")
    return lines


def show(conn: sqlite3.Connection, series_id: int) -> str:
    row = conn.execute("SELECT * FROM series WHERE id = ?", (series_id,)).fetchone()
    if row is None:
        raise FactoryError(f"series #{series_id} does not exist")
    lines = [f"Series #{series_id}: {row['topic']} in {row['parts']} parts"]
    if row["teaser_content_id"]:
        lines.append(f"  teaser: #{row['teaser_content_id']}")
    for r in conn.execute(
        "SELECT id, part_n, status, angle FROM contents WHERE series_id = ? ORDER BY part_n",
        (series_id,),
    ):
        lines.append(f"  part {r['part_n']}: #{r['id']} {r['status']:<16} {r['angle']}")
    return "\n".join(lines)
