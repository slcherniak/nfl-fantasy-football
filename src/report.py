"""Assemble the metric outputs into the presentation tables for each tab."""

from __future__ import annotations

import logging
import sqlite3

import pandas as pd

from src import load
from src.config import Settings
from src.metrics import all_play, awards, efficiency, luck, power_rankings, sos, volatility
from src.simulate import simulate_playoff_odds

logger = logging.getLogger(__name__)


def _name_map(teams: pd.DataFrame) -> dict[str, str]:
    return dict(zip(teams["team_key"], teams["name"]))


def _label(df: pd.DataFrame, names: dict[str, str], col: str = "team_key") -> pd.DataFrame:
    out = df.copy()
    out.insert(0, "team", out[col].map(names).fillna(out[col]))
    return out.drop(columns=[col])


def build_tables(conn: sqlite3.Connection, settings: Settings) -> dict[str, pd.DataFrame | list[list]]:
    teams = load.load_teams(conn)
    names = _name_map(teams)
    matchups = load.load_matchups(conn)
    if matchups.empty:
        raise RuntimeError("No completed matchups in the database — run --backfill first.")
    remaining = load.load_schedule_remaining(conn)
    rosters = load.load_rosters(conn)
    tabs = settings.sheet_tabs

    weeks = sorted(matchups["week"].unique())
    latest_week = weeks[-1]
    prior = matchups[matchups["week"] < latest_week]

    weekly_ap = all_play.weekly_all_play(matchups)
    season_ap = all_play.season_all_play(weekly_ap)
    luck_df = luck.luck_report(matchups, weekly_ap, settings.close_game_margin)
    power = power_rankings.power_rankings(
        matchups,
        settings.power_weights,
        settings.recent_weeks,
        prior_week_matchups=prior if not prior.empty else None,
    )
    vol = volatility.volatility(matchups)
    sos_played = sos.played_sos(matchups)

    eff_weekly = efficiency.weekly_efficiency(
        rosters, settings.roster_slots, settings.flex_eligibility,
        settings.non_starting_slots,
    ) if not rosters.empty else pd.DataFrame(
        columns=["week", "team_key", "actual_points", "optimal_points", "efficiency", "points_on_bench"]
    )
    eff_season = efficiency.season_efficiency(eff_weekly) if not eff_weekly.empty else eff_weekly

    playoff_cfg = settings.playoffs
    odds = None
    if not remaining.empty:
        odds = simulate_playoff_odds(
            matchups,
            remaining,
            num_playoff_teams=int(playoff_cfg["num_playoff_teams"]),
            num_byes=int(playoff_cfg["num_byes"]),
            num_sims=settings.num_sims,
            random_seed=settings.random_seed,
        )

    tables: dict[str, pd.DataFrame | list[list]] = {}

    # --- Dashboard -----------------------------------------------------
    dash = power.merge(season_ap[["team_key", "ap_wins", "ap_losses"]], on="team_key")
    dash = dash.merge(luck_df[["team_key", "actual_wins", "expected_wins", "luck"]], on="team_key")
    if odds is not None:
        dash = dash.merge(
            odds[["team_key", "playoffs_odds", "bye_odds", "title_odds"]], on="team_key", how="left"
        )
    dash["move"] = dash["movement"].map(power_rankings.movement_arrow)
    dash = dash.drop(columns=["movement"])
    ordered = ["rank", "move", "team_key", "composite", "season_ppg", "recent_ppg",
               "all_play_pct", "win_pct", "ap_wins", "ap_losses", "actual_wins",
               "expected_wins", "luck"]
    ordered += [c for c in ("playoffs_odds", "bye_odds", "title_odds") if c in dash.columns]
    tables[tabs["dashboard"]] = _label(dash[ordered], names)

    # --- All-Play Matrix -----------------------------------------------
    matrix = all_play.all_play_matrix(matchups)
    matrix.index = [names.get(k, k) for k in matrix.index]
    matrix.columns = [names.get(k, k) for k in matrix.columns]
    tables[tabs["all_play_matrix"]] = matrix.reset_index(names="team")

    # --- Luck Report ----------------------------------------------------
    tables[tabs["luck_report"]] = _label(luck_df, names)

    # --- Manager Efficiency ----------------------------------------------
    if not eff_season.empty:
        eff_table = _label(eff_season, names)
    else:
        eff_table = pd.DataFrame([{"note": "Roster data not yet fetched (Phase 2)"}])
    tables[tabs["efficiency"]] = eff_table

    # --- Weekly Log -------------------------------------------------------
    log = matchups.merge(weekly_ap[["week", "team_key", "ap_wins", "ap_win_pct"]],
                         on=["week", "team_key"])
    if not eff_weekly.empty:
        log = log.merge(eff_weekly[["week", "team_key", "points_on_bench"]],
                        on=["week", "team_key"], how="left")
    log["opponent"] = log["opp_key"].map(names).fillna(log["opp_key"])
    award_flags = _award_flags(matchups, eff_weekly)
    log = log.merge(award_flags, on=["week", "team_key"], how="left")
    log["awards"] = log["awards"].fillna("")
    keep = ["week", "team_key", "opponent", "points_for", "points_against",
            "result", "ap_wins", "ap_win_pct", "awards"]
    if "points_on_bench" in log.columns:
        keep.insert(-1, "points_on_bench")
    tables[tabs["weekly_log"]] = _label(log[keep].sort_values(["week", "team_key"]), names)

    # --- Trends (PPG-by-week pivot, then volatility/SOS block) --------------
    trend = matchups.pivot(index="week", columns="team_key", values="points_for")
    trend.columns = [names.get(k, k) for k in trend.columns]
    trend = trend.reset_index()
    vol_named = _label(vol.merge(sos_played, on="team_key"), names)
    if not remaining.empty:
        rem = sos.remaining_sos(matchups, remaining)
        vol_named = vol_named.merge(_label(rem, names), on="team", how="left")
    tables[tabs["trends"]] = _stack_blocks(trend, vol_named)

    # --- _raw ----------------------------------------------------------------
    tables[tabs["raw_matchups"]] = matchups
    snapshot = pd.read_sql_query(
        "SELECT * FROM standings_snapshot ORDER BY week, team_key", conn
    )
    tables[tabs["raw_standings"]] = snapshot

    logger.info("Built %d tables through week %d", len(tables), latest_week)
    return tables


def _award_flags(matchups: pd.DataFrame, eff_weekly: pd.DataFrame) -> pd.DataFrame:
    """One 'awards' string per team-week (comma-joined labels)."""
    flags: dict[tuple[int, str], list[str]] = {}
    for week, group in matchups.groupby("week"):
        week_eff = eff_weekly[eff_weekly["week"] == week] if not eff_weekly.empty else None
        for key, award in awards.weekly_awards(group, week_eff).items():
            flags.setdefault((week, award["team_key"]), []).append(awards.AWARD_LABELS[key])
    rows = [
        {"week": week, "team_key": team_key, "awards": ", ".join(labels)}
        for (week, team_key), labels in flags.items()
    ]
    return pd.DataFrame(rows, columns=["week", "team_key", "awards"])


def _stack_blocks(*frames: pd.DataFrame) -> list[list]:
    """Stack tables vertically on one tab (each with its own header row),
    separated by a blank row, as raw sheet values."""
    values: list[list] = []
    for i, df in enumerate(frames):
        if i:
            values.append([])
        values.append([str(c) for c in df.columns])
        for row in df.itertuples(index=False):
            values.append(
                ["" if pd.isna(v) else (round(v, 2) if isinstance(v, float) else v) for v in row]
            )
    return values
