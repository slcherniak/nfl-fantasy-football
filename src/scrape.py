"""Scrape-based fetch layer for Yahoo Fantasy (no API access required).

Yahoo denied programmatic Fantasy API access (it now requires an approved
application), so this module pulls the same data from the league's web pages
using a logged-in session cookie. Everything lands in the same SQLite tables
as the yfpy path (src/fetch.py), so metrics/report/publish are unchanged.

Auth: the full browser Cookie header, from env YAHOO_COOKIES (a GitHub secret
in CI, .env locally). Yahoo sessions eventually expire — when that happens
every request bounces to login.yahoo.com and CookiesExpiredError is raised
with instructions, rather than silently storing garbage.

Page anchors this relies on (verified against the live league, Sept 2026):
  - league home:   "Week N Matchups" header  -> current week
  - #matchupweek li[data-target*="mid1=..&mid2=.."] -> one matchup;
    per side: a.F-link (team name/id), div.Fz-lg (actual), .F-shade (proj)
  - /teams table rows -> team name, manager, FAAB remaining
  - team page ?week=N: table#statTable* rows -> td.pos (slot),
    td.player (name, "Team - POS[,POS]"), td.pts (fantasy points)
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
from typing import Any

import requests
from bs4 import BeautifulSoup

from src import models

logger = logging.getLogger(__name__)

BASE = "https://football.fantasysports.yahoo.com/f1"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
PAGE_SLEEP_SECONDS = 1.0  # be polite; ~30 pages per weekly run


class CookiesExpiredError(RuntimeError):
    pass


class YahooScraper:
    def __init__(self, league_id: str, cookies: str | None = None, sleep: float = PAGE_SLEEP_SECONDS):
        cookies = cookies or os.environ.get("YAHOO_COOKIES")
        if not cookies:
            raise RuntimeError(
                "YAHOO_COOKIES is not set. Copy the Cookie header from a "
                "logged-in browser session (README: 'Yahoo session cookies')."
            )
        self.league_id = str(league_id)
        self.sleep = sleep
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Cookie": cookies.strip()})

    def get(self, path: str) -> BeautifulSoup:
        url = f"{BASE}/{self.league_id}{path}"
        for attempt in range(3):
            response = self.session.get(url, timeout=30)
            if "login.yahoo.com" in response.url:
                raise CookiesExpiredError(
                    "Yahoo redirected to the login page — the session cookies "
                    "have expired. Refresh the YAHOO_COOKIES secret from a "
                    "logged-in browser (README: 'Yahoo session cookies')."
                )
            if response.status_code == 200:
                time.sleep(self.sleep)
                return BeautifulSoup(response.text, "lxml")
            logger.warning("GET %s -> %s (attempt %d)", url, response.status_code, attempt + 1)
            time.sleep(2 ** (attempt + 1))
        response.raise_for_status()
        raise RuntimeError(f"GET {url} failed after retries")


# --------------------------------------------------------------------------
# Parsers (pure functions on soup, unit-testable with fixture HTML)
# --------------------------------------------------------------------------

def parse_current_week(soup: BeautifulSoup) -> int:
    """League home shows the current matchup week as 'Week N Matchups'."""
    m = re.search(r"Week (\d+)\s+Matchups", soup.get_text(" ", strip=True))
    if not m:
        raise ValueError("Could not find 'Week N Matchups' header on league home")
    return int(m.group(1))


def parse_teams(soup: BeautifulSoup, league_id: str) -> list[dict[str, Any]]:
    """/teams page: one row per team with manager and FAAB remaining.

    The first cell holds two anchors to the team page: a logo-only one and a
    text one — the team name is whichever anchor has text.
    """
    rows = []
    for tr in soup.select("table tbody tr"):
        links = [
            a for a in tr.select("a[href]")
            if re.search(rf"/f1/{league_id}/(\d+)$", a["href"])
        ]
        if not links:
            continue
        team_id = int(re.search(rf"/f1/{league_id}/(\d+)$", links[0]["href"]).group(1))
        name = next((a.get_text(strip=True) for a in links if a.get_text(strip=True)), f"Team {team_id}")
        logo = tr.select_one("img[src]")
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        faab = next((c for c in cells if re.fullmatch(r"\$\d+", c)), None)
        manager = cells[1] if len(cells) > 1 else None
        rows.append(
            {
                "team_key": str(team_id),
                "team_id": team_id,
                "name": name,
                "manager": manager,
                "logo_url": logo["src"] if logo else None,
                "faab_remaining": float(faab.lstrip("$")) if faab else None,
            }
        )
    return rows


def _parse_score(side: Any) -> float | None:
    el = side.select_one("div.Fz-lg")
    if el is None:
        return None
    text = el.get_text(strip=True)
    try:
        return float(text)
    except ValueError:
        return None


def parse_matchups(
    soup: BeautifulSoup, league_id: str, week: int, is_complete: bool, is_playoffs: bool
) -> list[dict[str, Any]]:
    """#matchupweek list: two rows per matchup (one per side)."""
    rows: list[dict[str, Any]] = []
    for li in soup.select("#matchupweek li"):
        target = li.get("data-target", "")
        ids = re.search(r"mid1=(\d+)&mid2=(\d+)", target)
        sides = li.select("div.Grid-u-6-13")
        if not ids or len(sides) != 2:
            continue  # placeholder (e.g. TBD playoff pairing) — skip
        id_a, id_b = ids.group(1), ids.group(2)
        score_a, score_b = _parse_score(sides[0]), _parse_score(sides[1])

        for me, opp, pf, pa in ((id_a, id_b, score_a, score_b), (id_b, id_a, score_b, score_a)):
            if is_complete and pf is not None and pa is not None:
                result = "T" if pf == pa else ("W" if pf > pa else "L")
            else:
                result = None
            rows.append(
                {
                    "week": week,
                    "team_key": me,
                    "opp_key": opp,
                    "points_for": pf if is_complete else None,
                    "points_against": pa if is_complete else None,
                    "result": result,
                    "is_playoffs": int(is_playoffs),
                    "is_complete": int(is_complete),
                }
            )
    return rows


_TEAM_POS_RE = re.compile(r"\b[A-Za-z]{2,3}\s*-\s*([A-Z]{1,3}(?:,[A-Z]{1,3})*)\b")


def parse_roster(soup: BeautifulSoup, team_key: str, week: int) -> list[dict[str, Any]]:
    """team page ?week=N: all stat tables (offense, K, DEF)."""
    rows: list[dict[str, Any]] = []
    for table in soup.select("table[id^=statTable]"):
        for tr in table.select("tbody tr"):
            pos_td = tr.select_one("td.pos")
            player_td = tr.select_one("td.player")
            pts_td = tr.select_one("td.pts")
            if pos_td is None or player_td is None:
                continue
            slot = pos_td.get_text(strip=True)
            cell_text = player_td.get_text(" ", strip=True)
            if not cell_text or "(Empty)" in cell_text:
                continue
            name_link = player_td.select_one("a[href*='/players/'], a[href*='sports.yahoo.com']")
            name = name_link.get_text(strip=True) if name_link else cell_text.split(" Player Note")[0]
            key_match = re.search(r"/players/(\d+)", name_link["href"]) if name_link else None
            player_key = key_match.group(1) if key_match else re.sub(r"\W+", "-", name.lower())

            pos_match = _TEAM_POS_RE.search(cell_text)
            eligible = pos_match.group(1).split(",") if pos_match else []
            points_text = pts_td.get_text(strip=True) if pts_td else ""
            try:
                points = float(points_text)
            except ValueError:
                points = None

            rows.append(
                {
                    "week": week,
                    "team_key": team_key,
                    "player_key": player_key,
                    "player_name": name,
                    "position": eligible[0] if eligible else None,
                    "eligible_positions": ",".join(eligible),
                    "slot": slot,
                    "points": points,
                }
            )
    return rows


# --------------------------------------------------------------------------
# Fetch orchestration (writes to the same tables as src/fetch.py)
# --------------------------------------------------------------------------

def fetch_league_meta(scraper: YahooScraper, conn: sqlite3.Connection, expected_num_teams: int) -> dict[str, Any]:
    home = scraper.get("")
    current_week = parse_current_week(home)
    teams_page = scraper.get("/teams")
    team_rows = parse_teams(teams_page, scraper.league_id)
    num_teams = len(team_rows)

    if num_teams and num_teams != expected_num_teams:
        logger.warning(
            "!!! TEAM COUNT MISMATCH: site shows %s teams, config expected %s !!!",
            num_teams, expected_num_teams,
        )

    models.upsert(
        conn, "teams",
        [{k: v for k, v in r.items() if k != "faab_remaining"} for r in team_rows],
        ["team_key"],
    )
    meta = {"num_teams": num_teams, "current_week": current_week, "source": "scrape"}
    faab = {r["team_key"]: r["faab_remaining"] for r in team_rows if r["faab_remaining"] is not None}
    if faab:
        meta["faab_remaining"] = str(faab)
    for key, value in meta.items():
        models.set_meta(conn, key, value)
    logger.info("League meta (scraped): num_teams=%s current_week=%s", num_teams, current_week)
    return meta


def fetch_matchups_for_week(
    scraper: YahooScraper, conn: sqlite3.Connection, week: int, current_week: int, playoff_start_week: int
) -> int:
    soup = scraper.get(f"?matchup_week={week}&module=matchups")
    rows = parse_matchups(
        soup, scraper.league_id, week,
        is_complete=week < current_week,
        is_playoffs=week >= playoff_start_week,
    )
    count = models.upsert(conn, "matchups", rows, ["week", "team_key"])
    logger.info("Week %d: upserted %d matchup rows (scraped)", week, count)
    return count


def fetch_rosters_for_week(scraper: YahooScraper, conn: sqlite3.Connection, week: int) -> int:
    team_ids = [row[0] for row in conn.execute("SELECT team_id FROM teams ORDER BY team_id")]
    total = 0
    for team_id in team_ids:
        soup = scraper.get(f"/{team_id}?week={week}")
        rows = parse_roster(soup, str(team_id), week)
        total += models.upsert(conn, "rosters", rows, ["week", "team_key", "player_key"])
    logger.info("Week %d: upserted %d roster rows (scraped)", week, total)
    return total
