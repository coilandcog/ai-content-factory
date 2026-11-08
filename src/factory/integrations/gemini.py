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
# A file sent inside the request (no upload step) keeps the whole request under Gemini's 20 MB.
MAX_INLINE_BYTES = 18 * 1024 * 1024
# Free-tier requests per minute (INTEGRATIONS §1). A long-video script makes many quick calls and
# once got HTTP 429 (2026-11-08), so real calls to a model are spaced to stay under its limit.
FREE_TIER_RPM = {"gemini-3.5-flash-lite": 15, "gemini-3.8-flash": 5}
_last_call: dict[str, float] = {}  # model -> when it was last called (shared by every client)


class Media(BaseModel):
    """An image or video sent along with the prompt (clip review, D-097)."""

    data: bytes
    mime_type: str


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
        self._api_key = api_key
        self._before_call = before_call or (lambda: None)
        self._retry_wait_s = retry_wait_s  # tests pass 0 so retries don't sleep
        self._paced = sdk is None  # only real calls wait for the per-minute limit
        # retry_options is left unset on purpose: the SDK then never retries by itself, so only
        # our retry below runs and every attempt passes through before_call.
        self._sdk = sdk or genai.Client(
            api_key=api_key, http_options=types.HttpOptions(timeout=TIMEOUT_MS)
        )

    def generate_json[M: BaseModel](
        self,
        *,
        system: str,
        prompt: str,
        schema: type[M],
        max_reasks: int = 1,
        media: Media | None = None,
    ) -> M:
        """Return Gemini's answer as a `schema` object.

        If the JSON doesn't match the schema, ask again (up to `max_reasks` times) with the
        validation errors appended; still no match raises IntegrationError. `media` (an image or
        a short video) is sent with every attempt.
        """
        if media is not None and len(media.data) > MAX_INLINE_BYTES:
            raise IntegrationError(
                f"the file is {len(media.data) / 1e6:.0f} MB; at most "
                f"{MAX_INLINE_BYTES / 1e6:.0f} MB can be sent to Gemini"
            )
        request = prompt
        for attempt in range(max_reasks + 1):
            text = self._send(system, request, schema, media)
            try:
                return schema.model_validate_json(text)
            except ValidationError as exc:
                problems = _short_errors(exc)
                if attempt == max_reasks:
                    raise IntegrationError(
                        f"Gemini returned invalid {schema.__name__} {attempt + 1} times: {problems}"
                    ) from exc
                log.warning(
                    "Gemini answer did not match %s (%s); asking again",
                    schema.__name__,
                    problems.replace("\n", " "),
                    extra={"event": "gemini_reask"},
                )
                request = (
                    f"{prompt}\n\nYour previous answer did not match the required JSON schema:\n"
                    f"{problems}\nReturn only the corrected JSON."
                )
        raise AssertionError("unreachable")

    def with_model(self, model: str, timeout_s: float | None = None) -> GeminiClient:
        """The same client (key, budget check) for another model, e.g. long-video scripts
        (D-129). A real client gets its own connection with `timeout_s`: gemini-3.8-flash thinks
        for 20-60 s per chapter, past the usual 30 s (HTTP 504 on 2026-11-08)."""
        sdk = self._sdk
        if self._paced and timeout_s is not None:
            options = types.HttpOptions(timeout=int(timeout_s * 1000))
            sdk = genai.Client(api_key=self._api_key, http_options=options)
        other = GeminiClient(self._api_key, model, before_call=self._before_call, sdk=sdk,
                             retry_wait_s=self._retry_wait_s)  # fmt: skip
        other._paced = self._paced
        return other

    def _pace(self) -> None:
        """Wait until this model's per-minute limit allows the next request."""
        interval = 60 / FREE_TIER_RPM.get(self.model, 15) * 1.05
        wait = _last_call.get(self.model, 0.0) + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call[self.model] = time.monotonic()

    def _send(
        self, system: str, prompt: str, schema: type[BaseModel], media: Media | None = None
    ) -> str:
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
                return self._attempt(system, prompt, schema, media)
        raise AssertionError("unreachable: Retrying either returns or raises")

    def _attempt(
        self, system: str, prompt: str, schema: type[BaseModel], media: Media | None
    ) -> str:
        self._before_call()  # may raise BudgetExceededError, which is never retried
        if self._paced:
            self._pace()
        log.debug("Gemini prompt (%s): %s", self.model, prompt)
        started = time.monotonic()
        try:
            response = self._sdk.models.generate_content(
                model=self.model,
                # Text only stays a plain string; a file goes first, then the instructions.
                contents=prompt
                if media is None
                else [types.Part.from_bytes(data=media.data, mime_type=media.mime_type), prompt],
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
