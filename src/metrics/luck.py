"""Luck index and schedule-luck diagnostics.

Luck = Actual Wins - Expected Wins, where Expected Wins is the sum of each
week's all-play win percentage (all-play wins that week / (teams - 1)).
Positive luck = winning more often than scoring justifies. Ties count 0.5
in actual wins, mirroring the 0.5 all-play credit.
"""

from __future__ import annotations

import pandas as pd


def luck_report(matchups: pd.DataFrame, weekly_ap: pd.DataFrame, close_margin: float = 10.0) -> pd.DataFrame:
    """Per-team luck line. Expects completed regular-season matchups."""
    m = matchups.copy()
    m["margin"] = m["points_for"] - m["points_against"]

    # League median score each week, for sad wins / robbed losses.
    m["week_median"] = m.groupby("week")["points_for"].transform("median")

    actual = m.groupby("team_key").agg(
        wins=("result", lambda r: float((r == "W").sum())),
        losses=("result", lambda r: float((r == "L").sum())),
        ties=("result", lambda r: float((r == "T").sum())),
        points_for=("points_for", "sum"),
        points_against=("points_against", "sum"),
    )
    actual["actual_wins"] = actual["wins"] + 0.5 * actual["ties"]

    expected = weekly_ap.groupby("team_key")["ap_win_pct"].sum().rename("expected_wins")

    close = m[m["margin"].abs() < close_margin]
    close_record = close.groupby("team_key").agg(
        close_wins=("result", lambda r: int((r == "W").sum())),
        close_losses=("result", lambda r: int((r == "L").sum())),
        close_ties=("result", lambda r: int((r == "T").sum())),
    )

    sad = (
        m[(m["result"] == "W") & (m["points_for"] < m["week_median"])]
        .groupby("team_key")
        .size()
        .rename("sad_wins")
    )
    robbed = (
        m[(m["result"] == "L") & (m["points_for"] > m["week_median"])]
        .groupby("team_key")
        .size()
        .rename("robbed_losses")
    )

    report = actual.join([expected, close_record, sad, robbed]).fillna(0).reset_index()
    report["luck"] = report["actual_wins"] - report["expected_wins"]
    # Percentile of total points against: 100 = faced the most points
    # (unluckiest schedule), scaled so the top team reads 100.
    report["pa_percentile"] = (report["points_against"].rank(pct=True) * 100).round(1)
    for col in ("close_wins", "close_losses", "close_ties", "sad_wins", "robbed_losses"):
        report[col] = report[col].astype(int)
    return report.sort_values("luck", ascending=False).reset_index(drop=True)
