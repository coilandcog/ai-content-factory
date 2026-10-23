"""A stand-in for google.genai.Client: replays scripted outcomes and records every request.

Only the network layer is faked, so GeminiClient's own retry, validation and budget code runs
for real in unit tests (TESTING.md §2).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from google.genai import errors as genai_errors
from pydantic import BaseModel, Field

LLM_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "llm"


class ObjectFacts(BaseModel):
    """Small schema used by the Gemini tests (unit and integration)."""

    object: str = Field(min_length=1)
    summary: str = Field(min_length=10)
    parts: list[str] = Field(min_length=2)


class FakeGenaiClient:
    """Pass as `sdk=` to GeminiClient. Each outcome is a reply or an exception, used in order."""

    def __init__(self, *outcomes: Any) -> None:
        self.models = _FakeModels(list(outcomes))

    @property
    def calls(self) -> list[dict[str, Any]]:
        return self.models.calls


class _FakeModels:
    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = outcomes
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, *, model: str, contents: str, config: Any) -> Any:
        self.calls.append({"model": model, "contents": contents, "config": config})
        if not self._outcomes:
            raise AssertionError("FakeGenaiClient ran out of scripted outcomes")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def reply(text: str) -> SimpleNamespace:
    """A successful response carrying `text`."""
    usage = SimpleNamespace(prompt_token_count=42, candidates_token_count=17)
    return SimpleNamespace(text=text, usage_metadata=usage, prompt_feedback=None, candidates=[])


def fixture_reply(name: str) -> SimpleNamespace:
    """A successful response with the canned JSON in tests/fixtures/llm/<name>.json."""
    return reply((LLM_FIXTURES / f"{name}.json").read_text())


def blocked_reply(reason: str = "SAFETY") -> SimpleNamespace:
    """A response with no text because the prompt was blocked."""
    feedback = SimpleNamespace(block_reason=reason)
    return SimpleNamespace(text=None, usage_metadata=None, prompt_feedback=feedback, candidates=[])


def http_error(code: int) -> genai_errors.APIError:
    """The SDK's own exception for an HTTP error with this status code."""
    status = {400: "INVALID_ARGUMENT", 429: "RESOURCE_EXHAUSTED", 503: "UNAVAILABLE"}
    body = {"error": {"code": code, "message": "fake error", "status": status.get(code, "ERROR")}}
    cls = genai_errors.ServerError if code >= 500 else genai_errors.ClientError
    return cls(code, body)
