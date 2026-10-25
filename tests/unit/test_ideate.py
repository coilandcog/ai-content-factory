from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
from fakes.fake_gemini import LLM_FIXTURES, FakeGenaiClient, fixture_reply, reply

from factory.config import REPO_ROOT, EnvSettings, load_channel, load_settings
from factory.context import AppContext
from factory.db import add_content, connect, migrate, upsert_channel
from factory.integrations.gemini import GeminiClient
from factory.stages import ideate
from factory.stages import script as script_stage
from factory.stages.discover import TopicSignals

MONDAY = date(2026, 11, 2)


@pytest.fixture
def ctx(tmp_path: Path) -> AppContext:
    conn = connect(tmp_path / "test.db")
    migrate(conn)
    channel = load_channel("coil-and-cog")
    upsert_channel(conn, channel)
    env = EnvSettings(dry_run=True, factory_channel="coil-and-cog",
                      wikimedia_user_agent="CoilAndCogTest/0.1 (https://example.org)")  # fmt: skip
    context = AppContext(load_settings(), env, channel, conn, REPO_ROOT)  # real prompt templates
    yield context
    conn.close()


def llm(*outcomes: object) -> tuple[GeminiClient, FakeGenaiClient]:
    sdk = FakeGenaiClient(*outcomes)
    return GeminiClient("fake", "gemini-3.5-flash-lite", sdk=sdk, retry_wait_s=0), sdk


def items(ctx: AppContext) -> dict[str, dict]:
    rows = ctx.conn.execute("SELECT * FROM contents WHERE virality_score IS NOT NULL").fetchall()
    return {r["object_name"]: dict(r) for r in rows}


def signal(ctx: AppContext, topic: str, metric: str, value: float) -> None:
    ctx.conn.execute(
        """
        INSERT INTO trend_signals (channel_id, source, query, metric_name, metric_value,
            observed_at) VALUES (1, 'wikimedia_pageviews', ?, ?, ?, '2026-10-25T00:00:00Z')
        """,
        (topic, metric, value),
    )
    ctx.conn.commit()


# --- trend points --------------------------------------------------------------------------


def signals(trend: float | None, google: float | None, median: float | None) -> TopicSignals:
    return TopicSignals("x", None, None, trend, median, None, google)


@pytest.mark.parametrize(
    ("given", "points"),
    [
        (signals(None, None, None), 0),
        (signals(0.8, None, 10_000), 0),
        (signals(1.3, None, 1_000_000), 15),
        (signals(1.05, None, 100_000), 8),  # 4.5 rising + 3 demand
        (signals(0.9, 1.3, None), 9),  # the higher ratio counts
        (signals(2.5, None, 50_000_000), 15),  # capped
    ],
)
def test_trend_points(given: TopicSignals, points: int) -> None:
    assert ideate.trend_points(given)[0] == points


def test_no_signals_means_zero() -> None:
    assert ideate.trend_points(None) == (
        0,
        {"rising_ratio": None, "youtube_median": None, "rising_points": 0.0, "demand_points": 0.0},
    )


# --- ideate --------------------------------------------------------------------------------


def test_ideas_are_scored_filtered_and_saved(ctx: AppContext) -> None:
    add_content(ctx.conn, ctx.channel, object_name="alternator", fmt="short", pillar="vehicles")
    signal(ctx, "jet engine", "pageviews_trend_ratio", 1.3)
    client, sdk = llm(fixture_reply("ideate_batch"))

    result = ideate.run(ctx, llm=client)

    saved = items(ctx)
    assert set(saved) == {"jet engine", "turbocharger", "tunnel boring machine", "wind turbine",
                          "helicopter rotor", "escalator", "maglev train"}  # fmt: skip
    assert result.below_threshold == 1  # differential: 62
    assert sorted(result.skipped) == [
        "alternator: already covered",
        "tank autoloader: blocked (Weapon systems are blocklisted.)",
    ]
    jet = saved["jet engine"]
    assert jet["virality_score"] == 77 + 9  # Gemini's 77 + 9 rising points from the signal
    breakdown = json.loads(jet["score_breakdown_json"])
    assert breakdown["trend_signal"] == 9 and breakdown["suggested_hook"] == "reveal"
    assert (jet["format"], jet["pillar"], jet["status"]) == ("short", "air_and_sea", "idea")
    assert jet["angle"].startswith("What really happens")
    linked = ctx.conn.execute("SELECT COUNT(*) FROM content_signals").fetchone()[0]
    assert linked == 1
    prompt = sdk.calls[0]["contents"]
    assert "- alternator\n" in prompt.split("Already covered")[1]  # covered list
    assert "jet engine: Wikipedia trend 1.30" in prompt
    assert "weapons or firearm mechanisms" in sdk.calls[0]["config"].system_instruction


def test_a_bad_mix_is_sent_back_to_gemini(ctx: AppContext) -> None:
    answer = json.loads((LLM_FIXTURES / "ideate_batch.json").read_text())
    shorts_only = {"ideas": [i for i in answer["ideas"] if i["format"] == "short"]}
    client, sdk = llm(reply(json.dumps(shorts_only)), fixture_reply("ideate_batch"))

    ideate.run(ctx, llm=client)

    assert "0 deep_dive ideas" in sdk.calls[1]["contents"]


def test_scores_above_the_weights_are_refused(ctx: AppContext) -> None:
    answer = json.loads((LLM_FIXTURES / "ideate_batch.json").read_text())
    answer["ideas"][0]["scores"]["hook_strength"] = 30
    client, sdk = llm(reply(json.dumps(answer)), fixture_reply("ideate_batch"))

    ideate.run(ctx, llm=client)

    assert "hook_strength 30 (0-25)" in sdk.calls[1]["contents"]


def test_ideate_schema_converts_to_a_gemini_schema(ctx: AppContext) -> None:
    from google import genai
    from google.genai import _transformers

    client = genai.Client(api_key="offline-check")._api_client  # no network call is made
    assert "ideas" in _transformers.t_schema(client, ideate.idea_schema(ctx)).properties


# --- the weekly plan -----------------------------------------------------------------------


def planned(ctx: AppContext) -> list[tuple]:
    return [tuple(r) for r in ctx.conn.execute(
        """
        SELECT object_name, format, pillar, hook_type, beat_order, planned_publish_at
        FROM contents WHERE planned_publish_at IS NOT NULL ORDER BY planned_publish_at
        """
    )]  # fmt: skip


def test_plan_fills_the_week_with_variety(ctx: AppContext) -> None:
    ideate.run(ctx, llm=llm(fixture_reply("ideate_batch"))[0])

    slots = ideate.plan_week(ctx, MONDAY)

    assert [(s.day.isoformat(), s.fmt, s.object_name) for s in slots] == [
        ("2026-11-02", "short", "jet engine"),
        ("2026-11-03", "deep_dive", "helicopter rotor"),
        ("2026-11-04", "short", "turbocharger"),
        ("2026-11-05", "deep_dive", "maglev train"),
        ("2026-11-06", "short", "wind turbine"),
    ]
    rows = planned(ctx)
    hooks = [r[3] for r in rows]
    beats = [r[4] for r in rows]
    assert hooks == ["reveal", "myth_bust", "why_question", "reveal", "surprising_number"]
    assert all(a != b for a, b in zip(hooks, hooks[1:], strict=False))
    assert all(a != b for a, b in zip(beats, beats[1:], strict=False))
    assert rows[0][5] == "2026-11-02T16:00:00Z"  # 17:00 Berlin in winter time


def test_no_pillar_three_days_in_a_row(ctx: AppContext) -> None:
    for name, fmt, score in [("crane a", "short", 90), ("crane b", "deep_dive", 90),
                             ("crane c", "short", 90), ("bus", "short", 70)]:  # fmt: skip
        pillar = "vehicles" if name == "bus" else "industry_and_energy"
        content_id = add_content(ctx.conn, ctx.channel, object_name=name, fmt=fmt, pillar=pillar)
        ctx.conn.execute("UPDATE contents SET virality_score = ? WHERE id = ?", (score, content_id))
    ctx.conn.commit()

    slots = ideate.plan_week(ctx, MONDAY)

    assert [s.object_name for s in slots[:3]] == ["crane a", "crane b", "bus"]
    assert slots[3].content_id is None and "no unplanned deep_dive idea" in slots[3].note


def test_replanning_keeps_the_plan(ctx: AppContext) -> None:
    ideate.run(ctx, llm=llm(fixture_reply("ideate_batch"))[0])
    first = ideate.plan_week(ctx, MONDAY)

    again = ideate.plan_week(ctx, MONDAY)

    assert [s.content_id for s in again] == [s.content_id for s in first]
    assert {s.note for s in again} == {"kept"}


def test_week_start() -> None:
    class Ctx:
        channel = load_channel("coil-and-cog")

    sunday_night = datetime(2026, 11, 1, 22, 30, tzinfo=UTC)  # 23:30 Sunday in Berlin
    assert ideate.week_start(Ctx, "this", sunday_night) == date(2026, 10, 26)
    assert ideate.week_start(Ctx, "next", sunday_night) == date(2026, 11, 2)


# --- what the script stage takes -----------------------------------------------------------


def web(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "w/api.php" in url:
        return httpx.Response(200, json=["x", [], [], []])
    return httpx.Response(200, text="ok")


def test_script_takes_planned_and_manual_items_but_not_waiting_ideas(ctx: AppContext) -> None:
    manual = add_content(ctx.conn, ctx.channel, object_name="alternator", fmt="short",
                         pillar="vehicles")  # fmt: skip
    ideate.run(ctx, llm=llm(fixture_reply("ideate_batch"))[0])
    turbo = items(ctx)["turbocharger"]["id"]
    ctx.conn.execute(
        """
        UPDATE contents SET planned_publish_at = '2026-11-04T16:00:00Z',
            hook_type = 'why_question', beat_order = 'classic' WHERE id = ?
        """,
        (turbo,),
    )
    ctx.conn.commit()

    eligible = [r["id"] for r in script_stage._eligible_ideas(ctx, None)]

    assert eligible == [manual, turbo]


def test_a_planned_item_must_use_the_planned_hook_and_beats(ctx: AppContext) -> None:
    content_id = add_content(ctx.conn, ctx.channel, object_name="alternator", fmt="short",
                             pillar="vehicles")  # fmt: skip
    ctx.conn.execute(
        """
        UPDATE contents SET virality_score = 80, planned_publish_at = '2026-11-04T16:00:00Z',
            hook_type = 'why_question', beat_order = 'classic' WHERE id = ?
        """,
        (content_id,),
    )
    ctx.conn.commit()
    client, sdk = llm(fixture_reply("script_short_alternator"))

    script_stage.run(ctx, llm=client, http=httpx.Client(transport=httpx.MockTransport(web)))

    schema = sdk.calls[0]["config"].response_schema
    assert (schema.allowed_hooks, schema.allowed_beats) == (("why_question",), ("classic",))
    status = ctx.conn.execute("SELECT status FROM contents WHERE id = ?", (content_id,))
    assert status.fetchone()[0] == "scripted"
