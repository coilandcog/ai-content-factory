from __future__ import annotations

import json
import logging
from pathlib import Path

from factory.logging_setup import JSON_FIELDS, set_log_context, setup_logging

SECRET = "tok-1234567890-very-secret"


def read_lines(logs_dir: Path) -> list[dict[str, object]]:
    [log_file] = logs_dir.glob("factory-*.jsonl")
    return [json.loads(line) for line in log_file.read_text().splitlines()]


def test_json_line_has_every_required_field(tmp_path: Path) -> None:
    setup_logging(tmp_path, "INFO", "Europe/Berlin", [])
    set_log_context(run_id=7, stage="script", channel="coil-and-cog")

    logging.getLogger("factory.test").info(
        "script written", extra={"event": "script_saved", "content_id": 42}
    )

    [line] = read_lines(tmp_path)
    assert tuple(line) == JSON_FIELDS
    assert line["ts"].endswith("Z")
    assert (line["run_id"], line["stage"], line["content_id"]) == (7, "script", 42)
    assert (line["event"], line["msg"]) == ("script_saved", "script written")


def test_secrets_are_redacted_in_messages_args_and_tracebacks(tmp_path: Path) -> None:
    setup_logging(tmp_path, "INFO", "Europe/Berlin", [SECRET])
    log = logging.getLogger("factory.test")

    log.info(f"calling API with key {SECRET}")
    log.warning("token is %s", SECRET)
    try:
        raise RuntimeError(f"401 for key={SECRET}")
    except RuntimeError:
        log.exception("request failed")

    raw = next(tmp_path.glob("factory-*.jsonl")).read_text()
    assert SECRET not in raw
    assert raw.count("***") == 3


def test_debug_lines_are_dropped_at_info_level(tmp_path: Path) -> None:
    setup_logging(tmp_path, "INFO", "Europe/Berlin", [])

    logging.getLogger("factory.test").debug("full prompt text ...")

    assert read_lines(tmp_path) == []
