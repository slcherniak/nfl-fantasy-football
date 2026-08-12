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
Yahoo Fantasy API
      │  (yfpy, OAuth2)                          src/auth.py, src/fetch.py
      ▼
SQLite: data/league.db  ← source of truth        src/models.py
      │
      ▼
Metrics engine (pandas)                          src/metrics/*, src/simulate.py
      │
      ▼
Google Sheets (gspread, service account)         src/report.py, src/publish.py
```

**Runner:** GitHub Actions (`.github/workflows/weekly.yml`) — Tue + Thu
14:00 UTC (9am EST / 10am EDT), plus `workflow_dispatch`. CI always runs
`--backfill` because its database starts empty each run; the full re-pull is
idempotent and doubles as the Thursday stat-correction refresh.

## Code map

| Path | Responsibility |
|---|---|
| `src/auth.py` | Yahoo OAuth via yfpy; token persisted/refreshed through `.env` |
| `src/fetch.py` | API pulls → idempotent upserts keyed on (week, key) |
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

1. **Yahoo OAuth is the #1 failure mode.** Access tokens expire in ~1 hour;
   the refresh token in `YAHOO_ACCESS_TOKEN_JSON` is what keeps CI alive and
   does not rotate. Locally yfpy writes the refreshed token back to `.env`.
2. **Stat corrections** land Wed/Thu — hence the Thursday re-run; safety
   comes from upserts on (week, key), never inserts.
3. **Game keys are season-scoped** (`461.l.392520`-style). Fetched at
   runtime; never hardcode a game key or season.
4. **Rate limits:** roster pulls sleep between calls
   (`fetch.ROSTER_CALL_SLEEP_SECONDS`).
5. **Sheets quota:** one batched values write per tab, never cell loops.
6. **Ties exist** — result ∈ {W, L, T} everywhere; all-play ties are 0.5.
7. **Test the metrics.** `tests/test_metrics.py` asserts exact hand-computed
   values on a fixture league; CI refuses to publish if tests fail. Any new
   metric needs fixture-exact tests before it ships.

## Remaining open questions

- [ ] Actual team count (verify at runtime; loud log if ≠ 12)
- [ ] FAAB budget (auto-detect from API)
- [ ] Share sheet read-only with the league, or private?
- [ ] FAAB spend-efficiency (points added per FAAB dollar) — transactions are
      stored; the metric itself is not yet built
