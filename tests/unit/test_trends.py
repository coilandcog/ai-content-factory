from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from factory import cli
from factory.config import EnvSettings, load_channel, load_settings
from factory.context import AppContext
from factory.db import connect, migrate, upsert_channel
from factory.errors import IntegrationError
from factory.stages import trends

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "trends"


@pytest.fixture
def ctx(tmp_path: Path) -> AppContext:
    conn = connect(tmp_path / "test.db")
    migrate(conn)
    channel = load_channel("coil-and-cog")
    upsert_channel(conn, channel)
    env = EnvSettings(dry_run=True, factory_channel="coil-and-cog")
    context = AppContext(load_settings(), env, channel, conn, tmp_path)  # inbox under tmp_path
    yield context
    conn.close()


def drop(ctx: AppContext, *names: str) -> Path:
    folder = trends.inbox(ctx)
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        shutil.copy(FIXTURES / name, folder / name)
    return folder


def rows(ctx: AppContext) -> dict[tuple[str, str, str], float]:
    found = ctx.conn.execute(
        "SELECT query, title, metric_name, metric_value FROM trend_signals"
    ).fetchall()
    return {(r["query"], r["title"], r["metric_name"]): r["metric_value"] for r in found}


# --- parsing both layouts ------------------------------------------------------------------


def test_classic_layout_with_category_line_and_regions() -> None:
    series = trends.parse((FIXTURES / "classic_layout.csv").read_text())

    assert [(s.topic, s.region) for s in series] == [
        ("jet engine", "Worldwide"),
        ("tunnel boring machine", "Worldwide"),
    ]
    assert series[0].points[0] == (date(2026, 7, 27), 40.0)
    assert series[1].points[0][1] == 0.5  # "<1"
    assert len(series[0].points) == 90


def test_quoted_layout_without_region() -> None:
    series = trends.parse((FIXTURES / "quoted_layout.csv").read_text())

    assert [(s.topic, s.region) for s in series] == [
        ("turbocharger", "unspecified"),
        ("wind turbine", "unspecified"),
    ]


def test_metrics() -> None:
    jet, tbm = trends.parse((FIXTURES / "classic_layout.csv").read_text())

    assert trends.metrics(jet) == {"trends_interest": 60.0, "trends_ratio": 1.333}
    assert trends.metrics(tbm) == {"trends_interest": 2.0, "trends_ratio": 4.0}


def test_a_short_chart_gets_interest_but_no_ratio() -> None:
    text = "Week,alternator: (Worldwide)\n2026-10-11,5\n2026-10-18,7\n"

    [series] = trends.parse(text)

    assert trends.metrics(series) == {"trends_interest": 7.0}


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("hello,world\n1,2\n", "no header row"),
        ("Day,jet engine: (Worldwide)\nnot a date,1\n", "no topic column"),
    ],
)
def test_files_that_are_not_trends_exports(text: str, problem: str) -> None:
    with pytest.raises(IntegrationError, match=problem):
        trends.parse(text)


# --- the inbox import ----------------------------------------------------------------------


def test_import_stores_rows_and_moves_the_file(ctx: AppContext) -> None:
    folder = drop(ctx, "classic_layout.csv", "quoted_layout.csv")

    result = trends.import_inbox(ctx)

    assert (result.files, result.rows_added, result.broken) == (2, 8, [])
    stored = rows(ctx)
    assert stored[("jet engine", "Worldwide", "trends_ratio")] == 1.333
    assert stored[("wind turbine", "unspecified", "trends_ratio")] == 0.5
    assert list(folder.glob("*.csv")) == []
    assert sorted(p.name for p in (folder / "imported").iterdir()) == [
        "classic_layout.csv",
        "quoted_layout.csv",
    ]
    observed = ctx.conn.execute("SELECT DISTINCT observed_at FROM trend_signals").fetchall()
    assert [r[0] for r in observed] == ["2026-10-24T00:00:00Z"]  # the chart's last day


def test_importing_the_same_file_again_adds_nothing(ctx: AppContext) -> None:
    drop(ctx, "classic_layout.csv")
    trends.import_inbox(ctx)
    folder = drop(ctx, "classic_layout.csv")  # downloaded again under the same name

    result = trends.import_inbox(ctx)

    assert (result.rows_added, result.rows_skipped) == (0, 4)
    assert len(rows(ctx)) == 4
    assert len(list((folder / "imported").iterdir())) == 2  # the second copy is kept, renamed


def test_a_broken_file_is_named_and_stays_while_others_import(ctx: AppContext) -> None:
    folder = drop(ctx, "classic_layout.csv")
    (folder / "notes.csv").write_text("hello,world\n1,2\n")

    result = trends.import_inbox(ctx)

    assert result.files == 1
    assert result.broken == ["notes.csv: no header row with a Day/Week/Month/Time/Date column"]
    assert (folder / "notes.csv").is_file()


def test_empty_inbox_is_not_an_error(ctx: AppContext) -> None:
    result = trends.import_inbox(ctx)

    assert (result.files, result.broken) == (0, [])


def test_signals_report_shows_the_worldwide_trends_ratio(ctx: AppContext) -> None:
    drop(ctx, "classic_layout.csv")
    trends.import_inbox(ctx)

    report = cli._signals_report(ctx.conn, ctx.channel)

    jet = next(line for line in report.splitlines() if "jet engine" in line)
    assert jet.split()[-1] == "1.33x"


def test_cli_import_reports_files_and_problems(config_dir: Path) -> None:
    runner = CliRunner()
    runner.invoke(cli.app, ["db", "init", "--config-dir", str(config_dir)])
    inbox = config_dir.parent / "data" / "inbox" / "trends"

    empty = runner.invoke(cli.app, ["trends", "import", "--config-dir", str(config_dir)])
    inbox.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / "classic_layout.csv", inbox)
    (inbox / "notes.csv").write_text("hello\n")
    done = runner.invoke(cli.app, ["trends", "import", "--config-dir", str(config_dir)])

    assert empty.exit_code == 0 and "No CSV files in" in empty.output
    assert "Imported 1 file(s): 4 row(s) added, 0 already there" in done.output
    assert "Not imported (left in the inbox): notes.csv" in done.output


REAL_EXPORT = "time_series_Worldwide_20260725-0228_20261025-0228.csv"


def test_real_2026_export_takes_the_region_from_the_file_name() -> None:
    series = trends.parse((FIXTURES / REAL_EXPORT).read_text(), file_name=REAL_EXPORT)

    assert [(s.topic, s.region) for s in series] == [
        ("jet engine", "Worldwide"),
        ("wind turbine", "Worldwide"),
        ("turbocharger", "Worldwide"),
        ("tunnel boring machine", "Worldwide"),
    ]
    assert series[2].points[0] == (date(2026, 7, 28), 91.0)
    assert len(series[0].points) == 89
    assert trends.metrics(series[2]) == {"trends_interest": 81.29, "trends_ratio": 0.901}
