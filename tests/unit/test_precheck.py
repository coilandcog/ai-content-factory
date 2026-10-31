"""Script pre-check at Gate 1 (ROADMAP 2.22, D-118): numbers vs verified facts, the Gemini
claim check (cached per text), and approving only with an explicit "approve anyway"."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import httpx
import pytest
from fakes.fake_gemini import FakeGenaiClient, fixture_reply, reply

from factory.config import REPO_ROOT, EnvSettings, load_channel, load_settings
from factory.context import AppContext
from factory.db import add_content, connect, migrate, upsert_channel
from factory.integrations.gemini import GeminiClient
from factory.stages import precheck, review
from factory.stages import script as script_stage
from factory.stages.voice import asset_dir

WIKI = "https://en.wikipedia.org/wiki/Alternator"
MY_NOTE = "As an engineer, I like that the regulator holds 14 volts whether you idle or cruise."
CLEAN = {"unsupported_claims": [], "blocklist_hits": [], "told_too_early": []}


def web(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "w/api.php" in url:
        return httpx.Response(200, json=["alternator", ["Alternator"], [""], [WIKI]])
    if "page/summary" in url:
        body = {"type": "standard", "title": "Alternator", "extract": "An alternator ...",
                "content_urls": {"desktop": {"page": WIKI}}}  # fmt: skip
        return httpx.Response(200, json=body)
    return httpx.Response(200, text="ok")


def llm(*outcomes: object) -> tuple[GeminiClient, FakeGenaiClient]:
    sdk = FakeGenaiClient(*outcomes)
    return GeminiClient("fake", "gemini-3.5-flash-lite", sdk=sdk, retry_wait_s=0), sdk


def claims(**found: list[str]) -> object:
    return reply(json.dumps(CLEAN | found))


@pytest.fixture
def ctx(tmp_path: Path) -> AppContext:
    shutil.copytree(REPO_ROOT / "prompts", tmp_path / "prompts")  # never the real data/ (gotcha)
    conn = connect(tmp_path / "data" / "factory.db")
    migrate(conn)
    channel = load_channel("coil-and-cog").model_copy(
        update={"benchmarks": [], "video_style": "flow"}
    )
    upsert_channel(conn, channel)
    env = EnvSettings(dry_run=True, factory_channel="coil-and-cog",
                      wikimedia_user_agent="Test/0.1 (https://example.org)")  # fmt: skip
    context = AppContext(load_settings(), env, channel, conn, tmp_path)
    yield context
    conn.close()


def scripted_item(ctx: AppContext) -> int:
    content_id = add_content(
        ctx.conn, ctx.channel, object_name="alternator", fmt="short", pillar="vehicles"
    )
    client, _ = llm(fixture_reply("script_short_alternator"))
    script_stage.run(ctx, llm=client, http=httpx.Client(transport=httpx.MockTransport(web)))
    return content_id


def use_claims(monkeypatch: pytest.MonkeyPatch, *outcomes: object) -> FakeGenaiClient:
    client, sdk = llm(*outcomes)
    monkeypatch.setattr(precheck, "claim_client", lambda ctx: client)
    return sdk


# --- code checks --------------------------------------------------------------------------------


def test_blueprint_numbers_must_be_verified_facts_written_as_digits() -> None:
    facts = ["over 1,600 °C", "× 40"]

    assert (
        precheck.number_problems("It burns at over 1,600 °C and squeezes air 40 times.", facts)
        == []
    )
    assert precheck.number_problems("The gas reaches 2,000 °C.", facts) == [
        "number 2,000 is not in the verified facts"
    ]
    assert precheck.number_problems("It spins ten thousand times.", facts) == [
        "numbers written as words can't be checked: ten, thousand"
    ]


def test_a_flow_item_leaves_its_numbers_to_the_claim_check() -> None:
    assert precheck.number_problems("It makes 14 volts at 6,000 rpm.", None) == []


def test_a_blueprint_item_is_checked_against_its_drawing(ctx: AppContext) -> None:
    content_id = scripted_item(ctx)
    ctx.conn.execute(
        "UPDATE contents SET visual_style = 'blueprint', object_name = 'jet engine' WHERE id = ?",
        (content_id,),
    )

    result = precheck.check(ctx, content_id, "The turbine gas reaches 2,000 °C.")

    assert result.problems == ["number 2,000 is not in the verified facts"]
    assert result.notes == ["claim check skipped: no Gemini claim check in unit tests"]


# --- the Gemini claim check ---------------------------------------------------------------------


def test_claims_are_listed_and_the_same_text_is_not_checked_twice(
    ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    content_id = scripted_item(ctx)
    sdk = use_claims(monkeypatch, claims(unsupported_claims=["'single-stage turbine': it has two"]))

    first = precheck.check(ctx, content_id)
    again = precheck.check(ctx, content_id)

    assert first.problems == again.problems == ["claim: 'single-stage turbine': it has two"]
    assert len(sdk.calls) == 1  # the second answer came from script_precheck.json
    cached = json.loads((asset_dir(ctx, content_id) / precheck.PRECHECK_FILE).read_text())
    assert [entry["model"] for entry in cached.values()] == ["gemini-3.5-flash-lite"]
    assert "PRE-CHECK: 1 problem(s)" in first.report()


def test_a_clean_script_reports_ok(ctx: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:
    content_id = scripted_item(ctx)
    use_claims(monkeypatch, claims())

    assert review.precheck_report(ctx, content_id) == "PRE-CHECK: ok"


def test_blocklist_and_series_findings_are_problems(
    ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    content_id = scripted_item(ctx)
    sdk = use_claims(monkeypatch, claims(blocklist_hits=["fuel line removal steps"],
                                         told_too_early=["explains the turbine"]))  # fmt: skip

    result = precheck.check(ctx, content_id)

    assert result.problems == [
        "blocklist: fuel line removal steps",
        "told too early: explains the turbine",
    ]
    prompt = sdk.calls[0]["contents"]
    assert "weapons or firearm mechanisms" in prompt and "not a series, return []" in prompt


def test_without_gemini_the_review_still_shows(ctx: AppContext) -> None:
    content_id = scripted_item(ctx)  # tests/conftest.py: the claim client refuses

    result = precheck.check(ctx, content_id)

    assert result.ok and result.notes == [
        "claim check skipped: no Gemini claim check in unit tests"
    ]
    assert not (asset_dir(ctx, content_id) / precheck.PRECHECK_FILE).exists()  # retried next time


# --- approving ----------------------------------------------------------------------------------


def test_approve_checks_the_text_with_my_note_and_needs_anyway(
    ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    content_id = scripted_item(ctx)
    sdk = use_claims(monkeypatch, claims(unsupported_claims=["'14 volts': about 14.4 V"]))

    with pytest.raises(precheck.PrecheckError, match="1 pre-check problem"):
        review.approve(ctx, content_id, MY_NOTE)

    assert MY_NOTE in sdk.calls[0]["contents"]  # the note is part of the checked narration
    status = ctx.conn.execute("SELECT status FROM contents WHERE id = ?", (content_id,)).fetchone()
    assert status[0] == "scripted"

    review.approve(ctx, content_id, MY_NOTE, anyway=True)  # cached: no second call

    assert len(sdk.calls) == 1
    row = ctx.conn.execute("SELECT decision, reason FROM reviews").fetchone()
    assert tuple(row) == ("approve", "approved anyway: claim: '14 volts': about 14.4 V")


def test_a_clean_approval_has_no_reason(ctx: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:
    content_id = scripted_item(ctx)
    use_claims(monkeypatch, claims())

    review.approve(ctx, content_id, MY_NOTE)

    assert tuple(ctx.conn.execute("SELECT decision, reason FROM reviews").fetchone()) == (
        "approve",
        None,
    )
