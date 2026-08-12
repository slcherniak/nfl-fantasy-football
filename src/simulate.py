"""Monte Carlo playoff odds (Phase 3).

Each simulation samples every team's remaining weekly scores from
Normal(season PPG, season sigma), plays out the actual remaining schedule,
seeds the configured number of playoff teams by record, and runs a fixed
(no-reseed) bracket where the top `num_byes` seeds skip the first round.

Seeding tiebreaks, in order (mirroring the league's "record vs opponent
wins" setting as closely as season data allows):
  1. win percentage
  2. total wins by opponents faced (strength of record)
  3. total points for (Yahoo's fallback)

Bracket for 6 teams / 2 byes (no reseeding):
  Round 1: 3v6, 4v5 (1 and 2 idle)
  Round 2: 1 vs winner(4v5), 2 vs winner(3v6)
  Round 3: final

Because there is no reseeding, which half of the bracket a team lands in
matters; `finals_from_*` columns expose that bracket-path split.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _team_stats(completed: pd.DataFrame) -> pd.DataFrame:
    stats = completed.groupby("team_key").agg(
        ppg=("points_for", "mean"),
        sigma=("points_for", lambda s: s.std(ddof=0)),
        wins=("result", lambda r: float((r == "W").sum()) + 0.5 * float((r == "T").sum())),
        games=("result", "count"),
        points_for=("points_for", "sum"),
    )
    stats["sigma"] = stats["sigma"].fillna(0.0)
    return stats


def _opponent_wins(completed: pd.DataFrame, wins: dict[str, float], extra_opponents: dict[str, list[str]]) -> dict[str, float]:
    totals: dict[str, float] = {}
    faced = completed.groupby("team_key")["opp_key"].apply(list).to_dict()
    for team in wins:
        opponents = list(faced.get(team, [])) + extra_opponents.get(team, [])
        totals[team] = sum(wins.get(o, 0.0) for o in opponents)
    return totals


def simulate_playoff_odds(
    completed: pd.DataFrame,
    remaining_schedule: pd.DataFrame,
    num_playoff_teams: int = 6,
    num_byes: int = 2,
    num_sims: int = 10000,
    random_seed: int | None = None,
) -> pd.DataFrame:
    """Returns per-team odds: playoffs, bye, finals (split by bracket path),
    and title. remaining_schedule has one row per team-week (both sides)."""
    rng = np.random.default_rng(random_seed)
    stats = _team_stats(completed)
    teams = list(stats.index)
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)

    # Unique future games (one row per pairing).
    games = [
        (int(row.week), row.team_key, row.opp_key)
        for row in remaining_schedule.itertuples()
        if row.team_key < row.opp_key and row.team_key in idx and row.opp_key in idx
    ]
    future_opponents: dict[str, list[str]] = {t: [] for t in teams}
    for _, a, b in games:
        future_opponents[a].append(b)
        future_opponents[b].append(a)

    ppg = stats["ppg"].to_numpy()
    sigma = stats["sigma"].to_numpy()
    base_wins = stats["wins"].to_numpy()
    base_pf = stats["points_for"].to_numpy()

    counts = {
        key: np.zeros(n)
        for key in ("playoffs", "bye", "finals_from_bye", "finals_from_round1", "title")
    }

    for _ in range(num_sims):
        wins = base_wins.copy()
        pf = base_pf.copy()
        week_scores: dict[int, np.ndarray] = {}
        for week, a, b in games:
            if week not in week_scores:
                week_scores[week] = rng.normal(ppg, np.maximum(sigma, 1e-9))
            scores = week_scores[week]
            ia, ib = idx[a], idx[b]
            pf[ia] += scores[ia]
            pf[ib] += scores[ib]
            if scores[ia] > scores[ib]:
                wins[ia] += 1
            elif scores[ib] > scores[ia]:
                wins[ib] += 1
            else:
                wins[ia] += 0.5
                wins[ib] += 0.5

        wins_by_team = {t: wins[idx[t]] for t in teams}
        opp_wins = _opponent_wins(completed, wins_by_team, future_opponents)
        seed_order = sorted(
            teams,
            key=lambda t: (-wins[idx[t]], -opp_wins[t], -pf[idx[t]]),
        )
        seeds = seed_order[:num_playoff_teams]
        for t in seeds:
            counts["playoffs"][idx[t]] += 1
        for t in seeds[:num_byes]:
            counts["bye"][idx[t]] += 1

        finalists = _play_bracket(seeds, ppg, sigma, idx, rng)
        for finalist in finalists:
            if finalist in seeds[:num_byes]:
                counts["finals_from_bye"][idx[finalist]] += 1
            else:
                counts["finals_from_round1"][idx[finalist]] += 1
        champ_scores = rng.normal(ppg, np.maximum(sigma, 1e-9))
        ia, ib = idx[finalists[0]], idx[finalists[1]]
        champion = finalists[0] if champ_scores[ia] >= champ_scores[ib] else finalists[1]
        counts["title"][idx[champion]] += 1

    odds = pd.DataFrame({"team_key": teams})
    for key, arr in counts.items():
        odds[f"{key}_odds"] = arr / num_sims
    odds["finals_odds"] = odds["finals_from_bye_odds"] + odds["finals_from_round1_odds"]
    return odds.sort_values("playoffs_odds", ascending=False).reset_index(drop=True)


def _play_bracket(
    seeds: list[str],
    ppg: np.ndarray,
    sigma: np.ndarray,
    idx: dict[str, int],
    rng: np.random.Generator,
) -> tuple[str, str]:
    """Fixed 6-team bracket, returns (top-half finalist, bottom-half finalist)."""

    def game(a: str, b: str) -> str:
        scores = rng.normal(ppg, np.maximum(sigma, 1e-9))
        return a if scores[idx[a]] >= scores[idx[b]] else b

    if len(seeds) >= 6:
        w45 = game(seeds[3], seeds[4])  # 4 v 5
        w36 = game(seeds[2], seeds[5])  # 3 v 6
        top = game(seeds[0], w45)       # 1 vs winner(4v5)
        bottom = game(seeds[1], w36)    # 2 vs winner(3v6)
        return top, bottom
    if len(seeds) >= 4:
        return game(seeds[0], seeds[3]), game(seeds[1], seeds[2])
    return seeds[0], seeds[1]
