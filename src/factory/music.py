"""YouTube Audio Library tracks for the YouTube render (DESIGN_SYSTEM.md §8, D-042, D-068).

The owner downloads tracks in YouTube Studio -> Audio Library and registers them with
`factory music add`; they are only ever used in YouTube renders, never in TikTok ones.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from factory.context import AppContext
from factory.errors import FactoryError
from factory.media.ffmpeg import probe

AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")


@dataclass(frozen=True)
class Track:
    id: int
    title: str
    artist: str | None
    path: Path  # absolute
    attribution_text: str | None
    license_url: str | None


def library_dir(ctx: AppContext) -> Path:
    return ctx.root / ctx.settings.paths.assets_static_dir / "music" / "youtube_audio_library"


def add_track(
    ctx: AppContext,
    source: Path,
    *,
    title: str,
    artist: str | None = None,
    attribution: str | None = None,
    license_url: str | None = None,
    mood: str | None = None,
) -> Track:
    """Copy a downloaded Audio Library file into the library and record it in music_tracks."""
    if not source.is_file():
        raise FactoryError(f"file not found: {source}")
    if source.suffix.lower() not in AUDIO_EXTENSIONS:
        raise FactoryError(f"{source.name}: expected one of {', '.join(AUDIO_EXTENSIONS)}")
    try:
        streams = probe(source)["streams"]
    except FactoryError as exc:
        raise FactoryError(f"{source.name} is not a readable audio file") from exc
    if not any(s.get("codec_type") == "audio" for s in streams):
        raise FactoryError(f"{source.name} has no audio stream")
    folder = library_dir(ctx)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{_slug(title)}{source.suffix.lower()}"
    if target.exists():
        raise FactoryError(f"a track file named {target.name} is already in the library")
    shutil.copy2(source, target)
    with ctx.conn:
        track_id = ctx.conn.execute(
            """
            INSERT INTO music_tracks (title, artist, source, platform_scope, attribution_required,
                                      attribution_text, license_url, mood, path)
            VALUES (?, ?, 'youtube_audio_library', 'youtube', ?, ?, ?, ?, ?)
            """,
            (
                title,
                artist,
                int(bool(attribution)),
                attribution,
                license_url,
                mood,
                target.relative_to(ctx.root).as_posix(),
            ),
        ).lastrowid
    return Track(track_id, title, artist, target, attribution, license_url)


def list_tracks(ctx: AppContext) -> list[tuple[Track, int]]:
    """Every track with how many renders have used it."""
    rows = ctx.conn.execute(
        """
        SELECT t.*, COUNT(a.id) AS uses FROM music_tracks t
        LEFT JOIN assets a ON a.music_track_id = t.id AND a.kind = 'music'
        GROUP BY t.id ORDER BY t.id
        """
    ).fetchall()
    return [(_track(ctx, r), r["uses"]) for r in rows]


def pick_track(ctx: AppContext) -> Track:
    """The least-used track (ties: the oldest), so videos rotate through the library."""
    tracks = list_tracks(ctx)
    if not tracks:
        raise FactoryError(
            "no music in the library: download a track in YouTube Studio -> Audio Library, then "
            '`uv run factory music add <file> --title "..."`'
        )
    return min(tracks, key=lambda pair: (pair[1], pair[0].id))[0]


def _track(ctx: AppContext, row: sqlite3.Row) -> Track:
    return Track(
        row["id"],
        row["title"],
        row["artist"],
        ctx.root / row["path"],
        row["attribution_text"],
        row["license_url"],
    )


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "track"
