from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from factory.cli import app
from factory.config import REPO_ROOT, load_channel
from factory.db import connect
from factory.media.brand import AVATAR_SIZE, _monogram, render_avatar, render_logo
from factory.media.ffmpeg import probe

FONTS = REPO_ROOT / "assets_static" / "fonts"
FONT = FONTS / "Montserrat-ExtraBold.ttf"
CYAN = (0x00, 0xD1, 0xFF)
OFF_WHITE = (0xF5, 0xF7, 0xFA)


def test_logo_is_transparent_with_cyan_ampersand() -> None:
    logo = render_logo(load_channel("coil-and-cog"), FONT, width_px=1200)

    colors = {rgba[:3] for _, rgba in logo.getcolors(maxcolors=1_000_000) if rgba[3] == 255}
    assert logo.mode == "RGBA"
    assert logo.width == 1200
    assert logo.getpixel((0, 0))[3] == 0  # transparent corner
    assert CYAN in colors and OFF_WHITE in colors


def test_avatar_is_square_and_fits_inside_the_circle_crop() -> None:
    avatar = render_avatar(load_channel("coil-and-cog"), FONT)

    assert avatar.size == (AVATAR_SIZE, AVATAR_SIZE)
    # The box around everything that isn't background must fit inside the circle platforms crop to.
    gray = avatar.convert("L")
    bg_level = gray.getpixel((0, 0))
    mask = gray.point(lambda v: 255 if abs(v - bg_level) > 8 else 0)
    left, top, right, bottom = mask.getbbox()
    corners = [(left, top), (right, top), (left, bottom), (right, bottom)]
    radius = max(((x - 400) ** 2 + (y - 400) ** 2) ** 0.5 for x, y in corners)
    assert radius < 0.5 * AVATAR_SIZE


@pytest.mark.parametrize(("name", "expected"), [("Coil & Cog", "C&C"), ("Inside Tech", "IT")])
def test_monogram(name: str, expected: str) -> None:
    assert _monogram(name) == expected


@pytest.mark.slow
def test_brand_build_writes_every_file_and_valid_stings(config_dir: Path) -> None:
    root = config_dir.parent
    shutil.copytree(FONTS, root / "assets_static" / "fonts")
    runner = CliRunner()
    runner.invoke(app, ["db", "init", "--config-dir", str(config_dir)])

    result = runner.invoke(app, ["brand", "build", "--config-dir", str(config_dir)])

    assert result.exit_code == 0, result.output
    logo_dir = root / "assets_static" / "logo"
    expected = {"logo.png", "logo_on_bg.png", "avatar_800.png"}
    assert expected | {"sting_1080x1920.mp4", "sting_1920x1080.mp4"} == {
        p.name for p in logo_dir.iterdir()
    }
    for width, height in [(1080, 1920), (1920, 1080)]:
        info = probe(logo_dir / f"sting_{width}x{height}.mp4")
        video = next(s for s in info["streams"] if s["codec_type"] == "video")
        audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
        assert (video["codec_name"], video["width"], video["height"]) == ("h264", width, height)
        assert video["r_frame_rate"] == "30/1"
        assert (audio["codec_name"], audio["sample_rate"]) == ("aac", "48000")
        assert float(info["format"]["duration"]) == pytest.approx(1.0, abs=0.05)
    conn = connect(root / "data" / "factory.db")
    assert (
        conn.execute("SELECT status FROM runs WHERE command = 'brand build'").fetchone()[0] == "ok"
    )
    conn.close()
