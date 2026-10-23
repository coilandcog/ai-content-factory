from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from factory.config import ChannelConfig, load_channel
from factory.errors import ConfigError
from factory.prompts import render_prompt

FIXTURE_PROMPTS = Path(__file__).resolve().parents[1] / "fixtures" / "prompts"


@pytest.fixture
def channel() -> ChannelConfig:
    return load_channel("coil-and-cog")  # prompts_dir: prompts/coil-and-cog


def test_channel_override_wins(channel: ChannelConfig) -> None:
    prompt = render_prompt(
        "system", channel, prompts_root=FIXTURE_PROMPTS, channel_name="Coil & Cog"
    )

    assert "(channel override)" in prompt.text
    assert "Coil & Cog" in prompt.text
    assert prompt.template_path.parent.name == "coil-and-cog"


def test_default_is_used_without_an_override(channel: ChannelConfig) -> None:
    prompt = render_prompt("object_facts", channel, prompts_root=FIXTURE_PROMPTS, object="zipper")

    assert prompt.text.startswith("Explain the zipper")
    assert prompt.template_path.parent.name == "default"


def test_missing_variable_is_an_error(channel: ChannelConfig) -> None:
    with pytest.raises(ConfigError, match="'object' is undefined"):
        render_prompt("object_facts", channel, prompts_root=FIXTURE_PROMPTS)


def test_missing_template_is_an_error(channel: ChannelConfig) -> None:
    with pytest.raises(ConfigError, match="'nope.j2' not found"):
        render_prompt("nope", channel, prompts_root=FIXTURE_PROMPTS)


def test_version_changes_when_the_template_changes(channel: ChannelConfig, tmp_path: Path) -> None:
    root = tmp_path / "prompts"
    shutil.copytree(FIXTURE_PROMPTS, root)
    before = render_prompt("object_facts", channel, prompts_root=root, object="zipper").version

    template = root / "default" / "object_facts.j2"
    template.write_text(template.read_text() + "Keep it short.\n")
    after = render_prompt("object_facts", channel, prompts_root=root, object="zipper").version

    assert len(before) == len(after) == 10
    assert before != after


def test_version_covers_included_templates(channel: ChannelConfig, tmp_path: Path) -> None:
    root = tmp_path / "prompts"
    shutil.copytree(FIXTURE_PROMPTS, root)
    (root / "default" / "part.j2").write_text("Shared rule A.\n")
    (root / "default" / "whole.j2").write_text('Main text.\n{% include "part.j2" %}')
    before = render_prompt("whole", channel, prompts_root=root)

    (root / "default" / "part.j2").write_text("Shared rule B.\n")  # only the included file
    after = render_prompt("whole", channel, prompts_root=root)

    assert "Shared rule A." in before.text and "Shared rule B." in after.text
    assert before.version != after.version
