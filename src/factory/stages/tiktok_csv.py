"""TikTok Studio CSV import (ROADMAP 2.7, INTEGRATIONS.md §6, D-120).

TikTok has no API for us (D-116), so the owner downloads Analytics > Content from TikTok Studio
into data/inbox/tiktok/. Each row is one video's totals at the moment of the download (views,
likes, comments, shares; no watch time). A row is stored under the latest checkpoint the video
has reached by then (9 days old -> 7d), and only once per checkpoint: the first download after
a checkpoint is the closest to it, and numbers typed by hand are never replaced.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from factory.context import AppContext
from factory.errors import IntegrationError
from factory.stages.learn import CHECKPOINTS, WINDOWS
from factory.stages.trends import move_to_imported

log = logging.getLogger("factory.tiktok_csv")

# Column -> metrics field. Headers are matched without case or spaces around them.
COLUMNS = {"total views": "views", "total likes": "likes", "total comments": "comments",
           "total shares": "shares"}  # fmt: skip
LINK, TITLE = "video link", "video title"
VIDEO_ID = re.compile(r"/video/(\d+)")
SUFFIX = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}


@dataclass(frozen=True)
class Row:
    title: str
    video_id: str | None
    numbers: dict[str, int]


@dataclass
class ImportResult:
    files: int = 0
    stored: list[str] = field(default_factory=list)  # "#16 7d: 200 views"
    kept: int = 0  # that checkpoint already had numbers
    too_new: list[str] = field(default_factory=list)  # under 24 h old at the download
    unmatched: list[str] = field(default_factory=list)  # no recorded TikTok post
    broken: list[str] = field(default_factory=list)  # "file: reason"


def inbox(ctx: AppContext) -> Path:
    return ctx.root / ctx.settings.paths.inbox_dir / "tiktok"


def import_files(ctx: AppContext, paths: list[Path] | None = None) -> ImportResult:
    """Import the given CSVs, or every CSV in the inbox (moved to imported/ afterwards).

    A broken file is reported by name and the other files still go in."""
    from_inbox = paths is None
    if paths is None:
        folder = inbox(ctx)
        folder.mkdir(parents=True, exist_ok=True)
        paths = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".csv")
    result = ImportResult()
    for path in paths:
        try:
            rows = parse(path.read_bytes().decode("utf-8-sig"))
        except (OSError, IntegrationError, UnicodeDecodeError) as exc:
            result.broken.append(f"{path.name}: {exc}")
            log.warning("TikTok file %s not imported: %s", path.name, exc,
                        extra={"event": "tiktok_csv_broken"})  # fmt: skip
            continue
        # The download moment: the file's own time (copying it keeps it), never in the future.
        taken = min(datetime.fromtimestamp(path.stat().st_mtime, UTC), datetime.now(UTC))
        _store(ctx, rows, taken, result)
        result.files += 1
        if from_inbox:
            move_to_imported(path)
        log.info("TikTok file %s: %d row(s)", path.name, len(rows),
                 extra={"event": "tiktok_csv_imported"})  # fmt: skip
    return result


def parse(text: str) -> list[Row]:
    """The video rows of a TikTok Studio Content CSV. Raises IntegrationError if it isn't one."""
    lines = [r for r in csv.reader(io.StringIO(text)) if any(cell.strip() for cell in r)]
    if not lines:
        raise IntegrationError("the file is empty")
    header = [cell.strip().lower() for cell in lines[0]]
    if LINK not in header or "total views" not in header:
        raise IntegrationError("no 'Video link' and 'Total views' columns (not a Content export?)")
    at = {name: header.index(name) for name in (LINK, TITLE, *COLUMNS) if name in header}
    rows = []
    for line in lines[1:]:
        cells = line + [""] * (len(header) - len(line))
        numbers = {}
        for column, name in COLUMNS.items():
            if column in at and (value := number(cells[at[column]])) is not None:
                numbers[name] = value
        link = VIDEO_ID.search(cells[at[LINK]])
        title = cells[at[TITLE]].strip() if TITLE in at else ""
        rows.append(Row(title or cells[at[LINK]].strip(), link[1] if link else None, numbers))
    return rows


def number(cell: str) -> int | None:
    """ "1,234" -> 1234, "1.2K" -> 1200, "3M" -> 3000000; blank or "--" -> None."""
    cell = cell.strip().replace(",", "").replace(" ", "")
    if not cell or cell in {"-", "--"}:
        return None
    scale = SUFFIX.get(cell[-1].lower(), 1)
    if scale != 1:
        cell = cell[:-1]
    try:
        return round(float(cell) * scale)
    except ValueError:
        raise IntegrationError(f"{cell!r} is not a number") from None


def checkpoint_for(age_s: float) -> str | None:
    """The latest checkpoint a video of this age has reached; None under 24 hours."""
    reached = [c for c in CHECKPOINTS if age_s >= WINDOWS[c].total_seconds()]
    return reached[-1] if reached else None


def _store(ctx: AppContext, rows: list[Row], taken: datetime, result: ImportResult) -> None:
    for row in rows:
        post = ctx.conn.execute(
            """
            SELECT p.id, p.content_id, p.published_at FROM posts p
            JOIN contents c ON c.id = p.content_id JOIN channels ch ON ch.id = c.channel_id
            WHERE p.platform = 'tiktok' AND p.external_id = ? AND ch.slug = ?
            """,
            (row.video_id, ctx.channel.slug),
        ).fetchone()
        if post is None or post["published_at"] is None:
            result.unmatched.append(row.title)
            continue
        published = datetime.fromisoformat(post["published_at"].replace("Z", "+00:00"))
        checkpoint = checkpoint_for((taken - published).total_seconds())
        if checkpoint is None:
            result.too_new.append(f"#{post['content_id']}")
            continue
        if not row.numbers:
            continue
        values = row.numbers
        with ctx.conn:
            cursor = ctx.conn.execute(
                f"""
                INSERT INTO metrics (post_id, checkpoint, source, {", ".join(values)})
                VALUES (?, ?, 'csv', {", ".join("?" for _ in values)})
                ON CONFLICT (post_id, checkpoint) DO NOTHING
                """,
                (post["id"], checkpoint, *values.values()),
            )
        if cursor.rowcount:
            views = values.get("views")
            result.stored.append(f"#{post['content_id']} {checkpoint}: {views} views")
        else:
            result.kept += 1
