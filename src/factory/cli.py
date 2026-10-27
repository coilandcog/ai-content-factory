"""The `factory` command line: one subcommand per pipeline stage (ARCHITECTURE.md §2)."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

import click
import typer

from factory import music
from factory.budgets import check_and_record, usage_date, usage_today
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
from factory.context import AppContext, StageResult
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
from factory.integrations.kokoro_tts import KokoroTTS
from factory.integrations.telegram_bot import TelegramClient
from factory.logging_setup import Redactor, set_log_context, setup_logging
from factory.media import ffmpeg, subtitles
from factory.media.brand import build_brand_assets
from factory.stages import assemble as assemble_stage
from factory.stages import bot as bot_stage
from factory.stages import discover as discover_stage
from factory.stages import generate as generate_stage
from factory.stages import ideate as ideate_stage
from factory.stages import metadata as metadata_stage
from factory.stages import publish as publish_stage
from factory.stages import review as review_stage
from factory.stages import script as script_stage
from factory.stages import trends as trends_stage
from factory.stages import voice as voice_stage

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
voice_app = typer.Typer(help="Voice audition and voiceover (Kokoro).", no_args_is_help=True)
app.add_typer(voice_app, name="voice")
music_app = typer.Typer(
    help="YouTube Audio Library tracks for YouTube renders.", no_args_is_help=True
)
app.add_typer(music_app, name="music")
trends_app = typer.Typer(help="Google Trends CSV files you downloaded.", no_args_is_help=True)
app.add_typer(trends_app, name="trends")

ConfigDirOption = Annotated[Path, typer.Option(help="Folder holding settings.yaml and channels/.")]

# Stage name -> (help text, ROADMAP task that builds it). Order = pipeline order.
STAGES: dict[str, tuple[str, str]] = {
    "discover": ("Collect trend signals for the channel's seed topics.", "1.14"),
    "ideate": ("Turn signals into scored video ideas.", "1.15"),
    "script": ("Write scripts for approved ideas (Gemini).", "1.3"),
    "generate": ("Build the Flow prompt sheet, voiceover, subtitles and stills.", "1.6"),
    "assemble": ("Edit clips, voice and subtitles into YouTube and TikTok renders.", "1.9"),
    "review": ("Gate 1 (script) and Gate 2 (final) human approval.", "1.5"),
    "publish": ("Publish approved videos (--manual: record your own uploads).", "1.12"),
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
    # Stages add their counts here; they end up in runs.items_processed / items_failed.
    tally: StageResult = field(default_factory=StageResult)


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
    ffmpeg.use_tools(settings.tools.ffmpeg, settings.tools.ffprobe)
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
        run = _Run(settings, env, conn, run_id)
        try:
            yield run
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
        tally = run.tally
        status = "ok" if not tally.failed else ("partial" if tally.processed else "failed")
        finish_run(conn, run_id, status, items_processed=tally.processed, items_failed=tally.failed)
        log.info("run %s %s: %s", run_id, command, status, extra={"event": "run_finished"})
    finally:
        conn.close()
    if status == "failed":
        raise typer.Exit(code=1)  # every item failed: let calling scripts notice


# --- pipeline stages (stubs until their ROADMAP task) -----------------------------------


def _make_stub(stage: str, task: str) -> Callable[[Path], None]:
    """Build a placeholder command that says which ROADMAP task will implement it."""

    def stub(config_dir: ConfigDirOption = CONFIG_DIR) -> None:
        with _tracked_run(stage, config_dir):
            # A failed run (exit code 1), so a script calling a stub can't mistake it for work.
            raise FactoryError(f"'{stage}' is not built yet (ROADMAP task {task}).")

    return stub


def discover(config_dir: ConfigDirOption = CONFIG_DIR) -> None:
    """Collect demand signals for the seed topics: Wikipedia daily, YouTube weekly (1.14)."""
    with _tracked_run("discover", config_dir) as run:
        ctx = _context(run, config_dir)
        result = discover_stage.run(ctx)
        run.tally.processed, run.tally.failed = result.processed, result.failed
        typer.echo(
            f"Topics with new signals: {result.processed}, failed: {result.failed} "
            f"(YouTube searches: {result.youtube_searched})"
        )
        if result.youtube_note:
            typer.echo(result.youtube_note)
        typer.echo("Top topics: uv run factory status --signals")


def ideate(
    count: Annotated[int, typer.Option(help="How many ideas to ask Gemini for.")] = 10,
    config_dir: ConfigDirOption = CONFIG_DIR,
) -> None:
    """Scored video ideas from the signals: ideas scoring 65+ become `idea` items (1.15)."""
    with _tracked_run("ideate", config_dir) as run:
        ctx = _context(run, config_dir)
        result = ideate_stage.run(ctx, count=count)
        run.tally.processed = len(result.saved)
        threshold = ctx.channel.scoring.min_virality_score
        typer.echo(
            f"Ideas saved: {len(result.saved)}, below {threshold}: {result.below_threshold}, "
            f"skipped: {len(result.skipped)}"
        )
        for content_id in result.saved:
            row = run.conn.execute("SELECT * FROM contents WHERE id = ?", (content_id,)).fetchone()
            typer.echo(f"  #{content_id} {row['virality_score']:>3}  {row['format']:<9} "
                       f"{row['object_name']}: {row['angle']}")  # fmt: skip
        for line in result.skipped:
            typer.echo(f"  skipped {line}")
        typer.echo("Next: uv run factory plan --week next")


@app.command()
def plan(
    week: Annotated[str, typer.Option(help="'next' or 'this' week.")] = "next",
    config_dir: ConfigDirOption = CONFIG_DIR,
) -> None:
    """Fill the week's slots with the best ideas, with variety rules (ROADMAP 1.15)."""
    if week not in ("next", "this"):
        raise typer.BadParameter("use --week next or --week this")
    with _tracked_run("plan", config_dir) as run:
        ctx = _context(run, config_dir)
        monday = ideate_stage.week_start(ctx, week)
        slots = ideate_stage.plan_week(ctx, monday)
        run.tally.processed = sum(1 for s in slots if s.note == "planned now")
        typer.echo(f"Week of {monday.isoformat()} ({ctx.channel.cadence.publish_time} "
                   f"{ctx.channel.timezone})")  # fmt: skip
        typer.echo(f"  {'day':<10} {'format':<9} {'#':>4} {'score':>5}  {'machine':<24} "
                   f"{'pillar':<20} {'hook':<17} beats")  # fmt: skip
        for s in slots:
            day = s.day.strftime("%a %d %b")
            if s.content_id is None:
                typer.echo(f"  {day:<10} {s.fmt:<9}  ---  {s.note}")
                continue
            typer.echo(f"  {day:<10} {s.fmt:<9} {s.content_id:>4} {s.score or '-':>5}  "
                       f"{(s.object_name or '')[:24]:<24} {s.pillar or '':<20} "
                       f"{s.hook_type or '-':<17} {s.beat_order or '-'}")  # fmt: skip
        typer.echo("Next: uv run factory script  (writes the planned items' scripts)")


def script(
    limit: Annotated[int | None, typer.Option(help="Script at most this many ideas.")] = None,
    config_dir: ConfigDirOption = CONFIG_DIR,
) -> None:
    """Write scripts for eligible ideas with Gemini: idea -> scripted (ROADMAP 1.3)."""
    with _tracked_run("script", config_dir) as run:
        ctx = _context(run, config_dir)
        result = script_stage.run(ctx, limit=limit)
        run.tally.processed, run.tally.failed = result.processed, result.failed
        typer.echo(f"Scripted or archived: {result.processed}, failed: {result.failed}")


NOTE_HELP = (
    "# Write the Engineer's Note above: one sentence of your own insight or experience.\n"
    "# The AI draft is pre-filled; edit or replace it. Lines starting with # are ignored.\n"
)


def review(
    gate: Annotated[str, typer.Option(help="'script' = Gate 1, 'final' = Gate 2.")],
    content_id: Annotated[int | None, typer.Option("--id", help="Review only this item.")] = None,
    decision: Annotated[
        str | None, typer.Option(help="approve | reject | redo, without the interactive menu.")
    ] = None,
    reason: Annotated[str | None, typer.Option(help="Required for reject and redo.")] = None,
    note: Annotated[str | None, typer.Option(help="Your Engineer's Note (approve).")] = None,
    keep_note: Annotated[
        bool, typer.Option(help="Approve with the AI draft note as it is.")
    ] = False,
    checklist_ok: Annotated[
        bool,
        typer.Option(help="Gate 2 approve: I watched the preview; every checklist item holds."),
    ] = False,
    config_dir: ConfigDirOption = CONFIG_DIR,
) -> None:
    """Gate 1 (script) or Gate 2 (final video): approve, reject or redo each item (1.5, 1.11)."""
    with _tracked_run("review", config_dir) as run:
        if gate not in ("script", "final"):
            raise FactoryError(f"--gate must be 'script' or 'final', not '{gate}'")
        ctx = _context(run, config_dir)
        if gate == "final":
            run.tally.processed = _review_final(ctx, content_id, decision, reason, checklist_ok)
            return
        if decision is not None:
            if content_id is None:
                raise FactoryError("--decision needs --id")
            _decide(ctx, content_id, decision, reason, note, keep_note)
            run.tally.processed = 1
            return
        items = [content_id] if content_id else [r["id"] for r in review_stage.pending(ctx)]
        if not items:
            typer.echo("Nothing waiting for Gate 1.")
        for item_id in items:
            outcome = _review_interactively(ctx, item_id)
            if outcome == "quit":
                break
            if outcome != "skip":
                run.tally.processed += 1


def _decide(
    ctx: AppContext,
    content_id: int,
    decision: str,
    reason: str | None,
    note: str | None,
    keep_note: bool,
) -> None:
    """Non-interactive Gate 1 decision (for scripts, tests and later the Telegram bot)."""
    if decision == "approve":
        if note is None and not keep_note:
            raise FactoryError('approve needs --note "..." or --keep-note')
        text = (
            note
            if note is not None
            else review_stage.current_script(ctx.conn, content_id)["engineer_note"]
        )
        _echo_approval(content_id, review_stage.approve(ctx, content_id, text))
    elif decision == "reject":
        review_stage.reject(ctx, content_id, reason or "")
        typer.echo(f"#{content_id} rejected.")
    elif decision == "redo":
        script_id = review_stage.redo(ctx, content_id, reason or "")
        typer.echo(f"#{content_id}: new script version written (script {script_id}).")
    else:
        raise FactoryError(f"--decision must be approve, reject or redo, not '{decision}'")


# --- Gate 2 ---------------------------------------------------------------------------------


def _review_final(
    ctx: AppContext,
    content_id: int | None,
    decision: str | None,
    reason: str | None,
    checklist_ok: bool,
) -> int:
    """Gate 2 with flags (one item) or the interactive menu; returns how many were decided."""
    if decision is not None:
        if content_id is None:
            raise FactoryError("--decision needs --id")
        metadata_stage.ensure_metadata(ctx, content_id)
        if decision == "approve":
            review_stage.approve_final(ctx, content_id, checklist_ok=checklist_ok)
            typer.echo(f"#{content_id} approved: ready to publish.")
        elif decision == "reject":
            review_stage.reject_final(ctx, content_id, reason or "")
            typer.echo(f"#{content_id} rejected.")
        elif decision == "redo":
            review_stage.redo_final(ctx, content_id, reason or "")
            typer.echo(f"#{content_id} back to generated; fix it, then `factory assemble`.")
        else:
            raise FactoryError(f"--decision must be approve, reject or redo, not '{decision}'")
        return 1
    items = [content_id] if content_id else [r["id"] for r in review_stage.pending_final(ctx)]
    if not items:
        typer.echo("Nothing waiting for Gate 2.")
    decided = 0
    for item_id in items:
        outcome = _review_final_interactively(ctx, item_id)
        if outcome == "quit":
            break
        decided += outcome != "skip"
    return decided


def _review_final_interactively(ctx: AppContext, content_id: int) -> str:
    metadata_stage.ensure_metadata(ctx, content_id)
    while True:
        typer.echo(_final_report(ctx, content_id))
        choice = typer.prompt(
            "\n[a]pprove  [e]dit metadata  [r]eject  re[d]o  [s]kip  [q]uit",
            type=click.Choice(["a", "e", "r", "d", "s", "q"]),
            show_choices=False,
        )
        if choice in ("s", "q"):
            return {"s": "skip", "q": "quit"}[choice]
        if choice == "e":
            _edit_metadata(ctx, content_id)
            continue
        if choice == "r":
            review_stage.reject_final(ctx, content_id, typer.prompt("Reason for rejecting"))
            typer.echo(f"#{content_id} rejected.")
            return "reject"
        if choice == "d":
            review_stage.redo_final(ctx, content_id, typer.prompt("What must be fixed"))
            typer.echo(f"#{content_id} back to generated; fix it, then `factory assemble`.")
            return "redo"
        if not typer.confirm("Did you watch the preview, and does every checklist item hold?"):
            continue
        review_stage.approve_final(ctx, content_id, checklist_ok=True)
        typer.echo(f"#{content_id} approved: ready to publish.")
        return "approve"


def _final_report(ctx: AppContext, content_id: int) -> str:
    folder = voice_stage.asset_dir(ctx, content_id)
    lines = [
        review_stage.final_report(ctx, content_id),
        "",
        "WATCH BEFORE DECIDING (outside VS Code, it can't play the sound):",
        f'  explorer.exe "$(wslpath -w {folder.relative_to(ctx.root)})"',
        "  preview_720p.mp4 (both renders share the picture), cover.jpg",
        "",
        "CHECKLIST:",
        *(f"  [ ] {item}" for item in review_stage.FINAL_CHECKLIST),
    ]
    return "\n".join(lines)


def _edit_metadata(ctx: AppContext, content_id: int) -> None:
    """Open metadata.json in $EDITOR; keep the edit only if it is valid JSON within the rules."""
    text = json.dumps(metadata_stage.load(ctx, content_id), indent=2, ensure_ascii=False)
    edited = click.edit(text, extension=".json")
    if edited is None:
        typer.echo("No changes.")
        return
    try:
        data = json.loads(edited)
    except json.JSONDecodeError as exc:
        typer.echo(f"Not saved: the file is not valid JSON ({exc}).", err=True)
        return
    problems = metadata_stage.validate(data)
    if problems:
        typer.echo("Not saved: " + "; ".join(problems), err=True)
        return
    metadata_stage.write(ctx, content_id, data)
    typer.echo("metadata.json saved.")


def _review_interactively(ctx: AppContext, content_id: int) -> str:
    """Show one item and loop until it is decided. Returns approve/reject/skip/quit."""
    while True:
        typer.echo(review_stage.content_report(ctx.conn, content_id))
        typer.echo(review_stage.script_report(ctx.conn, content_id))
        choice = typer.prompt(
            "\n[a]pprove  [r]eject  re[d]o  [s]kip  [q]uit",
            type=click.Choice(["a", "r", "d", "s", "q"]),
            show_choices=False,
        )
        if choice == "s":
            return "skip"
        if choice == "q":
            return "quit"
        if choice == "r":
            review_stage.reject(ctx, content_id, typer.prompt("Reason for rejecting"))
            typer.echo(f"#{content_id} rejected.")
            return "reject"
        if choice == "d":
            feedback = typer.prompt("What should change in the next version")
            typer.echo("Writing the next version with Gemini ...")
            try:
                review_stage.redo(ctx, content_id, feedback)
            except FactoryError as exc:
                typer.echo(f"ERROR {exc}", err=True)
            continue  # show the (new) current version again
        draft = review_stage.current_script(ctx.conn, content_id)["engineer_note"] or ""
        note = _edit_note(draft)
        if not note:
            typer.echo("No note written; not approved.")
            continue
        if note == draft and not typer.confirm("Keep the AI draft as your Engineer's Note?"):
            continue
        _echo_approval(content_id, review_stage.approve(ctx, content_id, note))
        return "approve"


def _edit_note(draft: str) -> str:
    """Open the note in $EDITOR (falls back to nano/vim) and return it without # lines."""
    edited = click.edit(f"{draft}\n\n{NOTE_HELP}", extension=".txt")
    if edited is None:  # editor closed without saving
        return draft
    lines = [line for line in edited.splitlines() if not line.lstrip().startswith("#")]
    return " ".join(" ".join(lines).split())


def _echo_approval(content_id: int, approval: review_stage.Approval) -> None:
    typer.echo(
        f"#{content_id} approved: {approval.word_count} words, ~{approval.est_duration_s:.0f} s"
    )
    if approval.warning:
        typer.echo(f"WARNING {approval.warning}")


def generate(
    limit: Annotated[
        int | None, typer.Option(help="Handle at most this many items per pass.")
    ] = None,
    restage: Annotated[
        int | None,
        typer.Option(
            help="New film shots (Gemini) for this approved item's clips; the narration stays."
        ),
    ] = None,
    config_dir: ConfigDirOption = CONFIG_DIR,
) -> None:
    """Write Flow sheets + voice for approved scripts, then collect finished clips (ROADMAP 1.6)."""
    with _tracked_run("generate", config_dir) as run:
        ctx = _context(run, config_dir)
        if restage is not None:
            sheet = generate_stage.restage(ctx, restage)
            run.tally.processed = 1
            typer.echo(
                f"#{restage}: new film shots. "
                + (
                    f"Flow sheet: {generate_stage.windows_path(sheet)}"
                    if sheet
                    else "Run `factory generate` to write its Flow sheet."
                )
            )
            return
        result = generate_stage.run(ctx, limit=limit)
        run.tally.processed, run.tally.failed = result.processed, result.failed
        typer.echo(
            f"Moved forward: {result.processed}, waiting for clips: {result.skipped}, "
            f"failed: {result.failed}"
        )


def assemble(
    limit: Annotated[int | None, typer.Option(help="Render at most this many items.")] = None,
    force: Annotated[bool, typer.Option(help="Render again even if renders exist.")] = False,
    config_dir: ConfigDirOption = CONFIG_DIR,
) -> None:
    """Render generated items (YouTube, TikTok, 720p preview, cover), then run the automated QA:
    a pass moves the item to `assembled`, a failure keeps it `generated` with the reason."""
    with _tracked_run("assemble", config_dir) as run:
        ctx = _context(run, config_dir)
        result = assemble_stage.run(ctx, limit=limit, force=force)
        run.tally.processed, run.tally.failed = result.processed, result.failed
        typer.echo(
            f"QA passed -> assembled: {result.processed}, QA failed: {result.skipped} "
            f"(reasons: `factory status <id>`), errors: {result.failed}"
        )


def publish(
    manual: Annotated[
        bool, typer.Option(help="You upload by hand; the factory prints the kit and records it.")
    ] = False,
    content_id: Annotated[int | None, typer.Option("--id", help="The approved item.")] = None,
    platform: Annotated[
        str | None, typer.Option(help="youtube | tiktok: record the post you made there.")
    ] = None,
    url: Annotated[str | None, typer.Option(help="Link to the live video.")] = None,
    ai_label_on: Annotated[
        bool, typer.Option(help="I switched on the AI label on that platform.")
    ] = False,
    config_dir: ConfigDirOption = CONFIG_DIR,
) -> None:
    """Without --platform: print the upload kit. With --platform and --url: record the live post
    (the first one moves the item to `published`). ROADMAP 1.12, D-072."""
    with _tracked_run("publish", config_dir) as run:
        if not manual:
            raise FactoryError("only `--manual` is built yet (API publishing: ROADMAP task 2.3)")
        if content_id is None:
            raise FactoryError("--manual needs --id (approved items: `factory status`)")
        ctx = _context(run, config_dir)
        if platform is None:
            typer.echo(publish_stage.upload_kit(ctx, content_id))
            return
        if url is None:
            raise FactoryError("--platform needs --url (the link to the live video)")
        post = publish_stage.record_manual_post(
            ctx, content_id, platform, url, ai_label_on=ai_label_on
        )
        run.tally.processed = int(post.created)
        if post.created:
            typer.echo(f"#{content_id} recorded on {platform}: {post.url} ({post.content_status})")
        else:
            typer.echo(f"#{content_id} on {platform} was already recorded: {post.url}")


# Stages that are built; the rest stay stubs. Registered in pipeline order for --help.
IMPLEMENTED: dict[str, Callable[..., None]] = {
    "discover": discover,
    "ideate": ideate,
    "script": script,
    "generate": generate,
    "assemble": assemble,
    "review": review,
    "publish": publish,
}

for _name, (_help, _task) in STAGES.items():
    app.command(name=_name, help=_help)(IMPLEMENTED.get(_name) or _make_stub(_name, _task))


def _context(run: _Run, config_dir: Path) -> AppContext:
    """The AppContext a stage needs, for the channel named by FACTORY_CHANNEL."""
    channel = load_channel(run.env.factory_channel, config_dir)
    return AppContext(run.settings, run.env, channel, run.conn, config_dir.parent, run.run_id)


# --- Telegram bot ---------------------------------------------------------------------------


@app.command()
def bot(
    find_chat_id: Annotated[
        bool,
        typer.Option(
            "--find-chat-id", help="Print the chat id of whoever messaged the bot, then stop."
        ),
    ] = False,
    config_dir: ConfigDirOption = CONFIG_DIR,
) -> None:
    """Telegram review bot: Gate 1 and Gate 2 with buttons, plus alerts (ROADMAP 2.1).

    Runs until you press Ctrl+C. Only TELEGRAM_ALLOWED_CHAT_ID is listened to.
    """
    with _tracked_run("bot", config_dir) as run:
        ctx = _context(run, config_dir)
        token = run.env.telegram_bot_token
        if token is None:
            raise FactoryError("TELEGRAM_BOT_TOKEN is missing in .env (the @BotFather token)")
        client = TelegramClient(
            token.get_secret_value(),
            before_send=lambda: check_and_record(run.conn, ctx.channel, "telegram"),
        )
        if find_chat_id:
            typer.echo("Send /start to the bot in Telegram now. Waiting up to 30 s ...")
            chats = bot_stage.find_chats(client)
            for chat_id, name in chats:
                typer.echo(f"  chat id {chat_id} ({name})")
            typer.echo(
                "Put yours in .env as TELEGRAM_ALLOWED_CHAT_ID=<chat id>."
                if chats
                else "No messages arrived. Send /start to the bot, then run this again."
            )
            return
        chat_id = run.env.telegram_allowed_chat_id
        if chat_id is None:
            raise FactoryError(
                "TELEGRAM_ALLOWED_CHAT_ID is missing in .env: find it with "
                "`uv run factory bot --find-chat-id`"
            )
        review_bot = bot_stage.ReviewBot(ctx, client, chat_id)
        typer.echo("Bot running: open Telegram. Press Ctrl+C here to stop it.")
        try:
            review_bot.run_forever()
        except KeyboardInterrupt:  # the normal way to stop it: a clean 'ok' run, not a crash
            typer.echo("\nBot stopped.")
        run.tally.processed = review_bot.decisions


# --- status -------------------------------------------------------------------------------


@app.command()
def new(
    object_name: Annotated[str, typer.Option("--object", help="What the video explains.")],
    fmt: Annotated[str, typer.Option("--format", help="short | deep_dive (enabled formats).")],
    pillar: Annotated[str, typer.Option(help="One of the channel's pillars, e.g. vehicles.")],
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
    show: Annotated[
        str | None, typer.Option(help="With an id: 'script' prints the current script.")
    ] = None,
    signals: Annotated[
        bool, typer.Option("--signals", help="Top 10 seed topics by trend (factory discover).")
    ] = False,
    config_dir: ConfigDirOption = CONFIG_DIR,
) -> None:
    """Show the channel summary, or one content item when an id is given."""
    with _tracked_run("status", config_dir) as run:
        channel = load_channel(run.env.factory_channel, config_dir)
        if signals:
            typer.echo(_signals_report(run.conn, channel))
            return
        if content_id is None:
            typer.echo(_status_report(run.conn, channel, run.env.dry_run, run.run_id))
            return
        typer.echo(review_stage.content_report(run.conn, content_id))
        if show == "script":
            typer.echo(review_stage.script_report(run.conn, content_id))
        elif show is not None:
            raise FactoryError(f"--show accepts 'script', not '{show}'")


SIGNALS_SHOWN = 10


def _signals_report(conn: sqlite3.Connection, channel: ChannelConfig) -> str:
    channel_id = conn.execute("SELECT id FROM channels WHERE slug = ?", (channel.slug,)).fetchone()
    signals = discover_stage.latest_signals(conn, channel_id[0]) if channel_id else []
    if not signals:
        return "No signals yet. Run: uv run factory discover"

    def number(value: float | None) -> str:
        return "-" if value is None else f"{value:,.0f}"

    lines = [
        f"Top {min(SIGNALS_SHOWN, len(signals))} of {len(signals)} seed topics by Wikipedia trend "
        "(last 7 days vs the 28 before; above 1.00x = rising)",
        f"  {'#':>2}  {'topic':<26} {'views 7d':>10} {'trend':>7} {'YouTube median 30d':>19}"
        f" {'Trends':>7}",
    ]
    for rank, s in enumerate(signals[:SIGNALS_SHOWN], start=1):
        trend = "-" if s.trend_ratio is None else f"{s.trend_ratio:.2f}x"
        google = "-" if s.trends_ratio is None else f"{s.trends_ratio:.2f}x"
        lines.append(f"  {rank:>2}  {s.topic[:26]:<26} {number(s.views_7d):>10} {trend:>7} "
                     f"{number(s.youtube_median):>19} {google:>7}")  # fmt: skip
    lines.append(
        "  trend = Wikipedia; Trends = Google Trends CSV (worldwide, last 28 days vs the 56 "
        "before); '-' = no data yet"
    )
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
    if settings is not None:
        ffmpeg.use_tools(settings.tools.ffmpeg, settings.tools.ffprobe)
        tool_problems = ffmpeg.check_tools()
        for problem in tool_problems:
            typer.echo(f"ERROR tools: {problem}", err=True)
        if not tool_problems:
            typer.echo(f"OK  tools: {settings.tools.ffmpeg} (subtitles, H.264, AAC)")
        problems += len(tool_problems)
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


# --- Flow credits ---------------------------------------------------------------------------


@app.command("flow-log")
def flow_log(
    credits: Annotated[int, typer.Option(help="Flow credits you just spent.", min=1)],
    config_dir: ConfigDirOption = CONFIG_DIR,
) -> None:
    """Record Flow credits spent today; refused if it would pass budgets_per_day.flow_credits."""
    with _tracked_run("flow-log", config_dir) as run:
        used, budget = generate_stage.flow_log(_context(run, config_dir), credits)
        typer.echo(f"Flow credits today: {used} of {budget} ({budget - used} left)")


# --- voice ----------------------------------------------------------------------------------


@voice_app.command("audition")
def voice_audition(
    voices: Annotated[str, typer.Option(help="Comma-separated Kokoro voice ids.")] = ",".join(
        voice_stage.AUDITION_VOICES
    ),
    text: Annotated[str | None, typer.Option(help="Text to read instead of the sample.")] = None,
    content_id: Annotated[
        int | None, typer.Option(help="Read this item's current script instead.")
    ] = None,
    config_dir: ConfigDirOption = CONFIG_DIR,
) -> None:
    """Render the same text with several voices into data/audition/ to compare them (OQ-7)."""
    with _tracked_run("voice audition", config_dir) as run:
        ctx = _context(run, config_dir)
        if content_id is not None:
            row = ctx.conn.execute(
                "SELECT body_text FROM scripts WHERE content_id = ? AND is_current = 1",
                (content_id,),
            ).fetchone()
            if row is None:
                raise FactoryError(f"content #{content_id} has no script yet")
            text = row["body_text"]
        ids = tuple(v.strip() for v in voices.split(",") if v.strip())
        files = voice_stage.audition(
            ctx, KokoroTTS().synthesize, voices=ids, text=text or voice_stage.AUDITION_TEXT
        )
        for f in files:
            typer.echo(_voice_line(f, config_dir.parent))
        run.tally.processed = len(files)


@voice_app.command("render")
def voice_render(content_id: int, config_dir: ConfigDirOption = CONFIG_DIR) -> None:
    """Render an item's current script as voice.wav plus subs.ass (word-highlight subtitles)."""
    with _tracked_run("voice render", config_dir) as run:
        ctx = _context(run, config_dir)
        result = voice_stage.render(ctx, content_id, KokoroTTS().synthesize)
        typer.echo(_voice_line(result, config_dir.parent))
        typer.echo(f"wrote {result.path.with_name(voice_stage.SUBS_FILE).relative_to(ctx.root)}")
        run.tally.processed = 1


@voice_app.command("preview")
def voice_preview(content_id: int, config_dir: ConfigDirOption = CONFIG_DIR) -> None:
    """Make subs_preview.mp4 (voice + burned-in subtitles, 540x960) to check on a phone."""
    with _tracked_run("voice preview", config_dir) as run:
        ctx = _context(run, config_dir)
        folder = voice_stage.asset_dir(ctx, content_id)
        voice_wav, subs = folder / "voice.wav", folder / voice_stage.SUBS_FILE
        if not voice_wav.is_file() or not subs.is_file():
            raise FactoryError(
                f"content #{content_id} has no voice.wav/subs.ass: run `factory voice render "
                f"{content_id}`"
            )
        out = subtitles.render_preview(
            voice_wav,
            subs,
            voice_stage.fonts_dir(ctx),
            folder / "subs_preview.mp4",
            ctx.channel.brand.palette.bg,
        )
        typer.echo(f"wrote {out.relative_to(ctx.root)}")
        run.tally.processed = 1


def _voice_line(f: voice_stage.VoiceFile, root: Path) -> str:
    return (
        f"wrote {f.path.relative_to(root)}  ({f.voice_id}, {f.duration_s:.1f} s, "
        f"{f.loudness.integrated_lufs:.1f} LUFS, peak {f.loudness.true_peak_dbtp:.1f} dBTP)"
    )


# --- music ----------------------------------------------------------------------------------


@trends_app.command("import")
def trends_import(config_dir: ConfigDirOption = CONFIG_DIR) -> None:
    """Load Google Trends CSVs from data/inbox/trends/ into trend_signals (ROADMAP 1.14a)."""
    with _tracked_run("trends import", config_dir) as run:
        ctx = _context(run, config_dir)
        result = trends_stage.import_inbox(ctx)
        run.tally.processed, run.tally.failed = result.files, len(result.broken)
        typer.echo(
            f"Imported {result.files} file(s): {result.rows_added} row(s) added, "
            f"{result.rows_skipped} already there"
        )
        for problem in result.broken:
            typer.echo(f"Not imported (left in the inbox): {problem}", err=True)
        if not result.files and not result.broken:
            typer.echo(f"No CSV files in {trends_stage.inbox(ctx)}")


@music_app.command("add")
def music_add(
    path: Annotated[
        Path, typer.Argument(help="A track downloaded from the YouTube Audio Library.")
    ],
    title: Annotated[str, typer.Option(help="The track's title as shown in the Audio Library.")],
    artist: Annotated[str | None, typer.Option(help="The artist.")] = None,
    attribution: Annotated[
        str | None,
        typer.Option(help="Attribution text, only if the Audio Library says it's required."),
    ] = None,
    license_url: Annotated[str | None, typer.Option(help="Link to the track's license.")] = None,
    mood: Annotated[str | None, typer.Option(help="e.g. calm, cinematic.")] = None,
    config_dir: ConfigDirOption = CONFIG_DIR,
) -> None:
    """Add a YouTube Audio Library track to the library (used only in YouTube renders)."""
    with _tracked_run("music add", config_dir) as run:
        ctx = _context(run, config_dir)
        track = music.add_track(
            ctx, path, title=title, artist=artist, attribution=attribution,
            license_url=license_url, mood=mood,
        )  # fmt: skip
        typer.echo(f"Added track #{track.id}: {track.title} -> {track.path.relative_to(ctx.root)}")
        run.tally.processed = 1


@music_app.command("list")
def music_list(config_dir: ConfigDirOption = CONFIG_DIR) -> None:
    """Show the library and how often each track has been used."""
    with _tracked_run("music list", config_dir) as run:
        ctx = _context(run, config_dir)
        tracks = music.list_tracks(ctx)
        if not tracks:
            typer.echo('No tracks yet: `uv run factory music add <file> --title "..."`')
        for track, uses in tracks:
            note = " (attribution required)" if track.attribution_text else ""
            typer.echo(f"#{track.id} {track.title} - {track.artist or '?'}: used {uses}x{note}")


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
