"""Orchestrator: fetch -> compute -> publish.

Usage:
    python -m src.main                  # update: refresh recent weeks, compute, publish
    python -m src.main --backfill       # re-pull every week from week 1
    python -m src.main --week 6         # re-pull one specific week
    python -m src.main --skip-rosters   # matchup-level data only
    python -m src.main --skip-publish   # compute but don't touch the sheet
    python -m src.main --source api     # use the yfpy API path (needs approved
                                        # Yahoo API access; default is scraping)
    python -m src.main --auth-only      # API mode only: run the OAuth dance and exit
"""

from __future__ import annotations

import argparse
import logging
import sys

from src import models, report
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
    parser.add_argument("--source", choices=["scrape", "api"], default="scrape",
                        help="data source: scrape (session cookies, default) or api (yfpy)")
    parser.add_argument("--backfill", action="store_true", help="re-pull all weeks from week 1")
    parser.add_argument("--week", type=int, default=None, help="re-pull a single week")
    parser.add_argument("--skip-rosters", action="store_true", help="skip player-level pulls")
    parser.add_argument("--skip-fetch", action="store_true", help="compute/publish from the existing database only")
    parser.add_argument("--skip-publish", action="store_true", help="fetch/compute but do not write to Sheets")
    parser.add_argument("--auth-only", action="store_true", help="API mode: run the OAuth flow and exit")
    parser.add_argument("--db", default=str(models.DEFAULT_DB_PATH), help="SQLite path")
    parser.add_argument("--config", default="config.yaml", help="config file path")
    return parser.parse_args(argv)


def _fetch_scrape(args, settings, conn) -> None:
    from src import scrape

    scraper = scrape.YahooScraper(settings.league_id)
    meta = scrape.fetch_league_meta(scraper, conn, settings.expected_num_teams)
    current_week = int(meta["current_week"])
    playoff_start = min(settings.playoffs["weeks"])
    end_week = max(settings.playoffs["weeks"])

    if args.week:
        weeks = [args.week]
    else:
        # Completed weeks refresh scores (stat corrections land Wed/Thu);
        # future weeks refresh the schedule for SOS/simulation.
        weeks = list(range(1, end_week + 1))
    for week in weeks:
        scrape.fetch_matchups_for_week(scraper, conn, week, current_week, playoff_start)

    completed = [w for w in weeks if w < current_week]
    if not args.skip_rosters:
        if args.backfill or args.week:
            roster_weeks = completed
        else:
            roster_weeks = [w for w in (current_week - 2, current_week - 1) if w >= 1]
        for week in roster_weeks:
            scrape.fetch_rosters_for_week(scraper, conn, week)

    from src import fetch as fetch_common
    for week in completed:
        fetch_common.snapshot_standings(conn, week)


def _fetch_api(args, settings, conn) -> bool:
    """yfpy path — kept for the day Yahoo approves API access. Returns False
    if the run should stop (auth-only)."""
    from src import fetch
    from src.auth import build_query

    query = build_query(settings.league_id, settings.game_code)
    if args.auth_only:
        query.get_current_user()
        logger.info("Yahoo OAuth OK — token persisted to .env")
        return False

    meta = fetch.fetch_league_meta(query, conn, settings.expected_num_teams)
    fetch.fetch_teams(query, conn)
    current_week = int(meta.get("current_week") or 1)
    start_week = int(meta.get("start_week") or 1)
    end_week = int(meta.get("end_week") or 17)

    if args.week:
        fetch.fetch_matchups_for_week(query, conn, args.week)
        roster_weeks = [args.week]
    else:
        fetch.fetch_all_matchups(query, conn, start_week, end_week)
        if args.backfill:
            roster_weeks = list(range(start_week, current_week + 1))
        else:
            roster_weeks = [w for w in (current_week - 2, current_week - 1) if w >= start_week]

    completed_weeks = [
        row[0] for row in conn.execute(
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
    return True


def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings(args.config)
    conn = models.connect(args.db)

    if not args.skip_fetch:
        if args.source == "api":
            if not _fetch_api(args, settings, conn):
                return 0
        else:
            _fetch_scrape(args, settings, conn)

    tables = report.build_tables(conn, settings)

    if args.skip_publish:
        logger.info("--skip-publish: computed %d tables, not writing to Sheets", len(tables))
        return 0

    sheet_id = settings.sheet_id
    if not sheet_id:
        logger.error("SHEET_ID is not set — set it in .env or pass --skip-publish")
        return 1
    publish_tables(
        sheet_id,
        tables,
        owned_tabs=set(settings.sheet_tabs.values()),
        charts_tab_names=settings.sheet_tabs,
    )
    logger.info("Publish complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
