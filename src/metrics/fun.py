"""Fun-side metrics: schedule swaps, the championship belt, bench crimes.

All pure functions on the standard matchups/rosters frames, exact-tested in
tests/test_fun.py.
"""

from __future__ import annotations

import pandas as pd

NON_STARTING = ("BN", "IR", "IR+")


def schedule_swap(matchups: pd.DataFrame) -> dict[str, dict[str, dict[str, int]]]:
    """Record every team would have with every other team's schedule.

    Cell [scores_of][schedule_of]: play scores_of's weekly scores against the
    opponents schedule_of actually faced; when that opponent would be
    scores_of itself, face schedule_of instead (you can't play yourself).
    Diagonal is the actual record. Ties count as ties.
    """
    scores = matchups.pivot(index="week", columns="team_key", values="points_for")
    sched = matchups.pivot(index="week", columns="team_key", values="opp_key")
    teams = list(scores.columns)
    out: dict[str, dict[str, dict[str, int]]] = {}
    for a in teams:                      # whose scores
        out[a] = {}
        for b in teams:                  # whose schedule
            w = l = t = 0
            for week in scores.index:
                opp = sched.loc[week, b]
                if opp == a:
                    opp = b
                mine, theirs = scores.loc[week, a], scores.loc[week, opp]
                if pd.isna(mine) or pd.isna(theirs):
                    continue
                if mine > theirs:
                    w += 1
                elif mine < theirs:
                    l += 1
                else:
                    t += 1
            out[a][b] = {"w": w, "l": l, "t": t}
    return out


def championship_belt(matchups: pd.DataFrame) -> dict:
    """Lineal title: week 1's top scorer takes the belt; it changes hands
    only when the holder loses their head-to-head matchup (ties retain)."""
    weeks = sorted(matchups["week"].unique())
    if not weeks:
        return {"holder": None, "reigns": []}
    week1 = matchups[matchups["week"] == weeks[0]]
    holder = week1.loc[week1["points_for"].idxmax(), "team_key"]
    reigns = [{"key": holder, "from": int(weeks[0]), "to": None, "defenses": 0}]
    for week in weeks[1:]:
        game = matchups[(matchups["week"] == week) & (matchups["team_key"] == holder)]
        if game.empty:
            continue
        row = game.iloc[0]
        if row["result"] == "L":
            reigns[-1]["to"] = int(week)
            holder = row["opp_key"]
            reigns.append({"key": holder, "from": int(week), "to": None, "defenses": 0})
        else:
            reigns[-1]["defenses"] += 1
    return {"holder": holder, "reigns": reigns}


def _eligible_for(slot: str, eligible: set[str], flex: dict[str, list[str]]) -> bool:
    if slot in flex:
        return bool(eligible & set(flex[slot]))
    return slot in eligible


def bench_crimes(
    rosters: pd.DataFrame,
    flex: dict[str, list[str]],
    non_starting: tuple[str, ...] | list[str] = NON_STARTING,
) -> pd.DataFrame:
    """The single worst start/sit call per team-week: the legal one-player
    swap (bench player into a starter's slot) that would have gained the
    most points. Rows only where a positive-gain swap existed."""
    records = []
    for (week, team_key), group in rosters.groupby(["week", "team_key"]):
        starters = group[~group["slot"].isin(non_starting)]
        bench = group[group["slot"] == "BN"]  # IR stashes aren't start/sit calls
        best = None
        for b in bench.itertuples():
            b_pts = float(b.points or 0)
            b_elig = set(p for p in str(b.eligible_positions or "").split(",") if p)
            for s in starters.itertuples():
                s_pts = float(s.points or 0)
                if b_pts <= s_pts or not _eligible_for(s.slot, b_elig, flex):
                    continue
                gain = b_pts - s_pts
                if best is None or gain > best["gain"]:
                    best = {
                        "week": int(week), "team_key": team_key,
                        "benched": b.player_name, "benched_pts": round(b_pts, 1),
                        "started": s.player_name, "started_pts": round(s_pts, 1),
                        "slot": s.slot, "gain": round(gain, 1),
                    }
        if best:
            records.append(best)
    return pd.DataFrame(records)
