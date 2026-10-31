from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fakes.fake_gemini import FakeGenaiClient, reply

from factory.budgets import check_and_record, usage_today
from factory.config import REPO_ROOT, EnvSettings, load_channel, load_settings
from factory.context import AppContext
from factory.db import add_content, connect, migrate, set_status, upsert_channel
from factory.errors import FactoryError, IntegrationError
from factory.integrations import gemini
from factory.integrations.gemini import GeminiClient
from factory.stages import clip_review, generate
from factory.stages.voice import asset_dir

STORY = {"style": "product", "start_picture": "the machine at rest", "continues": False}

LOOK = "A compact car alternator with a ribbed aluminium housing and a steel pulley"
SHOTS = [
    {"n": 1, "beat": "studio", "kind": "flow_clip", "description": "On a dark set",
     "duration_s": 8, "prompt_vars": {"hero_look": LOOK, "scene": "It turns on a dark set",
                                      "camera": "orbit on a 50 mm lens",
                                      **STORY, "end_frame": "pulley"}},
    {"n": 2, "beat": "cutaway", "kind": "diagram", "description": "Labels", "duration_s": 4,
     "prompt_vars": {}, "labels": ["Rotor"], "energy": "electric"},
    {"n": 3, "beat": "flow", "kind": "flow_clip", "description": "Current flows",
     "duration_s": 8, "prompt_vars": {"hero_look": LOOK, "energy": "electric",
                                      "scene": "Seen from the side, current flows left to right",
                                      "camera": "side track",
                                      **STORY, "end_frame": "the terminal"}},
]  # fmt: skip
# images (D-104): shot 1 starts on image 1 and ends on image 2; shot 3 on images 3 and 4
ISSUED = {"1": "VIDEO PROMPT ONE", "3": "VIDEO PROMPT THREE: current flows left to right",
          "image_3": "IMAGE PROMPT THREE: the cutaway seen from the side"}  # fmt: skip
PASS = {"score": 8, "visible_problems": [], "fix": "",
        "expert_notes": ["real CVTs more often use a push-belt than a chain"]}  # fmt: skip
FAIL = {
    "score": 3,
    "visible_problems": ["current flows out of the wrong end"],
    "expert_notes": [],
    "fix": "show it from the side",
}


@pytest.fixture
def ctx(tmp_path: Path) -> AppContext:
    shutil.copytree(REPO_ROOT / "prompts", tmp_path / "prompts")
    conn = connect(tmp_path / "test.db")
    migrate(conn)
    channel = load_channel("coil-and-cog").model_copy(
        update={
            "benchmarks": [],
            "video_prompts": "story",
            "video_style": "flow",
        }  # image chains (D-104)
    )
    upsert_channel(conn, channel)
    env = EnvSettings(dry_run=True, factory_channel="coil-and-cog")
    yield AppContext(load_settings(), env, channel, conn, tmp_path)
    conn.close()


def waiting_item(ctx: AppContext, shots: list[dict] = SHOTS) -> int:
    content_id = add_content(
        ctx.conn, ctx.channel, object_name="alternator", fmt="short", pillar="vehicles"
    )
    with ctx.conn:
        ctx.conn.execute(
            "INSERT INTO scripts (content_id, version, body_text, shot_list_json) "
            "VALUES (?, 1, 'Narration.', ?)",
            (content_id, json.dumps(shots)),
        )
    for status in ("scripted", "script_approved", "awaiting_clips"):
        set_status(ctx.conn, content_id, status)
    folder = asset_dir(ctx, content_id)
    folder.mkdir(parents=True)
    (folder / generate.PROMPTS_FILE).write_text(json.dumps(ISSUED))
    generate.inbox_dir(ctx, content_id).mkdir(parents=True)
    return content_id


def save(ctx: AppContext, content_id: int, name: str, data: bytes = b"fake media") -> None:
    (generate.inbox_dir(ctx, content_id) / name).write_bytes(data)


def fake_llm(ctx: AppContext, *answers: dict) -> tuple[GeminiClient, FakeGenaiClient]:
    sdk = FakeGenaiClient(*(reply(json.dumps(a)) for a in answers))
    llm = GeminiClient(
        "fake", "gemini-3.5-flash-lite", sdk=sdk, retry_wait_s=0,
        before_call=lambda: check_and_record(ctx.conn, ctx.channel, "gemini"),
    )  # fmt: skip
    return llm, sdk


def sent(call: dict) -> tuple[str, str]:
    """(mime type of the file, prompt text) of one recorded Gemini request."""
    part, text = call["contents"]
    return part.inline_data.mime_type, text


def test_frame_and_clip_are_checked_against_their_prompts_and_kept(ctx: AppContext) -> None:
    content_id = waiting_item(ctx)
    save(ctx, content_id, "image_03.png")
    save(ctx, content_id, "shot_03.mp4")
    save(ctx, content_id, "shot_01.mp4")
    # 2 agreeing votes settle a file; shot 3 needs a third (fail, pass, fail)
    llm, sdk = fake_llm(ctx, PASS, PASS, PASS, PASS, FAIL, PASS, FAIL)

    entries = clip_review.review(ctx, content_id, llm=llm)

    assert [(e["file"], e["verdict"]) for e in entries] == [
        ("shot_01.mp4", "pass"), ("image_03.png", "pass"), ("shot_03.mp4", "fail"),
    ]  # fmt: skip  # shot order, a clip's images before the clip
    mime, text = sent(sdk.calls[4])
    assert mime == "video/mp4" and "VIDEO PROMPT THREE" in text
    assert "moves only in the direction the prompt describes" in text  # clip rules
    mime, text = sent(sdk.calls[2])
    assert mime == "image/png" and "IMAGE PROMPT THREE" in text  # the image's own prompt
    assert "not head-on into an opening" in text  # frame rules
    assert entries[2]["problems"] == ["current flows out of the wrong end"]
    assert entries[2]["votes"] == ["fail", "pass", "fail"]
    assert entries[0]["votes"] == ["pass", "pass"] and entries[0]["problems"] == []
    assert entries[0]["expert_notes"] == ["real CVTs more often use a push-belt than a chain"]
    assert "Only these fail the clip" in text  # visible problems vs expert notes (D-109)
    assert usage_today(ctx.conn, ctx.channel)["gemini"][0] == 7
    status = ctx.conn.execute("SELECT status FROM contents").fetchone()[0]
    assert status == "awaiting_clips"  # advice only

    llm, _ = fake_llm(ctx, PASS, PASS)
    clip_review.review(ctx, content_id, only=(1,), llm=llm)

    history = json.loads((asset_dir(ctx, content_id) / "clip_review.json").read_text())
    assert [e["file"] for e in history] == ["shot_01.mp4", "image_03.png", "shot_03.mp4",
                                            "shot_01.mp4"]  # fmt: skip
    assert len(history[0]["sha256"]) == 64 and history[0]["model"] == "gemini-3.5-flash-lite"


def test_clips_already_moved_into_the_project_are_found(ctx: AppContext) -> None:
    content_id = waiting_item(ctx)
    clips = asset_dir(ctx, content_id) / "clips"
    clips.mkdir()
    (clips / "shot_01.mp4").write_bytes(b"clip")
    llm, _ = fake_llm(ctx, PASS, PASS)

    entries = clip_review.review(ctx, content_id, llm=llm)

    assert [e["file"] for e in entries] == ["shot_01.mp4"]


def test_nothing_to_review_names_the_files_to_save(ctx: AppContext) -> None:
    content_id = waiting_item(ctx)
    llm, sdk = fake_llm(ctx)

    with pytest.raises(
        FactoryError, match="save image_01.png / image_02.png / shot_01.mp4, image_03"
    ):
        clip_review.review(ctx, content_id, llm=llm)
    assert sdk.calls == []


def test_unknown_shot_and_old_scripts_are_refused(ctx: AppContext) -> None:
    content_id = waiting_item(ctx)
    old = waiting_item(ctx, [{**SHOTS[0], "prompt_vars": {"hero_feature": "the pulley"}}])
    llm, _ = fake_llm(ctx)

    with pytest.raises(FactoryError, match=r"--shots \[2\] are not flow_clips"):
        clip_review.review(ctx, content_id, only=(2,), llm=llm)
    with pytest.raises(FactoryError, match=f"--restage {old}"):
        clip_review.review(ctx, old, llm=llm)


def test_too_large_file_is_refused_before_any_call(
    ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gemini, "MAX_INLINE_BYTES", 5)
    content_id = waiting_item(ctx)
    save(ctx, content_id, "shot_01.mp4", b"more than five bytes")
    llm, sdk = fake_llm(ctx)

    with pytest.raises(IntegrationError, match="can be sent to Gemini"):
        clip_review.review(ctx, content_id, llm=llm)
    assert sdk.calls == []


def test_frame_images_are_not_reported_as_unexpected_inbox_files(
    ctx: AppContext, caplog: pytest.LogCaptureFixture
) -> None:
    content_id = waiting_item(ctx)
    save(ctx, content_id, "image_01.png")
    save(ctx, content_id, "notes.txt")

    generate.run(ctx, tts=lambda: None)

    warning = next(r.message for r in caplog.records if "unexpected files" in r.message)
    assert "notes.txt" in warning and "image_01.png" not in warning


# --- prompt versions (D-098) ----------------------------------------------------------------------


def test_clip_saved_before_its_prompt_was_reissued_is_flagged(ctx: AppContext) -> None:
    content_id = waiting_item(ctx)
    issued = {"1": {"sha256": "a" * 64, "issued_at": "2000-01-01T00:00:00Z"},
              "3": {"sha256": "b" * 64, "issued_at": "2026-10-27T06:40:00Z"}}  # fmt: skip
    (asset_dir(ctx, content_id) / generate.ISSUED_FILE).write_text(json.dumps(issued))
    save(ctx, content_id, "shot_01.mp4")
    save(ctx, content_id, "shot_03.mp4")
    old = datetime(2026, 10, 26, 12, 0, tzinfo=UTC).timestamp()  # made before the restage
    os.utime(generate.inbox_dir(ctx, content_id) / "shot_03.mp4", (old, old))
    llm, _ = fake_llm(ctx, PASS, PASS, FAIL, FAIL)

    entries = clip_review.review(ctx, content_id, llm=llm)

    assert [(e["file"], e["older_than_prompt"]) for e in entries] == [
        ("shot_01.mp4", False), ("shot_03.mp4", True),
    ]  # fmt: skip
    assert entries[1]["prompt_sha256"] == "b" * 64
    assert entries[1]["file_saved_at"] == "2026-10-26T12:00:00Z"


def test_sheet_without_version_file_is_never_flagged(ctx: AppContext) -> None:
    content_id = waiting_item(ctx)
    save(ctx, content_id, "shot_01.mp4")
    llm, _ = fake_llm(ctx, PASS, PASS)

    entry = clip_review.review(ctx, content_id, only=(1,), llm=llm)[0]

    assert (entry["older_than_prompt"], entry["prompt_issued_at"]) == (False, None)


def test_one_odd_answer_cannot_flip_the_verdict(ctx: AppContext) -> None:
    content_id = waiting_item(ctx)
    save(ctx, content_id, "shot_01.mp4")
    llm, sdk = fake_llm(ctx, FAIL, PASS, PASS)

    entry = clip_review.review(ctx, content_id, only=(1,), llm=llm)[0]

    assert (entry["verdict"], entry["votes"], entry["problems"]) == (
        "pass", ["fail", "pass", "pass"], []
    )  # fmt: skip
    assert len(sdk.calls) == 3


def test_expert_notes_alone_never_fail_a_clip() -> None:
    notes_only = clip_review.ClipVerdict(
        score=6, visible_problems=[], fix="", expert_notes=["a chain instead of a push-belt"]
    )

    assert notes_only.verdict == "pass"
