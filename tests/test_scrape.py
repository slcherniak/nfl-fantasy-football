"""Parser tests for the scrape-based fetch layer.

Fixture HTML mirrors the structure verified against the live league pages
(Sept 2026): #matchupweek list items, statTable rows, teams table, and the
'Week N Matchups' header. If Yahoo redesigns, these break loudly here first.
"""

import pytest
from bs4 import BeautifulSoup

from src.scrape import parse_current_week, parse_matchups, parse_roster, parse_teams

LEAGUE = "392520"


def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def matchup_li(week, id_a, id_b, name_a, name_b, score_a, score_b):
    def side(team_id, name, score, extra_score_cls=""):
        return f"""
        <div class="Grid-u-6-13 Py-med">
          <div class="Fz-sm Ell"><a class="F-link"
             href="https://football.fantasysports.yahoo.com/f1/{LEAGUE}/{team_id}">{name}</a></div>
          <div class="Fz-xxs F-shade">1-0-0 | 5th</div>
          <div class="Ta-end"><div class="Fz-lg {extra_score_cls}">{score}</div>
          <div class="F-shade">99.99</div></div>
        </div>"""
    return f"""
    <li class="Linkable Listitem" data-target="/f1/{LEAGUE}/matchup?week={week}&mid1={id_a}&mid2={id_b}">
      {side(id_a, name_a, score_a, "Fw-b")}
      <div class="Grid-u-1-13"><span>vs</span></div>
      {side(id_b, name_b, score_b)}
    </li>"""


MATCHUPS_HTML = f"""
<html><body><section id="matchupweek"><ul>
{matchup_li(1, 6, 11, "Redraft", "New Henry", "108.36", "83.70")}
{matchup_li(1, 3, 7, "Team C", "Team G", "95.00", "95.00")}
<li class="Listitem" data-target="/f1/{LEAGUE}/matchup?week=15">placeholder-no-teams</li>
</ul></section></body></html>
"""


class TestParseMatchups:
    def test_completed_week(self):
        rows = parse_matchups(soup_of(MATCHUPS_HTML), LEAGUE, week=1, is_complete=True, is_playoffs=False)
        assert len(rows) == 4  # two matchups x two sides; placeholder skipped
        by_team = {r["team_key"]: r for r in rows}
        assert by_team["6"]["opp_key"] == "11"
        assert by_team["6"]["points_for"] == pytest.approx(108.36)
        assert by_team["6"]["result"] == "W"
        assert by_team["11"]["result"] == "L"
        assert by_team["11"]["points_against"] == pytest.approx(108.36)
        # tie game
        assert by_team["3"]["result"] == "T"
        assert by_team["7"]["result"] == "T"
        assert all(r["is_complete"] == 1 and r["is_playoffs"] == 0 for r in rows)

    def test_future_week_is_schedule_only(self):
        rows = parse_matchups(soup_of(MATCHUPS_HTML), LEAGUE, week=5, is_complete=False, is_playoffs=False)
        assert len(rows) == 4
        assert all(r["points_for"] is None and r["result"] is None for r in rows)
        assert all(r["is_complete"] == 0 for r in rows)
        assert {(r["team_key"], r["opp_key"]) for r in rows} >= {("6", "11"), ("11", "6")}


ROSTER_HTML = """
<html><body>
<table id="statTable0"><tbody>
  <tr>
    <td class="Alt Ta-c pos headcol">QB</td>
    <td class="Ta-start player Bdrstart">
      <a href="https://sports.yahoo.com/nfl/players/29369">Dak Prescott</a>
      <span>Player Note</span> Dal - QB Final L 20-28 @ NYG</td>
    <td class="Alt Ta-end Bdrstart">14</td>
    <td class="Ta-end Nowrap pts Bdrstart">15.40</td>
  </tr>
  <tr>
    <td class="pos">W/R/T</td>
    <td class="player"><a href="https://sports.yahoo.com/nfl/players/40990">Flex Guy</a>
      LAR - RB,WR Final W 27-7 vs SF</td>
    <td class="Alt">7</td>
    <td class="pts">8.10</td>
  </tr>
  <tr>
    <td class="pos">BN</td>
    <td class="player"><a href="https://sports.yahoo.com/nfl/players/33393">Ja'Marr Chase</a>
      New Player Note Cin - WR Final W 33-27 vs TB</td>
    <td class="Alt">10</td>
    <td class="pts">2.20</td>
  </tr>
  <tr>
    <td class="pos">BN</td>
    <td class="player">(Empty)</td>
    <td class="Alt"></td>
    <td class="pts">-</td>
  </tr>
</tbody></table>
<table id="statTable2"><tbody>
  <tr>
    <td class="pos">DEF</td>
    <td class="player"><a href="https://sports.yahoo.com/nfl/teams/jax">Jaguars</a>
      No new player Notes Jax - DEF Final W 26-10 vs Car</td>
    <td class="Alt">7</td>
    <td class="pts">13.00</td>
  </tr>
</tbody></table>
</body></html>
"""


class TestParseRoster:
    def test_slots_points_eligibility(self):
        rows = parse_roster(soup_of(ROSTER_HTML), team_key="1", week=1)
        assert len(rows) == 4  # empty bench slot skipped
        by_name = {r["player_name"]: r for r in rows}

        qb = by_name["Dak Prescott"]
        assert (qb["slot"], qb["points"], qb["eligible_positions"]) == ("QB", 15.40, "QB")
        assert qb["player_key"] == "29369"

        flex = by_name["Flex Guy"]
        assert flex["slot"] == "W/R/T"
        assert flex["eligible_positions"] == "RB,WR"   # multi-eligibility survives
        assert flex["points"] == pytest.approx(8.10)

        bench = by_name["Ja'Marr Chase"]
        assert bench["slot"] == "BN"

        dst = by_name["Jaguars"]
        assert dst["slot"] == "DEF"
        assert dst["eligible_positions"] == "DEF"
        assert dst["points"] == pytest.approx(13.00)
        assert dst["player_key"] == "jaguars"          # slug fallback (no numeric id)

    def test_feeds_efficiency_metric(self):
        """Scraped rows must satisfy the efficiency engine's contract."""
        import pandas as pd
        from src.metrics.efficiency import weekly_efficiency
        rows = parse_roster(soup_of(ROSTER_HTML), team_key="1", week=1)
        weekly = weekly_efficiency(pd.DataFrame(rows), slots={"QB": 1, "WR": 1, "DEF": 1})
        row = weekly.iloc[0]
        # starters: QB 15.4 + flex 8.1 + DEF 13.0 = 36.5;
        # optimal: QB 15.4 + WR max(8.1, 2.2) + DEF 13.0 = 36.5
        assert row["actual_points"] == pytest.approx(36.5)
        assert row["optimal_points"] == pytest.approx(36.5)


TEAMS_HTML = f"""
<html><body><table><tbody>
  <tr><td><a class="Grid-u" href="/f1/{LEAGUE}/1"><img alt="logo" src="https://x/logo1.png"/></a><a
        href="https://football.fantasysports.yahoo.com/f1/{LEAGUE}/1">Barry Mcockiner</a></td><td>Connor</td>
      <td>x@example.com</td><td>$100</td><td>1</td><td>0</td></tr>
  <tr><td><a href="/f1/{LEAGUE}/2">Camp Gator</a></td><td>Nico De Ruiter</td>
      <td>y@example.com</td><td>$99</td><td>10</td><td>4</td></tr>
</tbody></table></body></html>
"""


class TestParseTeams:
    def test_teams(self):
        rows = parse_teams(soup_of(TEAMS_HTML), LEAGUE)
        assert len(rows) == 2
        assert rows[0]["team_key"] == "1"
        # name comes from the text anchor, not the logo-only anchor
        assert rows[0]["name"] == "Barry Mcockiner"
        assert rows[0]["manager"] == "Connor"
        assert rows[0]["logo_url"] == "https://x/logo1.png"
        assert rows[0]["faab_remaining"] == 100.0
        assert rows[1]["faab_remaining"] == 99.0


class TestCurrentWeek:
    def test_header(self):
        # league home flattens to '... Matchups Week 2 Matchups ...' for the
        # currently displayed (= current) week
        assert parse_current_week(soup_of("<p>Matchups Week 2 Matchups Week 1</p>")) == 2

    def test_missing_header_raises(self):
        with pytest.raises(ValueError):
            parse_current_week(soup_of("<p>nothing here</p>"))
