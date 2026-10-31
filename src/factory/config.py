"""Load and validate config/settings.yaml and config/channels/<slug>.yaml (ARCHITECTURE.md §7).

Every model forbids unknown keys, so a typo in the YAML is an error instead of being ignored.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from dotenv import dotenv_values
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    SecretStr,
    ValidationError,
    model_validator,
)

from factory.errors import ConfigError

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"


def _check_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown time zone '{value}' (use e.g. 'Europe/Berlin')") from exc
    return value


TimeZone = Annotated[str, AfterValidator(_check_timezone)]
ClockTime = Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]  # "HH:MM", 24 h
HexColor = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")]
KebabSlug = Annotated[str, Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")]
SnakeId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
Text = Annotated[str, Field(min_length=1)]
FormatName = Literal["short", "deep_dive", "episode"]
Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class StrictModel(BaseModel):
    """Base for all config models: unknown keys are errors and loaded config is read-only."""

    model_config = ConfigDict(extra="forbid", frozen=True)


# --- config/settings.yaml ---------------------------------------------------------------


class Paths(StrictModel):
    """Folders and files, relative to the repo root."""

    data_dir: Path
    db_path: Path
    logs_dir: Path
    inbox_dir: Path
    assets_dir: Path
    assets_static_dir: Path
    secrets_dir: Path
    prompts_dir: Path


class Schedule(StrictModel):
    timezone: TimeZone
    nightly_run: ClockTime
    backup: ClockTime
    learn: ClockTime


class Tools(StrictModel):
    """System programs. Full paths, because several FFmpeg builds can be installed and only some
    can burn subtitles (D-065)."""

    ffmpeg: Text
    ffprobe: Text


class Settings(StrictModel):
    paths: Paths
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"]
    schedule: Schedule
    tools: Tools


# --- config/channels/<slug>.yaml --------------------------------------------------------


class Pillar(StrictModel):
    id: SnakeId
    description: Text


class Niche(StrictModel):
    name: Text
    promise: Text
    audience: Text
    pillars: list[Pillar] = Field(min_length=1)
    seed_topics: list[Text] = Field(min_length=1)
    # Optional: the English Wikipedia article to use for a seed topic when search picks the
    # wrong one (factory discover, D-078). Keys must be seed topics.
    wikipedia_articles: dict[Text, Text] = Field(default_factory=dict)
    blocklist: list[Text]

    @model_validator(mode="after")
    def _articles_for_known_topics(self) -> Niche:
        unknown = sorted(set(self.wikipedia_articles) - set(self.seed_topics))
        if unknown:
            raise ValueError(f"wikipedia_articles names topics that are not seed_topics: {unknown}")
        return self


class VideoFormat(StrictModel):
    enabled: bool
    min_s: PositiveInt
    max_s: PositiveInt
    aspect: Literal["9:16", "16:9"]
    series: Text

    @model_validator(mode="after")
    def _min_below_max(self) -> VideoFormat:
        if self.min_s >= self.max_s:
            raise ValueError(f"min_s ({self.min_s}) must be smaller than max_s ({self.max_s})")
        return self


class Formats(StrictModel):
    short: VideoFormat
    deep_dive: VideoFormat
    episode: VideoFormat


class Cadence(StrictModel):
    publish_time: ClockTime
    weekly_plan: dict[Weekday, FormatName] = Field(min_length=1)


class Voice(StrictModel):
    engine: Literal["kokoro"]
    voice_id: Text
    speed: float = Field(gt=0, le=2)
    target_wpm: PositiveInt


class Palette(StrictModel):
    bg: HexColor
    electric: HexColor
    heat: HexColor
    fluid: HexColor
    text: HexColor


class Fonts(StrictModel):
    subtitle: Text
    label: Text


class Brand(StrictModel):
    palette: Palette
    fonts: Fonts


# ai_disclosure only accepts true: we always label AI content (D-039).
class YouTubePlatform(StrictModel):
    enabled: bool
    handle: Annotated[str, Field(pattern=r"^@\w+$")]
    publish_mode: Literal["api_scheduled"]
    category_id: PositiveInt
    ai_disclosure: Literal[True]


class TikTokPlatform(StrictModel):
    enabled: bool
    handle: Annotated[str, Field(pattern=r"^@\w+$")]
    publish_mode: Literal["inbox_draft"]
    ai_disclosure: Literal[True]


class Platforms(StrictModel):
    youtube: YouTubePlatform
    tiktok: TikTokPlatform


class Llm(StrictModel):
    provider: Literal["gemini", "ollama"]
    model: Text
    fallback_provider: Literal["gemini", "ollama"]
    fallback_model: Text


class Budgets(StrictModel):
    gemini_calls: NonNegativeInt
    youtube_units: NonNegativeInt
    flow_credits: NonNegativeInt


class Scoring(StrictModel):
    min_virality_score: int = Field(ge=0, le=100)
    similarity_block_threshold: float = Field(ge=0, le=1)
    similarity_window: PositiveInt


# final only accepts "required": Gate 2 is never removed (D-047).
class Gates(StrictModel):
    script: Literal["required", "batch_weekly"]
    final: Literal["required"]


class Benchmark(StrictModel):
    """One of the owner's best videos: its Flow prompts are examples for new film shots (D-094)."""

    content_id: PositiveInt
    name: Text
    good_shots: list[PositiveInt] = Field(min_length=1)
    liked: Text
    improve: Text | None = None


class ChannelConfig(StrictModel):
    slug: KebabSlug
    display_name: Text
    status: Literal["active", "paused"]
    language: Annotated[str, Field(pattern=r"^[a-z]{2}$")]
    timezone: TimeZone
    niche: Niche
    formats: Formats
    cadence: Cadence
    voice: Voice
    brand: Brand
    platforms: Platforms
    llm: Llm
    budgets_per_day: Budgets
    scoring: Scoring
    gates: Gates
    # Optional: the owner's best videos, newest additions last (D-094).
    benchmarks: list[Benchmark] = Field(default_factory=list)
    # Optional: how Flow video prompts are written (D-105). "brief" = the short product-film
    # prompts that made the alternator and turbo videos; "story" = story shots with chained images.
    video_prompts: Literal["brief", "story"] = "brief"
    # Optional: how new items get their picture (D-111). "flow" = Flow clips made by hand;
    # "blueprint" = a film drawn by code, so the mechanism is calculated, not guessed.
    video_style: Literal["flow", "blueprint"] = "flow"
    prompts_dir: Path = Path("prompts/default")

    @model_validator(mode="after")
    def _plan_uses_enabled_formats(self) -> ChannelConfig:
        for day, fmt in self.cadence.weekly_plan.items():
            if not getattr(self.formats, fmt).enabled:
                raise ValueError(f"cadence.weekly_plan.{day} uses '{fmt}', which is not enabled")
        return self


# --- .env and environment variables ------------------------------------------------------

# Any variable whose name contains one of these is treated as a secret (SECURITY_COMPLIANCE §1).
SECRET_NAME_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD")
_MIN_SECRET_LENGTH = 8  # shorter values ("true", "1") would mask ordinary words in logs


class EnvSettings(StrictModel):
    """Runtime switches and API keys from .env / the environment. Keys are SecretStr, which
    prints as '**********'; `secrets` holds every secret value for the log redaction filter."""

    dry_run: bool
    factory_channel: KebabSlug
    gemini_api_key: SecretStr | None = None  # from GEMINI_API_KEY
    youtube_api_key: SecretStr | None = None  # from YOUTUBE_API_KEY (read-only discover calls)
    wikimedia_user_agent: str | None = None  # from WIKIMEDIA_USER_AGENT (not a secret)
    telegram_bot_token: SecretStr | None = None  # from TELEGRAM_BOT_TOKEN
    telegram_allowed_chat_id: int | None = None  # from TELEGRAM_ALLOWED_CHAT_ID (not a secret)
    # repr=False keeps this field out of repr() entirely.
    secrets: tuple[SecretStr, ...] = Field(default=(), repr=False)


def load_env(root: Path = REPO_ROOT) -> EnvSettings:
    """Read <root>/.env plus real environment variables (which win) into EnvSettings.

    The file is parsed in Python, never sourced by a shell, so values can't leak in shell errors.
    """
    file_values = dotenv_values(root / ".env") if (root / ".env").exists() else {}
    merged = {k: v for k, v in file_values.items() if v is not None} | dict(os.environ)
    secrets = tuple(
        SecretStr(value)
        for name, value in merged.items()
        if any(marker in name.upper() for marker in SECRET_NAME_MARKERS)
        and len(value) >= _MIN_SECRET_LENGTH
    )
    try:
        return EnvSettings(
            dry_run=_parse_dry_run(merged.get("DRY_RUN")),
            factory_channel=merged.get("FACTORY_CHANNEL", "coil-and-cog"),
            gemini_api_key=merged.get("GEMINI_API_KEY") or None,
            youtube_api_key=merged.get("YOUTUBE_API_KEY") or None,
            wikimedia_user_agent=merged.get("WIKIMEDIA_USER_AGENT") or None,
            telegram_bot_token=merged.get("TELEGRAM_BOT_TOKEN") or None,
            telegram_allowed_chat_id=merged.get("TELEGRAM_ALLOWED_CHAT_ID") or None,
            secrets=secrets,
        )
    except ValidationError as exc:
        lines = [f"  {_location(err['loc'])}: {_message(err['msg'])}" for err in exc.errors()]
        raise ConfigError(".env / environment\n" + "\n".join(lines)) from exc


def _parse_dry_run(raw: str | None) -> bool:
    # Missing means true: the safe default is "don't publish anything".
    if raw is None:
        return True
    value = raw.strip().lower()
    if value not in ("true", "false"):
        raise ConfigError(f"DRY_RUN must be 'true' or 'false', got '{raw}'")
    return value == "true"


# --- loading -----------------------------------------------------------------------------


def load_settings(config_dir: Path = CONFIG_DIR) -> Settings:
    """Load and validate settings.yaml; raise ConfigError with a readable message if it's wrong."""
    path = config_dir / "settings.yaml"
    return _validate(Settings, _read_yaml(path), path)


def load_channel(slug: str, config_dir: Path = CONFIG_DIR) -> ChannelConfig:
    """Load and validate channels/<slug>.yaml; raise ConfigError if it's wrong."""
    path = config_dir / "channels" / f"{slug}.yaml"
    channel = _validate(ChannelConfig, _read_yaml(path), path)
    if channel.slug != path.stem:
        raise ConfigError(
            f"{_display(path)}\n  slug: '{channel.slug}' must match the file name '{path.stem}'"
        )
    return channel


def channel_slugs(config_dir: Path = CONFIG_DIR) -> list[str]:
    """Slugs of every channel profile on disk, taken from the file names."""
    return sorted(path.stem for path in (config_dir / "channels").glob("*.yaml"))


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"{_display(path)}: file not found") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        # problem_mark is 0-based; editors count lines from 1.
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        problem = getattr(exc, "problem", None) or exc
        raise ConfigError(f"{_display(path)}: invalid YAML{where}: {problem}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{_display(path)}: expected 'key: value' lines at the top level")
    return data


def _validate[M: BaseModel](model: type[M], data: dict[str, Any], path: Path) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        lines = [f"  {_location(err['loc'])}: {_message(err['msg'])}" for err in exc.errors()]
        raise ConfigError(f"{_display(path)}\n" + "\n".join(lines)) from exc


def _location(loc: tuple[int | str, ...]) -> str:
    """('formats', 'short', 'min_s') -> 'formats.short.min_s'; list indexes become [n]."""
    out = ""
    for part in loc:
        out += f"[{part}]" if isinstance(part, int) else f".{part}"
    return out.lstrip(".") or "(top level)"


def _message(msg: str) -> str:
    # Pydantic prefixes our own ValueError messages with "Value error, ".
    return msg.removeprefix("Value error, ")


def _display(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)
