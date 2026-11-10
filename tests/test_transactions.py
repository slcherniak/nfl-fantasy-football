"""Parser tests for the transactions scraper (fixture mirrors the live DOM)."""

import pytest
from bs4 import BeautifulSoup

from src.scrape import _txn_week, parse_transactions
import datetime

LEAGUE = "392520"

TXN_HTML = f"""
<table class="Table Tst-transaction-table">
<tr>
  <td class="Grid-u-1-12 Ta-c"><span class="F-icon F-positive" title="Added Player"></span></td>
  <td class="Fill-x No-pstart" colspan="2">
    <div class="Pbot-xs">
      <a href="https://sports.yahoo.com/nfl/players/40000" target="sports">Hot Waiver Guy</a>
      <span class="F-position Fz-xxs">Det - RB</span>
      <h6 class="F-shade Fz-xxs">Waivers ($37)</h6></div>
    <div class="Pbot-xs">
      <a href="https://sports.yahoo.com/nfl/players/40001" target="sports">Cold Bench Guy</a>
      <span class="F-position Fz-xxs">Mia - WR</span>
      <h6 class="F-shade Fz-xxs"> To Waivers</h6></div>
  </td>
  <td class="Ta-end"><div><span class="Grid-u">
      <a class="Tst-team-name" href="https://football.fantasysports.yahoo.com/f1/{LEAGUE}/10">The Backdoor Bandits</a>
      <span class="Block F-timestamp Fz-xxs Nowrap">Sep 24, 9:15 am</span></span></div></td>
</tr>
<tr>
  <td class="Grid-u-1-12 Pstart-xl Ta-c" rowspan="2"><span class="F-icon Fz-xl F-trade"></span></td>
  <td class="No-pstart"><p>
    <a href="https://sports.yahoo.com/nfl/players/31868" target="sports">Deebo Samuel Sr.</a>
    <span class="F-position Fz-xxs">SF - WR</span></p></td>
  <td class="Fz-xxs">Traded to</td>
  <td class="Ta-end"><div><span class="Grid-u">
      <a href="https://football.fantasysports.yahoo.com/f1/{LEAGUE}/10">The Backdoor Bandits</a>
      <span class="Block F-timestamp Fz-xxs Nowrap">Oct 17, 3:33 pm</span></span></div></td>
</tr>
<tr>
  <td class="No-pstart"><p>
    <a href="https://sports.yahoo.com/nfl/players/30199" target="sports">Travis Kelce</a>
    <span class="F-position Fz-xxs">KC - TE</span></p><p>
    <a href="https://sports.yahoo.com/nfl/players/33313" target="sports">Rachaad White</a>
    <span class="F-position Fz-xxs">TB - RB</span></p></td>
  <td class="Fz-xxs">Traded to</td>
  <td class="Ta-end"><div><span class="Grid-u">
      <a href="https://football.fantasysports.yahoo.com/f1/{LEAGUE}/5">LUMI</a>
      <span class="Block F-timestamp Fz-xxs Nowrap">Oct 17, 3:33 pm</span></span></div></td>
</tr>
</table>
"""


class TestParseTransactions:
    @pytest.fixture
    def rows(self):
        return parse_transactions(BeautifulSoup(TXN_HTML, "lxml"), LEAGUE)

    def test_add_drop_row(self, rows):
        adds = [r for r in rows if r["move_type"] == "add"]
        drops = [r for r in rows if r["move_type"] == "drop"]
        assert len(adds) == 1 and len(drops) == 1
        add = adds[0]
        assert add["player_name"] == "Hot Waiver Guy"
        assert add["player_key"] == "40000"
        assert add["faab_spent"] == 37.0
        assert add["team_key"] == "10"
        assert add["week"] == 3           # Sep 24, 2026 falls in week 3
        assert drops[0]["player_name"] == "Cold Bench Guy"
        assert drops[0]["faab_spent"] is None

    def test_trade_sides(self, rows):
        trade = [r for r in rows if r["move_type"] == "trade-in"]
        assert len(trade) == 3
        by_team = {}
        for r in trade:
            by_team.setdefault(r["team_key"], []).append(r["player_name"])
        assert by_team["10"] == ["Deebo Samuel Sr."]
        assert sorted(by_team["5"]) == ["Rachaad White", "Travis Kelce"]
        assert all(r["week"] == 6 for r in trade)  # Oct 17, 2026 -> week 6

    def test_idempotent_keys(self, rows):
        keys = [(r["transaction_key"], r["player_key"], r["move_type"]) for r in rows]
        assert len(keys) == len(set(keys))


class TestTxnWeek:
    def test_clamps_and_maps(self):
        assert _txn_week(datetime.datetime(2026, 9, 9)) == 1    # Wed before week 1
        assert _txn_week(datetime.datetime(2026, 9, 16)) == 2   # Wed of week 2
        assert _txn_week(datetime.datetime(2026, 8, 20)) == 1   # preseason clamps to 1
        assert _txn_week(datetime.datetime(2027, 1, 30)) == 17  # post-season clamps
