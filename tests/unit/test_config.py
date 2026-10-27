from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from factory.cli import app
from factory.config import load_channel, load_settings
from factory.errors import ConfigError

SLUG = "coil-and-cog"  # config_dir fixture: tests/conftest.py


def edit_channel(config_dir: Path, change: dict[str, Any]) -> None:
    """Set dotted keys in the channel YAML, e.g. {"gates.final": "optional"}."""
    path = config_dir / "channels" / f"{SLUG}.yaml"
    data = yaml.safe_load(path.read_text())
    for dotted, value in change.items():
        *parents, last = dotted.split(".")
        node = data
        for key in parents:
            node = node[key]
        node[last] = value
    path.write_text(yaml.safe_dump(data))


def test_real_config_is_valid() -> None:
    settings = load_settings()
    channel = load_channel(SLUG)

    assert settings.log_level == "INFO"
    assert channel.llm.model == "gemini-3.5-flash-lite"
    assert len(channel.niche.seed_topics) == 26  # submarine ballast tanks dropped (D-093)
    assert channel.gates.final == "required"
    assert [b.content_id for b in channel.benchmarks] == [5, 6]  # alternator, turbocharger (D-094)


def test_missing_key_names_file_and_key(config_dir: Path) -> None:
    path = config_dir / "channels" / f"{SLUG}.yaml"
    data = yaml.safe_load(path.read_text())
    del data["budgets_per_day"]["gemini_calls"]
    path.write_text(yaml.safe_dump(data))

    with pytest.raises(ConfigError, match=r"coil-and-cog\.yaml") as err:
        load_channel(SLUG, config_dir)
    assert "budgets_per_day.gemini_calls: Field required" in str(err.value)


def test_broken_yaml_reports_line_number(config_dir: Path) -> None:
    (config_dir / "settings.yaml").write_text("log_level: INFO\npaths: [unclosed\n")

    with pytest.raises(ConfigError, match=r"invalid YAML at line \d+"):
        load_settings(config_dir)


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"budgets_per_day.gemini_calss": 60}, "gemini_calss: Extra inputs are not permitted"),
        ({"gates.final": "optional"}, "gates.final: Input should be 'required'"),
        ({"platforms.tiktok.ai_disclosure": False}, "platforms.tiktok.ai_disclosure"),
        ({"formats.short.min_s": 60}, "min_s (60) must be smaller than max_s (50)"),
        ({"cadence.weekly_plan.sat": "episode"}, "uses 'episode', which is not enabled"),
        ({"timezone": "Mars/Olympus"}, "unknown time zone 'Mars/Olympus'"),
        ({"brand.palette.bg": "blue"}, "brand.palette.bg"),
        (
            {"benchmarks": [{"content_id": 5, "name": "x", "good_shots": [], "liked": "y"}]},
            "benchmarks[0].good_shots: List should have at least 1 item",
        ),
        (
            {"benchmarks": [{"content_id": 5, "name": "x", "good_shots": [1], "likes": "y"}]},
            "benchmarks[0].likes: Extra inputs are not permitted",
        ),
    ],
)
def test_invalid_values_are_rejected(
    config_dir: Path, change: dict[str, Any], expected: str
) -> None:
    edit_channel(config_dir, change)

    with pytest.raises(ConfigError) as err:
        load_channel(SLUG, config_dir)
    assert expected in str(err.value)


def test_slug_must_match_file_name(config_dir: Path) -> None:
    channels = config_dir / "channels"
    shutil.copy(channels / f"{SLUG}.yaml", channels / "other-channel.yaml")

    with pytest.raises(ConfigError, match="must match the file name 'other-channel'"):
        load_channel("other-channel", config_dir)


def test_config_check_command(config_dir: Path) -> None:
    runner = CliRunner()

    ok = runner.invoke(app, ["config", "check", "--config-dir", str(config_dir)])
    assert ok.exit_code == 0
    assert "OK  coil-and-cog (Coil & Cog)" in ok.output

    edit_channel(config_dir, {"gates.final": "optional"})
    broken = runner.invoke(app, ["config", "check", "--config-dir", str(config_dir)])
    assert broken.exit_code == 1
    assert "ERROR" in broken.output
    assert "OK  settings" in broken.output  # the other file is still checked
