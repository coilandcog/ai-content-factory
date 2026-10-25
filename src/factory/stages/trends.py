"""Google Trends CSV import (ROADMAP 1.14a, INTEGRATIONS.md §3a, D-079).

The owner downloads "Interest over time" CSVs by hand from trends.google.com (permitted use of
the website; automated access is not, D-048/D-049) into data/inbox/trends/. Each topic column
becomes two trend_signals rows per import: `trends_interest` (average of the last 7 days) and
`trends_ratio` (last 28 days ÷ the 56 days before, per 28 days). Interest is relative: 100 =
the peak inside that chart. Google has changed the layout before, so the header row, the date
column and the topic columns are found by their content, not by line number.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from factory.context import AppContext
from factory.errors import IntegrationError

log = logging.getLogger("factory.trends")

SOURCE = "google_trends_csv"
DATE_HEADERS = {"day", "week", "month", "time", "date"}
UNKNOWN_REGION = "unspecified"
RECENT_DAYS, WINDOW_DAYS, BEFORE_DAYS = 7, 28, 56
TOPIC_HEADER = re.compile(
    r"^(?P<topic>.+?):\s*\((?P<region>[^)]*)\)\s*$"
)  # "jet engine: (Worldwide)"
# The 2026 download name: time_series_<Region>_<from>_<to>.csv
FILE_REGION = re.compile(r"^time_series_(?P<region>.+?)_\d{8}-\d{4}_\d{8}-\d{4}")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class Series:
    """One topic column of a Trends chart."""

    topic: str
    region: str
    points: list[tuple[date, float]]  # oldest first


@dataclass
class ImportResult:
    files: int = 0
    rows_added: int = 0
    rows_skipped: int = 0  # already imported (same topic, region and last date)
    broken: list[str] = field(default_factory=list)  # "file: reason"


def inbox(ctx: AppContext) -> Path:
    return ctx.root / ctx.settings.paths.inbox_dir / "trends"


def import_inbox(ctx: AppContext) -> ImportResult:
    """Import every CSV in the inbox. A broken file stays there and the others still go in."""
    folder = inbox(ctx)
    folder.mkdir(parents=True, exist_ok=True)
    result = ImportResult()
    channel_id = ctx.conn.execute(
        "SELECT id FROM channels WHERE slug = ?", (ctx.channel.slug,)
    ).fetchone()[0]
    for path in sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".csv"):
        try:
            series = parse(path.read_bytes().decode("utf-8-sig"), file_name=path.name)
        except (IntegrationError, UnicodeDecodeError) as exc:
            result.broken.append(f"{path.name}: {exc}")
            log.warning("Trends file %s not imported: %s", path.name, exc,
                        extra={"event": "trends_broken"})  # fmt: skip
            continue
        added, skipped = _store(ctx, channel_id, series, path.name)
        result.files += 1
        result.rows_added += added
        result.rows_skipped += skipped
        _move_to_imported(path)
        log.info("Trends file %s: %d topic(s), %d row(s) added", path.name, len(series), added,
                 extra={"event": "trends_imported"})  # fmt: skip
    return result


# --- parsing -----------------------------------------------------------------------------------


def parse(text: str, *, file_name: str = "") -> list[Series]:
    """The topic series in a Trends CSV. Raises IntegrationError if it isn't one.

    The region comes from the column ("jet engine: (Worldwide)", older exports) or else from
    the file name ("time_series_Worldwide_20260725-0228_20261025-0228.csv", 2026 exports)."""
    named = FILE_REGION.match(file_name)
    default_region = named["region"].replace("_", " ") if named else UNKNOWN_REGION
    rows = [r for r in csv.reader(io.StringIO(text)) if any(cell.strip() for cell in r)]
    header_at = next(
        (i for i, r in enumerate(rows) if len(r) >= 2 and r[0].strip().lower() in DATE_HEADERS),
        None,
    )
    if header_at is None:
        raise IntegrationError("no header row with a Day/Week/Month/Time/Date column")
    header = rows[header_at]
    points: list[list[tuple[date, float]]] = [[] for _ in header[1:]]
    for row in rows[header_at + 1 :]:
        day = _date(row[0])
        if day is None:
            continue  # notes or blank lines after the data
        for column, cell in enumerate(row[1 : len(header)]):
            value = _value(cell)
            if value is not None:
                points[column].append((day, value))
    series = []
    for name, column_points in zip(header[1:], points, strict=True):
        match = TOPIC_HEADER.match(name.strip())
        topic, region = (match["topic"], match["region"]) if match else (name, default_region)
        topic = " ".join(topic.split()).lower()
        if topic and column_points:
            series.append(Series(topic, region.strip() or UNKNOWN_REGION, sorted(column_points)))
    if not series:
        raise IntegrationError("no topic column with numbers")
    return series


def _date(cell: str) -> date | None:
    cell = cell.strip()
    found = ISO_DATE.search(cell)  # "2026-07-27" or a week range "2026-07-27 - 2026-08-02"
    if found:
        return date.fromisoformat(found.group())
    for pattern in ("%b %d, %Y", "%d %b %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(cell, pattern).date()
        except ValueError:
            continue
    return None


def _value(cell: str) -> float | None:
    cell = cell.strip()
    if cell == "<1":
        return 0.5  # Google's mark for "above zero but below 1"
    try:
        return float(cell)
    except ValueError:
        return None


# --- metrics and storage -----------------------------------------------------------------------


def metrics(series: Series) -> dict[str, float]:
    """trends_interest always; trends_ratio only when the chart covers 84 days and the earlier
    56 days were not all zero."""
    last = series.points[-1][0]

    def average(start: int, end: int) -> float | None:
        values = [v for d, v in series.points if last - timedelta(days=end) < d <= last
                  - timedelta(days=start)]  # fmt: skip
        return sum(values) / len(values) if values else None

    result = {"trends_interest": round(average(0, RECENT_DAYS) or 0.0, 2)}
    covered = (last - series.points[0][0]).days + 1
    recent, before = average(0, WINDOW_DAYS), average(WINDOW_DAYS, WINDOW_DAYS + BEFORE_DAYS)
    if covered >= WINDOW_DAYS + BEFORE_DAYS - 7 and recent is not None and before:
        result["trends_ratio"] = round(recent / before, 3)
    return result


def _store(
    ctx: AppContext, channel_id: int, series: list[Series], file_name: str
) -> tuple[int, int]:
    added = skipped = 0
    with ctx.conn:
        for s in series:
            observed = datetime.combine(s.points[-1][0], datetime.min.time(), UTC)
            observed_at = observed.strftime("%Y-%m-%dT%H:%M:%SZ")
            first, last = s.points[0][0].isoformat(), s.points[-1][0].isoformat()
            raw = json.dumps({"file": file_name, "from": first, "to": last,
                              "points": len(s.points)})  # fmt: skip
            for metric, value in metrics(s).items():
                exists = ctx.conn.execute(
                    """
                    SELECT 1 FROM trend_signals WHERE channel_id = ? AND source = ? AND query = ?
                      AND title = ? AND metric_name = ? AND observed_at = ?
                    """,
                    (channel_id, SOURCE, s.topic, s.region, metric, observed_at),
                ).fetchone()
                if exists:
                    skipped += 1
                    continue
                ctx.conn.execute(
                    """
                    INSERT INTO trend_signals (channel_id, source, query, title, metric_name,
                        metric_value, observed_at, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (channel_id, SOURCE, s.topic, s.region, metric, value, observed_at, raw),
                )
                added += 1
    return added, skipped


def _move_to_imported(path: Path) -> None:
    target_dir = path.parent / "imported"
    target_dir.mkdir(exist_ok=True)
    target = target_dir / path.name
    if target.exists():  # the same download name twice (e.g. multiTimeline.csv): keep both
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        target = target_dir / f"{path.stem}-{stamp}{path.suffix}"
    shutil.move(path, target)
