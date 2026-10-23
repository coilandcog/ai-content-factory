"""The `factory` command line: one subcommand per pipeline stage (ARCHITECTURE.md §2)."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

from factory.budgets import usage_date, usage_today
from factory.config import (
    CONFIG_DIR,
    ChannelConfig,
    EnvSettings,
    Settings,
    channel_slugs,
    load_channel,
    load_env,
    load_settings,
)
from factory.db import (
    add_content,
    connect,
    finish_run,
    migrate,
    schema_version,
    start_run,
    upsert_channel,
)
from factory.errors import ConfigError, FactoryError
from factory.logging_setup import Redactor, set_log_context, setup_logging
from factory.media.brand import build_brand_assets

log = logging.getLogger("factory.cli")

app = typer.Typer(
    help="Coil & Cog AI Content Factory.",
    no_args_is_help=True,
    add_completion=False,
)
config_app = typer.Typer(help="Validate and inspect configuration.", no_args_is_help=True)
app.add_typer(config_app, name="config")
db_app = typer.Typer(help="Create and update the SQLite database.", no_args_is_help=True)
app.add_typer(db_app, name="db")
brand_app = typer.Typer(help="Build logo, avatar and sting files.", no_args_is_help=True)
app.add_typer(brand_app, name="brand")

ConfigDirOption = Annotated[Path, typer.Option(help="Folder holding settings.yaml and channels/.")]

# Stage name -> (help text, ROADMAP task that builds it). Order = pipeline order.
STAGES: dict[str, tuple[str, str]] = {
    "discover": ("Collect trend signals for the channel's seed topics.", "1.14"),
    "ideate": ("Turn signals into scored video ideas.", "1.15"),
    "script": ("Write scripts for approved ideas (Gemini).", "1.3"),
    "generate": ("Build the Flow prompt sheet, voiceover, subtitles and stills.", "1.6"),
    "assemble": ("Edit clips, voice and subtitles into YouTube and TikTok renders.", "1.9"),
    "review": ("Gate 1 (script) and Gate 2 (final) human approval.", "1.5"),
    "publish": ("Schedule approved videos on YouTube and send TikTok drafts.", "1.12"),
    "learn": ("Collect analytics and label winners and losers.", "2.5"),
}


# --- shared start-up and run tracking ---------------------------------------------------


@dataclass(frozen=True)
class _Run:
    """What a tracked command gets: loaded config, an open database and its run id."""

    settings: Settings
    env: EnvSettings
    conn: sqlite3.Connection
    run_id: int


def _start(config_dir: Path) -> tuple[Settings, EnvSettings, Redactor]:
    """Load settings.yaml and .env, then switch on logging. Exits with code 1 on a config error."""
    try:
        settings = load_settings(config_dir)
        env = load_env(config_dir.parent)
    except ConfigError as exc:
        typer.echo(f"ERROR {exc}", err=True)
        raise typer.Exit(code=1) from exc
    # Paths in settings.yaml are relative to the folder that contains config/ (the repo root).
    redact = setup_logging(
        config_dir.parent / settings.paths.logs_dir,
        settings.log_level,
        settings.schedule.timezone,
        (secret.get_secret_value() for secret in env.secrets),
    )
    if env.dry_run:
        typer.echo("[DRY RUN] no uploads or public posts", err=True)
    return settings, env, redact


def _open_db(db_path: Path) -> sqlite3.Connection:
    """Open an initialised database, or stop with a hint to run `factory db init`."""
    conn = connect(db_path) if db_path.exists() else None
    if conn is None or schema_version(conn) == 0:
        if conn is not None:
            conn.close()
        log.error("no database at %s: run `uv run factory db init` first", db_path)
        raise typer.Exit(code=1)
    return conn


@contextmanager
def _tracked_run(command: str, config_dir: Path) -> Iterator[_Run]:
    """Wrap one command in a `runs` row and start/end log lines (ARCHITECTURE.md §6).

    A FactoryError inside the block is logged, stored in runs.error and turned into exit code 1.
    """
    settings, env, redact = _start(config_dir)
    conn = _open_db(config_dir.parent / settings.paths.db_path)
    try:
        run_id = start_run(conn, command, env.factory_channel, env.dry_run)
        set_log_context(run_id=run_id, stage=command, channel=env.factory_channel)
        log.debug("run %s started", run_id, extra={"event": "run_started"})
        try:
            yield _Run(settings, env, conn, run_id)
        except FactoryError as exc:
            message = redact(str(exc))
            finish_run(conn, run_id, "failed", error=message)
            log.error(message, extra={"event": "run_failed"})
            raise typer.Exit(code=1) from exc
        except typer.Exit as exc:
            finish_run(conn, run_id, "ok" if exc.exit_code == 0 else "failed")
            raise
        except BaseException as exc:  # a bug or Ctrl+C: still close the run row
            finish_run(conn, run_id, "failed", error=redact(repr(exc)))
            log.exception("run %s crashed", run_id, extra={"event": "run_crashed"})
            raise
        finish_run(conn, run_id, "ok")
        log.info("run %s %s: ok", run_id, command, extra={"event": "run_finished"})
    finally:
        conn.close()


# --- pipeline stages (stubs until their ROADMAP task) -----------------------------------


def _make_stub(stage: str, task: str) -> Callable[[Path], None]:
    """Build a placeholder command that says which ROADMAP task will implement it."""

    def stub(config_dir: ConfigDirOption = CONFIG_DIR) -> None:
        with _tracked_run(stage, config_dir):
            # A failed run (exit code 1), so a script calling a stub can't mistake it for work.
            raise FactoryError(f"'{stage}' is not built yet (ROADMAP task {task}).")

    return stub


for _name, (_help, _task) in STAGES.items():
    app.command(name=_name, help=_help)(_make_stub(_name, _task))


# --- status -------------------------------------------------------------------------------


@app.command()
def new(
    object_name: Annotated[str, typer.Option("--object", help="What the video explains.")],
    fmt: Annotated[str, typer.Option("--format", help="short | deep_dive (enabled formats).")],
    pillar: Annotated[str, typer.Option(help="One of the channel's pillars, e.g. machines.")],
    config_dir: ConfigDirOption = CONFIG_DIR,
) -> None:
    """Create a content item by hand, with status 'idea' (ROADMAP 1.1)."""
    with _tracked_run("new", config_dir) as run:
        channel = load_channel(run.env.factory_channel, config_dir)
        content_id = add_content(run.conn, channel, object_name=object_name, fmt=fmt, pillar=pillar)
        log.info(
            "created %s %s (%s)",
            fmt,
            object_name,
            pillar,
            extra={"event": "content_created", "content_id": content_id},
        )
        typer.echo(f"Created content #{content_id}: status idea")


@app.command()
def status(
    content_id: Annotated[int | None, typer.Argument(help="Show one item in detail.")] = None,
    config_dir: ConfigDirOption = CONFIG_DIR,
) -> None:
    """Show the channel summary, or one content item when an id is given."""
    with _tracked_run("status", config_dir) as run:
        channel = load_channel(run.env.factory_channel, config_dir)
        if content_id is None:
            typer.echo(_status_report(run.conn, channel, run.env.dry_run, run.run_id))
        else:
            typer.echo(_content_report(run.conn, content_id))


def _content_report(conn: sqlite3.Connection, content_id: int) -> str:
    row = conn.execute(
        """
        SELECT c.*, ch.slug FROM contents c JOIN channels ch ON ch.id = c.channel_id
        WHERE c.id = ?
        """,
        (content_id,),
    ).fetchone()
    if row is None:
        raise FactoryError(f"content #{content_id} does not exist")
    lines = [
        f"Content #{row['id']} ({row['slug']})",
        f"  object: {row['object_name']} | format: {row['format']} | pillar: {row['pillar']}",
        f"  status: {row['status']}",
    ]
    if row["status_before_failure"]:
        lines.append(f"  failed while: {row['status_before_failure']}")
    if row["status_reason"]:
        lines.append(f"  reason: {row['status_reason']}")
    lines.append(f"  created: {row['created_at']} | updated: {row['updated_at']}")
    return "\n".join(lines)


def _status_report(
    conn: sqlite3.Connection, channel: ChannelConfig, dry_run: bool, current_run_id: int
) -> str:
    counts = conn.execute(
        """
        SELECT c.status, COUNT(*) AS n FROM contents c JOIN channels ch ON ch.id = c.channel_id
        WHERE ch.slug = ? GROUP BY c.status ORDER BY n DESC
        """,
        (channel.slug,),
    ).fetchall()
    contents = ", ".join(f"{row['status']} {row['n']}" for row in counts) or "none yet"
    budgets = ", ".join(
        f"{service} {used}/{limit}" if limit is not None else f"{service} {used}"
        for service, (used, limit) in usage_today(conn, channel).items()
    )
    runs = conn.execute(
        "SELECT id, command, status, started_at FROM runs WHERE id <> ? ORDER BY id DESC LIMIT 5",
        (current_run_id,),
    ).fetchall()
    run_lines = [f"  #{r['id']} {r['command']}: {r['status']} ({r['started_at']})" for r in runs]
    return "\n".join(
        [
            f"{channel.slug} ({channel.display_name}) | mode: {'DRY RUN' if dry_run else 'LIVE'}",
            f"Contents: {contents}",
            f"Budgets today ({usage_date(channel)}, {channel.timezone}): {budgets}",
            "Last runs:",
            *(run_lines or ["  none"]),
        ]
    )


# --- config ---------------------------------------------------------------------------------


@config_app.command("check")
def config_check(config_dir: ConfigDirOption = CONFIG_DIR) -> None:
    """Validate settings.yaml, .env switches and every channel profile, then print a summary.

    Writes a log line but no runs row: it must work even when the config (and so the database
    location) is broken.
    """
    # Check every file, not just the first broken one, so one run shows all problems.
    problems = 0
    settings: Settings | None = None
    try:
        settings = load_settings(config_dir)
        typer.echo(_settings_summary(settings))
    except ConfigError as exc:
        typer.echo(f"ERROR {exc}", err=True)
        problems += 1
    try:
        env = load_env(config_dir.parent)
        typer.echo(
            f"OK  env: DRY_RUN={str(env.dry_run).lower()}, FACTORY_CHANNEL={env.factory_channel}"
        )
    except ConfigError as exc:
        typer.echo(f"ERROR {exc}", err=True)
        problems += 1
    slugs = channel_slugs(config_dir)
    if not slugs:
        typer.echo(f"ERROR no channel profiles found in {config_dir / 'channels'}", err=True)
        problems += 1
    for slug in slugs:
        try:
            typer.echo(_channel_summary(load_channel(slug, config_dir)))
        except ConfigError as exc:
            typer.echo(f"ERROR {exc}", err=True)
            problems += 1
    if settings is not None:
        setup_logging(
            config_dir.parent / settings.paths.logs_dir,
            settings.log_level,
            settings.schedule.timezone,
            (),
        )
        set_log_context(stage="config check")
        log.info("config check: %s problem(s)", problems, extra={"event": "config_checked"})
    if problems:
        raise typer.Exit(code=1)


def _settings_summary(settings: Settings) -> str:
    return f"OK  settings: data_dir={settings.paths.data_dir}, log_level={settings.log_level}"


def _channel_summary(ch: ChannelConfig) -> str:
    formats = {name: getattr(ch.formats, name) for name in ("short", "deep_dive", "episode")}
    enabled = ", ".join(f"{n} {f.min_s}-{f.max_s}s" for n, f in formats.items() if f.enabled)
    disabled = ", ".join(n for n, f in formats.items() if not f.enabled)
    b = ch.budgets_per_day
    return "\n".join(
        [
            f"OK  {ch.slug} ({ch.display_name}): {ch.status}, {ch.language}, {ch.timezone}",
            f"    formats: {enabled}" + (f" ({disabled} disabled)" if disabled else ""),
            f"    llm: {ch.llm.provider}/{ch.llm.model} "
            f"(fallback {ch.llm.fallback_provider}/{ch.llm.fallback_model})",
            f"    budgets/day: gemini {b.gemini_calls}, youtube {b.youtube_units} units, "
            f"flow {b.flow_credits} credits",
        ]
    )


# --- brand ----------------------------------------------------------------------------------


@brand_app.command("build")
def brand_build(config_dir: ConfigDirOption = CONFIG_DIR) -> None:
    """Rebuild logo.png, logo_on_bg.png, avatar_800.png and both stings from the channel YAML."""
    with _tracked_run("brand build", config_dir) as run:
        channel = load_channel(run.env.factory_channel, config_dir)
        static_dir = config_dir.parent / run.settings.paths.assets_static_dir
        written = build_brand_assets(channel, static_dir / "fonts", static_dir / "logo")
        for path in written:
            typer.echo(f"wrote {path.relative_to(config_dir.parent)}")


# --- database -------------------------------------------------------------------------------


@db_app.command("init")
def db_init(config_dir: ConfigDirOption = CONFIG_DIR) -> None:
    """Create the database if needed, apply pending migrations and save channel rows from YAML."""
    settings, env, _ = _start(config_dir)
    try:
        channels = [load_channel(slug, config_dir) for slug in channel_slugs(config_dir)]
    except ConfigError as exc:
        log.error(str(exc))
        raise typer.Exit(code=1) from exc
    db_path = config_dir.parent / settings.paths.db_path
    conn = connect(db_path)
    try:
        applied = migrate(conn)
        for channel in channels:
            upsert_channel(conn, channel)
        # The run row comes last: before the first migration there is no runs table.
        run_id = start_run(conn, "db init", env.factory_channel, env.dry_run)
        finish_run(conn, run_id, "ok", items_processed=len(applied))
    finally:
        conn.close()
    set_log_context(run_id=run_id, stage="db init", channel=env.factory_channel)
    log.info("db init: %d migration(s) applied", len(applied), extra={"event": "db_initialised"})
    typer.echo(f"Database: {db_path}")
    typer.echo(f"Migrations applied: {', '.join(applied)}" if applied else "Migrations: up to date")
    typer.echo(f"Channels saved: {', '.join(ch.slug for ch in channels) or 'none'}")
