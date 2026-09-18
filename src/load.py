"""Read SQLite tables into DataFrames for the metrics engine."""

from __future__ import annotations

import sqlite3

import pandas as pd


def load_teams(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM teams ORDER BY team_id", conn)


def load_matchups(conn: sqlite3.Connection, completed_only: bool = True, regular_season_only: bool = True) -> pd.DataFrame:
    query = "SELECT * FROM matchups WHERE 1=1"
    if completed_only:
        query += " AND is_complete = 1"
    if regular_season_only:
        query += " AND is_playoffs = 0"
    return pd.read_sql_query(query + " ORDER BY week, team_key", conn)


def load_schedule_remaining(conn: sqlite3.Connection) -> pd.DataFrame:
    """Future (not yet complete) regular-season matchups."""
    return pd.read_sql_query(
        "SELECT week, team_key, opp_key FROM matchups "
        "WHERE is_complete = 0 AND is_playoffs = 0 ORDER BY week, team_key",
        conn,
    )


def load_rosters(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM rosters ORDER BY week, team_key", conn)


def load_transactions(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM transactions ORDER BY timestamp", conn)
