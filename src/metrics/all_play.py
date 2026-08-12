"""All-play records: each week, every team 'plays' every other team.

Score higher than another team that week = 1 all-play win; equal scores count
0.5 for each side. Denominator each week is (teams that week - 1).
"""

from __future__ import annotations

import pandas as pd


def weekly_all_play(matchups: pd.DataFrame) -> pd.DataFrame:
    """Per team-week all-play line.

    Expects one row per team-week with columns week, team_key, points_for.
    Returns week, team_key, points_for, ap_wins (ties counted 0.5),
    ap_losses, ap_ties, ap_win_pct.
    """
    records = []
    for week, group in matchups.groupby("week"):
        scores = group.set_index("team_key")["points_for"]
        n_opponents = len(scores) - 1
        for team_key, score in scores.items():
            others = scores.drop(team_key)
            wins = float((others < score).sum())
            ties = float((others == score).sum())
            losses = float((others > score).sum())
            ap_wins = wins + 0.5 * ties
            records.append(
                {
                    "week": week,
                    "team_key": team_key,
                    "points_for": score,
                    "ap_wins": ap_wins,
                    "ap_losses": losses + 0.5 * ties,
                    "ap_ties": ties,
                    "ap_win_pct": ap_wins / n_opponents if n_opponents else 0.0,
                }
            )
    return pd.DataFrame(records)


def season_all_play(weekly: pd.DataFrame) -> pd.DataFrame:
    """Aggregate weekly all-play lines into season totals per team."""
    season = (
        weekly.groupby("team_key")
        .agg(
            ap_wins=("ap_wins", "sum"),
            ap_losses=("ap_losses", "sum"),
            ap_ties=("ap_ties", "sum"),
            weeks=("week", "nunique"),
        )
        .reset_index()
    )
    games = season["ap_wins"] + season["ap_losses"]
    season["ap_win_pct"] = (season["ap_wins"] / games).where(games > 0, 0.0)
    return season


def all_play_matrix(matchups: pd.DataFrame) -> pd.DataFrame:
    """N x N grid: cell (row, col) = row team's weekly-score record vs col
    team across all weeks, formatted 'W-L-T'. Diagonal is em-dash."""
    weeks = matchups.pivot(index="week", columns="team_key", values="points_for")
    teams = list(weeks.columns)
    matrix = pd.DataFrame(index=teams, columns=teams, dtype=object)
    for a in teams:
        for b in teams:
            if a == b:
                matrix.loc[a, b] = "—"
                continue
            both = weeks[[a, b]].dropna()
            wins = int((both[a] > both[b]).sum())
            losses = int((both[a] < both[b]).sum())
            ties = int((both[a] == both[b]).sum())
            matrix.loc[a, b] = f"{wins}-{losses}-{ties}"
    return matrix
