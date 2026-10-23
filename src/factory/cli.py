"""The `factory` command line: one subcommand per pipeline stage (ARCHITECTURE.md §2)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer

from factory.config import (
    CONFIG_DIR,
    ChannelConfig,
    Settings,
    channel_slugs,
    load_channel,
    load_settings,
)
from factory.errors import ConfigError

app = typer.Typer(
    help="Coil & Cog AI Content Factory.",
    no_args_is_help=True,
    add_completion=False,
)
config_app = typer.Typer(help="Validate and inspect configuration.", no_args_is_help=True)
app.add_typer(config_app, name="config")

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


def _make_stub(stage: str, task: str) -> Callable[[], None]:
    """Build a placeholder command that says which ROADMAP task will implement it."""

    def stub() -> None:
        typer.echo(f"'{stage}' is not built yet (ROADMAP task {task}).")
        # Non-zero exit so a script calling a stub can't mistake it for real work.
        raise typer.Exit(code=1)

    return stub


for _name, (_help, _task) in STAGES.items():
    app.command(name=_name, help=_help)(_make_stub(_name, _task))


@config_app.command("check")
def config_check(
    config_dir: Annotated[
        Path, typer.Option(help="Folder holding settings.yaml and channels/.")
    ] = CONFIG_DIR,
) -> None:
    """Validate settings.yaml and every channel profile, then print a summary."""
    # Check every file, not just the first broken one, so one run shows all problems.
    failed = False
    try:
        typer.echo(_settings_summary(load_settings(config_dir)))
    except ConfigError as exc:
        typer.echo(f"ERROR {exc}", err=True)
        failed = True
    slugs = channel_slugs(config_dir)
    if not slugs:
        typer.echo(f"ERROR no channel profiles found in {config_dir / 'channels'}", err=True)
        failed = True
    for slug in slugs:
        try:
            typer.echo(_channel_summary(load_channel(slug, config_dir)))
        except ConfigError as exc:
            typer.echo(f"ERROR {exc}", err=True)
            failed = True
    if failed:
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
