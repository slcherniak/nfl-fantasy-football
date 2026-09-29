"""Write computed tables to Google Sheets.

The sheet is presentation only: every write is plain values (no formulas),
each owned tab is cleared and fully rewritten in a single batched update, and
tabs not in config.yaml:sheets.tabs are never touched — manual tabs survive.
Auth is a service account (share the sheet with its client_email).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import gspread
import pandas as pd
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def get_client() -> gspread.Client:
    raw_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw_json:
        info = json.loads(raw_json)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
        creds = Credentials.from_service_account_file(path, scopes=SCOPES)
    return gspread.authorize(creds)


def _to_values(df: pd.DataFrame, index_label: str | None = None) -> list[list[Any]]:
    """DataFrame -> list-of-lists with a header row, JSON-safe cells."""
    frame = df.copy()
    if index_label is not None:
        frame = frame.reset_index(names=index_label)
    header = [str(c) for c in frame.columns]
    body = [
        ["" if pd.isna(v) else (round(v, 2) if isinstance(v, float) else v) for v in row]
        for row in frame.itertuples(index=False)
    ]
    return [header] + body


def publish_tables(
    sheet_id: str,
    tables: dict[str, pd.DataFrame | list[list[Any]]],
    owned_tabs: set[str],
    charts_tab_names: dict[str, str] | None = None,
) -> None:
    """Rewrite each named tab with its table. Creates missing tabs; refuses
    to write anywhere outside owned_tabs. When charts_tab_names is given,
    native charts on the charts tab are recreated after the values land."""
    client = get_client()
    spreadsheet = client.open_by_key(sheet_id)
    existing = {ws.title: ws for ws in spreadsheet.worksheets()}

    for tab_name, table in tables.items():
        if tab_name not in owned_tabs:
            raise ValueError(f"Refusing to write to unowned tab: {tab_name!r}")
        values = table if isinstance(table, list) else _to_values(table)
        if not values:
            continue
        rows = max(len(values), 20)
        cols = max(max(len(r) for r in values), 8)
        worksheet = existing.get(tab_name)
        if worksheet is None:
            worksheet = spreadsheet.add_worksheet(title=tab_name, rows=rows, cols=cols)
            existing[tab_name] = worksheet
        worksheet.clear()
        # One batched values write per tab — never cell-by-cell.
        worksheet.update(values=values, range_name="A1", value_input_option="RAW")
        if tab_name.startswith("_raw"):
            try:
                worksheet.hide()
            except Exception:  # older gspread or already hidden
                logger.debug("Could not hide tab %s", tab_name)
        logger.info("Published %d rows to tab %r", len(values), tab_name)

    if charts_tab_names:
        _refresh_charts(spreadsheet, existing, tables, charts_tab_names, owned_tabs)


def _refresh_charts(
    spreadsheet: gspread.Spreadsheet,
    existing: dict[str, gspread.Worksheet],
    tables: dict[str, Any],
    tab_names: dict[str, str],
    owned_tabs: set[str],
) -> None:
    from src.charts import build_chart_requests

    charts_tab = tab_names["charts"]
    if charts_tab not in owned_tabs:
        raise ValueError(f"Charts tab {charts_tab!r} is not in the owned tab list")
    if charts_tab not in existing:
        existing[charts_tab] = spreadsheet.add_worksheet(title=charts_tab, rows=120, cols=26)
    metadata = spreadsheet.fetch_sheet_metadata()
    requests = build_chart_requests(metadata, tables, tab_names)
    if requests:
        spreadsheet.batch_update({"requests": requests})
    logger.info("Refreshed charts on tab %r", charts_tab)
