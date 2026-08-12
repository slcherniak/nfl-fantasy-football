"""Composite power rankings.

Z-score each component across the league, blend with configurable weights,
rank by the blend. Components: season PPG, last-N-weeks PPG, all-play win %,
actual win % (ties = half a win).
"""

from __future__ import annotations

import pandas as pd

from src.metrics.all_play import season_all_play, weekly_all_play

DEFAULT_WEIGHTS = {
    "season_ppg": 0.35,
    "recent_ppg": 0.25,
    "all_play_pct": 0.25,
    "win_pct": 0.15,
}


def _zscore(series: pd.Series) -> pd.Series:
    std = series.std(ddof=0)
    if std == 0 or pd.isna(std):
        return pd.Series(0.0, index=series.index)
    return (series - series.mean()) / std


def compute_components(matchups: pd.DataFrame, recent_weeks: int = 3) -> pd.DataFrame:
    """Per-team component values from completed matchups."""
    weekly = weekly_all_play(matchups)
    season_ap = season_all_play(weekly).set_index("team_key")

    per_team = matchups.groupby("team_key")
    season_ppg = per_team["points_for"].mean().rename("season_ppg")

    last_weeks = sorted(matchups["week"].unique())[-recent_weeks:]
    recent = matchups[matchups["week"].isin(last_weeks)]
    recent_ppg = recent.groupby("team_key")["points_for"].mean().rename("recent_ppg")

    results = per_team["result"].agg(
        lambda r: (float((r == "W").sum()) + 0.5 * float((r == "T").sum())) / len(r)
    ).rename("win_pct")

    components = pd.concat(
        [season_ppg, recent_ppg, season_ap["ap_win_pct"].rename("all_play_pct"), results],
        axis=1,
    )
    return components.reset_index()


def power_rankings(
    matchups: pd.DataFrame,
    weights: dict[str, float] | None = None,
    recent_weeks: int = 3,
    prior_week_matchups: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Rank teams by the weighted z-score blend.

    When prior_week_matchups is given (matchups through the previous week),
    a 'movement' column shows rank change vs that week (positive = climbed).
    """
    weights = dict(weights or DEFAULT_WEIGHTS)
    total = sum(weights.values())
    weights = {k: v / total for k, v in weights.items()}

    components = compute_components(matchups, recent_weeks).set_index("team_key")
    composite = sum(
        weight * _zscore(components[name]) for name, weight in weights.items()
    )
    ranked = components.assign(composite=composite).sort_values("composite", ascending=False)
    ranked["rank"] = range(1, len(ranked) + 1)

    if prior_week_matchups is not None and not prior_week_matchups.empty:
        prior = power_rankings(prior_week_matchups, weights, recent_weeks)
        prior_rank = prior.set_index("team_key")["rank"]
        ranked["movement"] = (prior_rank.reindex(ranked.index) - ranked["rank"]).fillna(0).astype(int)
    else:
        ranked["movement"] = 0
    return ranked.reset_index()


def movement_arrow(movement: int) -> str:
    if movement > 0:
        return f"▲{movement}"
    if movement < 0:
        return f"▼{-movement}"
    return "–"
