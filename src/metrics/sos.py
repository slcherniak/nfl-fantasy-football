"""Strength of schedule.

Played SOS: for each game a team played, take the opponent's season PPG
computed *excluding* their games against this team (so a team doesn't deflate
its own SOS by beating up an opponent), averaged across games faced.

Remaining SOS: average season-to-date PPG of future opponents, weighted by
how many times each is still on the schedule.
"""

from __future__ import annotations

import pandas as pd


def played_sos(matchups: pd.DataFrame) -> pd.DataFrame:
    records = []
    for team_key, games in matchups.groupby("team_key"):
        strengths = []
        for opp_key in games["opp_key"]:
            opp_games = matchups[
                (matchups["team_key"] == opp_key) & (matchups["opp_key"] != team_key)
            ]
            if not opp_games.empty:
                strengths.append(opp_games["points_for"].mean())
        records.append(
            {
                "team_key": team_key,
                "sos_played": sum(strengths) / len(strengths) if strengths else 0.0,
            }
        )
    return pd.DataFrame(records)


def remaining_sos(completed: pd.DataFrame, remaining: pd.DataFrame) -> pd.DataFrame:
    """completed: finished matchups (for opponent PPG); remaining: future
    schedule rows with team_key/opp_key."""
    ppg = completed.groupby("team_key")["points_for"].mean()
    records = []
    for team_key, games in remaining.groupby("team_key"):
        opp_ppgs = [ppg.get(opp, float("nan")) for opp in games["opp_key"]]
        opp_ppgs = [p for p in opp_ppgs if pd.notna(p)]
        records.append(
            {
                "team_key": team_key,
                "sos_remaining": sum(opp_ppgs) / len(opp_ppgs) if opp_ppgs else 0.0,
                "games_remaining": len(games),
            }
        )
    return pd.DataFrame(records)
