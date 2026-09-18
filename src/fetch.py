"""Yahoo API pulls -> normalized rows -> idempotent SQLite upserts.

Everything here is keyed on (week, team/player key) so re-running any week —
including the Thursday stat-correction pass — overwrites in place instead of
duplicating. Auto-detected league facts (num_teams, faab_budget, current_week,
game_key) land in league_meta; nothing league-shaped is hardcoded.

yfpy returns typed objects whose exact attribute layout has shifted between
versions, so all attribute access goes through small tolerant helpers.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any

from src import models

logger = logging.getLogger(__name__)

# Gentle pacing for the roster loop (num_teams x num_weeks calls).
ROSTER_CALL_SLEEP_SECONDS = 0.5


# --------------------------------------------------------------------------
# Tolerant accessors for yfpy objects
# --------------------------------------------------------------------------

def _get(obj: Any, *names: str, default: Any = None) -> Any:
    """Return the first present, non-None attribute among names."""
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return default


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _team_points(team: Any) -> float | None:
    points = _get(team, "team_points")
    if points is not None:
        return _float(_get(points, "total"))
    return _float(_get(team, "points"))


def _manager_name(team: Any) -> str | None:
    managers = _get(team, "managers", default=[])
    if not isinstance(managers, list):
        managers = [managers]
    names = []
    for m in managers:
        manager = _get(m, "manager", default=m)
        name = _text(_get(manager, "nickname", "name"))
        if name:
            names.append(name)
    return ", ".join(names) or None


# --------------------------------------------------------------------------
# League metadata
# --------------------------------------------------------------------------

def fetch_league_meta(query: Any, conn: sqlite3.Connection, expected_num_teams: int) -> dict[str, Any]:
    """Pull league info + settings; persist auto-detected facts."""
    league = query.get_league_info()
    settings = query.get_league_settings()

    num_teams = _int(_get(league, "num_teams"))
    current_week = _int(_get(league, "current_week"), default=1)
    start_week = _int(_get(league, "start_week"), default=1)
    end_week = _int(_get(league, "end_week"), default=17)
    game_key = _text(_get(league, "game_code")) or ""
    league_key = _text(_get(league, "league_key")) or ""
    playoff_start_week = _int(_get(settings, "playoff_start_week"), default=15)
    faab_budget = _get(settings, "faab_budget", "uses_faab")

    if num_teams and num_teams != expected_num_teams:
        logger.warning(
            "!!! TEAM COUNT MISMATCH: API reports %s teams, config expected %s. "
            "All denominators derive from the API value — update "
            "config.yaml:league.expected_num_teams to silence this. !!!",
            num_teams,
            expected_num_teams,
        )

    meta = {
        "num_teams": num_teams,
        "current_week": current_week,
        "start_week": start_week,
        "end_week": end_week,
        "playoff_start_week": playoff_start_week,
        "league_key": league_key,
        "game_key": game_key,
        "faab_budget": faab_budget,
    }
    for key, value in meta.items():
        if value is not None:
            models.set_meta(conn, key, value)
    logger.info("League meta: %s", meta)
    return meta


def fetch_teams(query: Any, conn: sqlite3.Connection) -> int:
    teams = query.get_league_teams()
    rows = []
    for team in teams:
        team = _get(team, "team", default=team)
        logo = _get(team, "team_logos", default=[])
        if isinstance(logo, list) and logo:
            logo = _get(_get(logo[0], "team_logo", default=logo[0]), "url")
        else:
            logo = None
        rows.append(
            {
                "team_key": _text(_get(team, "team_key")),
                "team_id": _int(_get(team, "team_id")),
                "name": _text(_get(team, "name")),
                "manager": _manager_name(team),
                "logo_url": _text(logo),
            }
        )
    count = models.upsert(conn, "teams", rows, ["team_key"])
    logger.info("Upserted %d teams", count)
    return count


# --------------------------------------------------------------------------
# Matchups / scoreboard
# --------------------------------------------------------------------------

def fetch_matchups_for_week(query: Any, conn: sqlite3.Connection, week: int) -> int:
    """Store both sides of every matchup for a week (completed or future)."""
    matchups = query.get_league_matchups_by_week(week)
    rows: list[dict[str, Any]] = []
    for matchup in matchups:
        matchup = _get(matchup, "matchup", default=matchup)
        status = _text(_get(matchup, "status")) or ""
        is_complete = 1 if status == "postevent" else 0
        is_playoffs = _int(_get(matchup, "is_playoffs"))
        is_tied = _int(_get(matchup, "is_tied"))
        winner_key = _text(_get(matchup, "winner_team_key"))

        teams = _get(matchup, "teams", default=[])
        teams = [_get(t, "team", default=t) for t in teams]
        if len(teams) != 2:
            logger.warning("Week %s: matchup without exactly 2 teams, skipping", week)
            continue

        for me, opp in ((teams[0], teams[1]), (teams[1], teams[0])):
            my_key = _text(_get(me, "team_key"))
            my_points = _team_points(me)
            opp_points = _team_points(opp)
            if is_complete:
                if is_tied:
                    result = "T"
                elif winner_key:
                    result = "W" if my_key == winner_key else "L"
                elif my_points is not None and opp_points is not None:
                    result = "T" if my_points == opp_points else ("W" if my_points > opp_points else "L")
                else:
                    result = None
            else:
                result = None
            rows.append(
                {
                    "week": week,
                    "team_key": my_key,
                    "opp_key": _text(_get(opp, "team_key")),
                    "points_for": my_points if is_complete else None,
                    "points_against": opp_points if is_complete else None,
                    "result": result,
                    "is_playoffs": is_playoffs,
                    "is_complete": is_complete,
                }
            )
    count = models.upsert(conn, "matchups", rows, ["week", "team_key"])
    logger.info("Week %d: upserted %d matchup rows", week, count)
    return count


def fetch_all_matchups(query: Any, conn: sqlite3.Connection, start_week: int, end_week: int) -> None:
    """Full-season pull: completed weeks get scores, future weeks get the
    schedule (needed for remaining-SOS and playoff simulation)."""
    for week in range(start_week, end_week + 1):
        fetch_matchups_for_week(query, conn, week)


# --------------------------------------------------------------------------
# Rosters (Phase 2 — num_teams x num_weeks calls, paced)
# --------------------------------------------------------------------------

def fetch_rosters_for_week(query: Any, conn: sqlite3.Connection, week: int) -> int:
    team_ids = [row[0] for row in conn.execute("SELECT team_id FROM teams ORDER BY team_id")]
    total = 0
    for team_id in team_ids:
        players = query.get_team_roster_player_stats_by_week(str(team_id), chosen_week=week)
        team_key_row = conn.execute(
            "SELECT team_key FROM teams WHERE team_id = ?", (team_id,)
        ).fetchone()
        team_key = team_key_row[0] if team_key_row else None
        rows = []
        for player in players:
            player = _get(player, "player", default=player)
            name = _get(player, "name")
            selected = _get(player, "selected_position")
            slot = _text(_get(selected, "position")) if selected is not None else None
            eligible = _get(player, "eligible_positions", default=[])
            if not isinstance(eligible, list):
                eligible = [eligible]
            eligible_list = []
            for pos in eligible:
                pos_text = _text(_get(pos, "position", default=pos))
                if pos_text:
                    eligible_list.append(pos_text)
            points_obj = _get(player, "player_points")
            points = _float(_get(points_obj, "total")) if points_obj is not None else None
            rows.append(
                {
                    "week": week,
                    "team_key": team_key,
                    "player_key": _text(_get(player, "player_key")),
                    "player_name": _text(_get(name, "full", default=name)),
                    "position": _text(_get(player, "primary_position", "display_position")),
                    "eligible_positions": ",".join(eligible_list),
                    "slot": slot,
                    "points": points,
                }
            )
        total += models.upsert(conn, "rosters", rows, ["week", "team_key", "player_key"])
        time.sleep(ROSTER_CALL_SLEEP_SECONDS)
    logger.info("Week %d: upserted %d roster rows", week, total)
    return total


# --------------------------------------------------------------------------
# Transactions (FAAB tracking)
# --------------------------------------------------------------------------

def fetch_transactions(query: Any, conn: sqlite3.Connection) -> int:
    transactions = query.get_league_transactions()
    rows: list[dict[str, Any]] = []
    for txn in transactions:
        txn = _get(txn, "transaction", default=txn)
        txn_key = _text(_get(txn, "transaction_key"))
        txn_type = _text(_get(txn, "type"))
        timestamp = _int(_get(txn, "timestamp"))
        faab = _float(_get(txn, "faab_bid"))
        players = _get(txn, "players", default=[])
        if not isinstance(players, list):
            players = [players]
        for p in players:
            player = _get(p, "player", default=p)
            data = _get(player, "transaction_data")
            if isinstance(data, list) and data:
                data = data[0]
            move_type = _text(_get(data, "type")) or txn_type
            dest = _text(_get(data, "destination_team_key"))
            source = _text(_get(data, "source_team_key"))
            team_key = dest or source
            name = _get(player, "name")
            rows.append(
                {
                    "transaction_key": txn_key,
                    "player_key": _text(_get(player, "player_key")),
                    "type": txn_type,
                    "week": None,  # Yahoo stamps time, not week; derived downstream
                    "timestamp": timestamp,
                    "team_key": team_key,
                    "player_name": _text(_get(name, "full", default=name)),
                    "move_type": move_type,
                    "faab_spent": faab if move_type == "add" else None,
                }
            )
    count = models.upsert(conn, "transactions", rows, ["transaction_key", "player_key", "move_type"])
    logger.info("Upserted %d transaction rows", count)
    return count


# --------------------------------------------------------------------------
# Standings snapshot
# --------------------------------------------------------------------------

def snapshot_standings(conn: sqlite3.Connection, through_week: int) -> int:
    """Compute cumulative W-L-T / PF / PA from stored matchups as of a week.

    Derived from our own matchup rows rather than Yahoo's standings endpoint
    so historical 'as of week N' snapshots can be rebuilt at any time.
    """
    rows = conn.execute(
        """
        SELECT team_key,
               SUM(result = 'W'), SUM(result = 'L'), SUM(result = 'T'),
               SUM(points_for), SUM(points_against)
        FROM matchups
        WHERE week <= ? AND is_complete = 1 AND is_playoffs = 0
        GROUP BY team_key
        """,
        (through_week,),
    ).fetchall()
    snapshot = [
        {
            "week": through_week,
            "team_key": team_key,
            "wins": wins or 0,
            "losses": losses or 0,
            "ties": ties or 0,
            "points_for": pf or 0.0,
            "points_against": pa or 0.0,
        }
        for team_key, wins, losses, ties, pf, pa in rows
    ]
    return models.upsert(conn, "standings_snapshot", snapshot, ["week", "team_key"])
