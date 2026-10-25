"""Real Telegram calls to your own chat. Off by default; run with:
uv run pytest -m integration -k telegram

Sends one text message with a button and one 2-second test video, then takes the button off.
Needs TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_CHAT_ID in .env.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.config import load_env
from factory.integrations.telegram_bot import TelegramClient
from factory.media import ffmpeg

pytestmark = pytest.mark.integration


def test_real_message_button_and_video_reach_my_chat(tmp_path: Path) -> None:
    env = load_env()
    if env.telegram_bot_token is None or env.telegram_allowed_chat_id is None:
        pytest.skip("TELEGRAM_BOT_TOKEN / TELEGRAM_ALLOWED_CHAT_ID are not set in .env")
    client = TelegramClient(env.telegram_bot_token.get_secret_value())
    chat = env.telegram_allowed_chat_id
    clip = tmp_path / "test_clip.mp4"
    ffmpeg.run_ffmpeg(["-f", "lavfi", "-i", "testsrc=size=540x960:rate=30:duration=2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)])  # fmt: skip

    message_id = client.send_message(
        chat, "[TEST] factory integration test: this button does nothing.",
        [[("Test button", "skip:script:0")]],
    )  # fmt: skip
    video_id = client.send_video(chat, clip, caption="[TEST] 2 s test pattern")
    client.remove_buttons(chat, message_id)

    assert message_id > 0 and video_id > message_id
    print(f"\nsent message {message_id} and video {video_id} ({clip.stat().st_size} bytes)")
