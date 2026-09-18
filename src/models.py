"""SQLite schema and upsert helpers.

The database is the source of truth for all metrics. Every writer upserts on
the table's natural key, so re-running any week (e.g. the Thursday
stat-correction pass) never duplicates rows.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable, Mapping

DEFAULT_DB_PATH = Path("data/league.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    team_key   TEXT PRIMARY KEY,
    team_id    INTEGER NOT NULL,
    name       TEXT NOT NULL,
    manager    TEXT,
    logo_url   TEXT
);

-- Grain: team-week. Two rows per matchup (one from each side); future weeks
-- carry the schedule with is_complete = 0 and NULL points.
CREATE TABLE IF NOT EXISTS matchups (
    week            INTEGER NOT NULL,
    team_key        TEXT NOT NULL,
    opp_key         TEXT NOT NULL,
    points_for      REAL,
    points_against  REAL,
    result          TEXT CHECK (result IN ('W', 'L', 'T') OR result IS NULL),
    is_playoffs     INTEGER NOT NULL DEFAULT 0,
    is_complete     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (week, team_key)
);

-- Grain: player-team-week. slot is the Yahoo selected position
-- (QB/RB/WR/TE/W\\/R\\/T/K/DEF/BN/IR); eligible_positions is comma-joined.
CREATE TABLE IF NOT EXISTS rosters (
    week               INTEGER NOT NULL,
    team_key           TEXT NOT NULL,
    player_key         TEXT NOT NULL,
    player_name        TEXT,
    position           TEXT,
    eligible_positions TEXT,
    slot               TEXT,
    points             REAL,
    PRIMARY KEY (week, team_key, player_key)
);

CREATE TABLE IF NOT EXISTS transactions (
    transaction_key  TEXT NOT NULL,
    player_key       TEXT NOT NULL,
    type             TEXT,
    week             INTEGER,
    timestamp        INTEGER,
    team_key         TEXT,
    player_name      TEXT,
    move_type        TEXT,           -- add | drop | trade-in | trade-out
    faab_spent       REAL,
    PRIMARY KEY (transaction_key, player_key, move_type)
);

-- Cumulative record "as of week N" so historical views survive recomputes.
CREATE TABLE IF NOT EXISTS standings_snapshot (
    week            INTEGER NOT NULL,
    team_key        TEXT NOT NULL,
    wins            INTEGER,
    losses          INTEGER,
    ties            INTEGER,
    points_for      REAL,
    points_against  REAL,
    PRIMARY KEY (week, team_key)
);

-- Auto-detected league facts (num_teams, faab_budget, game_key, ...).
CREATE TABLE IF NOT EXISTS league_meta (
    key    TEXT PRIMARY KEY,
    value  TEXT
);
"""


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def upsert(
    conn: sqlite3.Connection,
    table: str,
    rows: Iterable[Mapping[str, Any]],
    key_columns: list[str],
) -> int:
    """Insert rows, replacing any existing row with the same natural key."""
    rows = list(rows)
    if not rows:
        return 0
    columns = list(rows[0].keys())
    placeholders = ", ".join(f":{c}" for c in columns)
    updates = ", ".join(f"{c} = excluded.{c}" for c in columns if c not in key_columns)
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({', '.join(key_columns)}) DO UPDATE SET {updates}"
    )
    with conn:
        conn.executemany(sql, rows)
    return len(rows)


def set_meta(conn: sqlite3.Connection, key: str, value: Any) -> None:
    with conn:
        conn.execute(
            "INSERT INTO league_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM league_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default
