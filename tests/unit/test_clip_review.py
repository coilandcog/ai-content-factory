from __future__ import annotations

import json
import shutil
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

LOOK = "A compact car alternator with a ribbed aluminium housing and a steel pulley"
SHOTS = [
    {"n": 1, "beat": "studio", "kind": "flow_clip", "description": "On a dark set",
     "duration_s": 8, "prompt_vars": {"hero_look": LOOK, "scene": "It turns on a dark set",
                                      "camera": "orbit on a 50 mm lens", "end_frame": "pulley"}},
    {"n": 2, "beat": "cutaway", "kind": "diagram", "description": "Labels", "duration_s": 4,
     "prompt_vars": {}, "labels": ["Rotor"], "energy": "electric"},
    {"n": 3, "beat": "flow", "kind": "flow_clip", "description": "Current flows",
     "duration_s": 8, "prompt_vars": {"hero_look": LOOK, "energy": "electric",
                                      "scene": "Seen from the side, current flows left to right",
                                      "camera": "side track", "end_frame": "the terminal"}},
]  # fmt: skip
ISSUED = {"1": "VIDEO PROMPT ONE", "3": "VIDEO PROMPT THREE: current flows left to right"}
PASS = {"verdict": "pass", "score": 8, "problems": [], "fix": ""}
FAIL = {
    "verdict": "fail",
    "score": 3,
    "problems": ["current flows out of the wrong end"],
    "fix": "show it from the side",
}


@pytest.fixture
def ctx(tmp_path: Path) -> AppContext:
    shutil.copytree(REPO_ROOT / "prompts", tmp_path / "prompts")
    conn = connect(tmp_path / "test.db")
    migrate(conn)
    channel = load_channel("coil-and-cog").model_copy(update={"benchmarks": []})
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
    save(ctx, content_id, "frame_03.png")
    save(ctx, content_id, "shot_03.mp4")
    save(ctx, content_id, "shot_01.mp4")
    llm, sdk = fake_llm(ctx, PASS, PASS, FAIL)

    entries = clip_review.review(ctx, content_id, llm=llm)

    assert [(e["file"], e["verdict"]) for e in entries] == [
        ("shot_01.mp4", "pass"), ("frame_03.png", "pass"), ("shot_03.mp4", "fail"),
    ]  # fmt: skip  # shot order, the frame before its clip
    mime, text = sent(sdk.calls[2])
    assert mime == "video/mp4" and "VIDEO PROMPT THREE" in text
    assert "moves only in the direction the prompt describes" in text  # clip rules
    mime, text = sent(sdk.calls[1])
    assert mime == "image/png" and "opening frame image" in text
    assert "Framing: the first camera angle of this shot: side track" in text  # frame prompt
    assert "not head-on into an opening" in text  # frame rules
    assert entries[2]["problems"] == ["current flows out of the wrong end"]
    assert usage_today(ctx.conn, ctx.channel)["gemini"][0] == 3
    status = ctx.conn.execute("SELECT status FROM contents").fetchone()[0]
    assert status == "awaiting_clips"  # advice only

    llm, _ = fake_llm(ctx, PASS)
    clip_review.review(ctx, content_id, only=(1,), llm=llm)

    history = json.loads((asset_dir(ctx, content_id) / "clip_review.json").read_text())
    assert [e["file"] for e in history] == ["shot_01.mp4", "frame_03.png", "shot_03.mp4",
                                            "shot_01.mp4"]  # fmt: skip
    assert len(history[0]["sha256"]) == 64 and history[0]["model"] == "gemini-3.5-flash-lite"


def test_clips_already_moved_into_the_project_are_found(ctx: AppContext) -> None:
    content_id = waiting_item(ctx)
    clips = asset_dir(ctx, content_id) / "clips"
    clips.mkdir()
    (clips / "shot_01.mp4").write_bytes(b"clip")
    llm, _ = fake_llm(ctx, PASS)

    entries = clip_review.review(ctx, content_id, llm=llm)

    assert [e["file"] for e in entries] == ["shot_01.mp4"]


def test_nothing_to_review_names_the_files_to_save(ctx: AppContext) -> None:
    content_id = waiting_item(ctx)
    llm, sdk = fake_llm(ctx)

    with pytest.raises(FactoryError, match="save frame_01.png / shot_01.mp4, frame_03.png"):
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
    save(ctx, content_id, "frame_01.png")
    save(ctx, content_id, "notes.txt")

    generate.run(ctx, tts=lambda: None)

    warning = next(r.message for r in caplog.records if "unexpected files" in r.message)
    assert "notes.txt" in warning and "frame_01.png" not in warning
