from __future__ import annotations

import logging
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from factory.config import CONFIG_DIR


@pytest.fixture
def config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway copy of config/ in a temp "repo", so tests can break it and never touch the
    real data/, logs or .env. The test's own environment is cleared of factory switches."""
    target = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, target)
    for name in ("DRY_RUN", "FACTORY_CHANNEL"):
        monkeypatch.delenv(name, raising=False)
    return target


@pytest.fixture(autouse=True)
def reset_factory_logging() -> Iterator[None]:
    """CLI tests attach log handlers to streams that are closed after the call; drop them so
    the next test doesn't log into a closed stream."""
    yield
    logger = logging.getLogger("factory")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
