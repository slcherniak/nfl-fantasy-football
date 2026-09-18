"""Weekly awards for the league chat.

All awards are derived from one week's completed matchups (plus that week's
efficiency table for 'most points on bench'). Deterministic tie handling:
ties broken by team_key so re-runs never flip a winner arbitrarily.
"""

from __future__ import annotations

import pandas as pd


def weekly_awards(week_matchups: pd.DataFrame, week_efficiency: pd.DataFrame | None = None) -> dict[str, dict]:
    m = week_matchups.sort_values("team_key").reset_index(drop=True)
    awards: dict[str, dict] = {}

    def entry(row: pd.Series, **extra) -> dict:
        return {"team_key": row["team_key"], "points": float(row["points_for"]), **extra}

    highest = m.loc[m["points_for"].idxmax()]
    lowest = m.loc[m["points_for"].idxmin()]
    awards["highest_score"] = entry(highest)
    awards["lowest_score"] = entry(lowest)

    winners = m[m["result"] == "W"].copy()
    if not winners.empty:
        winners["margin"] = winners["points_for"] - winners["points_against"]
        blowout = winners.loc[winners["margin"].idxmax()]
        awards["biggest_blowout"] = entry(
            blowout, opp_key=blowout["opp_key"], margin=float(blowout["margin"])
        )
        # Lucky win of the week: lowest score that still won.
        lucky = winners.loc[winners["points_for"].idxmin()]
        awards["lucky_win"] = entry(lucky, opp_key=lucky["opp_key"])

    decided = m[m["result"].isin(["W", "T"])].copy()
    if not decided.empty:
        decided["margin"] = (decided["points_for"] - decided["points_against"]).abs()
        closest = decided.loc[decided["margin"].idxmin()]
        awards["closest_game"] = entry(
            closest, opp_key=closest["opp_key"], margin=float(closest["margin"])
        )

    losers = m[m["result"] == "L"]
    if not losers.empty:
        # Toughest loss: highest score that still lost.
        tough = losers.loc[losers["points_for"].idxmax()]
        awards["toughest_loss"] = entry(tough, opp_key=tough["opp_key"])

    if week_efficiency is not None and not week_efficiency.empty:
        bench = week_efficiency.sort_values("team_key").reset_index(drop=True)
        most_bench = bench.loc[bench["points_on_bench"].idxmax()]
        awards["most_points_on_bench"] = {
            "team_key": most_bench["team_key"],
            "points": float(most_bench["points_on_bench"]),
        }
    return awards


AWARD_LABELS = {
    "highest_score": "Highest score",
    "lowest_score": "Lowest score",
    "biggest_blowout": "Biggest blowout",
    "closest_game": "Closest game",
    "lucky_win": "Lucky win of the week",
    "toughest_loss": "Toughest loss",
    "most_points_on_bench": "Most points on bench",
}
