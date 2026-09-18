# League Tracker — Man Camp

Yahoo Fantasy API → Python ETL → Google Sheets. Weekly automated update for
the "Man Camp" Yahoo league: luck-adjusted standings, all-play records, power
rankings, and manager-skill metrics beyond the Yahoo standings page.

## League configuration (confirmed from Yahoo settings, Aug 12, 2026)

- **League:** Man Camp, Yahoo League ID **392520**
- **Scoring:** H2H, 0.5 PPR, fractional + negative points
- **Roster:** QB, RB, RB, WR, WR, TE, W/R/T, K, DEF, 6 BN, 1 IR
- **Waivers:** FAAB, weekly Tue. Budget is **auto-detected** from the API,
  never hardcoded (likely $100, not shown in settings)
- **Playoffs:** 6 teams, Weeks 15–17, seeding by record (tiebreak = record vs
  opponent wins), **no reseeding**, top 2 seeds get Week 15 byes
- **Trades:** deadline Nov 28, 2026; no review
- **Team count:** settings allow 14 — **auto-detected at runtime**
  (`num_teams`); a mismatch vs `config.yaml:league.expected_num_teams` logs a
  loud warning. All-play denominators and z-scores derive from `num_teams − 1`.

## Architecture (the decision that matters most)

**Compute in Python, not in Sheets.** Google Sheets is the *presentation
layer only*. All metrics are computed in Python against a local SQLite store,
then written to Sheets as values — no formulas that depend on raw tabs.

```
Yahoo league web pages (session cookies)         src/scrape.py  ← DEFAULT
  [or Yahoo Fantasy API via yfpy, --source api]  src/auth.py, src/fetch.py
      ▼
SQLite: data/league.db  ← source of truth        src/models.py
      ▼
Metrics engine (pandas)                          src/metrics/*, src/simulate.py
      ▼
Google Sheets (gspread, service account)         src/report.py, src/publish.py
```

**Data source:** Yahoo denied Fantasy API access (now application-gated at
sports.yahoo.com/developer/access), so scraping is the default. The scraper
authenticates with a browser session cookie (`YAHOO_COOKIES`) and parses:
league home ("Week N Matchups" → current week), `#matchupweek` list items
(matchup scores/ids), `/teams` (managers, FAAB remaining), and per-team
`?week=N` stat tables (`td.pos` / `td.player` / `td.pts`). Weeks before the
current week count as complete. The yfpy path stays behind `--source api` +
`pip install .[api]`.

**Runner:** GitHub Actions (`.github/workflows/weekly.yml`) — Tue + Thu
14:00 UTC (9am EST / 10am EDT), plus `workflow_dispatch`. CI always runs
`--backfill` because its database starts empty each run; the full re-pull is
idempotent and doubles as the Thursday stat-correction refresh.

## Code map

| Path | Responsibility |
|---|---|
| `src/scrape.py` | **default fetch layer**: cookie-auth page scraping → same tables |
| `src/auth.py` | Yahoo OAuth via yfpy (API mode only) |
| `src/fetch.py` | yfpy API pulls (API mode only) → idempotent upserts keyed on (week, key) |
| `src/models.py` | SQLite schema + upsert helpers |
| `src/load.py` | SQLite → DataFrames |
| `src/metrics/` | all_play, luck, power_rankings, efficiency, volatility, sos, awards |
| `src/simulate.py` | Monte Carlo playoff odds (fixed 6-team bracket, no reseed) |
| `src/report.py` | metric outputs → presentation tables per tab |
| `src/publish.py` | batched value writes; only touches tabs listed in config |
| `src/main.py` | orchestrator: fetch → compute → publish; `--backfill`, `--week N`, `--skip-*` |
| `tests/` | exact-value metric tests on a hand-computed 4-team fixture |

## Metric definitions (implemented exactly — don't improvise)

- **All-play:** each week, compare each team's score to all others; higher =
  win, equal = 0.5. Denominator = teams that week − 1.
- **Luck:** `Actual Wins − Expected Wins`; expected = Σ weekly all-play win %.
  Ties count 0.5 in actual wins. Plus PA percentile, close-game record
  (margin < 10), sad wins (won below weekly median), robbed losses (lost
  above it).
- **Power rankings:** z-score blend, weights in `config.yaml`
  (0.35 season PPG / 0.25 last-3 PPG / 0.25 all-play % / 0.15 win %), with
  ▲▼ movement vs prior week.
- **Efficiency:** actual starter points ÷ optimal lineup points. Optimal is
  an exact bitmask-DP solve respecting Yahoo per-player eligibility and
  FLEX = W/R/T — greedy top-scorers overstates optimal.
- **Volatility:** per-team score std dev + boom/bust weeks vs league
  mean ± 1σ (population, ddof=0).
- **SOS:** average opponent PPG faced, excluding the opponent's games vs the
  team being measured; remaining SOS from the stored future schedule.
- **Playoff odds:** 10k Monte Carlo sims, scores ~ Normal(PPG, σ), actual
  remaining schedule, 6 seeds by record (tiebreak: opponents' wins, then PF),
  no reseed, top-2 byes. Reports playoff/bye/finals/title odds and the
  bracket-path split (`finals_from_bye` vs `finals_from_round1`).

## Sheet tabs (owned by the script, fully rewritten each run)

Dashboard, All-Play Matrix, Luck Report, Manager Efficiency, Weekly Log,
Trends, `_raw_matchups`, `_raw_standings` (hidden). **Never write to any
other tab** — manual tabs (notes, side bets) must survive. `publish.py`
raises if asked to write an unowned tab.

## Known gotchas (encoded, don't regress them)

1. **Cookie expiry is the #1 failure mode.** Yahoo sessions last weeks, not
   forever. Every scrape checks for a login redirect and raises
   `CookiesExpiredError` with refresh instructions instead of storing junk.
   The fix is always: re-copy the browser Cookie header into the
   `YAHOO_COOKIES` secret.
2. **Yahoo can redesign pages.** All DOM anchors live in `src/scrape.py`
   parsers with fixture-pinned tests in `tests/test_scrape.py` — a redesign
   breaks tests loudly before it corrupts data.
3. **Stat corrections** land Wed/Thu — hence the Thursday re-run; safety
   comes from upserts on (week, key), never inserts.
4. **Scrape politely.** One page per second (`scrape.PAGE_SLEEP_SECONDS`);
   a weekly run is ~30 pages.
5. **Sheets quota:** one batched values write per tab, never cell loops.
6. **Ties exist** — result ∈ {W, L, T} everywhere; all-play ties are 0.5.
7. **Test the metrics.** `tests/test_metrics.py` asserts exact hand-computed
   values on a fixture league; CI refuses to publish if tests fail. Any new
   metric needs fixture-exact tests before it ships.
8. **Playoff-week placeholders:** weeks 15–17 show TBD matchups until seeds
   are set; the parser skips list items without two team links.

## Confirmed at runtime (Sept 2026)

- Team count: **12** (matches config)
- FAAB budget: **$100** (remaining balances scraped from /teams into
  `league_meta.faab_remaining`)

## Remaining open questions

- [ ] Share sheet read-only with the league, or private?
- [ ] FAAB spend-efficiency (points added per FAAB dollar) — needs a
      transactions scraper (`/f1/<id>/transactions`); not yet built
- [ ] Yahoo Fantasy API application (if ever approved, switch back with
      `--source api`)
