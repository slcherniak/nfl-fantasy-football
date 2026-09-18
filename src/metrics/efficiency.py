"""Manager efficiency: actual starter points vs the optimal legal lineup.

Optimal lineup is an exact solve (bitmask DP over players x slots), not a
greedy top-scorer pass — greedy overstates optimal whenever FLEX and a base
slot compete for the same player. Eligibility comes from Yahoo's per-player
eligible position list; FLEX accepts the configured position set.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

DEFAULT_SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "W/R/T": 1, "K": 1, "DEF": 1}
DEFAULT_FLEX = {"W/R/T": ["WR", "RB", "TE"]}
DEFAULT_NON_STARTING = ("BN", "IR", "IR+")


@dataclass(frozen=True)
class LineupPlayer:
    player_key: str
    points: float
    eligible_positions: frozenset[str]


def _slot_list(slots: dict[str, int]) -> list[str]:
    expanded: list[str] = []
    for slot, count in slots.items():
        expanded.extend([slot] * count)
    return expanded


def _eligible_for_slot(player: LineupPlayer, slot: str, flex: dict[str, list[str]]) -> bool:
    if slot in flex:
        return bool(player.eligible_positions & set(flex[slot]))
    return slot in player.eligible_positions


def optimal_lineup_points(
    players: list[LineupPlayer],
    slots: dict[str, int] | None = None,
    flex: dict[str, list[str]] | None = None,
) -> float:
    """Maximum points from any legal assignment of players to slots.

    DP over slots with a bitmask of used players; exact for roster sizes in
    the low twenties (2^n masks). Unfillable slots simply contribute nothing,
    matching a real lineup with an empty slot.
    """
    slots = slots or DEFAULT_SLOTS
    flex = flex or DEFAULT_FLEX
    slot_order = _slot_list(slots)
    n = len(players)
    if n > 24:
        raise ValueError(f"Roster of {n} players is too large for exact solve")

    # Fill scarce slots first (fewest eligible players) to prune the DP.
    eligibility = [
        [i for i, p in enumerate(players) if _eligible_for_slot(p, slot, flex)]
        for slot in slot_order
    ]
    order = sorted(range(len(slot_order)), key=lambda s: len(eligibility[s]))

    states: dict[int, float] = {0: 0.0}
    for slot_idx in order:
        next_states: dict[int, float] = {}
        for mask, points in states.items():
            # Leaving the slot empty is always allowed (handles short rosters).
            if points > next_states.get(mask, -1.0):
                next_states[mask] = points
            for i in eligibility[slot_idx]:
                bit = 1 << i
                if mask & bit:
                    continue
                new_points = points + players[i].points
                new_mask = mask | bit
                if new_points > next_states.get(new_mask, -1.0):
                    next_states[new_mask] = new_points
        states = next_states
    return max(states.values()) if states else 0.0


def _roster_players(group: pd.DataFrame) -> list[LineupPlayer]:
    players = []
    for row in group.itertuples():
        eligible = frozenset(
            p for p in str(row.eligible_positions or "").split(",") if p and p not in DEFAULT_NON_STARTING
        )
        players.append(
            LineupPlayer(
                player_key=row.player_key,
                points=float(row.points or 0.0),
                eligible_positions=eligible,
            )
        )
    return players


def weekly_efficiency(
    rosters: pd.DataFrame,
    slots: dict[str, int] | None = None,
    flex: dict[str, list[str]] | None = None,
    non_starting_slots: tuple[str, ...] | list[str] = DEFAULT_NON_STARTING,
) -> pd.DataFrame:
    """Per team-week: actual starter points, optimal points, efficiency,
    and points left on bench (optimal - actual)."""
    records = []
    for (week, team_key), group in rosters.groupby(["week", "team_key"]):
        starters = group[~group["slot"].isin(non_starting_slots)]
        actual = float(starters["points"].fillna(0).sum())
        optimal = optimal_lineup_points(_roster_players(group), slots, flex)
        records.append(
            {
                "week": week,
                "team_key": team_key,
                "actual_points": actual,
                "optimal_points": optimal,
                "efficiency": actual / optimal if optimal > 0 else 1.0,
                "points_on_bench": optimal - actual,
            }
        )
    return pd.DataFrame(records)


def season_efficiency(weekly: pd.DataFrame) -> pd.DataFrame:
    """Cumulative efficiency and total points left on bench per team."""
    season = (
        weekly.groupby("team_key")
        .agg(
            actual_points=("actual_points", "sum"),
            optimal_points=("optimal_points", "sum"),
            points_on_bench=("points_on_bench", "sum"),
        )
        .reset_index()
    )
    season["efficiency"] = (
        season["actual_points"] / season["optimal_points"]
    ).where(season["optimal_points"] > 0, 1.0)
    return season.sort_values("efficiency", ascending=False).reset_index(drop=True)
