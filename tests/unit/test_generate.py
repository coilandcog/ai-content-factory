from __future__ import annotations

import dataclasses
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fakes.fake_gemini import FakeGenaiClient, reply
from fakes.fake_tts import make_fake_tts
from PIL import Image
from typer.testing import CliRunner

from factory.budgets import check_and_record
from factory.cli import app
from factory.config import REPO_ROOT, Benchmark, EnvSettings, load_channel, load_settings
from factory.context import AppContext
from factory.db import add_content, connect, migrate, set_status, upsert_channel
from factory.errors import BudgetExceededError, FactoryError
from factory.integrations.gemini import GeminiClient
from factory.media.ffmpeg import probe
from factory.stages import generate, voice

STORY = {"style": "product", "start_picture": "the machine at rest", "continues": False}

pytestmark = pytest.mark.slow  # runs ffmpeg

LOOK = "A compact car alternator with a ribbed aluminium housing and a steel pulley"
FILM = {"hero_look": LOOK}
# A story (D-104): 1 product -> 2 open (continues on 1's end) -> 4 explain (new view) ->
# 5 installed (continues on 4's end). Images: 1 -> 2 (shots 1, 2) -> 3; 4 -> 5 (shots 4, 5) -> 6.
SHOTS = [
    {"n": 1, "beat": "studio", "kind": "flow_clip", "description": "Alternator on a dark set",
     "duration_s": 6, "prompt_vars": {**FILM, "scene": "The alternator turns as its pulley spins",
                                      "camera": "slow orbit to the right on a 50 mm lens",
                                      "style": "product", "continues": False,
                                      "start_picture": "the closed alternator on a dark set",
                                      "end_frame": "the front face with the pulley spinning."}},
    {"n": 2, "beat": "cutaway", "kind": "flow_clip", "description": "Casing opens",
     "duration_s": 7, "prompt_vars": {**FILM, "scene": "The housing splits and the rotor spins",
                                      "camera": "slow dolly-in", "style": "open",
                                      "continues": True, "start_picture": "ignored",
                                      "end_frame": "the rotor"}},
    {"n": 3, "beat": "cutaway", "kind": "diagram", "description": "Labels", "duration_s": 4,
     "prompt_vars": {}, "labels": ["rotor", "stator coils"], "energy": "electric"},
    {"n": 4, "beat": "flow", "kind": "flow_clip", "description": "Current flows",
     "duration_s": 8, "prompt_vars": {**FILM, "energy": "electric",
                                      "scene": "Current pulses from the stator coils",
                                      "camera": "steady push", "style": "explain",
                                      "continues": False,
                                      "start_picture": "the opened alternator standing upright",
                                      "end_frame": "the output terminal"}},
    {"n": 5, "beat": "in_situ", "kind": "flow_clip", "description": "Engine bay",
     "duration_s": 6, "prompt_vars": {**FILM, "scene": "In a car engine bay the belt drives it",
                                      "camera": "crane down", "style": "installed",
                                      "continues": True, "start_picture": "ignored",
                                      "end_frame": "the pulley spinning"}},
    {"n": 6, "beat": "studio", "kind": "still", "description": "Hero view of the alternator",
     "duration_s": 2, "prompt_vars": {}},
]  # fmt: skip
CLIPS = [1, 2, 4, 5]


fake_tts = make_fake_tts(seconds=1.5, amplitude=0.1)


def make_clip(path: Path, size: str = "108x192", seconds: float = 3.5) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", f"testsrc=size={size}:rate=10:duration={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )  # fmt: skip


@pytest.fixture
def ctx(tmp_path: Path) -> AppContext:
    shutil.copytree(REPO_ROOT / "prompts", tmp_path / "prompts")
    conn = connect(tmp_path / "test.db")
    migrate(conn)
    # these tests cover story shots (D-104); brief prompts (the default, D-105) have their own
    channel = load_channel("coil-and-cog").model_copy(
        update={"video_prompts": "story", "video_style": "flow"}  # Flow clips (blueprint: D-111)
    )
    upsert_channel(conn, channel)
    env = EnvSettings(dry_run=True, factory_channel="coil-and-cog")
    # outputs go under tmp_path; subtitles need the real font files there too
    shutil.copytree(REPO_ROOT / "assets_static" / "fonts", tmp_path / "assets_static" / "fonts")
    yield AppContext(load_settings(), env, channel, conn, tmp_path)
    conn.close()


def approved_item(ctx: AppContext) -> int:
    content_id = add_content(
        ctx.conn, ctx.channel, object_name="alternator", fmt="short", pillar="vehicles"
    )
    with ctx.conn:
        ctx.conn.execute(
            "INSERT INTO scripts (content_id, version, body_text, est_duration_s, shot_list_json) "
            "VALUES (?, 1, 'Hello there, this is the alternator.', 40, ?)",
            (content_id, json.dumps(SHOTS)),
        )
        ctx.conn.execute(
            "UPDATE contents SET working_title = 'Alternator in 40 seconds' WHERE id = ?",
            (content_id,),
        )
    set_status(ctx.conn, content_id, "scripted")
    set_status(ctx.conn, content_id, "script_approved")
    return content_id


def run(ctx: AppContext) -> generate.StageResult:
    return generate.run(ctx, tts=lambda: fake_tts)


def status(ctx: AppContext, content_id: int) -> str:
    return ctx.conn.execute("SELECT status FROM contents WHERE id = ?", (content_id,)).fetchone()[0]


def assets(ctx: AppContext, kind: str) -> list:
    return ctx.conn.execute(
        "SELECT * FROM assets WHERE kind = ? ORDER BY shot_n", (kind,)
    ).fetchall()


def test_approved_script_gets_flow_sheet_inbox_and_voice(ctx: AppContext) -> None:
    content_id = approved_item(ctx)

    result = run(ctx)

    assert (result.processed, status(ctx, content_id)) == (1, "awaiting_clips")
    folder = ctx.root / "data" / "assets" / "coil-and-cog" / f"{content_id:06d}"
    sheet = (folder / "flow_sheet.md").read_text()
    assert "4 × Veo 3.1 - Fast, **80 credits**" in sheet
    assert "you have 50 of 50 left today" in sheet
    assert "make 2 clip(s) today and the rest on the next day(s)" in sheet
    assert "Save as: `shot_01.mp4`" in sheet and "Save as: `shot_03.mp4`" not in sheet  # diagram
    assert "No logos, no text" in sheet and "no extra or distorted components" in sheet
    assert "\\\\wsl$\\" in sheet and "\\data\\inbox\\flow\\000001" in sheet
    prompts = json.loads((folder / "flow_prompts.json").read_text())
    # D-092/D-104: every clip is a story shot of the same machine, between two images
    assert all(f"The machine: {LOOK}." in prompts[str(n)] for n in CLIPS)
    assert "cinematic short film that shows how it works inside: alternator." in prompts["1"]
    assert "The alternator turns as its pulley spins." in prompts["1"]
    assert "Camera, three smooth angles: slow orbit to the right on a 50 mm lens." in prompts["1"]
    assert "starts exactly on the start image: the closed alternator on a dark set." in prompts["1"]
    assert (
        "ends exactly on the end image: the front face with the pulley spinning.\n" in prompts["1"]
    )
    assert "Camera: almost still: one slow, small move while the casing opens." in prompts["2"]
    # shot 2 continues: its video starts on shot 1's end picture, not on its own (unused) text
    assert "start image: the front face with the pulley spinning." in prompts["2"]
    assert "Camera: locked off and completely still, like a tripod." in prompts["4"]
    assert "stands upright as a clean vertical cutaway" in prompts["4"]  # explain only
    assert "vertical cutaway" not in prompts["2"]
    assert "Camera, two or three gentle smooth angles: crane down." in prompts["5"]
    assert "nothing appears, disappears, separates, floats or changes shape" in prompts["5"]
    assert "Glowing cyan light traces show the electric current" in prompts["4"]
    assert "Glowing" not in prompts["5"]
    assert "cool cyan light on one side and a warm amber light" in prompts["5"]  # signature
    assert "smooth polished metal" in prompts["5"]  # the premium product look of #5
    assert "motion has already begun in the very first frame" in prompts["1"]  # D-090: shot 1
    assert "already begun" not in prompts["5"]
    # the image chain: shots that continue reuse the previous end image
    assert sorted(k for k in prompts if k.startswith("image_")) == [
        f"image_{i}" for i in range(1, 7)
    ]
    assert "the front face with the pulley spinning" in prompts["image_2"]
    assert "the opened alternator standing upright" in prompts["image_4"]
    assert "stands upright as a clean vertical cutaway" in prompts["image_4"]
    assert "Start image: `image_02.png` | End image: `image_03.png`" in sheet  # shot 2
    assert "Start image: `image_04.png` | End image: `image_05.png`" in sheet  # shot 4
    assert "### Image 02 (end of shot 1, start of shot 2)" in sheet
    assert (ctx.root / "data" / "inbox" / "flow" / "000001").is_dir()
    assert (folder / "voice.wav").is_file()
    assert len(assets(ctx, "flow_sheet")) == 1 and len(assets(ctx, "voiceover")) == 1
    assert "Images cost **0 credits** in Flow, so make them" in sheet
    assert "Save as: `still_06.png`" in sheet
    # D-095/D-104: the free images come first, then the 20-credit videos
    assert "**Images first (0 credits):**" in sheet and "Frames to video" in sheet
    assert sheet.count("Save as: `image_") == 6
    assert "The machine fills at least half of the frame." in sheet
    assert "product image of the alternator, vertical 9:16.\nHero view" in prompts["6"]


def test_script_without_diagram_labels_is_refused_before_any_flow_work(ctx: AppContext) -> None:
    content_id = approved_item(ctx)
    old = [{**s, "labels": []} if s["kind"] == "diagram" else s for s in SHOTS]
    with ctx.conn:
        ctx.conn.execute("UPDATE scripts SET shot_list_json = ?", (json.dumps(old),))

    result = run(ctx)

    assert (result.failed, status(ctx, content_id)) == (1, "failed")
    reason = ctx.conn.execute("SELECT status_reason FROM contents").fetchone()[0]
    assert "have no labels" in reason and "redo the script" in reason


def test_missing_or_bad_clips_keep_the_item_waiting(ctx: AppContext) -> None:
    content_id = approved_item(ctx)
    run(ctx)
    inbox = generate.inbox_dir(ctx, content_id)
    make_clip(inbox / "shot_01.mp4")
    make_clip(inbox / "shot_02.mp4", size="192x108")  # landscape
    (inbox / "shot_04.mp4").write_text("not a video")

    result = run(ctx)

    assert (result.processed, result.skipped) == (0, 1)
    assert status(ctx, content_id) == "awaiting_clips"
    assert assets(ctx, "flow_clip") == []


def test_complete_clip_set_is_collected(ctx: AppContext) -> None:
    content_id = approved_item(ctx)
    run(ctx)
    inbox = generate.inbox_dir(ctx, content_id)
    for n in CLIPS:
        make_clip(inbox / f"shot_{n:02d}.mp4")
    (inbox / "notes.txt").write_text("ignored")

    result = run(ctx)

    assert (result.processed, status(ctx, content_id)) == (1, "generated")
    rows = assets(ctx, "flow_clip")
    assert [r["shot_n"] for r in rows] == CLIPS
    first = rows[0]
    assert (first["generator"], first["model"], first["license"]) == (
        "google_flow",
        "Veo 3.1 - Fast",
        "google_flow_output",
    )
    assert (first["width"], first["height"]) == (108, 192)
    assert first["path"].endswith(f"{content_id:06d}/clips/shot_01.mp4")
    assert "The alternator turns as its pulley spins." in first["prompt"]
    assert (inbox / "shot_01.mp4").is_file()  # the original download stays in the inbox
    folder = ctx.root / "data" / "assets" / "coil-and-cog" / f"{content_id:06d}"
    [still] = assets(ctx, "still")
    assert (still["generator"], still["model"]) == ("ffmpeg", "frame of shot_01")  # same beat
    [anim] = assets(ctx, "still_anim")
    info = probe(folder / "stills" / "still_06_anim.mp4")
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"], video["r_frame_rate"]) == (1080, 1920, "30/1")
    assert float(info["format"]["duration"]) == pytest.approx(2.0, abs=0.1)
    [diagram] = assets(ctx, "diagram")
    assert (diagram["shot_n"], diagram["generator"], diagram["license"]) == (
        3,
        "pillow",
        "own_work",
    )
    assert (folder / "diagrams" / "diag_03_preview.jpg").is_file()
    assert run(ctx).processed == 0  # nothing left to do: safe to re-run


def test_owner_still_in_the_inbox_is_used(ctx: AppContext) -> None:
    content_id = approved_item(ctx)
    run(ctx)
    inbox = generate.inbox_dir(ctx, content_id)
    for n in CLIPS:
        make_clip(inbox / f"shot_{n:02d}.mp4")
    Image.new("RGB", (900, 1600), "#334455").save(inbox / "still_06.jpg")

    run(ctx)

    [still] = assets(ctx, "still")
    assert still["generator"] == "google_flow"
    assert "Hero view" in still["prompt"]


def test_flow_log_respects_the_daily_budget(ctx: AppContext) -> None:
    assert generate.flow_log(ctx, 20) == (20, 50)
    assert generate.flow_log(ctx, 20) == (40, 50)

    with pytest.raises(BudgetExceededError):
        generate.flow_log(ctx, 20)


def test_flow_log_command(config_dir: Path) -> None:
    runner = CliRunner()
    runner.invoke(app, ["db", "init", "--config-dir", str(config_dir)])

    ok = runner.invoke(app, ["flow-log", "--credits", "20", "--config-dir", str(config_dir)])
    over = runner.invoke(app, ["flow-log", "--credits", "40", "--config-dir", str(config_dir)])

    assert ok.exit_code == 0 and "Flow credits today: 20 of 50 (30 left)" in ok.output
    assert over.exit_code == 1 and "would exceed today's limit" in over.output


# --- film shots and restage (D-092) ---------------------------------------------------------------

OLD_SHOTS = [
    {**s, "prompt_vars": {"energy": "electric", "path": "the coils"}}
    if s["beat"] == "flow" and s["kind"] == "flow_clip"
    else {**s, "prompt_vars": {"context": "an engine bay"}}
    if s["kind"] == "flow_clip"
    else s
    for s in SHOTS
]
VISUALS = {
    "hero_look": " ".join(["A ribbed aluminium car alternator with a steel pulley"] * 3),
    "shots": [
        {"n": n, "scene": f"Shot {n}: the rotor spins inside the stator windings",
         "camera": "slow dolly-in on a 35 mm lens", **STORY, "end_frame": f"frame {n}"}
        for n in CLIPS
    ],
}  # fmt: skip


def old_script(ctx: AppContext) -> int:
    content_id = approved_item(ctx)
    with ctx.conn:
        ctx.conn.execute("UPDATE scripts SET shot_list_json = ?", (json.dumps(OLD_SHOTS),))
    return content_id


def fake_llm(ctx: AppContext, *answers: str) -> tuple[GeminiClient, FakeGenaiClient]:
    sdk = FakeGenaiClient(*(reply(a) for a in answers))
    llm = GeminiClient(
        "fake-key", "gemini-3.5-flash-lite", sdk=sdk, retry_wait_s=0,
        before_call=lambda: check_and_record(ctx.conn, ctx.channel, "gemini"),
    )  # fmt: skip
    return llm, sdk


def test_script_made_before_film_shots_is_refused_with_the_restage_hint(ctx: AppContext) -> None:
    content_id = old_script(ctx)

    result = run(ctx)

    assert (result.failed, status(ctx, content_id)) == (1, "failed")
    reason = ctx.conn.execute("SELECT status_reason FROM contents").fetchone()[0]
    assert f"factory generate --restage {content_id}" in reason


def test_restage_gives_a_waiting_item_new_film_shots_and_flow_sheet(ctx: AppContext) -> None:
    content_id = approved_item(ctx)
    run(ctx)  # awaiting_clips with a first sheet
    with ctx.conn:
        ctx.conn.execute("UPDATE scripts SET shot_list_json = ?", (json.dumps(OLD_SHOTS),))
    wrong = {**VISUALS, "shots": VISUALS["shots"][:2]}
    llm, sdk = fake_llm(ctx, json.dumps(wrong), json.dumps(VISUALS))

    sheet = generate.restage(ctx, content_id, llm=llm)

    assert status(ctx, content_id) == "awaiting_clips"  # narration, voice and status stay
    assert "give exactly the flow_clips [1, 2, 4, 5]" in sdk.calls[1]["contents"]
    assert "Hello there, this is the alternator." in sdk.calls[0]["contents"]  # the narration
    shots = json.loads(ctx.conn.execute("SELECT shot_list_json FROM scripts").fetchone()[0])
    assert shots[3]["prompt_vars"]["energy"] == "electric"  # the flow beat keeps its colour
    assert "path" not in shots[3]["prompt_vars"] and "context" not in shots[4]["prompt_vars"]
    assert shots[2] == SHOTS[2]  # diagrams are untouched
    prompts = json.loads((sheet.parent / "flow_prompts.json").read_text())
    assert "Shot 2: the rotor spins" in prompts["2"]
    assert "starts exactly on the start image: the machine at rest." in prompts["2"]
    assert "Shot 2: the rotor spins" in sheet.read_text()


def test_restage_of_an_approved_script_waits_for_generate(ctx: AppContext) -> None:
    content_id = old_script(ctx)
    llm, _ = fake_llm(ctx, json.dumps(VISUALS))

    assert generate.restage(ctx, content_id, llm=llm) is None

    assert run(ctx).processed == 1 and status(ctx, content_id) == "awaiting_clips"


def test_restage_refuses_an_item_that_already_has_its_clips(ctx: AppContext) -> None:
    content_id = approved_item(ctx)
    set_status(ctx.conn, content_id, "awaiting_clips")
    set_status(ctx.conn, content_id, "generated")
    llm, sdk = fake_llm(ctx)

    with pytest.raises(FactoryError, match="already has its clips: name the clips to redo"):
        generate.restage(ctx, content_id, llm=llm)
    assert sdk.calls == []


# --- benchmarks (D-094) --------------------------------------------------------------------------


def test_restage_shows_gemini_the_benchmark_prompts_and_notes(ctx: AppContext) -> None:
    bench = Benchmark(content_id=5, name="alternator", good_shots=[1, 3], liked="the copper coils",
                      improve="the rotor was blurry")  # fmt: skip
    missing = Benchmark(content_id=6, name="turbocharger", good_shots=[1], liked="clean look")
    ctx = dataclasses.replace(
        ctx, channel=ctx.channel.model_copy(update={"benchmarks": [bench, missing]})
    )
    folder = voice.asset_dir(ctx, 5)
    folder.mkdir(parents=True)
    issued = {"1": "Create a premium 8-second video of the alternator", "2": "skip me",
              "3": "Glowing cyan current surges\nthrough the stator"}  # fmt: skip
    (folder / "flow_prompts.json").write_text(json.dumps(issued))
    content_id = old_script(ctx)
    llm, sdk = fake_llm(ctx, json.dumps(VISUALS))

    generate.restage(ctx, content_id, llm=llm)

    prompt = sdk.calls[0]["contents"]
    assert "Benchmarks: the owner's best videos so far" in prompt
    assert "- alternator. The owner liked: the copper coils. Do better than it: the rotor" in prompt
    assert "Create a premium 8-second video of the alternator" in prompt
    assert "Glowing cyan current surges\n  through the stator" in prompt  # indented example
    assert "skip me" not in prompt  # only the good shots are examples
    assert "- turbocharger. The owner liked: clean look." in prompt  # notes without a file


def test_spinning_parts_are_asked_to_stay_sharp(ctx: AppContext) -> None:
    content_id = approved_item(ctx)

    run(ctx)

    folder = voice.asset_dir(ctx, content_id)
    prompts = json.loads((folder / "flow_prompts.json").read_text())
    assert "Spinning parts turn in smooth slow motion, every blade and edge sharp" in prompts["1"]


# --- restage only some clips (D-096) -------------------------------------------------------------


def test_restage_of_chosen_clips_keeps_the_look_and_the_other_clips(ctx: AppContext) -> None:
    content_id = approved_item(ctx)
    run(ctx)
    before = json.loads(ctx.conn.execute("SELECT shot_list_json FROM scripts").fetchone()[0])
    redo = {
        "shots": [
            {
                "n": 4,
                "scene": "Side cutaway: blue air enters on the left and leaves right",
                "camera": "side tracking shot",
                **STORY,
                "end_frame": "air leaving on the right",
            }
        ]
    }
    llm, sdk = fake_llm(ctx, json.dumps({"shots": VISUALS["shots"][:1]}), json.dumps(redo))

    sheet = generate.restage(ctx, content_id, llm=llm, only=(4,))

    asked = sdk.calls[0]["contents"]
    assert (
        "Write new style, start_picture, scene, camera, end_frame and continues only for" in asked
    )
    assert f'"""\n{LOOK}\n"""' in asked  # the look is given, not asked for
    assert (
        "- clip 2 (cutaway): The housing splits and the rotor spins It ends on: the rotor" in asked
    )
    assert "give exactly the flow_clips [4]" in sdk.calls[1]["contents"]
    after = json.loads(ctx.conn.execute("SELECT shot_list_json FROM scripts").fetchone()[0])
    assert [s for s in after if s["n"] != 4] == [s for s in before if s["n"] != 4]
    assert after[3]["prompt_vars"] == {
        "hero_look": LOOK, "scene": redo["shots"][0]["scene"], "camera": "side tracking shot",
        **STORY, "end_frame": "air leaving on the right", "energy": "electric",
    }  # fmt: skip
    prompts = json.loads((sheet.parent / "flow_prompts.json").read_text())
    assert "nothing flows backwards or out of the wrong opening" in prompts["4"]  # flow beat
    assert "nothing flows backwards" not in prompts["5"]
    # shot 5 continues the story: it starts on shot 4's new end image
    assert "starts exactly on the start image: air leaving on the right." in prompts["5"]


@pytest.mark.parametrize(
    ("only", "problem"), [((3,), "--shots must name flow_clips"), ((), "--shots must name")]
)
def test_restage_refuses_shots_that_are_not_clips(
    ctx: AppContext, only: tuple[int, ...], problem: str
) -> None:
    content_id = approved_item(ctx)
    llm, sdk = fake_llm(ctx)

    with pytest.raises(FactoryError, match=problem):
        generate.restage(ctx, content_id, llm=llm, only=only)
    assert sdk.calls == []


def test_restage_of_chosen_clips_needs_film_shots_first(ctx: AppContext) -> None:
    content_id = old_script(ctx)
    llm, _ = fake_llm(ctx)

    with pytest.raises(FactoryError, match="without --shots first"):
        generate.restage(ctx, content_id, llm=llm, only=(4,))


def test_shots_option_needs_restage(config_dir: Path) -> None:
    opts = ["--config-dir", str(config_dir)]
    CliRunner().invoke(app, ["db", "init", *opts])

    result = CliRunner().invoke(app, ["generate", "--shots", "3", *opts])

    assert result.exit_code == 1
    assert "--shots only works together with --restage" in result.output


def test_restage_gives_only_changed_prompts_a_new_issue_time(ctx: AppContext) -> None:
    content_id = approved_item(ctx)
    run(ctx)
    folder = voice.asset_dir(ctx, content_id)
    first = json.loads((folder / generate.ISSUED_FILE).read_text())
    assert set(first) == {"1", "2", "4", "5", *(f"image_{i}" for i in range(1, 7))}
    stamped = {k: {**v, "issued_at": "2026-01-01T00:00:00Z"} for k, v in first.items()}
    (folder / generate.ISSUED_FILE).write_text(json.dumps(stamped))
    redo = {"shots": [{"n": 4, "scene": "Side cutaway: current flows from left to right",
                       "camera": "side track",
                       **STORY, "end_frame": "the diode plate"}]}  # fmt: skip
    llm, _ = fake_llm(ctx, json.dumps(redo))

    generate.restage(ctx, content_id, llm=llm, only=(4,))

    after = json.loads((folder / generate.ISSUED_FILE).read_text())
    changed = {k for k in after if after[k]["issued_at"] != "2026-01-01T00:00:00Z"}
    # shot 4's own start and end image change; 5 continues from 4's new end image
    assert changed == {"4", "image_4", "image_5", "5"}
    assert after["4"]["sha256"] != first["4"]["sha256"]


# --- story shots (D-104) ------------------------------------------------------------------------


def clip(n: int, continues: bool) -> dict:
    return {"n": n, "prompt_vars": {"start_picture": f"start {n}", "end_frame": f"end {n}",
                                    "style": "open", "continues": continues}}  # fmt: skip


def test_image_chain_reuses_end_images_and_never_lets_the_first_clip_continue() -> None:
    pictures, frames = generate.image_chain([clip(1, True), clip(2, True), clip(4, False)])

    assert frames == {1: (1, 2), 2: (2, 3), 4: (4, 5)}
    assert [p.description for p in pictures] == ["start 1", "end 1", "end 2", "start 4", "end 4"]
    assert pictures[1].used_by == "end of shot 1, start of shot 2"


# --- brief prompts, the default (D-105) ---------------------------------------------------------


def test_brief_prompts_are_like_the_alternator_video_with_one_check_image(ctx: AppContext) -> None:
    ctx = dataclasses.replace(
        ctx, channel=ctx.channel.model_copy(update={"video_prompts": "brief"})
    )
    content_id = approved_item(ctx)

    run(ctx)

    folder = voice.asset_dir(ctx, content_id)
    prompts = json.loads((folder / "flow_prompts.json").read_text())
    sheet = (folder / "flow_sheet.md").read_text()
    assert prompts["1"].startswith(
        "Create a premium 8-second product video of the alternator, vertical 9:16, with three "
        "smooth camera angles.\nThe alternator turns as its pulley spins."
    )
    assert prompts["2"].startswith("Create a premium 8-second technical cutaway video")
    assert prompts["4"].startswith("Create a premium 8-second technical animation")
    assert prompts["5"].startswith("Create a premium 8-second video of the alternator at work,")
    assert "Glowing cyan light traces show the electric current" in prompts["4"]
    assert "already moving in the very first frame" in prompts["1"]
    assert "No logos, no text" in prompts["5"]
    for long_rule in ("The machine:", "start image", "end image", "vertical cutaway"):
        assert long_rule not in prompts["1"]  # simple: no story-shot layers
    assert sorted(k for k in prompts if k.startswith("image_")) == [
        f"image_{i}" for i in (1, 2, 3, 4)
    ]
    assert "Check image: `image_03.png`" in sheet and "End image" not in sheet  # shot 4's
    for key in ("image_1", "image_2", "image_3", "image_4"):
        assert "vertical cutaway" not in prompts[key]  # images match their brief clips (D-110)
    assert "**Check image first (0 credits, recommended):**" in sheet


# --- reshoot (D-107) ----------------------------------------------------------------------------


def test_reshoot_keeps_the_good_clips_and_waits_for_the_new_ones(ctx: AppContext) -> None:
    content_id = approved_item(ctx)
    run(ctx)
    inbox = generate.inbox_dir(ctx, content_id)
    for n in CLIPS:
        make_clip(inbox / f"shot_{n:02d}.mp4")
    run(ctx)  # all clips in -> generated, clips moved into the project
    folder = voice.asset_dir(ctx, content_id)
    (folder / "render_youtube.mp4").write_bytes(b"old render")
    (folder / "qa_report.json").write_text("[]")
    redo = {"shots": [{"n": 2, "style": "open", "start_picture": "the closed alternator",
                       "scene": "The housing splits cleanly and the rotor turns",
                       "camera": "slow dolly-in", "end_frame": "the open rotor",
                       "continues": False}]}  # fmt: skip
    llm, _ = fake_llm(ctx, json.dumps(redo))

    generate.restage(ctx, content_id, llm=llm, only=(2,))

    assert status(ctx, content_id) == "awaiting_clips"
    names = sorted(p.name for p in inbox.glob("*.mp4"))
    assert names == ["old_shot_02.mp4", "shot_01.mp4", "shot_04.mp4", "shot_05.mp4"]
    assert not (folder / "render_youtube.mp4").exists() and not (folder / "qa_report.json").exists()
    assert assets(ctx, "flow_clip") == []

    make_clip(inbox / "shot_02.mp4")  # the owner's new shot 2
    result = run(ctx)

    assert (result.processed, status(ctx, content_id)) == (1, "generated")
    assert [r["shot_n"] for r in assets(ctx, "flow_clip")] == CLIPS


def test_a_new_rules_line_does_not_make_a_prompt_look_changed() -> None:
    base = "Create a premium 8-second product video.\nThe gearbox turns."
    with_rules = base + "\nRules from earlier clips: the camera never passes through parts."

    assert generate._content_sha(with_rules) == generate._content_sha(base)  # D-109
    assert generate._content_sha(base + " Faster.") != generate._content_sha(base)
