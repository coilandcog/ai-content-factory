"""The `factory` command line: one subcommand per pipeline stage (ARCHITECTURE.md §2)."""

from __future__ import annotations

from collections.abc import Callable

import typer

app = typer.Typer(
    help="Coil & Cog AI Content Factory.",
    no_args_is_help=True,
    add_completion=False,
)

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
