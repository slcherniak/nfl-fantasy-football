"""End-to-end smoke test: fixture rows in SQLite -> report.build_tables.

Catches wiring bugs between the store, the metrics engine, and the tab
builder (column renames, merge keys, empty-roster handling).
"""

import pandas as pd
import pytest

from src import models, report
from src.config import Settings
from tests.test_metrics import make_row


@pytest.fixture
def settings():
    return Settings(raw={
        "league": {"league_id": "392520", "expected_num_teams": 4},
        "playoffs": {"num_playoff_teams": 4, "num_byes": 2, "weeks": [15, 16, 17], "reseeding": False},
        "regular_season": {"weeks": list(range(1, 15))},
        "power_rankings": {
            "weights": {"season_ppg": 0.35, "recent_ppg": 0.25, "all_play_pct": 0.25, "win_pct": 0.15},
            "recent_weeks": 3,
        },
        "luck": {"close_game_margin": 10.0},
        "roster_slots": {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "W/R/T": 1, "K": 1, "DEF": 1},
        "flex_eligibility": {"W/R/T": ["WR", "RB", "TE"]},
        "non_starting_slots": ["BN", "IR"],
        "simulation": {"num_sims": 25, "random_seed": 1},
        "sheets": {"tabs": {
            "dashboard": "Dashboard", "charts": "Charts",
            "all_play_matrix": "All-Play Matrix",
            "luck_report": "Luck Report", "efficiency": "Manager Efficiency",
            "weekly_log": "Weekly Log", "trends": "Trends",
            "raw_matchups": "_raw_matchups", "raw_standings": "_raw_standings",
        }},
    })


@pytest.fixture
def conn(tmp_path):
    conn = models.connect(tmp_path / "test.db")
    teams = [
        {"team_key": k, "team_id": i + 1, "name": f"Team {k}", "manager": k, "logo_url": None}
        for i, k in enumerate("ABCD")
    ]
    models.upsert(conn, "teams", teams, ["team_key"])

    completed = [
        make_row(1, "A", "B", 100, 90, "W"), make_row(1, "B", "A", 90, 100, "L"),
        make_row(1, "C", "D", 60, 50, "W"), make_row(1, "D", "C", 50, 60, "L"),
        make_row(2, "A", "C", 95, 95, "T"), make_row(2, "C", "A", 95, 95, "T"),
        make_row(2, "D", "B", 110, 60, "W"), make_row(2, "B", "D", 60, 110, "L"),
        make_row(3, "D", "A", 120, 50, "W"), make_row(3, "A", "D", 50, 120, "L"),
        make_row(3, "B", "C", 85, 85, "T"), make_row(3, "C", "B", 85, 85, "T"),
    ]
    future = []
    for me, opp in (("A", "B"), ("B", "A"), ("C", "D"), ("D", "C")):
        future.append({
            "week": 4, "team_key": me, "opp_key": opp, "points_for": None,
            "points_against": None, "result": None, "is_playoffs": 0, "is_complete": 0,
        })
    models.upsert(conn, "matchups", completed + future, ["week", "team_key"])
    return conn


def test_build_tables_smoke(conn, settings):
    tables = report.build_tables(conn, settings)
    expected_tabs = set(settings.sheet_tabs.values()) - {"Charts"}  # charts tab holds objects, not values
    assert set(tables.keys()) == expected_tabs

    dash = tables["Dashboard"]
    assert list(dash["rank"]) == [1, 2, 3, 4]
    assert dash.iloc[0]["team"] == "Team D"          # D dominates every component
    assert "luck" in dash.columns
    assert "playoffs_odds" in dash.columns           # future games -> odds present

    matrix = tables["All-Play Matrix"]
    assert matrix.shape == (4, 5)                    # team column + 4 opponents

    log = tables["Weekly Log"]
    assert len(log) == 12                            # 4 teams x 3 completed weeks
    assert (log[log["week"] == 1]["awards"].str.len() > 0).any()

    # Phase 1: no roster rows yet -> efficiency tab carries the note
    assert "note" in tables["Manager Efficiency"].columns

    trends = tables["Trends"]                        # raw values block
    assert trends[0][0] == "week"


def test_upsert_is_idempotent(conn, settings):
    before = pd.read_sql_query("SELECT COUNT(*) n FROM matchups", conn)["n"][0]
    models.upsert(
        conn, "matchups",
        [make_row(1, "A", "B", 101.5, 90, "W")],     # stat correction
        ["week", "team_key"],
    )
    after = pd.read_sql_query("SELECT COUNT(*) n FROM matchups", conn)["n"][0]
    assert after == before                            # no duplicate row
    pf = conn.execute(
        "SELECT points_for FROM matchups WHERE week = 1 AND team_key = 'A'"
    ).fetchone()[0]
    assert pf == 101.5                                # value replaced in place


def test_standings_snapshot(conn):
    from src import fetch
    fetch.snapshot_standings(conn, through_week=2)
    snapshot = pd.read_sql_query(
        "SELECT * FROM standings_snapshot WHERE week = 2", conn
    ).set_index("team_key")
    assert snapshot.loc["A", "wins"] == 1
    assert snapshot.loc["A", "ties"] == 1
    assert snapshot.loc["D", "wins"] == 1
    assert snapshot.loc["A", "points_for"] == pytest.approx(195.0)
