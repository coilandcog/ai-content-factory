from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageStat

from factory.config import REPO_ROOT, load_channel
from factory.media.diagrams import SAFE_BOX, preview_diagram, render_diagram, title_case
from factory.media.ffmpeg import probe
from factory.media.stills import FRAME, animate_still, sharpest_frame

LABEL_FONT = REPO_ROOT / "assets_static" / "fonts" / "Inter-SemiBold.ttf"


@pytest.mark.parametrize(
    "labels",
    [
        ["Rotor"],
        ["Rotor", "Stator Coils", "Diode Bridge", "Voltage Regulator"],
        ["Electromagnetic induction field lines"] * 4,  # the longest allowed: 4 long words each
    ],
)
def test_label_card_stays_inside_the_safe_box(labels: list[str], tmp_path: Path) -> None:
    png = render_diagram(
        labels, "heat", load_channel("coil-and-cog"), LABEL_FONT, tmp_path / "d.png"
    )

    image = Image.open(png)
    left, top, right, bottom = image.getchannel("A").getbbox()
    assert image.size == (1080, 1920)
    assert left >= SAFE_BOX[0] and top >= SAFE_BOX[1]
    assert right <= SAFE_BOX[2] and bottom <= SAFE_BOX[3]


def test_dots_use_the_energy_colour(tmp_path: Path) -> None:
    png = render_diagram(["Hot Exhaust"], "heat", load_channel("coil-and-cog"), LABEL_FONT,
                         tmp_path / "d.png")  # fmt: skip

    colors = {c for _, c in Image.open(png).getcolors(maxcolors=1_000_000)}
    assert (0xFF, 0x8A, 0x00, 255) in colors  # amber = heat


def test_title_case_keeps_acronyms_and_small_words() -> None:
    assert title_case("diode bridge to battery") == "Diode Bridge to Battery"
    assert title_case("AC to DC") == "AC to DC"
    assert title_case("the rotor") == "The Rotor"


def test_preview_puts_the_card_over_a_frame(tmp_path: Path) -> None:
    card = render_diagram(["Rotor"], None, load_channel("coil-and-cog"), LABEL_FONT,
                          tmp_path / "d.png")  # fmt: skip
    frame = tmp_path / "frame.png"
    Image.new("RGB", (720, 1280), "#808080").save(frame)

    out = preview_diagram(card, frame, tmp_path / "preview.jpg")

    preview = Image.open(out)
    assert preview.size == (1080, 1920)
    assert preview.getpixel((60, 1500))[0] in range(120, 136)  # untouched grey outside the card


@pytest.mark.slow
def test_ken_burns_clip_has_the_right_shape(tmp_path: Path) -> None:
    still = tmp_path / "still.png"
    Image.new("RGB", (720, 1280), "#446688").save(still)

    out = animate_still(still, tmp_path / "anim.mp4", 1.5, variant=3)

    info = probe(out)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"], video["r_frame_rate"]) == (*FRAME, "30/1")
    assert float(info["format"]["duration"]) == pytest.approx(1.5, abs=0.1)


@pytest.mark.slow
def test_sharpest_frame_skips_blurred_frames(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    # A test pattern that is heavily blurred except between 1.5 s and 2.5 s.
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "testsrc=size=180x320:rate=10:duration=4",
         "-vf", "gblur=sigma=12:enable='not(between(t,1.5,2.5))'",
         "-pix_fmt", "yuv420p", str(clip)],
        check=True,
    )  # fmt: skip
    sharp_reference = tmp_path / "ref.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "2", "-i", str(clip), "-frames:v", "1",
                    str(sharp_reference)], check=True)  # fmt: skip

    out = sharpest_frame(clip, tmp_path / "best.png", samples=8)

    best, ref = Image.open(out).convert("L"), Image.open(sharp_reference).convert("L")
    mean_diff = ImageStat.Stat(ImageChops.difference(best, ref)).mean[0]
    assert mean_diff < 25  # close to the sharp middle, not a blurred frame
