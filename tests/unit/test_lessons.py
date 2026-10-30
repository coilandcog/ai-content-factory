from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fakes.fake_gemini import FakeGenaiClient, reply

from factory.budgets import check_and_record
from factory.config import REPO_ROOT, EnvSettings, load_channel, load_settings
from factory.context import AppContext
from factory.db import add_content, connect, migrate, set_status, upsert_channel
from factory.errors import FactoryError
from factory.integrations.gemini import GeminiClient
from factory.stages import clip_review, generate, lessons
from factory.stages.voice import asset_dir

STORY = {"style": "product", "start_picture": "the machine at rest", "continues": False}

LOOK = "A compact car alternator with a ribbed aluminium housing and a steel pulley"
SHOT = {"n": 1, "beat": "studio", "kind": "flow_clip", "description": "On a dark set",
        "duration_s": 8, "prompt_vars": {"hero_look": LOOK, "scene": "It turns on a dark set",
                                         "camera": "orbit",
                                         **STORY, "end_frame": "pulley"}}  # fmt: skip


def review(file: str, verdict: str, when: str, problems: list[str]) -> dict:
    return {"file": file, "verdict": verdict, "reviewed_at": when, "problems": problems,
            "fix": "keep the camera outside", "score": 3, "shot": 1, "kind": "clip"}  # fmt: skip


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


def reviews(ctx: AppContext, content_id: int, entries: list[dict]) -> None:
    folder = asset_dir(ctx, content_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / clip_review.REPORT_FILE).write_text(json.dumps(entries))


def fake_llm(ctx: AppContext, *answers: dict) -> tuple[GeminiClient, FakeGenaiClient]:
    sdk = FakeGenaiClient(*(reply(json.dumps(a)) for a in answers))
    llm = GeminiClient(
        "fake", "gemini-3.5-flash-lite", sdk=sdk, retry_wait_s=0,
        before_call=lambda: check_and_record(ctx.conn, ctx.channel, "gemini"),
    )  # fmt: skip
    return llm, sdk


CAMERA = {"text": "The camera stops at the outer surface and never passes into solid parts",
          "applies_to": "clip", "evidence": [1, 2]}  # fmt: skip
CUTAWAY = {"text": "A cutaway keeps every wall at a believable thickness",
           "applies_to": "frame", "evidence": [3]}  # fmt: skip


def test_failures_from_all_items_become_pending_lessons_once(ctx: AppContext) -> None:
    reviews(ctx, 10, [
        review("shot_04.mp4", "fail", "2026-10-28T16:09:09Z", ["camera pushes into the spinner"]),
        review("frame_02.png", "pass", "2026-10-28T16:07:22Z", []),
        review("frame_03.png", "fail", "2026-10-28T16:08:09Z", ["casing like a half-pipe"]),
    ])  # fmt: skip
    reviews(ctx, 8, [review("shot_01.mp4", "fail", "2026-10-27T10:00:00Z", ["zooms through gear"])])
    # numbered oldest first: 1 = #8 shot_01, 2 = #10 frame_03, 3 = #10 shot_04
    llm, sdk = fake_llm(ctx, {"lessons": [{**CAMERA, "evidence": [9]}]},
                        {"lessons": [{**CAMERA, "evidence": [3, 1]},
                                     {**CUTAWAY, "evidence": [2]}]})  # fmt: skip

    ids = lessons.learn(ctx, llm=llm)

    prompt = sdk.calls[0]["contents"]
    assert prompt.index("#8, shot_01.mp4") < prompt.index("#10, shot_04.mp4")  # oldest first
    assert "frame_02.png" not in prompt  # passes teach nothing here
    assert "evidence must be failure numbers 1-3" in sdk.calls[1]["contents"]  # asked again
    rows = lessons.listing(ctx)
    assert [(r["id"], r["status"], r["applies_to"]) for r in rows] == [
        (ids[0], "pending", "clip"), (ids[1], "pending", "frame"),
    ]  # fmt: skip
    assert json.loads(rows[0]["evidence_json"]) == [
        "000008/shot_01.mp4/2026-10-27T10:00:00Z", "000010/shot_04.mp4/2026-10-28T16:09:09Z",
    ]  # fmt: skip
    assert lessons.approved_for_prompts(ctx) == []  # pending lessons never reach prompts
    with pytest.raises(FactoryError, match="no new failed reviews"):
        lessons.learn(ctx, llm=fake_llm(ctx)[0])


def test_only_new_failures_are_sent_and_known_lessons_are_shown(ctx: AppContext) -> None:
    reviews(ctx, 10, [review("shot_04.mp4", "fail", "2026-10-28T16:00:00Z", ["into spinner"])])
    lessons.learn(ctx, llm=fake_llm(ctx, {"lessons": [{**CAMERA, "evidence": [1]}]})[0])
    lessons.decide(ctx, 1, "removed")
    reviews(ctx, 10, [
        review("shot_04.mp4", "fail", "2026-10-28T16:00:00Z", ["into spinner"]),
        review("shot_02.mp4", "fail", "2026-10-29T09:00:00Z", ["casing cracked"]),
    ])  # fmt: skip
    llm, sdk = fake_llm(ctx, {"lessons": [{**CUTAWAY, "evidence": [1]}]})

    lessons.learn(ctx, llm=llm)

    prompt = sdk.calls[0]["contents"]
    assert "shot_02.mp4" in prompt and "shot_04.mp4" not in prompt
    assert "[removed] The camera stops at the outer surface" in prompt  # not proposed again


def test_approved_lessons_reach_prompts_and_the_matching_reviews(ctx: AppContext) -> None:
    reviews(ctx, 10, [
        review("a.mp4", "fail", "2026-10-28T10:00:00Z", ["x"]),
        review("b.mp4", "fail", "2026-10-28T11:00:00Z", ["y"]),
        review("c.png", "fail", "2026-10-28T12:00:00Z", ["z"]),
    ])  # fmt: skip
    lessons.learn(ctx, llm=fake_llm(ctx, {"lessons": [CAMERA, CUTAWAY]})[0])
    lessons.decide(ctx, 1, "approved")
    lessons.decide(ctx, 2, "approved", "  Cut walls stay as thick as the real part  ")

    assert lessons.approved_for_prompts(ctx) == [
        CAMERA["text"],
        "Cut walls stay as thick as the real part",
    ]
    assert lessons.approved_for_prompts(ctx, "clip") == [CAMERA["text"]]
    assert lessons.approved_for_prompts(ctx, "frame") == [
        "Cut walls stay as thick as the real part"
    ]
    with pytest.raises(FactoryError, match="lesson #9 does not exist"):
        lessons.decide(ctx, 9, "approved")

    content_id = add_content(ctx.conn, ctx.channel, object_name="alternator", fmt="short",
                             pillar="vehicles")  # fmt: skip
    with ctx.conn:
        ctx.conn.execute(
            "INSERT INTO scripts (content_id, version, body_text, shot_list_json) "
            "VALUES (?, 1, 'Narration.', ?)",
            (content_id, json.dumps([SHOT])),
        )
    for status in ("scripted", "script_approved"):
        set_status(ctx.conn, content_id, status)
    plan = {"hero_look": " ".join(["polished alternator"] * 12),
            "shots": [{"n": 1, "scene": "It turns slowly on a dark set", "camera": "orbit",
                       **STORY, "end_frame": "the pulley"}]}  # fmt: skip
    llm, sdk = fake_llm(ctx, plan)
    generate.restage(ctx, content_id, llm=llm)
    assert "Lessons from earlier clips that failed" in sdk.calls[0]["contents"]
    assert CAMERA["text"] in sdk.calls[0]["contents"]

    set_status(ctx.conn, content_id, "awaiting_clips")
    folder = asset_dir(ctx, content_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / generate.PROMPTS_FILE).write_text(json.dumps({"1": "VIDEO PROMPT"}))
    generate.inbox_dir(ctx, content_id).mkdir(parents=True)
    (generate.inbox_dir(ctx, content_id) / "shot_01.mp4").write_bytes(b"clip")
    llm, sdk = fake_llm(ctx, {"verdict": "pass", "score": 8, "problems": [], "fix": ""})
    clip_review.review(ctx, content_id, llm=llm)
    checked = sdk.calls[0]["contents"][1]
    assert CAMERA["text"] in checked and "Cut walls stay" not in checked  # clip lessons only


def test_approved_lessons_are_written_into_the_flow_prompts(ctx: AppContext) -> None:
    reviews(ctx, 10, [review("a.mp4", "fail", "2026-10-28T10:00:00Z", ["x"]),
                      review("b.mp4", "fail", "2026-10-28T11:00:00Z", ["y"]),
                      review("c.png", "fail", "2026-10-28T12:00:00Z", ["z"])])  # fmt: skip
    lessons.learn(ctx, llm=fake_llm(ctx, {"lessons": [CAMERA, CUTAWAY]})[0])
    lessons.decide(ctx, 1, "approved")
    lessons.decide(ctx, 2, "approved")
    content_id = add_content(ctx.conn, ctx.channel, object_name="alternator", fmt="short",
                             pillar="vehicles")  # fmt: skip
    with ctx.conn:
        ctx.conn.execute(
            "INSERT INTO scripts (content_id, version, body_text, shot_list_json) "
            "VALUES (?, 1, 'Narration.', ?)",
            (content_id, json.dumps([SHOT])),
        )
    for status in ("scripted", "script_approved"):
        set_status(ctx.conn, content_id, status)

    generate.run(ctx, tts=lambda: lambda text, voice, speed: None)  # noqa: ARG005

    prompts = json.loads((asset_dir(ctx, content_id) / generate.PROMPTS_FILE).read_text())
    assert f"Rules from earlier clips: {CAMERA['text']}." in prompts["1"]  # Veo reads it (D-108)
    assert CUTAWAY["text"] not in prompts["1"]  # a frame lesson stays out of the video prompt
    assert f"Rules from earlier clips: {CUTAWAY['text']}." in prompts["image_1"]
