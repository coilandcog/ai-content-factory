"""A stand-in for api.telegram.org: queues updates for getUpdates and records every call.

Only the network is faked (httpx.MockTransport), so TelegramClient's own request building,
error handling and retries run for real in unit tests (TESTING.md §2).
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from factory.integrations.telegram_bot import TelegramClient

OWNER = 1111  # the chat id the bot is told to listen to
STRANGER = 2222
TOKEN = "123456:fake-token-for-tests"


class FakeTelegram:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.failures: list[httpx.Response] = []  # answered first, one per request
        self._updates: list[dict[str, Any]] = []
        self._next_update = 1
        self._next_message = 100

    def client(self, **kwargs: Any) -> TelegramClient:
        transport = httpx.MockTransport(self._handle)
        return TelegramClient(TOKEN, http=httpx.Client(transport=transport), retry_wait_s=0,
                              **kwargs)  # fmt: skip

    # --- what the owner (or a stranger) does in Telegram ----------------------------------

    def type(self, text: str, chat_id: int = OWNER) -> None:
        self._add({"message": {"message_id": 1, "chat": {"id": chat_id, "first_name": "Owner"},
                               "text": text}})  # fmt: skip

    def press(self, data: str, message_id: int, chat_id: int = OWNER) -> None:
        self._add({"callback_query": {"id": f"cb{self._next_update}", "from": {"id": chat_id},
                                      "data": data,
                                      "message": {"message_id": message_id,
                                                  "chat": {"id": chat_id}}}})  # fmt: skip

    # --- what the bot sent ----------------------------------------------------------------

    def sent(self, method: str = "sendMessage") -> list[dict[str, Any]]:
        return [body for name, body in self.calls if name == method]

    @property
    def texts(self) -> list[str]:
        return [body["text"] for body in self.sent()]

    def message_with(self, button_data: str) -> dict[str, Any]:
        """The last sent message carrying a button with this callback data."""
        for body in reversed(self.sent()):
            rows = (body.get("reply_markup") or {}).get("inline_keyboard", [])
            if any(b["callback_data"] == button_data for row in rows for b in row):
                return body
        raise AssertionError(f"no message with button {button_data!r}")

    def clear(self) -> None:
        self.calls.clear()

    # --- the fake server ------------------------------------------------------------------

    def _add(self, update: dict[str, Any]) -> None:
        self._updates.append({"update_id": self._next_update, **update})
        self._next_update += 1

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if self.failures:
            return self.failures.pop(0)
        method = request.url.path.rsplit("/", 1)[-1]
        if request.headers.get("content-type", "").startswith("application/json"):
            body = json.loads(request.content)
        else:  # multipart upload (sendVideo)
            body = {"multipart_bytes": len(request.read())}
        self.calls.append((method, body))
        if method == "getUpdates":
            offset = body.get("offset", 0)
            result: Any = [u for u in self._updates if u["update_id"] >= offset]
            self._updates = result  # what's below the offset is confirmed and dropped
        elif method in ("sendMessage", "sendVideo"):
            body["message_id"] = self._next_message
            result = {"message_id": self._next_message}
            self._next_message += 1
        else:
            result = True
        return httpx.Response(200, json={"ok": True, "result": result})
