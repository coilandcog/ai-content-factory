"""Long-video scripts in chapters (ROADMAP 2.29, D-129), with a fake Gemini."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fakes.fake_gemini import FakeGenaiClient, reply
from test_script_stage import (
    SECOND_SOURCE,
    THIRD_SOURCE,
    WIKI_URL,
    http_for,
    llm_for,
    wiki_and_sources,
)

from factory.blender import alternator
from factory.config import REPO_ROOT, EnvSettings, load_channel, load_settings
from factory.context import AppContext
from factory.db import add_content, connect, migrate, set_status, upsert_channel
from factory.stages import episode_script, review
from factory.stages import script as stage
from factory.stages.script import NOTE_LEAD_IN, word_count

PLAN = [  # chapter title, templates of its scenes
    ("What it is", ["whole", "exploded", "rotor", "rotor"]),
    ("The spinning magnet", ["rotor", "brushes", "brushes", "stator"]),
    ("Making current", ["stator", "stator", "rectifier", "rectifier"]),
    ("Why it matters", ["rectifier", "rectifier", "note", "whole"]),
]
FIRST = {  # one sentence per template that names only parts the template shows
    "whole": "The pulley turns with the engine's belt all day.",
    "exploded": "Every part has one job inside the housing.",
    "rotor": "The rotor spins, and its field coil turns it into a magnet.",
    "brushes": "Two carbon brushes press on the slip rings of the rotor.",
    "stator": "The stator windings sit around the spinning rotor.",
    "rectifier": "The 6 diodes let current through in only one direction.",
}
FILLER = "The engine keeps turning, so the battery stays ready for the next start."


def scene_text(template: str, words: int) -> str:
    if template == "note":
        return f"{NOTE_LEAD_IN} the brushes carry only the small current for the field coil."
    text = FIRST[template]
    while word_count(text) + word_count(FILLER) <= words:
        text += " " + FILLER
    return text


def outline_answer(plan: list = PLAN, **change: object) -> str:
    data = {
        "policy": {"allowed": True, "reason": ""},
        "working_title": "Inside the Alternator: How It Works",
        "hook_type": "why_question",
        "chapters": [
            {"title": t, "scenes": [{"template": s, "explains": f"the {s}"} for s in ss]}
            for t, ss in plan
        ],  # fmt: skip
        "sources": [
            {"url": u, "title": "T", "publisher": "P", "claim": "C"}
            for u in (WIKI_URL, SECOND_SOURCE, THIRD_SOURCE)
        ],  # fmt: skip
    }
    return json.dumps({**data, **change})


def chapter_answer(n: int, plan: list = PLAN, hook: str | None = None) -> str:
    templates = plan[n - 1][1]
    share = sum(stage.word_range(load_channel("coil-and-cog"), "episode")) / 2 / 4
    scenes = [scene_text(t, round(share / len(templates))) for t in templates]
    if n == 1:
        scenes[0] = (hook or "Why does your car battery never run flat?") + " " + scenes[0]
    if n == len(plan):
        scenes[-1] = "That is how the engine keeps the battery charged."
        scenes[-2] = scenes[-2]  # the note stays as written
        extra = round(share) - word_count(" ".join(scenes))
        scenes[0] = scene_text("rectifier", word_count(scenes[0]) + extra)
    return json.dumps({"scenes": [{"narration": s} for s in scenes]})


def all_answers() -> list:
    return [reply(outline_answer())] + [reply(chapter_answer(n)) for n in range(1, 5)]


@pytest.fixture
def ctx(tmp_path: Path) -> AppContext:
    conn = connect(tmp_path / "test.db")
    migrate(conn)
    channel = load_channel("coil-and-cog").model_copy(update={"benchmarks": []})
    upsert_channel(conn, channel)
    env = EnvSettings(dry_run=True, factory_channel="coil-and-cog",
                      wikimedia_user_agent="CoilAndCogTest/0.1 (https://example.org)")  # fmt: skip
    yield AppContext(load_settings(), env, channel, conn, REPO_ROOT)
    conn.close()


def episode(ctx: AppContext, style: str = "blender", name: str = "alternator") -> int:
    return add_content(ctx.conn, ctx.channel, object_name=name, fmt="episode",
                       pillar="vehicles", style=style)  # fmt: skip


def run(ctx: AppContext, *answers) -> FakeGenaiClient:  # noqa: ANN002
    sdk = FakeGenaiClient(*answers)
    stage.run(ctx, llm=llm_for(ctx, sdk), http=http_for(wiki_and_sources()))
    return sdk


def current(ctx: AppContext, content_id: int):  # noqa: ANN201
    return ctx.conn.execute(
        "SELECT * FROM scripts WHERE content_id = ? AND is_current = 1", (content_id,)
    ).fetchone()


def status(ctx: AppContext, content_id: int) -> tuple[str, str | None]:
    return tuple(ctx.conn.execute("SELECT status, status_reason FROM contents WHERE id = ?",
                                  (content_id,)).fetchone())  # fmt: skip


def test_a_long_video_is_written_in_four_chapters_of_scenes(ctx: AppContext) -> None:
    content_id = episode(ctx)

    sdk = run(ctx, *all_answers())

    assert status(ctx, content_id)[0] == "scripted"
    assert len(sdk.calls) == 5  # the outline, then one call per chapter
    script = current(ctx, content_id)
    low, high = stage.word_range(ctx.channel, "episode")
    assert low <= script["word_count"] <= high
    plan = json.loads(script["shot_list_json"])
    assert [s["template"] for s in plan][:3] == ["whole", "exploded", "rotor"]
    assert {s["kind"] for s in plan} == {"scene"} and len(plan) == 16
    assert script["engineer_note"].startswith(NOTE_LEAD_IN)
    assert script["loop_line"] == "That is how the engine keeps the battery charged."
    titles = [r[0] for r in ctx.conn.execute(
        "SELECT title FROM chapters WHERE content_id = ? ORDER BY n", (content_id,))]  # fmt: skip
    assert titles == ["What it is", "The spinning magnet", "Making current", "Why it matters"]
    item = ctx.conn.execute("SELECT * FROM contents WHERE id = ?", (content_id,)).fetchone()
    assert (item["hook_type"], item["beat_order"]) == ("why_question", None)
    report = review.script_report(ctx.conn, content_id)
    assert "CHAPTER 2: The spinning magnet" in report and "[brushes] Two carbon brushes" in report
    assert "SHOTS:" not in report


def test_a_sentence_naming_a_part_its_scene_doesnt_show_is_sent_back(ctx: AppContext) -> None:
    content_id = episode(ctx)
    wrong = json.loads(chapter_answer(1))
    wrong["scenes"][2]["narration"] += " Then the diodes turn it into direct current."

    sdk = run(ctx, reply(outline_answer()), reply(json.dumps(wrong)),
              *[reply(chapter_answer(n)) for n in range(1, 5)])  # fmt: skip

    assert status(ctx, content_id)[0] == "scripted"
    asked = sdk.calls[2]["contents"]
    assert "scene 3 [rotor]" in asked and "names the diodes, which this scene doesn't show" in asked
    assert "no earlier scene explained" in asked


def test_an_outline_with_a_view_the_model_cant_show_is_sent_back(ctx: AppContext) -> None:
    content_id = episode(ctx)
    plan = [("What it is", ["whole", "cutaway", "rotor", "rotor"]), *PLAN[1:]]

    sdk = run(ctx, reply(outline_answer(plan)), *all_answers())

    assert status(ctx, content_id)[0] == "scripted"
    assert "uses template 'cutaway', which the model can't show" in sdk.calls[1]["contents"]


@pytest.mark.parametrize(
    ("style", "name", "problem"),
    [
        ("blueprint", "alternator", "long videos are made with Blender"),
        ("blender", "turbocharger", "no Blender model for 'turbocharger'"),
    ],
)
def test_no_gemini_call_without_a_blender_model(
    ctx: AppContext, style: str, name: str, problem: str
) -> None:
    content_id = episode(ctx, style, name)

    sdk = run(ctx)

    assert sdk.calls == []
    state, reason = status(ctx, content_id)
    assert state == "failed" and problem in reason


def test_numbers_must_be_verified_digits_and_the_video_ends_on_a_statement(
    ctx: AppContext,
) -> None:
    content_id = episode(ctx)
    last = json.loads(chapter_answer(4))
    last["scenes"][-1]["narration"] = "So how does it keep twelve volts?"

    sdk = run(ctx, *all_answers()[:4], reply(json.dumps(last)), reply(chapter_answer(4)))

    assert status(ctx, content_id)[0] == "scripted"
    asked_again = sdk.calls[5]["contents"]
    assert "ends on a statement, not a question" in asked_again
    assert "write numbers as digits (twelve)" in asked_again


def test_approving_with_your_note_updates_its_scene_too(ctx: AppContext) -> None:
    content_id = episode(ctx)
    run(ctx, *all_answers())
    note = "As an engineer, I like that the brushes carry only the small field current."

    review.approve(ctx, content_id, note, anyway=True)

    script = current(ctx, content_id)
    assert note in script["body_text"] and script["engineer_note"] == note
    plan = json.loads(script["shot_list_json"])
    assert next(s for s in plan if s["template"] == "note")["narration"] == note


def test_part_names_are_matched_carefully() -> None:
    rotor = "rotor"
    assert episode_script.part_problems(alternator, rotor, "The field windings are copper.") == []
    assert episode_script.part_problems(alternator, rotor, "It feeds the inner winding.") == []
    assert episode_script.part_problems(alternator, "whole", "The engine's belt turns it.") == []
    found = episode_script.part_problems(alternator, rotor, "The stator windings sit around it.")
    assert len(found) == 1 and "names the stator" in found[0]
    assert episode_script.part_problems(alternator, "exploded", "Diodes and brushes.") == []
    # a short reference back to a part an earlier scene explained is fine (D-129)
    later = "The regulator adjusts the rotor's field current."
    assert episode_script.part_problems(alternator, "rectifier", later) != []
    assert episode_script.part_problems(alternator, "rectifier", later,
                                        frozenset({"rotor", "field coil"})) == []  # fmt: skip


def test_a_part_scene_must_name_what_it_shows(ctx: AppContext) -> None:
    content_id = episode(ctx)
    off_topic = json.loads(chapter_answer(1))
    text = scene_text("whole", 70).replace("The pulley turns", "Power arrives")
    off_topic["scenes"][3]["narration"] = text

    sdk = run(ctx, reply(outline_answer()), reply(json.dumps(off_topic)),
              *[reply(chapter_answer(n)) for n in range(1, 5)])  # fmt: skip

    assert status(ctx, content_id)[0] == "scripted"
    assert "scene 4 [rotor] never names what it shows" in sdk.calls[2]["contents"]


def test_long_videos_wait_before_rendering_until_2_30(ctx: AppContext) -> None:
    from fakes.fake_tts import make_fake_tts

    from factory.stages import generate

    content_id = episode(ctx)
    run(ctx, *all_answers())
    set_status(ctx.conn, content_id, "script_approved")

    generate.run(ctx, tts=lambda: make_fake_tts(seconds=2.0))

    assert status(ctx, content_id)[0] == "script_approved"


def test_long_scripts_use_the_long_script_model(ctx: AppContext) -> None:
    from dataclasses import replace

    llm_config = ctx.channel.llm.model_copy(update={"long_script_model": "gemini-3.8-flash"})
    ctx = replace(ctx, channel=ctx.channel.model_copy(update={"llm": llm_config}))
    episode(ctx)

    sdk = run(ctx, *all_answers())

    assert {c["model"] for c in sdk.calls} == {"gemini-3.8-flash"}  # flash-lite can't (D-129)


def test_real_calls_are_spaced_to_the_models_per_minute_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from factory.integrations import gemini

    waits: list[float] = []
    monkeypatch.setattr(gemini.time, "sleep", waits.append)
    monkeypatch.setattr(gemini, "_last_call", {})
    client = gemini.GeminiClient("k", "gemini-3.8-flash", sdk=object())
    client._paced = True  # as for a real client

    client._pace()
    client._pace()

    assert len(waits) == 1 and 12 < waits[0] <= 60 / 5 * 1.05  # 5 requests a minute
