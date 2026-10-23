"""What every stage receives (AppContext) and reports back (StageResult) (CONVENTIONS.md §1)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from factory.config import ChannelConfig, EnvSettings, Settings


@dataclass(frozen=True)
class AppContext:
    """Loaded config plus an open database, passed explicitly instead of global state."""

    settings: Settings
    env: EnvSettings
    channel: ChannelConfig
    conn: sqlite3.Connection
    root: Path  # the folder that contains config/; settings paths are relative to it
    run_id: int | None = None


@dataclass
class StageResult:
    """Counts a stage reports; they end up in runs.items_processed / items_failed."""

    processed: int = 0
    failed: int = 0
    skipped: int = 0
