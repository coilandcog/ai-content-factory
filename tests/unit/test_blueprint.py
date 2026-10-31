from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from fakes.fake_tts import make_fake_tts
from PIL import Image

from factory import blueprint
from factory.blueprint import canvas, cvt_gearbox, jet_engine
from factory.blueprint.canvas import Fact, Scene, fact_problems, scene_starts
from factory.config import REPO_ROOT, EnvSettings, load_channel, load_settings
from factory.context import AppContext
from factory.db import add_content, connect, migrate, set_status, upsert_channel
from factory.errors import FactoryError
from factory.media.ffmpeg import probe
from factory.stages import generate
from factory.stages.voice import asset_dir

FONTS = REPO_ROOT / "assets_static" / "fonts"
ROLES = ["hook", "body", "body", "body", "note", "close"]


def spoken(*starts: float) -> list[tuple[float, float]]:
    return [(s, s + 0.9) for s in starts]


# --- timing --------------------------------------------------------------------------------------


def test_scenes_start_on_their_sentences() -> None:
    # hook, 3 body sentences, a 2-sentence note, the closing line
    starts = scene_starts(ROLES, spoken(0.4, 3, 6, 9, 12, 15, 18), note_n=2, duration=20)

    assert starts == pytest.approx([0, 2.85, 5.85, 8.85, 11.85, 17.85])


def test_fewer_body_sentences_than_scenes_share_the_time_evenly() -> None:
    starts = scene_starts(ROLES, spoken(0.4, 3, 9, 12), note_n=1, duration=14)

    assert starts == pytest.approx([0, 2.85, 4.85, 6.85, 8.85, 11.85])


def test_a_story_without_hook_note_and_close_is_refused() -> None:
    with pytest.raises(FactoryError, match="hook ... note, close"):
        scene_starts(["body", "close"], spoken(0, 2, 4), note_n=1, duration=6)
    with pytest.raises(FactoryError, match="too few"):
        scene_starts(ROLES, spoken(0, 2), note_n=1, duration=6)


def test_sentences_come_from_the_word_timings() -> None:
    words = [{"text": t, "start_s": i, "end_s": i + 0.5}
             for i, t in enumerate(["Air", "enters.", "Then", "it", "burns!", "Why?"])]  # fmt: skip

    assert canvas.sentences(words) == [(0, 1.5), (2, 4.5), (5, 5.5)]
    assert canvas.count_sentences("As an engineer, it pushes. It never pulls.") == 2


# --- verified facts ------------------------------------------------------------------------------


def test_numbers_on_screen_need_a_verified_fact() -> None:
    facts = {"gas": Fact("over 1,600 °C", "https://en.wikipedia.org/wiki/Turbine_blade")}
    drawn = {"gas around the blade: over 1,600 °C", "melts at 1,300 °C", "ratio 2.14 : 1",
             "no numbers here"}  # fmt: skip

    problems = fact_problems(drawn, facts, live=(r"ratio \d+\.\d\d : 1",))

    assert problems == ["on-screen 'melts at 1,300 °C' shows 1,300 without a verified fact"]
    assert fact_problems(set(), {"x": Fact("5 kg", "a book")}, ()) == [
        "fact 'x' has no source page"
    ]


@pytest.mark.parametrize("module", [jet_engine, cvt_gearbox])
def test_every_scene_of_every_machine_shows_only_verified_numbers(module) -> None:
    starts = [0, 1, 2, 3, 4, 5]
    drawn: set[str] = set()
    frames = canvas.frames(module, starts, 6.0, FONTS, ss=0.25, size=(54, 96), fps=6, drawn=drawn)
    assert sum(1 for _ in frames) == 36
    assert fact_problems(drawn, module.FACTS, module.LIVE) == []
    assert all(f.source.startswith("https://") for f in module.FACTS.values())
    assert any(any(ch.isdigit() for ch in s) for s in drawn)  # numbers really were checked


def ink(img: Image.Image) -> float:
    """Share of bright pixels (line work, flows, text): the navy paper and grid stay dark."""
    hist = img.convert("L").resize((108, 192)).histogram()
    return sum(hist[91:]) / sum(hist)


@pytest.mark.parametrize(("module", "part"), [(jet_engine, None), (jet_engine, 1), (jet_engine, 2),
                                              (jet_engine, 3), (cvt_gearbox, None)])  # fmt: skip
def test_the_first_frame_already_shows_the_machine(
    module, part, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gate 2 checklist: the hook works with the sound off. Measured on the drawing alone (text
    off): the old openings, an engine just starting to draw itself or bare chart axes, had
    0.01-0.05 % ink; a machine drawn and moving from frame 0 has 0.6-1.3 %."""
    monkeypatch.setattr(canvas.Canvas, "text", lambda self, *a, **k: None)
    scenes = canvas.scenes_for(module, part)
    starts = [float(i) for i in range(len(scenes))]
    first = next(canvas.frames(module, starts, len(scenes), FONTS, scenes=scenes, ss=0.5,
                               size=(270, 480), fps=10))  # fmt: skip

    assert ink(Image.frombytes("RGB", (270, 480), first)) > 0.003


# --- the mechanisms are calculated ---------------------------------------------------------------


def test_cvt_belt_keeps_one_length_and_the_small_pulley_spins_faster() -> None:
    lengths, outputs = [], []
    for r1 in (95, 120, 150, 190):
        r2 = cvt_gearbox.r2_for(r1)
        lengths.append(cvt_gearbox.belt_length(r1, r2))
        outputs.append(r2)
    assert max(lengths) - min(lengths) < 0.01
    assert outputs == sorted(outputs, reverse=True)  # input grows -> output shrinks
    st = cvt_gearbox.new_state([0, 1, 2, 3, 4, 5], 6)
    cvt_gearbox.step(st, 0, 0.1)
    assert st["th2"] / st["th1"] == pytest.approx(cvt_gearbox.R_LOW / cvt_gearbox.r2_for(95))


def test_jet_air_only_ever_moves_forward() -> None:
    assert min(jet_engine.core_speed(x) for x in range(-30, 1110, 5)) > 0
    assert min(jet_engine.bypass_speed(x) for x in range(-30, 1110, 5)) > 0
    assert jet_engine.core_speed(1000) > jet_engine.core_speed(100)  # out faster than in
    st = jet_engine.new_state([0, 1, 2, 3, 4, 5], 6)
    before = [p.x for p in st["particles"]]
    jet_engine.step(st, 0, 1 / 30)
    assert all(p.x > b or p.x == -30 for p, b in zip(st["particles"], before, strict=True))


# --- the stage ----------------------------------------------------------------------------------


def tiny_machine(text: str = "a quiet test drawing") -> SimpleNamespace:
    def draw(c, t, st, t0, t1):
        c.text((540, 400), text, "label", canvas.LINE)

    return SimpleNamespace(
        MACHINE="alternator", FACTS={}, LIVE=(), new_state=lambda starts, d: {},
        step=lambda st, t, dt: None, SCENES=[Scene(r, draw) for r in ROLES],
    )  # fmt: skip


@pytest.fixture
def ctx(tmp_path: Path) -> AppContext:
    conn = connect(tmp_path / "test.db")
    migrate(conn)
    channel = load_channel("coil-and-cog")
    upsert_channel(conn, channel)
    shutil.copytree(REPO_ROOT / "assets_static" / "fonts", tmp_path / "assets_static" / "fonts")
    env = EnvSettings(dry_run=True, factory_channel="coil-and-cog")
    yield AppContext(load_settings(), env, channel, conn, tmp_path)
    conn.close()


NARRATION = ("The alternator makes power. A belt turns it. Magnets spin inside. Coils catch the "
             "current. As an engineer, I like its diodes. It never stops charging.")  # fmt: skip


def approved_item(ctx: AppContext, object_name: str = "alternator") -> int:
    content_id = add_content(ctx.conn, ctx.channel, object_name=object_name, fmt="short",
                             pillar="vehicles")  # fmt: skip
    with ctx.conn:
        ctx.conn.execute(
            "INSERT INTO scripts (content_id, version, body_text, engineer_note, shot_list_json) "
            "VALUES (?, 1, ?, 'As an engineer, I like its diodes.', '[]')",
            (content_id, NARRATION),
        )
    set_status(ctx.conn, content_id, "scripted")
    set_status(ctx.conn, content_id, "script_approved")
    return content_id


def status(ctx: AppContext, content_id: int) -> tuple[str, str | None]:
    return ctx.conn.execute(
        "SELECT status, status_reason FROM contents WHERE id = ?", (content_id,)
    ).fetchone()


def fast_render(monkeypatch: pytest.MonkeyPatch) -> None:
    real = generate.render_film
    monkeypatch.setattr(generate, "render_film", lambda *a, **k: real(*a, **k, ss=0.25, fps=10))


def test_new_items_get_the_channel_style_unless_told_otherwise(ctx: AppContext) -> None:
    assert ctx.channel.video_style == "blueprint"  # the channel default (D-111)
    first = add_content(ctx.conn, ctx.channel, object_name="cvt gearbox", fmt="short",
                        pillar="vehicles")  # fmt: skip
    second = add_content(ctx.conn, ctx.channel, object_name="turbo", fmt="short",
                         pillar="vehicles", style="flow")  # fmt: skip

    styles = dict(ctx.conn.execute("SELECT id, visual_style FROM contents").fetchall())
    assert styles == {first: "blueprint", second: "flow"}


def test_blueprint_item_is_drawn_and_generated_in_one_run(
    ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(blueprint.MODULES, "alternator", tiny_machine())
    fast_render(monkeypatch)
    content_id = approved_item(ctx)

    result = generate.run(ctx, tts=lambda: make_fake_tts(seconds=6.0))

    assert (result.processed, result.failed) == (1, 0)
    assert status(ctx, content_id)[0] == "generated"
    folder = asset_dir(ctx, content_id)
    film = probe(folder / generate.BLUEPRINT_FILE)
    video = next(s for s in film["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1080, 1920)
    assert float(film["format"]["duration"]) == pytest.approx(6.0, abs=0.15)
    assert not (folder / "flow_sheet.md").exists()  # no Flow at all
    kinds = {r[0] for r in ctx.conn.execute("SELECT kind FROM assets WHERE content_id = ?",
                                              (content_id,))}  # fmt: skip
    assert {"voiceover", "subtitles", "blueprint"} <= kinds


def test_machine_without_a_drawing_waits_without_any_work(ctx: AppContext) -> None:
    content_id = approved_item(ctx, "tower crane")

    result = generate.run(ctx, tts=lambda: make_fake_tts())

    assert (result.processed, result.skipped, result.failed) == (0, 1, 0)
    assert status(ctx, content_id)[0] == "script_approved"
    assert not (asset_dir(ctx, content_id) / "voice.wav").exists()


def test_film_with_an_unverified_number_is_refused(
    ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(blueprint.MODULES, "alternator", tiny_machine("charges at 99 amps"))
    fast_render(monkeypatch)
    content_id = approved_item(ctx)

    generate.run(ctx, tts=lambda: make_fake_tts(seconds=6.0))

    state, reason = status(ctx, content_id)
    assert state == "failed"
    assert "shows 99 without a verified fact" in reason
    assert not (asset_dir(ctx, content_id) / generate.BLUEPRINT_FILE).exists()


def test_voice_words_drive_the_scene_times(
    ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[float]] = []
    real = canvas.scene_starts
    monkeypatch.setattr(canvas, "scene_starts", lambda *a: seen.append(real(*a)) or seen[-1])
    monkeypatch.setitem(blueprint.MODULES, "alternator", tiny_machine())
    fast_render(monkeypatch)
    content_id = approved_item(ctx)

    generate.run(ctx, tts=lambda: make_fake_tts(seconds=6.0))

    words = json.loads((asset_dir(ctx, content_id) / "voice_words.json").read_text())
    starts = {w["start_s"] for w in words}
    assert seen and all(round(s + 0.15, 3) in {round(x, 3) for x in starts} for s in seen[0][1:])
