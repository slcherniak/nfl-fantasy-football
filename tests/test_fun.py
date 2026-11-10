"""Exact-value tests for the fun metrics (schedule swap, belt, bench crimes)
on the hand-computed 4-team fixture, plus the draft-results parser."""

import pandas as pd
import pytest
from bs4 import BeautifulSoup

from src.metrics import fun
from src.scrape import parse_draft
from tests.test_metrics import make_row


@pytest.fixture
def matchups():
    # W1: A 100 d. B 90 | C 60 d. D 50
    # W2: A 95 t C 95   | D 110 d. B 60
    # W3: D 120 d. A 50 | B 85 t C 85
    rows = [
        make_row(1, "A", "B", 100, 90, "W"), make_row(1, "B", "A", 90, 100, "L"),
        make_row(1, "C", "D", 60, 50, "W"), make_row(1, "D", "C", 50, 60, "L"),
        make_row(2, "A", "C", 95, 95, "T"), make_row(2, "C", "A", 95, 95, "T"),
        make_row(2, "D", "B", 110, 60, "W"), make_row(2, "B", "D", 60, 110, "L"),
        make_row(3, "D", "A", 120, 50, "W"), make_row(3, "A", "D", 50, 120, "L"),
        make_row(3, "B", "C", 85, 85, "T"), make_row(3, "C", "B", 85, 85, "T"),
    ]
    return pd.DataFrame(rows)


class TestScheduleSwap:
    def test_diagonal_is_actual_record(self, matchups):
        swap = fun.schedule_swap(matchups)
        assert swap["A"]["A"] == {"w": 1, "l": 1, "t": 1}
        assert swap["D"]["D"] == {"w": 2, "l": 1, "t": 0}

    def test_swapped_schedule(self, matchups):
        swap = fun.schedule_swap(matchups)
        # A with B's schedule: wk1 B faced A -> A faces B (100>90 W);
        # wk2 B faced D (95<110 L); wk3 B faced C (50<85 L)
        assert swap["A"]["B"] == {"w": 1, "l": 2, "t": 0}
        # D with C's schedule: wk1 C faced D -> D faces C (50<60 L);
        # wk2 C faced A (110>95 W); wk3 C faced B (120>85 W)
        assert swap["D"]["C"] == {"w": 2, "l": 1, "t": 0}


class TestBelt:
    def test_lineage(self, matchups):
        belt = fun.championship_belt(matchups)
        # Wk1 top scorer A takes it; wk2 A ties C (retains, counts a defense);
        # wk3 A loses to D -> belt passes to D.
        assert belt["holder"] == "D"
        assert [r["key"] for r in belt["reigns"]] == ["A", "D"]
        assert belt["reigns"][0]["defenses"] == 1
        assert belt["reigns"][0]["to"] == 3
        assert belt["reigns"][1]["to"] is None


class TestBenchCrimes:
    def test_worst_swap_named(self):
        rows = [
            {"week": 1, "team_key": "A", "player_key": "s1", "player_name": "Starter QB",
             "position": "QB", "eligible_positions": "QB", "slot": "QB", "points": 10.0},
            {"week": 1, "team_key": "A", "player_key": "s2", "player_name": "Starter WR",
             "position": "WR", "eligible_positions": "WR", "slot": "WR", "points": 5.0},
            {"week": 1, "team_key": "A", "player_key": "b1", "player_name": "Bench WR",
             "position": "WR", "eligible_positions": "WR", "slot": "BN", "points": 21.0},
            {"week": 1, "team_key": "A", "player_key": "b2", "player_name": "Bench QB",
             "position": "QB", "eligible_positions": "QB", "slot": "BN", "points": 12.0},
            {"week": 1, "team_key": "A", "player_key": "ir", "player_name": "IR Guy",
             "position": "RB", "eligible_positions": "RB", "slot": "IR", "points": 30.0},
        ]
        crimes = fun.bench_crimes(pd.DataFrame(rows), {"W/R/T": ["WR", "RB", "TE"]})
        assert len(crimes) == 1
        crime = crimes.iloc[0]
        # Best swap: Bench WR (21) for Starter WR (5) = +16; IR never counts.
        assert crime["benched"] == "Bench WR"
        assert crime["started"] == "Starter WR"
        assert crime["gain"] == pytest.approx(16.0)

    def test_flex_eligibility(self):
        rows = [
            {"week": 1, "team_key": "A", "player_key": "s1", "player_name": "Flex RB",
             "position": "RB", "eligible_positions": "RB", "slot": "W/R/T", "points": 3.0},
            {"week": 1, "team_key": "A", "player_key": "b1", "player_name": "Bench TE",
             "position": "TE", "eligible_positions": "TE", "slot": "BN", "points": 15.0},
        ]
        crimes = fun.bench_crimes(pd.DataFrame(rows), {"W/R/T": ["WR", "RB", "TE"]})
        assert crimes.iloc[0]["gain"] == pytest.approx(12.0)  # TE fits the flex


DRAFT_HTML = """
<table><tr><td colspan="3">Round 1</td></tr>
<tr><td class="first">1.</td>
    <td class="player"><a class="name" href="https://sports.yahoo.com/nfl/players/40059">Jahmyr Gibbs</a>
        <span class="Block">(Det - RB)</span></td>
    <td class="last" title="LUMI TUMIS FAAB MONSTAAAS">LUMI TUMIS FAAB MONSTAAAS</td></tr>
<tr><td class="first">2.</td>
    <td class="player"><a class="name" href="https://sports.yahoo.com/nfl/players/33393">Ja'Marr Chase</a>
        <span class="Block">(Cin - WR)</span></td>
    <td class="last" title="Russell Stilson">Russell Stilson</td></tr>
</table>
<table><tr><td colspan="3">Round 2</td></tr>
<tr><td class="first">1.</td>
    <td class="player"><a class="name" href="https://sports.yahoo.com/nfl/players/99999">Mystery Man</a>
        <span class="Block">(KC - TE)</span></td>
    <td class="last" title="Unknown Team Name">Unknown Team Name</td></tr>
</table>
"""


class TestParseDraft:
    def test_rounds_picks_and_unknown_team_dropped(self):
        picks = parse_draft(
            BeautifulSoup(DRAFT_HTML, "lxml"),
            {"LUMI TUMIS FAAB MONSTAAAS": "5", "Russell Stilson": "1"},
        )
        assert len(picks) == 2  # the unknown-team pick is dropped
        assert picks[0] == {"overall": 1, "round": 1, "pick": 1, "team_key": "5",
                            "player_key": "40059", "player_name": "Jahmyr Gibbs",
                            "position": "RB"}
        assert picks[1]["round"] == 1 and picks[1]["pick"] == 2
