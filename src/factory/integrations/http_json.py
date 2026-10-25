"""GET a JSON API with the factory's retry rules (ARCHITECTURE.md §6).

Only RetryableError is retried (network errors, 429, 5xx): 1 try + 3 retries with waits of
2, 4, 8 s plus jitter. `before_call` runs before every attempt, so each one passes the budget
check. Error messages name the service and status code but never the URL, because some URLs
carry an API key as a query parameter.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import httpx
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    wait_random,
)

from factory.errors import IntegrationError, RetryableError

log = logging.getLogger("factory.http")

MAX_ATTEMPTS = 4


class HttpStatusError(IntegrationError):
    """A non-retryable HTTP answer (4xx other than 429). `status` lets callers treat 404 apart."""

    def __init__(self, service: str, status: int, reason: str = "") -> None:
        super().__init__(f"{service} HTTP {status}{f' {reason}' if reason else ''}")
        self.status = status


def get_json(
    http: httpx.Client,
    url: str,
    *,
    service: str,
    params: dict[str, Any] | None = None,
    before_call: Callable[[], None] | None = None,
    retry_wait_s: float = 2,
    reason_of: Callable[[httpx.Response], str] | None = None,
) -> Any:
    """The decoded JSON body of a successful GET."""
    retrying = Retrying(
        retry=retry_if_exception_type(RetryableError),
        stop=stop_after_attempt(MAX_ATTEMPTS),
        wait=wait_exponential(multiplier=retry_wait_s) + wait_random(0, retry_wait_s / 2),
        before_sleep=lambda state: log.warning(
            "%s attempt %d failed (%s); retrying",
            service,
            state.attempt_number,
            state.outcome.exception() if state.outcome else "?",
            extra={"event": "http_retry"},
        ),
        reraise=True,
    )
    for attempt in retrying:
        with attempt:
            if before_call is not None:
                before_call()  # may raise BudgetExceededError, which is never retried
            try:
                response = http.get(url, params=params)
            except httpx.TransportError as exc:
                raise RetryableError(f"{service} network error: {type(exc).__name__}") from None
            if response.status_code == 429 or response.status_code >= 500:
                raise RetryableError(f"{service} HTTP {response.status_code}")
            if response.status_code >= 400:
                reason = reason_of(response) if reason_of else ""
                raise HttpStatusError(service, response.status_code, reason)
            try:
                return response.json()
            except ValueError:
                raise IntegrationError(f"{service} returned a body that is not JSON") from None
    raise AssertionError("unreachable: Retrying either returns or raises")
