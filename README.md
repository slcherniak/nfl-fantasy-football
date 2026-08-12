# League Tracker — Man Camp

Yahoo Fantasy → SQLite → Google Sheets, automated. Every Tuesday and Thursday
a GitHub Action pulls the league, recomputes every metric from raw data, and
rewrites the sheet: luck-adjusted standings, all-play records, power
rankings, manager efficiency, volatility, weekly awards, and Monte Carlo
playoff odds.

> The previous JSON-dump scripts this repo started from are documented in
> [`docs/legacy_README.md`](docs/legacy_README.md).

## How it works

Sheets is presentation only. Python computes everything against a local
SQLite database (`data/league.db`), then writes plain values. Every fetch
upserts on `(week, key)`, so re-running any week — including the Thursday
stat-correction pass — is always safe, and `--backfill` can rebuild the
whole season from scratch at any time.

## One-time setup

### 1. Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install .            # plus: pip install pytest  (to run tests)
cp .env.example .env
```

### 2. Yahoo OAuth (the fiddly part)

1. Create an app at [developer.yahoo.com](https://developer.yahoo.com/apps/)
   → Create an App → **Installed Application**, API permission **Fantasy
   Sports (Read)**. The redirect URI can be `oob`/localhost.
2. Copy the Client ID / Client Secret into `.env` as `YAHOO_CONSUMER_KEY` /
   `YAHOO_CONSUMER_SECRET`.
3. Run the one-time browser dance:

   ```bash
   python -m src.main --auth-only
   ```

   Follow the printed URL, approve, paste the code back. yfpy writes
   `YAHOO_ACCESS_TOKEN_JSON` into `.env` and refreshes it automatically on
   every future run (access tokens only live ~1 hour; the refresh token is
   long-lived).

### 3. Google Sheets service account

1. In Google Cloud Console: create a project → enable the **Google Sheets
   API** → create a **service account** → download its JSON key as
   `service_account.json` (gitignored).
2. Create the spreadsheet, copy its ID from the URL into `.env` as
   `SHEET_ID`.
3. Share the spreadsheet with the service account's `client_email`
   (Editor). No user OAuth involved.

### 4. First run

```bash
python -m src.main --backfill        # pull every completed week, compute, publish
```

Useful flags: `--week 6` (re-pull one week), `--skip-rosters` (matchup-level
only, far fewer API calls), `--skip-publish` (compute without touching the
sheet), `--skip-fetch` (recompute/publish from the existing database).

## GitHub Actions (weekly automation)

`.github/workflows/weekly.yml` runs Tuesday and Thursday at 14:00 UTC
(9am EST / 10am EDT — after MNF stats post; Thursday catches Yahoo stat
corrections). Manual runs via **Actions → weekly-update → Run workflow**.

Repository secrets to set:

| Secret | Value |
|---|---|
| `YAHOO_CONSUMER_KEY` / `YAHOO_CONSUMER_SECRET` | from your Yahoo app |
| `YAHOO_ACCESS_TOKEN_JSON` | the JSON value yfpy wrote into your local `.env` |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | full contents of `service_account.json` |
| `SHEET_ID` | the spreadsheet ID |

The Yahoo refresh token inside `YAHOO_ACCESS_TOKEN_JSON` does not rotate, so
the secret keeps working season-long. CI runs the test suite first and never
publishes on red.

## The sheet

The script owns and fully rewrites these tabs: **Dashboard** (power rankings
with ▲▼ movement, luck index, playoff/bye/title odds), **All-Play Matrix**,
**Luck Report**, **Manager Efficiency**, **Weekly Log** (with award flags),
**Trends** (PPG by week + volatility/SOS), and two hidden `_raw` tabs. Any
tab you add by hand is never touched.

## Tests

```bash
python -m pytest tests/ -q
```

Metric bugs are silent, so `tests/test_metrics.py` asserts exact
hand-computed values on a fixture league (4 teams, 3 weeks, ties, a sad win,
a robbed loss). If you change a metric, recompute the fixture by hand.

## Metric definitions

See [`CLAUDE.md`](CLAUDE.md) for the exact definition of every metric and
the league-specific gotchas encoded in the implementation.
