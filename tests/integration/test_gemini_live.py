"""One real Gemini call. Off by default; run with: uv run pytest -m integration -k gemini

Uses 1 of the free tier's daily requests (gemini-3.5-flash-lite: 500/day).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fakes.fake_gemini import ObjectFacts

from factory.config import load_channel, load_env
from factory.integrations.gemini import GeminiClient
from factory.prompts import render_prompt

pytestmark = pytest.mark.integration

FIXTURE_PROMPTS = Path(__file__).resolve().parents[1] / "fixtures" / "prompts"


def test_real_gemini_call_returns_valid_json() -> None:
    env = load_env()
    if env.gemini_api_key is None:
        pytest.skip("GEMINI_API_KEY is not set in .env")
    channel = load_channel("coil-and-cog")
    system = render_prompt(
        "system", channel, prompts_root=FIXTURE_PROMPTS, channel_name="Coil & Cog"
    )
    prompt = render_prompt(
        "object_facts", channel, prompts_root=FIXTURE_PROMPTS, object="alternator"
    )
    gemini = GeminiClient(env.gemini_api_key.get_secret_value(), channel.llm.model)

    facts = gemini.generate_json(system=system.text, prompt=prompt.text, schema=ObjectFacts)

    assert "alternator" in facts.object.lower()
    assert len(facts.parts) >= 2
    print(f"\n{channel.llm.model}: {facts.model_dump_json()}")
