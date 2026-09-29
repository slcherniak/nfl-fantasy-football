"""Tests for the embedded-charts request builder."""

import pandas as pd
import pytest

from src.charts import PALETTE, build_chart_requests

TAB_NAMES = {
    "dashboard": "Dashboard", "charts": "Charts", "all_play_matrix": "All-Play Matrix",
    "luck_report": "Luck Report", "efficiency": "Manager Efficiency",
    "weekly_log": "Weekly Log", "trends": "Trends",
    "raw_matchups": "_raw_matchups", "raw_standings": "_raw_standings",
}


def metadata(existing_chart_ids=()):
    return {
        "sheets": [
            {"properties": {"title": "Dashboard", "sheetId": 1}},
            {"properties": {"title": "Luck Report", "sheetId": 2}},
            {"properties": {"title": "Trends", "sheetId": 3}},
            {"properties": {"title": "Manager Efficiency", "sheetId": 4}},
            {
                "properties": {"title": "Charts", "sheetId": 9},
                "charts": [{"chartId": cid} for cid in existing_chart_ids],
            },
        ]
    }


@pytest.fixture
def tables():
    dash = pd.DataFrame({
        "team": ["A", "B"], "rank": [1, 2], "composite": [1.2, -0.3],
        "playoffs_odds": [0.9, 0.4],
    })
    luck = pd.DataFrame({
        "team": ["A", "B"], "actual_wins": [2.0, 1.0], "expected_wins": [1.5, 1.5],
    })
    # Trends is a stacked values block: pivot, blank spacer, second table.
    trends = [
        ["week", "Team A", "Team B"],
        [1, 100.0, 90.0],
        [2, 95.0, 60.0],
        [],
        ["team", "ppg", "std_dev"],
        ["Team A", 97.5, 2.5],
    ]
    eff = pd.DataFrame({"team": ["A", "B"], "points_on_bench": [10.0, 22.5], "efficiency": [0.9, 0.8]})
    return {
        "Dashboard": dash, "Luck Report": luck, "Trends": trends,
        "Manager Efficiency": eff,
    }


def chart_specs(requests):
    return [r["addChart"]["chart"]["spec"] for r in requests if "addChart" in r]


class TestBuildChartRequests:
    def test_deletes_existing_charts_and_adds_new(self, tables):
        requests = build_chart_requests(metadata([77, 78]), tables, TAB_NAMES)
        deletes = [r for r in requests if "deleteEmbeddedObject" in r]
        assert {d["deleteEmbeddedObject"]["objectId"] for d in deletes} == {77, 78}
        assert len(chart_specs(requests)) == 5  # ppg, luck, power, odds, bench

    def test_trends_line_chart_stops_at_spacer(self, tables):
        specs = chart_specs(build_chart_requests(metadata(), tables, TAB_NAMES))
        line = next(s for s in specs if s["basicChart"]["chartType"] == "LINE")
        rng = line["basicChart"]["domains"][0]["domain"]["sourceRange"]["sources"][0]
        assert rng["endRowIndex"] == 3  # header + 2 pivot rows, spacer excluded
        assert len(line["basicChart"]["series"]) == 2  # one per team column

    def test_team_colors_follow_column_position(self, tables):
        specs = chart_specs(build_chart_requests(metadata(), tables, TAB_NAMES))
        line = next(s for s in specs if s["basicChart"]["chartType"] == "LINE")
        first = line["basicChart"]["series"][0]["colorStyle"]["rgbColor"]
        expected = int(PALETTE[0][1:3], 16) / 255
        assert first["red"] == pytest.approx(expected)

    def test_luck_chart_uses_named_columns(self, tables):
        specs = chart_specs(build_chart_requests(metadata(), tables, TAB_NAMES))
        luck = next(s for s in specs if "expected wins" in s["title"])
        cols = [
            srs["series"]["sourceRange"]["sources"][0]["startColumnIndex"]
            for srs in luck["basicChart"]["series"]
        ]
        assert cols == [1, 2]  # actual_wins, expected_wins in the fixture frame

    def test_odds_chart_skipped_without_column(self, tables):
        tables["Dashboard"] = tables["Dashboard"].drop(columns=["playoffs_odds"])
        specs = chart_specs(build_chart_requests(metadata(), tables, TAB_NAMES))
        assert not any("Playoff odds" in s["title"] for s in specs)
        assert len(specs) == 4

    def test_bench_chart_skipped_for_placeholder(self, tables):
        tables["Manager Efficiency"] = pd.DataFrame([{"note": "no rosters yet"}])
        specs = chart_specs(build_chart_requests(metadata(), tables, TAB_NAMES))
        assert not any("bench" in s["title"].lower() for s in specs)

    def test_missing_charts_tab_raises(self, tables):
        meta = {"sheets": [{"properties": {"title": "Dashboard", "sheetId": 1}}]}
        with pytest.raises(ValueError):
            build_chart_requests(meta, tables, TAB_NAMES)
