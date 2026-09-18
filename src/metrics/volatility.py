"""Score volatility: weekly-score standard deviation plus boom/bust rates.

Boom = a week scoring above (league mean + 1 sigma) of all team-week scores;
bust = below (league mean - 1 sigma). Population statistics (ddof=0) so the
thresholds are stable early in the season.
"""

from __future__ import annotations

import pandas as pd


def volatility(matchups: pd.DataFrame) -> pd.DataFrame:
    scores = matchups["points_for"]
    league_mean = scores.mean()
    league_std = scores.std(ddof=0)
    boom_line = league_mean + league_std
    bust_line = league_mean - league_std

    per_team = matchups.groupby("team_key")["points_for"]
    report = per_team.agg(
        ppg="mean",
        std_dev=lambda s: s.std(ddof=0),
        weeks="count",
    )
    report["boom_weeks"] = per_team.apply(lambda s: int((s > boom_line).sum()))
    report["bust_weeks"] = per_team.apply(lambda s: int((s < bust_line).sum()))
    report["boom_rate"] = report["boom_weeks"] / report["weeks"]
    report["bust_rate"] = report["bust_weeks"] / report["weeks"]
    report.attrs["boom_line"] = float(boom_line)
    report.attrs["bust_line"] = float(bust_line)
    return report.reset_index().sort_values("std_dev", ascending=False).reset_index(drop=True)
