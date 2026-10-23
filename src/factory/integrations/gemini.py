"""Gemini API client that returns validated Pydantic objects (INTEGRATIONS.md §0 and §1)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, ValidationError
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    wait_random,
)

from factory.errors import IntegrationError, RetryableError

log = logging.getLogger("factory.gemini")

TIMEOUT_MS = 30_000
MAX_ATTEMPTS = 4  # 1 try + 3 retries, waiting 2 s, 4 s and 8 s (plus up to 1 s of jitter)


class GeminiClient:
    """Sends one prompt and returns JSON parsed into a Pydantic model.

    `before_call` runs before every attempt, retries included; stages pass a budget check
    (budgets.check_and_record) so a call that would go over budget is never sent.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        before_call: Callable[[], None] | None = None,
        sdk: Any | None = None,
        retry_wait_s: float = 2.0,
    ) -> None:
        self.model = model
        self._before_call = before_call or (lambda: None)
        self._retry_wait_s = retry_wait_s  # tests pass 0 so retries don't sleep
        # retry_options is left unset on purpose: the SDK then never retries by itself, so only
        # our retry below runs and every attempt passes through before_call.
        self._sdk = sdk or genai.Client(
            api_key=api_key, http_options=types.HttpOptions(timeout=TIMEOUT_MS)
        )

    def generate_json[M: BaseModel](self, *, system: str, prompt: str, schema: type[M]) -> M:
        """Return Gemini's answer as a `schema` object.

        If the JSON doesn't match the schema, ask once more with the validation errors; a
        second mismatch raises IntegrationError.
        """
        text = self._send(system, prompt, schema)
        try:
            return schema.model_validate_json(text)
        except ValidationError as first:
            log.warning(
                "Gemini answer did not match %s; asking once more",
                schema.__name__,
                extra={"event": "gemini_reask"},
            )
            reask = (
                f"{prompt}\n\nYour previous answer did not match the required JSON schema:\n"
                f"{_short_errors(first)}\nReturn only the corrected JSON."
            )
            text = self._send(system, reask, schema)
            try:
                return schema.model_validate_json(text)
            except ValidationError as second:
                raise IntegrationError(
                    f"Gemini returned invalid {schema.__name__} twice: {_short_errors(second)}"
                ) from second

    def _send(self, system: str, prompt: str, schema: type[BaseModel]) -> str:
        """One logical request, retried on RetryableError only (429, 5xx, network)."""
        retrying = Retrying(
            retry=retry_if_exception_type(RetryableError),
            stop=stop_after_attempt(MAX_ATTEMPTS),
            # multiplier 2 gives waits of 2, 4, 8 s; the random part spreads out retries.
            wait=wait_exponential(multiplier=self._retry_wait_s)
            + wait_random(0, self._retry_wait_s / 2),
            before_sleep=lambda state: log.warning(
                "Gemini attempt %d failed (%s); retrying",
                state.attempt_number,
                state.outcome.exception() if state.outcome else "?",
                extra={"event": "gemini_retry"},
            ),
            reraise=True,  # after the last attempt, raise the real error, not tenacity's
        )
        for attempt in retrying:
            with attempt:
                return self._attempt(system, prompt, schema)
        raise AssertionError("unreachable: Retrying either returns or raises")

    def _attempt(self, system: str, prompt: str, schema: type[BaseModel]) -> str:
        self._before_call()  # may raise BudgetExceededError, which is never retried
        log.debug("Gemini prompt (%s): %s", self.model, prompt)
        started = time.monotonic()
        try:
            response = self._sdk.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                    response_schema=schema,  # Gemini then answers in exactly this shape
                ),
            )
        except genai_errors.APIError as exc:
            if exc.code == 429 or exc.code >= 500:
                raise RetryableError(f"Gemini HTTP {exc.code} {exc.status}") from exc
            raise IntegrationError(f"Gemini HTTP {exc.code} {exc.status}: {exc.message}") from exc
        except httpx.TransportError as exc:  # timeouts and connection errors
            raise RetryableError(f"Gemini network error: {type(exc).__name__}") from exc

        text = response.text
        if not text:
            raise IntegrationError(f"Gemini returned no text ({_no_text_reason(response)})")
        usage = response.usage_metadata
        log.info(
            "Gemini %s answered in %.1f s (%s tokens in, %s out)",
            self.model,
            time.monotonic() - started,
            getattr(usage, "prompt_token_count", "?"),
            getattr(usage, "candidates_token_count", "?"),
            extra={"event": "gemini_call"},
        )
        return text


def _no_text_reason(response: Any) -> str:
    """Why an answer came back empty: a blocked prompt or the candidate's finish reason."""
    feedback = getattr(response, "prompt_feedback", None)
    if feedback is not None and getattr(feedback, "block_reason", None):
        return f"prompt blocked: {feedback.block_reason}"
    candidates = getattr(response, "candidates", None) or []
    if candidates and getattr(candidates[0], "finish_reason", None):
        return f"finish reason: {candidates[0].finish_reason}"
    return "no candidates"


def _short_errors(exc: ValidationError) -> str:
    """At most 5 'field: problem' lines, enough for the model to correct itself."""
    lines = [
        f"- {'.'.join(str(p) for p in err['loc']) or '(top level)'}: {err['msg']}"
        for err in exc.errors()[:5]
    ]
    return "\n".join(lines)
