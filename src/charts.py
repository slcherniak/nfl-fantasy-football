"""Embedded Google Sheets charts for the visual dashboard.

Builds batchUpdate requests that (re)create native Sheets charts on the
Charts tab, anchored to the value tabs the publisher rewrites. Because the
charts reference cell ranges, every publish refreshes them automatically —
the chart objects themselves only need to be recreated when this layout
changes, but recreating them idempotently each run is cheap and keeps the
tab self-healing.

Colors: a 12-slot categorical palette validated with the dataviz skill's
checker (light surface; worst adjacent CVD pair sits in the 6-8 warn band,
covered by the legend + hover tooltips + the data tabs as table view).
Team colors are assigned by column position in the Trends pivot, which is
ordered by stable team_key — so a team keeps its color all season regardless
of rank.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# Validated 12-slot categorical order (see module docstring).
PALETTE = [
    "#2a78d6", "#eb6834", "#1baf7a", "#9c5b21", "#e87ba4", "#4a3aa7",
    "#eda100", "#00879e", "#e34948", "#008300", "#b04fc4", "#77820a",
]
# High-contrast single-series slots for bar charts.
BLUE, VIOLET, ORANGE, RED = "#2a78d6", "#4a3aa7", "#eb6834", "#e34948"

CHART_WIDTH_PX = 640
CHART_HEIGHT_PX = 400
ROWS_PER_CHART = 22


def _rgb(hex_color: str) -> dict[str, float]:
    hex_color = hex_color.lstrip("#")
    return {
        "red": int(hex_color[0:2], 16) / 255,
        "green": int(hex_color[2:4], 16) / 255,
        "blue": int(hex_color[4:6], 16) / 255,
    }


def _grid_range(sheet_id: int, n_rows: int, col: int) -> dict[str, Any]:
    """One column including its header row."""
    return {
        "sheetId": sheet_id,
        "startRowIndex": 0,
        "endRowIndex": n_rows + 1,
        "startColumnIndex": col,
        "endColumnIndex": col + 1,
    }


def _source(sheet_id: int, n_rows: int, col: int) -> dict[str, Any]:
    return {"sourceRange": {"sources": [_grid_range(sheet_id, n_rows, col)]}}


def _basic_chart(
    title: str,
    chart_type: str,
    sheet_id: int,
    n_rows: int,
    domain_col: int,
    series: list[tuple[int, str]],
    charts_sheet_id: int,
    anchor_row: int,
    anchor_col: int,
    legend: str,
    y_title: str,
) -> dict[str, Any]:
    return {
        "addChart": {
            "chart": {
                "spec": {
                    "title": title,
                    "basicChart": {
                        "chartType": chart_type,
                        "legendPosition": legend,
                        "headerCount": 1,
                        "domains": [{"domain": _source(sheet_id, n_rows, domain_col)}],
                        "series": [
                            {
                                "series": _source(sheet_id, n_rows, col),
                                "targetAxis": "LEFT_AXIS",
                                "colorStyle": {"rgbColor": _rgb(color)},
                            }
                            for col, color in series
                        ],
                        "axis": [{"position": "LEFT_AXIS", "title": y_title}],
                    },
                },
                "position": {
                    "overlayPosition": {
                        "anchorCell": {
                            "sheetId": charts_sheet_id,
                            "rowIndex": anchor_row,
                            "columnIndex": anchor_col,
                        },
                        "widthPixels": CHART_WIDTH_PX,
                        "heightPixels": CHART_HEIGHT_PX,
                    }
                },
            }
        }
    }


def _columns(table: pd.DataFrame | list[list[Any]]) -> list[str]:
    if isinstance(table, pd.DataFrame):
        return [str(c) for c in table.columns]
    return [str(c) for c in table[0]] if table else []


def _n_rows(table: pd.DataFrame | list[list[Any]]) -> int:
    """Data rows under the header. For a stacked values block (Trends), only
    the first block — up to the blank spacer row — belongs to the chart."""
    if isinstance(table, pd.DataFrame):
        return len(table)
    count = 0
    for row in table[1:]:
        if not row or all(v == "" for v in row):
            break
        count += 1
    return count


def build_chart_requests(
    metadata: dict[str, Any],
    tables: dict[str, pd.DataFrame | list[list[Any]]],
    tab_names: dict[str, str],
) -> list[dict[str, Any]]:
    """All batchUpdate requests: delete this tab's old charts, add fresh ones.

    metadata is the spreadsheet metadata (spreadsheets.get), used for sheet
    ids and existing chart ids. Charts whose data column is missing (e.g.
    playoff odds before any future games exist) are skipped.
    """
    sheet_ids: dict[str, int] = {}
    requests: list[dict[str, Any]] = []
    charts_tab = tab_names["charts"]

    for sheet in metadata.get("sheets", []):
        props = sheet.get("properties", {})
        sheet_ids[props.get("title", "")] = props.get("sheetId")
        if props.get("title") == charts_tab:
            for chart in sheet.get("charts", []):
                requests.append({"deleteEmbeddedObject": {"objectId": chart["chartId"]}})

    charts_sheet_id = sheet_ids.get(charts_tab)
    if charts_sheet_id is None:
        raise ValueError(f"Charts tab {charts_tab!r} does not exist yet")

    slot = 0

    def anchor() -> tuple[int, int]:
        row = (slot // 2) * ROWS_PER_CHART
        col = 0 if slot % 2 == 0 else 10
        return row, col

    # 1. PPG by week — one line per team, colors stable by column position.
    trends = tables.get(tab_names["trends"])
    trends_id = sheet_ids.get(tab_names["trends"])
    if trends is not None and trends_id is not None:
        cols = _columns(trends)
        n = _n_rows(trends)
        series = [
            (i, PALETTE[(i - 1) % len(PALETTE)]) for i in range(1, len(cols))
        ]
        row, col = anchor()
        requests.append(_basic_chart(
            "Points scored by week", "LINE", trends_id, n, 0, series,
            charts_sheet_id, row, col, "RIGHT_LEGEND", "Points",
        ))
        slot += 1

    # 2. Actual vs expected wins (the luck chart).
    luck = tables.get(tab_names["luck_report"])
    luck_id = sheet_ids.get(tab_names["luck_report"])
    if luck is not None and luck_id is not None:
        cols = _columns(luck)
        if "actual_wins" in cols and "expected_wins" in cols:
            row, col = anchor()
            requests.append(_basic_chart(
                "Actual vs expected wins (gap = luck)", "COLUMN",
                luck_id, _n_rows(luck), cols.index("team"),
                [(cols.index("actual_wins"), BLUE), (cols.index("expected_wins"), ORANGE)],
                charts_sheet_id, row, col, "BOTTOM_LEGEND", "Wins",
            ))
            slot += 1

    # 3. Power ranking composite.
    dash = tables.get(tab_names["dashboard"])
    dash_id = sheet_ids.get(tab_names["dashboard"])
    dash_cols = _columns(dash) if dash is not None else []
    if dash is not None and dash_id is not None and "composite" in dash_cols:
        row, col = anchor()
        requests.append(_basic_chart(
            "Power ranking composite score", "COLUMN",
            dash_id, _n_rows(dash), dash_cols.index("team"),
            [(dash_cols.index("composite"), VIOLET)],
            charts_sheet_id, row, col, "NO_LEGEND", "Composite z-score",
        ))
        slot += 1

    # 4. Playoff odds (present once future games exist).
    if dash is not None and dash_id is not None and "playoffs_odds" in dash_cols:
        row, col = anchor()
        requests.append(_basic_chart(
            "Playoff odds", "COLUMN",
            dash_id, _n_rows(dash), dash_cols.index("team"),
            [(dash_cols.index("playoffs_odds"), BLUE)],
            charts_sheet_id, row, col, "NO_LEGEND", "Probability",
        ))
        slot += 1

    # 5. Points left on bench (skipped while rosters are Phase-1 empty).
    eff = tables.get(tab_names["efficiency"])
    eff_id = sheet_ids.get(tab_names["efficiency"])
    if eff is not None and eff_id is not None:
        cols = _columns(eff)
        if "points_on_bench" in cols:
            row, col = anchor()
            requests.append(_basic_chart(
                "Points left on bench (season)", "COLUMN",
                eff_id, _n_rows(eff), cols.index("team"),
                [(cols.index("points_on_bench"), RED)],
                charts_sheet_id, row, col, "NO_LEGEND", "Points",
            ))
            slot += 1

    logger.info("Built %d chart requests (%d charts)", len(requests), slot)
    return requests
