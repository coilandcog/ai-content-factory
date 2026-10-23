"""Prompt templates (Jinja2) with per-channel overrides (CONVENTIONS.md §6).

`render_prompt("script_short", channel, object="alternator")` looks for
prompts/<channel prompts_dir>/script_short.j2 first, then prompts/default/script_short.j2.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jinja2

from factory.config import REPO_ROOT, ChannelConfig
from factory.errors import ConfigError

PROMPTS_ROOT = REPO_ROOT / "prompts"
DEFAULT_DIR_NAME = "default"


@dataclass(frozen=True)
class RenderedPrompt:
    text: str
    template_path: Path
    version: str  # stored with every LLM call as prompt_version


def render_prompt(
    name: str,
    channel: ChannelConfig,
    *,
    prompts_root: Path = PROMPTS_ROOT,
    **variables: Any,
) -> RenderedPrompt:
    """Render <name>.j2, preferring the channel's override over prompts/default.

    Templates always get `channel` (the ChannelConfig) plus the keyword variables passed here.
    A variable the template uses but the caller didn't pass is an error, not an empty gap.
    """
    search_path = [_channel_dir(channel, prompts_root), prompts_root / DEFAULT_DIR_NAME]
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader([str(p) for p in search_path if p.is_dir()]),
        undefined=jinja2.StrictUndefined,  # missing variables raise instead of rendering ""
        autoescape=False,  # prompts are plain text, not HTML
        keep_trailing_newline=True,
    )
    try:
        template = env.get_template(f"{name}.j2")
        text = template.render(channel=channel, **variables)  # every template can use `channel`
    except jinja2.TemplateNotFound as exc:
        raise ConfigError(
            f"prompt template '{name}.j2' not found in {[str(p) for p in search_path]}"
        ) from exc
    except jinja2.UndefinedError as exc:
        raise ConfigError(f"prompt template '{name}.j2': {exc.message}") from exc
    path = Path(template.filename or "")
    return RenderedPrompt(text=text, template_path=path, version=template_version(path))


def template_version(path: Path) -> str:
    """First 10 hex chars of the SHA-256 of the template file.

    Unlike a git hash, it works before a commit and changes with every edit (D-057).
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()[:10]


def _channel_dir(channel: ChannelConfig, prompts_root: Path) -> Path:
    # channel.prompts_dir is written like "prompts/coil-and-cog"; only its last part names the
    # folder under prompts_root, so tests can point prompts_root at fixtures.
    return prompts_root / channel.prompts_dir.name
