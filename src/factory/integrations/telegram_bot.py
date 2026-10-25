"""A small Telegram Bot API client over httpx (INTEGRATIONS.md §7, D-082).

Only the five calls the review bot needs. Long polling (`getUpdates`) means no public server.
The token is part of every request URL (https://api.telegram.org/bot<token>/<method>), so URLs
never appear in errors or logs: errors name the method and Telegram's description only, and
httpx's own request log line is switched off.

Retries follow ARCHITECTURE.md §6 (network errors and 5xx: 1 try + 3 retries, 2/4/8 s plus
jitter). A 429 says how long to wait (`retry_after`), and that wait is used instead.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from tenacity import RetryCallState, Retrying, retry_if_exception_type, stop_after_attempt

from factory.errors import IntegrationError, RetryableError
from factory.integrations.http_json import MAX_ATTEMPTS, HttpStatusError

log = logging.getLogger("factory.telegram")
logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO line prints the URL (token)

API = "https://api.telegram.org"
MAX_TEXT = 4096  # characters per message
MAX_CAPTION = 1024  # characters per video caption
MAX_VIDEO_MB = 50  # bot uploads (sendVideo); our preview stays under 45 MB (qa.PREVIEW_MAX_MB)

# One row of buttons is a list of (label, callback data); callback data is at most 64 bytes.
Buttons = list[list[tuple[str, str]]]


class TelegramRetryAfter(RetryableError):
    def __init__(self, method: str, seconds: float) -> None:
        super().__init__(f"telegram {method}: HTTP 429, retry after {seconds:.0f} s")
        self.seconds = seconds


class TelegramClient:
    """`before_send` runs before each sendMessage/sendVideo attempt (usage counting)."""

    def __init__(
        self,
        token: str,
        *,
        http: httpx.Client | None = None,
        retry_wait_s: float = 2,
        before_send: Callable[[], None] | None = None,
    ) -> None:
        self._base = f"{API}/bot{token}"
        self._http = http or httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0))
        self._retry_wait_s = retry_wait_s
        self._before_send = before_send

    def get_updates(self, offset: int | None, timeout_s: int) -> list[dict[str, Any]]:
        """New messages and button presses; waits up to `timeout_s` for one (long polling).

        Updates below `offset` are confirmed and Telegram drops them; unconfirmed ones (kept for
        24 hours) come back on the next call, so a press made while the bot was off isn't lost.
        """
        body: dict[str, Any] = {
            "timeout": timeout_s,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            body["offset"] = offset
        return self._call("getUpdates", json_body=body, timeout_s=timeout_s + 15)

    def send_message(self, chat_id: int, text: str, buttons: Buttons | None = None) -> int:
        """Send plain text (no Markdown, so nothing needs escaping); returns the message id."""
        body: dict[str, Any] = {"chat_id": chat_id, "text": text[:MAX_TEXT]}
        if buttons:
            body["reply_markup"] = _keyboard(buttons)
        return self._call("sendMessage", json_body=body, send=True)["message_id"]

    def send_video(self, chat_id: int, path: Path, caption: str = "") -> int:
        """Upload an MP4 that plays inside the chat; returns the message id."""
        size_mb = path.stat().st_size / 1_000_000
        if size_mb >= MAX_VIDEO_MB:
            raise IntegrationError(f"{path.name} is {size_mb:.1f} MB; bots can send {MAX_VIDEO_MB}")
        data = {
            "chat_id": str(chat_id),
            "caption": caption[:MAX_CAPTION],
            "supports_streaming": "true",
        }
        with path.open("rb") as video:
            result = self._call(
                "sendVideo",
                data=data,
                files={"video": (path.name, video, "video/mp4")},
                send=True,
                timeout_s=180,
            )
        return result["message_id"]

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        """Stop the button's loading spinner (Telegram expects this for every press)."""
        self._call("answerCallbackQuery", json_body={"callback_query_id": callback_id,
                                                     "text": text[:200]})  # fmt: skip

    def remove_buttons(self, chat_id: int, message_id: int) -> None:
        """Take the buttons off a decided message, so it can't be pressed again."""
        try:
            self._call(
                "editMessageReplyMarkup",
                json_body={"chat_id": chat_id, "message_id": message_id,
                           "reply_markup": {"inline_keyboard": []}},
            )  # fmt: skip
        except HttpStatusError as exc:  # 400 "message is not modified" / too old: harmless
            log.debug("buttons not removed: %s", exc)

    def _call(
        self,
        method: str,
        *,
        json_body: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
        files: dict[str, Any] | None = None,
        send: bool = False,
        timeout_s: float = 30,
    ) -> Any:
        retrying = Retrying(
            retry=retry_if_exception_type(RetryableError),
            stop=stop_after_attempt(MAX_ATTEMPTS),
            wait=self._wait,
            before_sleep=lambda state: log.warning(
                "telegram %s attempt %d failed (%s); retrying",
                method,
                state.attempt_number,
                state.outcome.exception() if state.outcome else "?",
                extra={"event": "http_retry"},
            ),
            reraise=True,
        )
        for attempt in retrying:
            with attempt:
                if send and self._before_send is not None:
                    self._before_send()
                if files:
                    for _, handle, _ in files.values():
                        handle.seek(0)  # a retry uploads the file again from the start
                try:
                    response = self._http.post(
                        f"{self._base}/{method}",
                        json=json_body,
                        data=data,
                        files=files,
                        timeout=timeout_s,
                    )
                except httpx.TransportError as exc:
                    raise RetryableError(f"telegram {method}: {type(exc).__name__}") from None
                return _result(method, response)
        raise AssertionError("unreachable: Retrying either returns or raises")

    def _wait(self, state: RetryCallState) -> float:
        exc = state.outcome.exception() if state.outcome else None
        if isinstance(exc, TelegramRetryAfter):
            return exc.seconds
        base = self._retry_wait_s * 2 ** (state.attempt_number - 1)
        return base + random.uniform(0, self._retry_wait_s / 2)  # jitter


def _result(method: str, response: httpx.Response) -> Any:
    """Telegram answers {"ok": true, "result": ...} or {"ok": false, "description": ...}."""
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code == 429:
        seconds = float((body.get("parameters") or {}).get("retry_after", 5))
        raise TelegramRetryAfter(method, seconds)
    if response.status_code >= 500:
        raise RetryableError(f"telegram {method}: HTTP {response.status_code}")
    if response.status_code == 401:  # Telegram's reason is just "Unauthorized"
        raise HttpStatusError(f"telegram {method}", 401, "Unauthorized: check TELEGRAM_BOT_TOKEN")
    if response.status_code >= 400 or not body.get("ok"):
        raise HttpStatusError(f"telegram {method}", response.status_code,
                              str(body.get("description", ""))[:200])  # fmt: skip
    return body["result"]


def _keyboard(buttons: Buttons) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": label, "callback_data": data} for label, data in row] for row in buttons
        ]
    }
