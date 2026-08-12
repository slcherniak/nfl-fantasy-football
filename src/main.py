"""Orchestrator: fetch -> compute -> publish.

Usage:
    python -m src.main                  # update the latest completed week(s)
    python -m src.main --backfill       # re-pull every week from week 1
    python -m src.main --week 6         # re-pull one specific week
    python -m src.main --skip-rosters   # Phase 1 only (matchup-level data)
    python -m src.main --skip-publish   # compute but don't touch the sheet
    python -m src.main --auth-only      # run the one-time OAuth dance and exit
"""

from __future__ import annotations

import argparse
import logging
import sys

from src import fetch, models, report
from src.auth import build_query
from src.config import load_settings
from src.publish import publish_tables

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("league-tracker")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Man Camp league tracker")
    parser.add_argument("--backfill", action="store_true", help="re-pull all weeks from week 1")
    parser.add_argument("--week", type=int, default=None, help="re-pull a single week")
    parser.add_argument("--skip-rosters", action="store_true", help="skip player-level pulls (Phase 1 mode)")
    parser.add_argument("--skip-fetch", action="store_true", help="compute/publish from the existing database only")
    parser.add_argument("--skip-publish", action="store_true", help="fetch/compute but do not write to Sheets")
    parser.add_argument("--auth-only", action="store_true", help="run the OAuth flow and exit")
    parser.add_argument("--db", default=str(models.DEFAULT_DB_PATH), help="SQLite path")
    parser.add_argument("--config", default="config.yaml", help="config file path")
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings(args.config)
    conn = models.connect(args.db)

    if not args.skip_fetch:
        query = build_query(settings.league_id, settings.game_code)
        if args.auth_only:
            query.get_current_user()
            logger.info("Yahoo OAuth OK — token persisted to .env")
            return 0

        meta = fetch.fetch_league_meta(query, conn, settings.expected_num_teams)
        fetch.fetch_teams(query, conn)

        current_week = int(meta.get("current_week") or 1)
        start_week = int(meta.get("start_week") or 1)
        end_week = int(meta.get("end_week") or 17)

        if args.week:
            fetch.fetch_matchups_for_week(query, conn, args.week)
            roster_weeks = [args.week]
        else:
            # Always walk the full season: completed weeks refresh scores
            # (stat corrections), future weeks refresh the schedule.
            fetch.fetch_all_matchups(query, conn, start_week, end_week)
            if args.backfill:
                roster_weeks = list(range(start_week, current_week + 1))
            else:
                # current_week is the in-progress week; the last completed one
                # (and the one before it, for late corrections) get re-pulled.
                roster_weeks = [w for w in (current_week - 2, current_week - 1) if w >= start_week]

        completed_weeks = [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT week FROM matchups WHERE is_complete = 1 ORDER BY week"
            )
        ]
        if not args.skip_rosters:
            for week in roster_weeks:
                if week in completed_weeks:
                    fetch.fetch_rosters_for_week(query, conn, week)
            fetch.fetch_transactions(query, conn)

        for week in completed_weeks:
            fetch.snapshot_standings(conn, week)

    tables = report.build_tables(conn, settings)

    if args.skip_publish:
        logger.info("--skip-publish: computed %d tables, not writing to Sheets", len(tables))
        return 0

    sheet_id = settings.sheet_id
    if not sheet_id:
        logger.error("SHEET_ID is not set — set it in .env or pass --skip-publish")
        return 1
    publish_tables(sheet_id, tables, owned_tabs=set(settings.sheet_tabs.values()))
    logger.info("Publish complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
