from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from fakes.fake_gemini import LLM_FIXTURES, FakeGenaiClient, reply
from typer.testing import CliRunner

from factory.cli import app
from factory.config import REPO_ROOT, EnvSettings, load_channel, load_settings
from factory.context import AppContext
from factory.db import add_content, connect, migrate, set_status, upsert_channel
from factory.errors import FactoryError
from factory.integrations.gemini import GeminiClient
from factory.similarity import recent_scripts
from factory.stages import generate, metadata, series
from factory.stages import script as script_stage

runner = CliRunner()
DRAFT = json.loads((LLM_FIXTURES / "script_short_alternator.json").read_text())


@pytest.fixture
def ctx(tmp_path: Path) -> AppContext:
    conn = connect(tmp_path / "test.db")
    migrate(conn)
    channel = load_channel("coil-and-cog").model_copy(update={"benchmarks": []})
    upsert_channel(conn, channel)
    env = EnvSettings(dry_run=True, factory_channel="coil-and-cog",
                      wikimedia_user_agent="CoilAndCogTest/0.1 (https://example.org)")  # fmt: skip
    shutil.copytree(REPO_ROOT / "prompts", tmp_path / "prompts")  # real templates, own data/
    yield AppContext(load_settings(), env, channel, conn, tmp_path)
    conn.close()


def jet_series(ctx: AppContext, teaser: int | None = None) -> tuple[int, list[int]]:
    return series.create(ctx, "Jet  Engine", parts=3, fmt="short", pillar="air_and_sea",
                         teaser=teaser)  # fmt: skip


def item(ctx: AppContext, content_id: int):  # noqa: ANN201
    return ctx.conn.execute("SELECT * FROM contents WHERE id = ?", (content_id,)).fetchone()


# --- creating a series ---------------------------------------------------------------------------


def test_a_series_is_one_blueprint_idea_per_drawn_part_in_order(ctx: AppContext) -> None:
    series_id, ids = jet_series(ctx)

    rows = [item(ctx, i) for i in ids]
    assert [(r["part_n"], r["series_id"], r["status"], r["visual_style"]) for r in rows] == [
        (1, series_id, "idea", "blueprint"), (2, series_id, "idea", "blueprint"),
        (3, series_id, "idea", "blueprint"),
    ]  # fmt: skip
    assert rows[1]["angle"].startswith("Part 2/3: the core: the compressor squeezes")
    shown = series.show(ctx.conn, series_id)
    assert "jet engine in 3 parts" in shown and f"part 3: #{ids[2]} idea" in shown


@pytest.mark.parametrize(
    ("name", "parts", "message"),
    [("tower crane", 3, "no blueprint drawing for 'tower crane'"),
     ("cvt gearbox", 3, "the cvt gearbox has no parts drawn yet"),
     ("jet engine", 2, "is drawn in 3 parts; ask Claude to draw it in 2 parts"),
     ("jet engine", 6, "--parts must be 2-5")],
)  # fmt: skip
def test_a_series_needs_a_drawing_with_that_many_parts(
    ctx: AppContext, name: str, parts: int, message: str
) -> None:
    with pytest.raises(FactoryError, match=message):
        series.create(ctx, name, parts=parts, fmt="short", pillar="vehicles")
    assert ctx.conn.execute("SELECT COUNT(*) FROM contents").fetchone()[0] == 0


def test_context_knows_the_outline_earlier_parts_and_siblings(ctx: AppContext) -> None:
    teaser = add_content(ctx.conn, ctx.channel, object_name="jet engine", fmt="short",
                         pillar="air_and_sea")  # fmt: skip
    _, (one, two, three) = jet_series(ctx, teaser=teaser)
    with ctx.conn:
        ctx.conn.execute(
            "INSERT INTO scripts (content_id, version, body_text) VALUES (?, 1, 'Part one said.')",
            (one,),
        )

    part = series.context(ctx.conn, two)

    assert (part.part_n, part.parts, part.this.title) == (2, 3, "Compressor and Combustion")
    assert part.next.n == 3 and part.earlier == ["Part one said."]
    assert sorted(part.sibling_ids) == sorted([one, three, teaser])
    assert part.title == "Jet Engine, Part 2/3: Compressor and Combustion"
    assert "× 40" in part.facts
    assert series.context(ctx.conn, teaser) is None  # the teaser is a normal video


def test_series_commands(config_dir: Path) -> None:
    opts = ["--config-dir", str(config_dir)]
    runner.invoke(app, ["db", "init", *opts])

    made = runner.invoke(
        app, ["series", "new", "--object", "jet engine", "--pillar", "air_and_sea", *opts]
    )
    shown = runner.invoke(app, ["series", "show", "1", *opts])
    status = runner.invoke(app, ["status", "2", *opts])
    refused = runner.invoke(app, ["series", "new", "--object", "tower crane", *opts])

    assert made.exit_code == 0, made.output
    assert "Series #1: jet engine in 3 parts" in shown.output
    assert "series #1 part 2" in status.output
    assert refused.exit_code == 1 and "no blueprint drawing" in refused.output


# --- scripts -------------------------------------------------------------------------------------


def no_wikipedia() -> Callable:
    def handler(request: httpx.Request) -> httpx.Response:
        if "w/api.php" in str(request.url):
            return httpx.Response(200, json=["jet engine", [], [], []])
        return httpx.Response(200, text="<html>ok</html>")

    return handler


def script_with(ctx: AppContext, *drafts: dict) -> FakeGenaiClient:
    sdk = FakeGenaiClient(*(reply(json.dumps(d)) for d in drafts))
    llm = GeminiClient("fake-key", "gemini-3.5-flash-lite", sdk=sdk, retry_wait_s=0)
    client = httpx.Client(transport=httpx.MockTransport(no_wikipedia()),
                          headers={"User-Agent": "CoilAndCogTest/0.1"})  # fmt: skip
    script_stage.run(ctx, limit=1, llm=llm, http=client)
    return sdk


def test_a_part_knows_its_series_and_ends_on_a_statement(ctx: AppContext) -> None:
    _, (one, _, _) = jet_series(ctx)
    asking = {**DRAFT, "loop_line": "So where does all that air go?"}
    closing = {**DRAFT, "loop_line": "Next: how the core squeezes and burns it."}

    sdk = script_with(ctx, asking, closing)

    assert item(ctx, one)["status"] == "scripted"
    prompt = sdk.calls[0]["contents"]
    assert "part 1 of 3 of a series about the jet engine" in prompt
    assert "Part 1: Intake, Fan and Bypass" in prompt and "<- THIS VIDEO" in prompt
    assert (
        "names what part 2" in prompt and "Verified facts (the film shows exactly these)" in prompt
    )
    assert "closing statement in a series, not a question" in sdk.calls[1]["contents"]


def test_the_narration_may_only_use_verified_numbers(ctx: AppContext) -> None:
    jet_series(ctx)
    guessed = {**DRAFT, "loop_line": "Next: the core.",
               "body": DRAFT["body"] + " It swallows 2,000 kg of air."}  # fmt: skip
    verified = {**DRAFT, "loop_line": "Next: the core.",
                "body": DRAFT["body"] + " It swallows over 1,000 kg of air."}  # fmt: skip

    sdk = script_with(ctx, guessed, verified)

    assert "says 2,000, which is not in the verified facts" in sdk.calls[1]["contents"]
    assert len(sdk.calls) == 2  # the verified number passes


def test_later_parts_see_earlier_parts_and_are_not_blocked_by_them(ctx: AppContext) -> None:
    _, (one, two, _) = jet_series(ctx)
    closing = {**DRAFT, "loop_line": "Next: the core."}
    script_with(ctx, closing)
    first = next(x for x in DRAFT["shots"] if x["beat"] == "in_situ")  # context first:
    shots = [first, *(x for x in DRAFT["shots"] if x is not first)]  # a new beat order (variety)
    shots = [{**x, "n": n} for n, x in enumerate(shots, 1)]
    varied = {**closing, "hook_type": "reveal", "beat_order": "context_first", "shots": shots}

    sdk = script_with(ctx, varied)  # the same words as part 1: only a sibling, so not blocked

    assert item(ctx, two)["status"] == "scripted"
    assert DRAFT["body"][:40] in sdk.calls[0]["contents"]  # part 1's narration, not to repeat
    channel_id = item(ctx, one)["channel_id"]
    assert recent_scripts(ctx.conn, channel_id, two, 30, exclude=[one]) == []
    assert len(recent_scripts(ctx.conn, channel_id, two, 30)) == 1


# --- film and metadata ---------------------------------------------------------------------------


def test_a_part_film_draws_only_that_part(
    ctx: AppContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, (_, two, _) = jet_series(ctx)
    folder = generate.asset_dir(ctx, two)
    folder.mkdir(parents=True)
    (folder / "voice.wav").write_bytes(b"")
    (folder / "voice_words.json").write_text("[]")
    seen = {}

    def fake_render(module, words, note_n, duration, fonts, out, **kw):  # noqa: ANN001, ANN202
        seen.update(kw)
        out.write_bytes(b"film")
        return []

    monkeypatch.setattr(generate, "render_film", fake_render)
    monkeypatch.setattr(generate, "probe", lambda p: {"format": {"duration": "30"}})
    with ctx.conn:
        ctx.conn.execute(
            "INSERT INTO scripts (content_id, version, body_text, engineer_note) "
            "VALUES (?, 1, 'x', 'As an engineer, x.')",
            (two,),
        )
    for status in ("scripted", "script_approved", "awaiting_clips"):
        set_status(ctx.conn, two, status)

    generate._draw(ctx, item(ctx, two))

    assert seen == {"part_n": 2}
    assert item(ctx, two)["status"] == "generated"


def test_part_titles_count_and_link_the_parts_already_posted(ctx: AppContext) -> None:
    teaser = add_content(ctx.conn, ctx.channel, object_name="jet engine", fmt="short",
                         pillar="air_and_sea")  # fmt: skip
    _, (one, two, _) = jet_series(ctx, teaser=teaser)
    with ctx.conn:
        ctx.conn.execute(
            "INSERT OR IGNORE INTO platform_accounts (channel_id, platform, handle, publish_mode) "
            "VALUES (1, 'youtube', '@coilandcog', 'manual')"
        )
        for cid, vid in ((teaser, "aaa"), (one, "bbb")):
            ctx.conn.execute(
                "INSERT INTO posts (content_id, platform_account_id, platform, status, url, "
                "ai_disclosure, dry_run) SELECT ?, id, 'youtube', 'published', ?, 1, 0 "
                "FROM platform_accounts WHERE platform = 'youtube'",
                (cid, f"https://youtube.com/shorts/{vid}"),
            )
    draft = metadata.PostMetadata(
        youtube_title="Whatever Gemini wrote", youtube_summary="How the core squeezes and burns.",
        youtube_hashtags=["#JetEngine", "#Aviation", "#Engineering"],
        tiktok_caption="Squeezed 40 times", tiktok_hashtags=["#HowItWorks", "#Jet", "#Planes"],
    )  # fmt: skip

    data = metadata.build(ctx, item(ctx, two), draft, [], None)

    assert data["youtube"]["title"] == "Jet Engine, Part 2/3: Compressor and Combustion"
    assert data["youtube"]["description"].split("\n")[:5] == [
        "How the core squeezes and burns.", "", "Part 2 of 3.",
        "Overview: https://youtube.com/shorts/aaa", "Part 1: https://youtube.com/shorts/bbb",
    ]  # fmt: skip
    assert data["tiktok"]["caption"].startswith("Part 2/3: Squeezed 40 times")
