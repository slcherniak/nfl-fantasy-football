"""Generate the Film Room web dashboard (HTML) from the SQLite store.

Assembles every dataset the page needs — luck quadrant, manager quadrant,
consistency quadrant, score distributions, retroactive playoff-odds race,
small-multiple trajectories, all-play heatmap, weekly awards — and injects
them as JSON into src/templates/dashboard.html. The output file is published
as a claude.ai artifact; this module only produces the HTML.

Team colors: 12-slot categorical palette validated (light + dark surfaces)
with the dataviz palette checker; assigned by stable team_id order so a team
keeps its color all season. The one CVD warn-band pair is covered by direct
labels on every chart.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

from src import load
from src.config import Settings
from src.metrics import all_play, awards, efficiency, fun, luck, power_rankings, volatility
from src.simulate import simulate_playoff_odds

TEMPLATE_PATH = Path(__file__).parent / "templates" / "dashboard.html"

PALETTE_LIGHT = [
    "#2a78d6", "#eb6834", "#1baf7a", "#9c5b21", "#e87ba4", "#4a3aa7",
    "#eda100", "#00879e", "#e34948", "#008300", "#b04fc4", "#77820a",
]
PALETTE_DARK = [
    "#3987e5", "#d95926", "#199e70", "#b5793f", "#d55181", "#9085e9",
    "#c98500", "#00a7c0", "#e66767", "#008300", "#c46ad6", "#849016",
]

ODDS_RACE_SIMS = 3000
ODDS_RACE_SEED = 42


def _round_map(d: dict, digits: int = 2) -> dict:
    return {k: (round(v, digits) if isinstance(v, float) else v) for k, v in d.items()}


def build_data(conn: sqlite3.Connection, settings: Settings) -> dict:
    teams = load.load_teams(conn).sort_values("team_id").reset_index(drop=True)
    matchups = load.load_matchups(conn)
    if matchups.empty:
        raise RuntimeError("No completed matchups — run the pipeline first.")
    rosters = load.load_rosters(conn)
    all_rows = pd.read_sql_query(
        "SELECT * FROM matchups WHERE is_playoffs = 0 ORDER BY week, team_key", conn
    )

    weeks = sorted(matchups["week"].unique())
    latest = int(weeks[-1])
    names = dict(zip(teams["team_key"], teams["name"]))

    team_meta = [
        {
            "key": row.team_key,
            "name": row.name,
            "manager": row.manager,
            "color": PALETTE_LIGHT[i % len(PALETTE_LIGHT)],
            "colorDark": PALETTE_DARK[i % len(PALETTE_DARK)],
        }
        for i, row in enumerate(teams.itertuples())
    ]

    # --- core metrics -----------------------------------------------------
    weekly_ap = all_play.weekly_all_play(matchups)
    luck_df = luck.luck_report(matchups, weekly_ap, settings.close_game_margin).set_index("team_key")
    vol = volatility.volatility(matchups).set_index("team_key")
    power = power_rankings.power_rankings(
        matchups, settings.power_weights, settings.recent_weeks,
        prior_week_matchups=matchups[matchups["week"] < latest],
    ).set_index("team_key")

    eff_weekly = efficiency.weekly_efficiency(
        rosters, settings.roster_slots, settings.flex_eligibility, settings.non_starting_slots
    ) if not rosters.empty else pd.DataFrame()
    eff_season = efficiency.season_efficiency(eff_weekly).set_index("team_key") if not eff_weekly.empty else None

    playoff_cfg = settings.playoffs
    remaining = all_rows[all_rows["is_complete"] == 0][["week", "team_key", "opp_key"]]
    final_odds = simulate_playoff_odds(
        matchups, remaining,
        num_playoff_teams=int(playoff_cfg["num_playoff_teams"]),
        num_byes=int(playoff_cfg["num_byes"]),
        num_sims=settings.num_sims, random_seed=ODDS_RACE_SEED,
    ).set_index("team_key") if not remaining.empty else None

    # --- standings table --------------------------------------------------
    season_ap_idx = all_play.season_all_play(weekly_ap).set_index("team_key")
    standings = []
    for key in teams["team_key"]:
        lrow = luck_df.loc[key]
        ap = season_ap_idx.loc[key]
        # ap_wins/ap_losses carry the 0.5-per-tie split; undo it for a
        # displayable integer W-L-T line.
        ap_t = int(ap["ap_ties"])
        ap_w = int(round(float(ap["ap_wins"]) - 0.5 * ap_t))
        ap_l = int(round(float(ap["ap_losses"]) - 0.5 * ap_t))
        row = _round_map({
            "key": key,
            "record": f"{int(lrow['wins'])}-{int(lrow['losses'])}" + (f"-{int(lrow['ties'])}" if lrow["ties"] else ""),
            "allPlay": f"{ap_w}-{ap_l}" + (f"-{ap_t}" if ap_t else ""),
            "wins": float(lrow["actual_wins"]),
            "pf": round(float(lrow["points_for"]), 1),
            "pa": round(float(lrow["points_against"]), 1),
            "ppg": round(float(vol.loc[key, "ppg"]), 1),
            "luck": float(lrow["luck"]),
            "powerRank": int(power.loc[key, "rank"]),
            "move": int(power.loc[key, "movement"]),
            "playoffOdds": float(final_odds.loc[key, "playoffs_odds"]) if final_odds is not None else None,
            "titleOdds": float(final_odds.loc[key, "title_odds"]) if final_odds is not None else None,
        })
        # Three decimals, past _round_map's default two: .596 and .600 differ.
        row["apPct"] = round(float(ap["ap_win_pct"]), 3)
        standings.append(row)
    standings.sort(key=lambda r: (-r["wins"], -r["pf"]))
    for i, row in enumerate(standings):
        row["rank"] = i + 1

    # --- quadrants ----------------------------------------------------------
    luck_quad = [
        _round_map({"key": k, "x": float(luck_df.loc[k, "expected_wins"]),
                    "y": float(luck_df.loc[k, "actual_wins"])})
        for k in teams["team_key"]
    ]
    mgr_quad = [
        _round_map({"key": k,
                    "x": round(float(eff_season.loc[k, "optimal_points"]) / len(weeks), 1),
                    "y": round(float(eff_season.loc[k, "efficiency"]) * 100, 1)})
        for k in teams["team_key"]
    ] if eff_season is not None else []
    # Floor vs ceiling: worst and best week, with PPG for the tooltip.
    # Raw sigma mislabels high-floor teams as boom/bust (upside variance is
    # not risk); min/max separates downside from upside honestly.
    per_team_scores = matchups.groupby("team_key")["points_for"]
    lo_hi = per_team_scores.agg(["min", "max"])
    range_quad = [
        _round_map({"key": k,
                    "x": round(float(lo_hi.loc[k, "min"]), 1),
                    "y": round(float(lo_hi.loc[k, "max"]), 1),
                    "ppg": round(float(vol.loc[k, "ppg"]), 1)})
        for k in teams["team_key"]
    ]

    # --- weekly scores (trajectories + distribution strips) -----------------
    pivot = matchups.pivot(index="week", columns="team_key", values="points_for")
    weekly_scores = {k: [round(float(v), 2) for v in pivot[k]] for k in pivot.columns}
    medians = [round(float(m), 2) for m in pivot.median(axis=1)]

    # --- retroactive playoff-odds race --------------------------------------
    race_weeks, race = [], {k: [] for k in teams["team_key"]}
    for w in range(2, latest + 1):
        past = matchups[matchups["week"] <= w]
        future = all_rows[(all_rows["week"] > w)][["week", "team_key", "opp_key"]]
        if future.empty:
            continue
        odds_w = simulate_playoff_odds(
            past, future,
            num_playoff_teams=int(playoff_cfg["num_playoff_teams"]),
            num_byes=int(playoff_cfg["num_byes"]),
            num_sims=ODDS_RACE_SIMS, random_seed=ODDS_RACE_SEED,
        ).set_index("team_key")
        race_weeks.append(w)
        for k in teams["team_key"]:
            race[k].append(round(float(odds_w.loc[k, "playoffs_odds"]), 3))

    # --- all-play heatmap -----------------------------------------------------
    season_ap = season_ap_idx.reset_index().sort_values("ap_wins", ascending=False)
    order = list(season_ap["team_key"])
    cells = {}
    for a in order:
        cells[a] = {}
        for b in order:
            if a == b:
                continue
            both = pivot[[a, b]].dropna()
            w = int((both[a] > both[b]).sum())
            l = int((both[a] < both[b]).sum())
            t = int((both[a] == both[b]).sum())
            games = w + l + t
            cells[a][b] = {"w": w, "l": l, "t": t,
                           "pct": round((w + 0.5 * t) / games, 3) if games else 0.0}

    # --- transactions: pickups, FAAB market, trade scoreboard ----------------
    txns = pd.read_sql_query("SELECT * FROM transactions", conn)
    starter_slots = set(settings.non_starting_slots)
    txn_data = {"pickups": [], "faabMarket": [], "trades": []}
    best_pickup_tile = None
    if not txns.empty and not rosters.empty:
        def points_since(player_key: str, team_key: str, from_week: int) -> tuple[float, float, int]:
            r = rosters[(rosters.player_key == player_key) & (rosters.team_key == team_key)
                        & (rosters.week >= from_week)]
            started = r[~r.slot.isin(starter_slots)]
            return float(started.points.fillna(0).sum()), float(r.points.fillna(0).sum()), int(len(started))

        adds = txns[txns.move_type == "add"].sort_values("timestamp")
        adds = adds.drop_duplicates(subset=["team_key", "player_key"], keep="first")
        graded = []
        for a in adds.itertuples():
            started, rostered, n_starts = points_since(a.player_key, a.team_key, int(a.week))
            graded.append({
                "key": a.team_key, "player": a.player_name, "week": int(a.week),
                "faab": None if pd.isna(a.faab_spent) else int(a.faab_spent),
                "started": round(started, 1), "rostered": round(rostered, 1),
                "starts": n_starts,
            })
        graded.sort(key=lambda g: -g["started"])
        txn_data["pickups"] = graded[:10]
        txn_data["faabMarket"] = [g for g in graded if (g["faab"] or 0) > 0]

        week_adds = [g for g in graded if g["week"] == latest and g["started"] > 0]
        if week_adds:
            best = max(week_adds, key=lambda g: g["started"])
            best_pickup_tile = {"label": "Best pickup", "key": best["key"],
                                "points": best["started"], "who": best["player"]}

        trades = txns[txns.move_type == "trade-in"]
        slots_cfg, flex_cfg = settings.roster_slots, settings.flex_eligibility
        match_idx = {
            (int(r.week), r.team_key): (float(r.points_for), float(r.points_against), r.result)
            for r in matchups.itertuples()
        }

        def lineup_of(rows) -> list[efficiency.LineupPlayer]:
            players = []
            for r in rows.itertuples():
                elig = frozenset(p for p in str(r.eligible_positions or "").split(",")
                                 if p and p not in starter_slots)
                players.append(efficiency.LineupPlayer(r.player_key, float(r.points or 0.0), elig))
            return players

        def sent_player_rows(sent, week):
            """A sent player's real stat line that week, wherever they are."""
            rows = []
            for p in sent:
                r = rosters[(rosters.player_key == p.player_key) & (rosters.week == week)]
                if len(r):
                    rows.append(r.iloc[0])
            return rows

        seen_trades = set()
        for ts, group in sorted(trades.groupby("timestamp"), key=lambda kv: -kv[0]):
            group = group.drop_duplicates(subset=["team_key", "player_key"])
            if group.team_key.nunique() != 2:
                continue
            wk0 = int(group.week.iloc[0])
            sig = (wk0, frozenset(zip(group.team_key, group.player_key)))
            if sig in seen_trades:   # same trade re-keyed by a timezone shift
                continue
            seen_trades.add(sig)

            team_players = {tk: list(g.itertuples()) for tk, g in group.groupby("team_key")}
            team_keys = list(team_players)
            sides = []
            for tk in team_keys:
                received = team_players[tk]
                sent = team_players[team_keys[1] if tk == team_keys[0] else team_keys[0]]
                rec_keys = {p.player_key for p in received}
                players = []
                for p in received:
                    started, rostered, _ = points_since(p.player_key, tk, wk0)
                    players.append({"name": p.player_name, "started": round(started, 1),
                                    "rostered": round(rostered, 1)})
                players.sort(key=lambda q: -q["started"])

                # Counterfactual replay: each week, this roster vs the same
                # roster with the trade undone (sent players' real lines back).
                swing, flips = 0.0, []
                for w in range(wk0, latest + 1):
                    mine = rosters[(rosters.team_key == tk) & (rosters.week == w)]
                    if mine.empty:
                        continue
                    cf_players = lineup_of(mine[~mine.player_key.isin(rec_keys)])
                    sent_rows = sent_player_rows(sent, w)
                    for rr in sent_rows:
                        elig = frozenset(x for x in str(rr.eligible_positions or "").split(",")
                                         if x and x not in starter_slots)
                        cf_players.append(efficiency.LineupPlayer(rr.player_key, float(rr.points or 0.0), elig))
                    a_opt = efficiency.optimal_lineup_points(lineup_of(mine), slots_cfg, flex_cfg)
                    c_opt = efficiency.optimal_lineup_points(cf_players, slots_cfg, flex_cfg)
                    swing += a_opt - c_opt

                    # Wins flipped: undo only the started received players and
                    # refill their exact slots optimally from bench + sent.
                    game = match_idx.get((w, tk))
                    started_rec = mine[mine.player_key.isin(rec_keys) & ~mine.slot.isin(starter_slots)]
                    if game and len(started_rec):
                        pf, pa, res = game
                        vacated = started_rec.slot.value_counts().to_dict()
                        removed = float(started_rec.points.fillna(0).sum())
                        pool = lineup_of(mine[mine.slot.isin(starter_slots) & ~mine.player_key.isin(rec_keys)])
                        for rr in sent_rows:
                            elig = frozenset(x for x in str(rr.eligible_positions or "").split(",")
                                             if x and x not in starter_slots)
                            pool.append(efficiency.LineupPlayer(rr.player_key, float(rr.points or 0.0), elig))
                        refill = efficiency.optimal_lineup_points(pool, vacated, flex_cfg)
                        cf_pf = pf - removed + refill
                        cf_res = "T" if abs(cf_pf - pa) < 1e-9 else ("W" if cf_pf > pa else "L")
                        if cf_res != res:
                            # the trade turned a would-be cf_res into res
                            flips.append({"week": w, "from": cf_res, "to": res})

                sides.append({"key": tk, "players": players,
                              "total": round(sum(q["started"] for q in players), 1),
                              "swing": round(swing, 1), "flips": flips})
            txn_data["trades"].append({"week": wk0, "sides": sides})

    # --- schedule swap, belt, bench crimes -----------------------------------
    swap = fun.schedule_swap(matchups)
    wins_actual = {k: swap[k][k]["w"] + 0.5 * swap[k][k]["t"] for k in swap}
    swap_order = sorted(swap, key=lambda k: -wins_actual[k])
    belt = fun.championship_belt(matchups)
    crimes = fun.bench_crimes(rosters, settings.flex_eligibility, settings.non_starting_slots) \
        if not rosters.empty else pd.DataFrame()
    crime_data = {"perTeam": [], "thisWeek": []}
    if not crimes.empty:
        season_tot = crimes.groupby("team_key")["gain"].sum()
        for k, g in crimes.groupby("team_key"):
            worst = g.loc[g["gain"].idxmax()]
            crime_data["perTeam"].append({
                "key": k, "total": round(float(season_tot[k]), 1),
                "worst": {"week": int(worst["week"]), "benched": worst["benched"],
                          "benchedPts": worst["benched_pts"], "started": worst["started"],
                          "startedPts": worst["started_pts"], "slot": worst["slot"],
                          "gain": worst["gain"]},
            })
        crime_data["perTeam"].sort(key=lambda r: -r["total"])
        week_c = crimes[crimes["week"] == latest].sort_values("gain", ascending=False)
        crime_data["thisWeek"] = [
            {"key": r.team_key, "benched": r.benched, "benchedPts": r.benched_pts,
             "started": r.started, "startedPts": r.started_pts, "slot": r.slot, "gain": r.gain}
            for r in week_c.head(5).itertuples()
        ]

    # --- this week's leverage (odds if win vs if lose next matchup) ----------
    leverage = []
    if not remaining.empty:
        next_week = int(remaining["week"].min())
        next_games = remaining[remaining["week"] == next_week]
        pairs = {tuple(sorted((r.team_key, r.opp_key))) for r in next_games.itertuples()}
        ppg_now = matchups.groupby("team_key")["points_for"].mean()
        for a, b in sorted(pairs):
            cond_odds = {}
            for winner, loser in ((a, b), (b, a)):
                synth = pd.DataFrame([
                    {"week": next_week, "team_key": winner, "opp_key": loser,
                     "points_for": float(ppg_now.get(winner, 100)) + 5,
                     "points_against": float(ppg_now.get(loser, 100)) - 5,
                     "result": "W", "is_playoffs": 0, "is_complete": 1},
                    {"week": next_week, "team_key": loser, "opp_key": winner,
                     "points_for": float(ppg_now.get(loser, 100)) - 5,
                     "points_against": float(ppg_now.get(winner, 100)) + 5,
                     "result": "L", "is_playoffs": 0, "is_complete": 1},
                ])
                cond = simulate_playoff_odds(
                    pd.concat([matchups, synth], ignore_index=True),
                    remaining[remaining["week"] > next_week],
                    num_playoff_teams=int(playoff_cfg["num_playoff_teams"]),
                    num_byes=int(playoff_cfg["num_byes"]),
                    num_sims=1500, random_seed=ODDS_RACE_SEED,
                ).set_index("team_key")["playoffs_odds"]
                cond_odds[winner] = cond
            for team, opp in ((a, b), (b, a)):
                if_win = float(cond_odds[team].get(team, 0))
                if_loss = float(cond_odds[opp].get(team, 0))
                leverage.append({"key": team, "opp": opp, "week": next_week,
                                 "ifWin": round(if_win, 3), "ifLoss": round(if_loss, 3),
                                 "swing": round(if_win - if_loss, 3)})
        leverage.sort(key=lambda r: -r["swing"])

    # --- seed probability matrix ----------------------------------------------
    seed_matrix = []
    if final_odds is not None:
        n_seeds = int(playoff_cfg["num_playoff_teams"])
        for k in teams["team_key"]:
            seed_matrix.append({"key": k, "seeds": [
                round(float(final_odds.loc[k, f"seed{s}_odds"]), 3) for s in range(1, n_seeds + 1)
            ]})
        seed_matrix.sort(key=lambda r: -sum(r["seeds"]))

    # --- power ranking history (retroactive bump chart) ------------------------
    power_weeks, power_ranks = [], {k: [] for k in teams["team_key"]}
    for w in range(2, latest + 1):
        pw = power_rankings.power_rankings(
            matchups[matchups["week"] <= w], settings.power_weights, settings.recent_weeks
        ).set_index("team_key")["rank"]
        power_weeks.append(w)
        for k in teams["team_key"]:
            power_ranks[k].append(int(pw.get(k, len(teams))))

    # --- draft report card -------------------------------------------------------
    draft = pd.read_sql_query("SELECT * FROM draft_picks ORDER BY overall", conn)
    draft_card = None
    if not draft.empty and not rosters.empty:
        season_pts = rosters.groupby("player_key")["points"].sum()
        draft["points"] = draft["player_key"].map(season_pts).fillna(0.0).round(1)
        picks = [
            {"overall": int(r.overall), "round": int(r.round), "player": r.player_name,
             "position": r.position, "key": r.team_key, "points": float(r.points)}
            for r in draft.itertuples()
        ]
        late = [p for p in picks if p["round"] >= 8]
        early = [p for p in picks if p["round"] <= 3]
        team_totals = draft.groupby("team_key")["points"].sum().round(1)
        draft_card = {
            "steals": sorted(late, key=lambda p: -p["points"])[:5],
            "busts": sorted(early, key=lambda p: p["points"])[:5],
            "teams": sorted(
                [{"key": k, "points": float(v)} for k, v in team_totals.items()],
                key=lambda r: -r["points"],
            ),
        }

    # --- trade deadline war room (until Nov 28) ---------------------------------
    war_room = None
    if final_odds is not None and not rosters.empty and date.today() <= date(2026, 11, 28):
        starters = rosters[~rosters.slot.isin(settings.non_starting_slots)].copy()
        base_slot = starters[starters.slot.isin(["QB", "RB", "WR", "TE", "K", "DEF"])]
        slot_avg = base_slot.groupby("slot")["points"].mean()
        bubble = []
        for k in teams["team_key"]:
            odds_k = float(final_odds.loc[k, "playoffs_odds"])
            if not (0.10 <= odds_k <= 0.90):
                continue
            own = base_slot[base_slot.team_key == k].groupby("slot")["points"].mean()
            deficits = {s: float(own.get(s, 0) - slot_avg[s]) for s in slot_avg.index}
            weak = min(deficits, key=deficits.get)
            lev = next((l for l in leverage if l["key"] == k), None)
            bubble.append({"key": k, "odds": round(odds_k, 3), "weakSlot": weak,
                           "deficit": round(deficits[weak], 1),
                           "swing": lev["swing"] if lev else None})
        bubble.sort(key=lambda r: -r["odds"])
        war_room = {"deadline": "Nov 28", "bubble": bubble}

    # --- latest-week awards ----------------------------------------------------
    week_m = matchups[matchups["week"] == latest]
    week_eff = eff_weekly[eff_weekly["week"] == latest] if not eff_weekly.empty else None
    week_awards = awards.weekly_awards(week_m, week_eff)
    tiles = {}
    for slot, label in (("highest_score", "Top score"), ("lucky_win", "Lucky win"),
                        ("toughest_loss", "Toughest loss"), ("most_points_on_bench", "Left on bench")):
        a = week_awards.get(slot)
        if a:
            tiles[slot] = {"label": label, "key": a["team_key"], "points": round(a["points"], 1)}
    if best_pickup_tile:
        tiles["best_pickup"] = best_pickup_tile

    return {
        "asOfWeek": latest,
        "generated": date.today().isoformat(),
        "numPlayoffTeams": int(playoff_cfg["num_playoff_teams"]),
        "teams": team_meta,
        "standings": standings,
        "luckQuad": luck_quad,
        "mgrQuad": mgr_quad,
        "rangeQuad": range_quad,
        "weekly": {"weeks": [int(w) for w in weeks], "scores": weekly_scores, "median": medians},
        "oddsRace": {"weeks": race_weeks, "odds": race},
        "allPlay": {"order": order, "cells": cells},
        "scheduleSwap": {"order": swap_order, "cells": swap},
        "belt": belt,
        "crimes": crime_data,
        "leverage": leverage,
        "seedMatrix": seed_matrix,
        "powerHistory": {"weeks": power_weeks, "ranks": power_ranks},
        "draftCard": draft_card,
        "warRoom": war_room,
        "txns": txn_data,
        "tiles": tiles,
    }


def render(conn: sqlite3.Connection, settings: Settings, out_path: str | Path) -> Path:
    data = build_data(conn, settings)
    template = TEMPLATE_PATH.read_text()
    html = template.replace("/*__DATA__*/null", json.dumps(data, separators=(",", ":")))
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    return out


if __name__ == "__main__":
    from src import models
    from src.config import load_settings
    out = render(models.connect(), load_settings(), "data/film_room.html")
    print(f"wrote {out}")
    # Same page, committed path: public/index.html is what Vercel serves as a
    # static site, so every pipeline run refreshes the deployable copy too.
    site = Path("public/index.html")
    site.parent.mkdir(parents=True, exist_ok=True)
    site.write_text(out.read_text())
    print(f"wrote {site}")
