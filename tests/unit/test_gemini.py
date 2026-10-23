from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from fakes.fake_gemini import (
    FakeGenaiClient,
    ObjectFacts,
    blocked_reply,
    fixture_reply,
    http_error,
    reply,
)

from factory.errors import BudgetExceededError, IntegrationError, RetryableError
from factory.integrations.gemini import MAX_ATTEMPTS, GeminiClient
from factory.logging_setup import setup_logging

FAKE_KEY = "AIzaFAKE-unit-test-key-0000000000"


def client(sdk: FakeGenaiClient, **kwargs: object) -> GeminiClient:
    return GeminiClient(FAKE_KEY, "gemini-3.5-flash-lite", sdk=sdk, retry_wait_s=0, **kwargs)


def ask(gemini: GeminiClient) -> ObjectFacts:
    return gemini.generate_json(system="sys", prompt="Explain the alternator.", schema=ObjectFacts)


def test_valid_answer_becomes_a_model_and_request_is_structured() -> None:
    sdk = FakeGenaiClient(fixture_reply("object_facts"))

    facts = ask(client(sdk))

    assert facts.object == "alternator"
    assert len(facts.parts) == 4
    [call] = sdk.calls
    assert call["model"] == "gemini-3.5-flash-lite"
    assert call["config"].response_mime_type == "application/json"
    assert call["config"].response_schema is ObjectFacts
    assert call["config"].system_instruction == "sys"


def test_server_overload_is_retried_then_succeeds() -> None:
    sdk = FakeGenaiClient(http_error(503), fixture_reply("object_facts"))

    assert ask(client(sdk)).object == "alternator"
    assert len(sdk.calls) == 2


def test_rate_limit_gives_up_after_max_attempts() -> None:
    sdk = FakeGenaiClient(*[http_error(429)] * MAX_ATTEMPTS)

    with pytest.raises(RetryableError, match="429"):
        ask(client(sdk))
    assert len(sdk.calls) == MAX_ATTEMPTS == 4


def test_client_errors_are_not_retried() -> None:
    sdk = FakeGenaiClient(http_error(400))

    with pytest.raises(IntegrationError, match="400") as err:
        ask(client(sdk))
    assert not isinstance(err.value, RetryableError)
    assert len(sdk.calls) == 1


def test_invalid_json_is_reasked_once_with_the_errors() -> None:
    bad = reply(json.dumps({"object": "alternator", "summary": "short", "parts": ["rotor"]}))
    sdk = FakeGenaiClient(bad, fixture_reply("object_facts"))

    assert ask(client(sdk)).object == "alternator"
    assert len(sdk.calls) == 2
    assert "did not match the required JSON schema" in sdk.calls[1]["contents"]
    assert "parts" in sdk.calls[1]["contents"]


def test_invalid_twice_raises() -> None:
    sdk = FakeGenaiClient(reply("not json at all"), reply("{}"))

    with pytest.raises(IntegrationError, match="invalid ObjectFacts 2 times"):
        ask(client(sdk))


def test_blocked_answer_raises_with_reason() -> None:
    sdk = FakeGenaiClient(blocked_reply("SAFETY"))

    with pytest.raises(IntegrationError, match="prompt blocked: SAFETY"):
        ask(client(sdk))


def test_budget_hook_runs_before_every_attempt() -> None:
    checks: list[int] = []
    sdk = FakeGenaiClient(http_error(503), http_error(503), fixture_reply("object_facts"))

    ask(client(sdk, before_call=lambda: checks.append(len(sdk.calls))))

    assert checks == [0, 1, 2]  # each check happens before that attempt is sent


def test_budget_exceeded_stops_before_sending() -> None:
    def over_budget() -> None:
        raise BudgetExceededError("gemini: 60 used + 1 requested would exceed today's limit")

    sdk = FakeGenaiClient(fixture_reply("object_facts"))

    with pytest.raises(BudgetExceededError):
        ask(client(sdk, before_call=over_budget))
    assert sdk.calls == []  # never retried, never sent


def test_key_never_reaches_the_log(tmp_path: Path) -> None:
    setup_logging(tmp_path, "DEBUG", "Europe/Berlin", [FAKE_KEY])
    sdk = FakeGenaiClient(http_error(503), fixture_reply("object_facts"))

    ask(client(sdk))
    logging.getLogger("factory.gemini").info("debug with key %s", FAKE_KEY)

    text = next(tmp_path.glob("factory-*.jsonl")).read_text()
    assert FAKE_KEY not in text
    assert '"event": "gemini_call"' in text
    assert '"event": "gemini_retry"' in text
