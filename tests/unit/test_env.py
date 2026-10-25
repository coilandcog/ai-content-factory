from __future__ import annotations

from pathlib import Path

import pytest

from factory.config import load_env
from factory.errors import ConfigError

FAKE_KEY = "AIzaFAKE-not-a-real-key-123456"


@pytest.fixture
def root(config_dir: Path) -> Path:
    """The temp repo root (the conftest fixture also clears DRY_RUN / FACTORY_CHANNEL)."""
    return config_dir.parent


def test_missing_dry_run_means_true(root: Path) -> None:
    (root / ".env").write_text(f"GEMINI_API_KEY={FAKE_KEY}\n")

    env = load_env(root)

    assert env.dry_run is True
    assert env.factory_channel == "coil-and-cog"


def test_dry_run_false_and_environment_wins(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (root / ".env").write_text("DRY_RUN=true\n")
    monkeypatch.setenv("DRY_RUN", "False")

    assert load_env(root).dry_run is False


def test_dry_run_typo_is_an_error_not_false(root: Path) -> None:
    (root / ".env").write_text("DRY_RUN=flase\n")

    with pytest.raises(ConfigError, match="DRY_RUN must be 'true' or 'false', got 'flase'"):
        load_env(root)


def test_secrets_are_collected_but_never_shown(root: Path) -> None:
    (root / ".env").write_text(f"GEMINI_API_KEY={FAKE_KEY}\nOLLAMA_URL=http://localhost:11434\n")

    env = load_env(root)

    assert FAKE_KEY in [s.get_secret_value() for s in env.secrets]
    assert "http://localhost:11434" not in [s.get_secret_value() for s in env.secrets]
    assert FAKE_KEY not in repr(env)
    assert FAKE_KEY not in str(env.secrets)


def test_telegram_settings_load_and_the_token_is_a_secret(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_CHAT_ID"):
        monkeypatch.delenv(name, raising=False)
    token = "123456:FAKE-telegram-token"
    (root / ".env").write_text(f"TELEGRAM_BOT_TOKEN={token}\nTELEGRAM_ALLOWED_CHAT_ID=987654\n")

    env = load_env(root)

    assert env.telegram_allowed_chat_id == 987654
    assert env.telegram_bot_token is not None and token not in repr(env)
    assert token in [s.get_secret_value() for s in env.secrets]  # masked in logs


def test_a_chat_id_that_is_not_a_number_is_an_error(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_ID", raising=False)
    (root / ".env").write_text("TELEGRAM_ALLOWED_CHAT_ID=@me\n")

    with pytest.raises(ConfigError, match="telegram_allowed_chat_id"):
        load_env(root)
