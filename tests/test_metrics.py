"""Metric unit tests on a hand-computed fixture league.

Fixture: 4 teams (A, B, C, D), 3 weeks, including a tie week and a
sad-win / robbed-loss week. Every expected value below was computed by hand;
if an implementation change moves any of these numbers, that is a bug in the
change, not in the test.

Schedule and scores:
  W1: A 100 def. B 90   | C 60 def. D 50
  W2: A 95 tie  C 95    | D 110 def. B 60
  W3: D 120 def. A 50   | B 85 tie C 85
"""

import numpy as np
import pandas as pd
import pytest

from src.metrics import all_play, awards, efficiency, luck, power_rankings, sos, volatility
from src.metrics.efficiency import LineupPlayer
from src.simulate import simulate_playoff_odds


def make_row(week, team, opp, pf, pa, result):
    return {
        "week": week, "team_key": team, "opp_key": opp,
        "points_for": float(pf), "points_against": float(pa),
        "result": result, "is_playoffs": 0, "is_complete": 1,
    }


@pytest.fixture
def matchups():
    rows = [
        make_row(1, "A", "B", 100, 90, "W"), make_row(1, "B", "A", 90, 100, "L"),
        make_row(1, "C", "D", 60, 50, "W"), make_row(1, "D", "C", 50, 60, "L"),
        make_row(2, "A", "C", 95, 95, "T"), make_row(2, "C", "A", 95, 95, "T"),
        make_row(2, "D", "B", 110, 60, "W"), make_row(2, "B", "D", 60, 110, "L"),
        make_row(3, "D", "A", 120, 50, "W"), make_row(3, "A", "D", 50, 120, "L"),
        make_row(3, "B", "C", 85, 85, "T"), make_row(3, "C", "B", 85, 85, "T"),
    ]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# All-play
# ---------------------------------------------------------------------------

class TestAllPlay:
    def test_weekly_all_play(self, matchups):
        weekly = all_play.weekly_all_play(matchups).set_index(["week", "team_key"])
        # Week 1: 100 > 90 > 60 > 50
        assert weekly.loc[(1, "A"), "ap_wins"] == 3.0
        assert weekly.loc[(1, "B"), "ap_wins"] == 2.0
        assert weekly.loc[(1, "C"), "ap_wins"] == 1.0
        assert weekly.loc[(1, "D"), "ap_wins"] == 0.0
        # Week 2: D 110 > A 95 = C 95 > B 60 — ties are 0.5 each
        assert weekly.loc[(2, "D"), "ap_wins"] == 3.0
        assert weekly.loc[(2, "A"), "ap_wins"] == 1.5
        assert weekly.loc[(2, "C"), "ap_wins"] == 1.5
        assert weekly.loc[(2, "B"), "ap_wins"] == 0.0
        assert weekly.loc[(2, "A"), "ap_ties"] == 1.0
        # win pct denominator is (teams - 1)
        assert weekly.loc[(1, "A"), "ap_win_pct"] == 1.0
        assert weekly.loc[(2, "A"), "ap_win_pct"] == 0.5

    def test_season_all_play(self, matchups):
        season = all_play.season_all_play(all_play.weekly_all_play(matchups)).set_index("team_key")
        assert season.loc["A", "ap_wins"] == 4.5
        assert season.loc["B", "ap_wins"] == 3.5
        assert season.loc["C", "ap_wins"] == 4.0
        assert season.loc["D", "ap_wins"] == 6.0
        # every team's wins + losses = 9 all-play games
        assert ((season["ap_wins"] + season["ap_losses"]) == 9.0).all()
        assert season.loc["D", "ap_win_pct"] == pytest.approx(6 / 9)

    def test_all_play_matrix(self, matchups):
        matrix = all_play.all_play_matrix(matchups)
        assert matrix.loc["A", "B"] == "2-1-0"   # 100>90, 95>60, 50<85
        assert matrix.loc["B", "A"] == "1-2-0"
        assert matrix.loc["A", "C"] == "1-1-1"   # 100>60, 95=95, 50<85
        assert matrix.loc["C", "D"] == "1-2-0"   # 60>50, 95<110, 85<120
        assert matrix.loc["A", "A"] == "—"


# ---------------------------------------------------------------------------
# Luck
# ---------------------------------------------------------------------------

class TestLuck:
    @pytest.fixture
    def report(self, matchups):
        weekly = all_play.weekly_all_play(matchups)
        return luck.luck_report(matchups, weekly, close_margin=10.0).set_index("team_key")

    def test_actual_and_expected_wins(self, report):
        # Actual (ties = 0.5): A 1.5, B 0.5, C 2.0, D 2.0
        assert report.loc["A", "actual_wins"] == 1.5
        assert report.loc["B", "actual_wins"] == 0.5
        # Expected = sum of weekly all-play win pcts
        assert report.loc["A", "expected_wins"] == pytest.approx(1.5)          # 1 + .5 + 0
        assert report.loc["B", "expected_wins"] == pytest.approx(2 / 3 + 0.5)  # 7/6
        assert report.loc["C", "expected_wins"] == pytest.approx(4 / 3)
        assert report.loc["D", "expected_wins"] == pytest.approx(2.0)

    def test_luck_index(self, report):
        assert report.loc["A", "luck"] == pytest.approx(0.0)
        assert report.loc["B", "luck"] == pytest.approx(-2 / 3)   # unlucky
        assert report.loc["C", "luck"] == pytest.approx(2 / 3)    # lucky
        assert report.loc["D", "luck"] == pytest.approx(0.0)

    def test_sad_wins_and_robbed_losses(self, report):
        # W1 median is 75: C won scoring 60 (sad), B lost scoring 90 (robbed)
        assert report.loc["C", "sad_wins"] == 1
        assert report.loc["B", "robbed_losses"] == 1
        assert report.loc["A", "sad_wins"] == 0
        assert report.loc["D", "robbed_losses"] == 0

    def test_close_games(self, report):
        # Only the two ties (margin 0) are inside the 10-point margin;
        # the week-1 games were decided by exactly 10 (not < 10).
        assert report.loc["A", "close_ties"] == 1
        assert report.loc["C", "close_ties"] == 2
        assert report.loc["A", "close_wins"] == 0
        assert report.loc["B", "close_losses"] == 0

    def test_pa_percentile(self, report):
        # Total PA: A 305 > B 295 > C 230 > D 170
        assert report.loc["A", "pa_percentile"] == 100.0
        assert report.loc["B", "pa_percentile"] == 75.0
        assert report.loc["D", "pa_percentile"] == 25.0


# ---------------------------------------------------------------------------
# Power rankings
# ---------------------------------------------------------------------------

class TestPowerRankings:
    def test_components_and_order(self, matchups):
        ranked = power_rankings.power_rankings(matchups).set_index("team_key")
        # D leads every component: PPG 93.33, perfect recency, AP .667, W% .833
        assert ranked.loc["D", "rank"] == 1
        assert ranked.loc["D", "season_ppg"] == pytest.approx(280 / 3)
        assert ranked.loc["A", "season_ppg"] == pytest.approx(245 / 3)
        assert ranked.loc["D", "win_pct"] == pytest.approx(2 / 3)  # 2 of 3, ties 0.5
        assert ranked.loc["B", "win_pct"] == pytest.approx(0.5 / 3)

    def test_composite_matches_independent_zscore_blend(self, matchups):
        weights = {"season_ppg": 0.35, "recent_ppg": 0.25, "all_play_pct": 0.25, "win_pct": 0.15}
        ranked = power_rankings.power_rankings(matchups, weights).set_index("team_key").sort_index()
        components = ranked[["season_ppg", "recent_ppg", "all_play_pct", "win_pct"]]
        expected = np.zeros(len(components))
        for name, weight in weights.items():
            col = components[name].to_numpy(dtype=float)
            std = col.std()  # numpy default ddof=0, matching the implementation
            z = np.zeros_like(col) if std == 0 else (col - col.mean()) / std
            expected = expected + weight * z
        assert ranked["composite"].to_numpy() == pytest.approx(expected)

    def test_movement(self, matchups):
        prior = matchups[matchups["week"] < 3]
        ranked = power_rankings.power_rankings(matchups, prior_week_matchups=prior)
        assert set(ranked["movement"]) != set()  # column exists and is int
        assert ranked["movement"].sum() == 0     # rank moves are zero-sum

    def test_movement_arrows(self):
        assert power_rankings.movement_arrow(2) == "▲2"
        assert power_rankings.movement_arrow(-1) == "▼1"
        assert power_rankings.movement_arrow(0) == "–"


# ---------------------------------------------------------------------------
# Efficiency / optimal lineup
# ---------------------------------------------------------------------------

def player(key, points, *positions):
    return LineupPlayer(key, points, frozenset(positions))


class TestOptimalLineup:
    def test_flex_trap_beats_greedy(self):
        # Slots: 1 WR + 1 FLEX(WR/RB). Greedy puts the 10-pointer in FLEX and
        # can only add the 2-point RB; optimal is WR=10, FLEX=9 -> 19.
        slots = {"WR": 1, "W/R/T": 1}
        players = [
            player("wr_only", 10.0, "WR"),
            player("dual", 9.0, "WR", "RB"),
            player("rb", 2.0, "RB"),
        ]
        assert efficiency.optimal_lineup_points(players, slots) == pytest.approx(19.0)

    def test_full_roster(self):
        players = [
            player("qb1", 20, "QB"), player("rb_a", 10, "RB"), player("rb_b", 8, "RB"),
            player("wr_a", 12, "WR"), player("wr_b", 9, "WR"), player("te_a", 5, "TE"),
            player("flx", 7, "WR"), player("k1", 6, "K"), player("def1", 4, "DEF"),
            player("rb_c", 15, "RB"), player("wr_c", 3, "WR"), player("te_b", 14, "TE"),
        ]
        # QB 20 + RB 15,10 + WR 12,9 + TE 14 + FLEX 8 + K 6 + DEF 4
        assert efficiency.optimal_lineup_points(players) == pytest.approx(98.0)

    def test_short_roster_leaves_slots_empty(self):
        players = [player("qb1", 20, "QB")]
        assert efficiency.optimal_lineup_points(players) == pytest.approx(20.0)

    def test_weekly_efficiency(self):
        rows = []
        starters = [
            ("qb1", "QB", "QB", 20), ("rb_a", "RB", "RB", 10), ("rb_b", "RB", "RB", 8),
            ("wr_a", "WR", "WR", 12), ("wr_b", "WR", "WR", 9), ("te_a", "TE", "TE", 5),
            ("flx", "WR", "W/R/T", 7), ("k1", "K", "K", 6), ("def1", "DEF", "DEF", 4),
        ]
        bench = [("rb_c", "RB", "BN", 15), ("wr_c", "WR", "BN", 3), ("te_b", "TE", "BN", 14)]
        for key, pos, slot, pts in starters + bench:
            rows.append({
                "week": 1, "team_key": "A", "player_key": key, "player_name": key,
                "position": pos, "eligible_positions": pos, "slot": slot, "points": float(pts),
            })
        weekly = efficiency.weekly_efficiency(pd.DataFrame(rows))
        row = weekly.iloc[0]
        assert row["actual_points"] == pytest.approx(81.0)
        assert row["optimal_points"] == pytest.approx(98.0)
        assert row["efficiency"] == pytest.approx(81.0 / 98.0)
        assert row["points_on_bench"] == pytest.approx(17.0)


# ---------------------------------------------------------------------------
# Volatility
# ---------------------------------------------------------------------------

class TestVolatility:
    def test_std_and_boom_bust(self, matchups):
        vol = volatility.volatility(matchups).set_index("team_key")
        # A: scores 100, 95, 50 -> population std = sqrt(1516.667/3)
        assert vol.loc["A", "std_dev"] == pytest.approx(np.sqrt(1516.666667 / 3))
        # League mean 83.33, sigma 22.30 -> boom line 105.63, bust line 61.04
        assert vol.loc["D", "boom_weeks"] == 2      # 110 and 120
        assert vol.loc["A", "boom_weeks"] == 0      # 100 is under the line
        for team in "ABCD":
            assert vol.loc[team, "bust_weeks"] == 1  # 50, 60, 60, 50


# ---------------------------------------------------------------------------
# Strength of schedule
# ---------------------------------------------------------------------------

class TestSOS:
    def test_played_sos_excludes_games_vs_self(self, matchups):
        table = sos.played_sos(matchups).set_index("team_key")
        # A faced B (72.5 exc. A), C (72.5 exc. A), D (80 exc. A) -> 75.0
        assert table.loc["A", "sos_played"] == pytest.approx(75.0)
        assert table.loc["B", "sos_played"] == pytest.approx((72.5 + 85 + 77.5) / 3)

    def test_remaining_sos(self, matchups):
        remaining = pd.DataFrame([
            {"week": 4, "team_key": "A", "opp_key": "B"},
            {"week": 4, "team_key": "B", "opp_key": "A"},
        ])
        table = sos.remaining_sos(matchups, remaining).set_index("team_key")
        assert table.loc["A", "sos_remaining"] == pytest.approx(235 / 3)  # B season PPG
        assert table.loc["A", "games_remaining"] == 1


# ---------------------------------------------------------------------------
# Awards
# ---------------------------------------------------------------------------

class TestAwards:
    def test_week1(self, matchups):
        week1 = matchups[matchups["week"] == 1]
        result = awards.weekly_awards(week1)
        assert result["highest_score"]["team_key"] == "A"
        assert result["lowest_score"]["team_key"] == "D"
        assert result["lucky_win"]["team_key"] == "C"       # lowest winning score (60)
        assert result["toughest_loss"]["team_key"] == "B"   # highest losing score (90)
        assert result["biggest_blowout"]["margin"] == pytest.approx(10.0)

    def test_week2_tie_is_closest_game(self, matchups):
        week2 = matchups[matchups["week"] == 2]
        result = awards.weekly_awards(week2)
        assert result["closest_game"]["margin"] == pytest.approx(0.0)
        assert result["highest_score"]["team_key"] == "D"
        assert result["lucky_win"]["team_key"] == "D"       # only winner that week

    def test_bench_award(self, matchups):
        week1 = matchups[matchups["week"] == 1]
        eff = pd.DataFrame([
            {"week": 1, "team_key": "A", "points_on_bench": 5.0},
            {"week": 1, "team_key": "B", "points_on_bench": 22.5},
        ])
        result = awards.weekly_awards(week1, eff)
        assert result["most_points_on_bench"]["team_key"] == "B"
        assert result["most_points_on_bench"]["points"] == pytest.approx(22.5)


# ---------------------------------------------------------------------------
# Playoff simulation (deterministic when sigma ~ 0)
# ---------------------------------------------------------------------------

class TestSimulation:
    def test_deterministic_bracket(self):
        # Round-robin so far: A beat B/C/D, B beat C/D, C beat D. Constant
        # scores (sigma -> 0) make every future game and the bracket certain.
        rows = []
        fixed = {"A": 100.0, "B": 90.0, "C": 80.0, "D": 70.0}
        schedule = {1: [("A", "B"), ("C", "D")], 2: [("A", "C"), ("B", "D")], 3: [("A", "D"), ("B", "C")]}
        for week, games in schedule.items():
            for home, away in games:
                winner = home if fixed[home] > fixed[away] else away
                for me, opp in ((home, away), (away, home)):
                    rows.append(make_row(
                        week, me, opp, fixed[me], fixed[opp],
                        "W" if me == winner else "L",
                    ))
        completed = pd.DataFrame(rows)
        remaining = pd.DataFrame([
            {"week": 4, "team_key": "A", "opp_key": "B"},
            {"week": 4, "team_key": "B", "opp_key": "A"},
            {"week": 4, "team_key": "C", "opp_key": "D"},
            {"week": 4, "team_key": "D", "opp_key": "C"},
        ])
        odds = simulate_playoff_odds(
            completed, remaining, num_playoff_teams=4, num_byes=2,
            num_sims=50, random_seed=7,
        ).set_index("team_key")
        assert (odds["playoffs_odds"] == 1.0).all()      # 4 teams, 4 spots
        assert odds.loc["A", "bye_odds"] == 1.0
        assert odds.loc["B", "bye_odds"] == 1.0
        assert odds.loc["C", "bye_odds"] == 0.0
        # Bracket 1v4 / 2v3 with fixed scores: A and B reach the final, A wins.
        assert odds.loc["A", "title_odds"] == 1.0
        assert odds.loc["B", "finals_odds"] == 1.0
        assert odds.loc["C", "title_odds"] == 0.0
