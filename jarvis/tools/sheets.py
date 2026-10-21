"""The owner's job-search Google Sheet (GOOGLE_SHEET_ID): read rows, add rows, change cells."""

from googleapiclient.discovery import build

from jarvis import config, google_auth, notify
from jarvis.tools import tool


def _values():
    if not config.GOOGLE_SHEET_ID:
        raise RuntimeError("No job-search sheet set (GOOGLE_SHEET_ID in .env)")
    # New client per call; see gmail._gmail.
    service = build("sheets", "v4", credentials=google_auth.credentials(), cache_discovery=False)
    return service.spreadsheets()


def _tab(name):
    return "'" + name.replace("'", "''") + "'"


def _column_letter(index):
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


@tool("List the tabs of the owner's job-search Google Sheet with their column headings.")
def list_sheet_tabs():
    sheets = _values()
    meta = sheets.get(spreadsheetId=config.GOOGLE_SHEET_ID,
                      fields="sheets.properties(title,gridProperties.rowCount)").execute()
    titles = [s["properties"]["title"] for s in meta["sheets"]]
    heads = sheets.values().batchGet(spreadsheetId=config.GOOGLE_SHEET_ID,
                                     ranges=[f"{_tab(t)}!1:1" for t in titles]).execute()
    return [{"tab": t, "columns": (r.get("values") or [[]])[0]}
            for t, r in zip(titles, heads["valueRanges"])]


@tool("Read rows from a tab of the job-search sheet. Each row comes with its sheet row number "
      "(row 1 is the headings).",
      tab={"type": "string"},
      contains={"type": "string", "description": "Only rows containing this text "
                "(case-insensitive), or empty for all"})
def read_sheet(tab, contains):
    rows = _values().values().get(spreadsheetId=config.GOOGLE_SHEET_ID,
                                  range=_tab(tab)).execute().get("values", [])
    if not rows:
        return "That tab is empty."
    header = rows[0]
    matching = [(n, row) for n, row in enumerate(rows[1:], start=2)
                if any(c != "" for c in row)
                and (not contains or any(contains.lower() in str(c).lower() for c in row))]
    if not matching:
        return "No matching rows."
    if len(header) < 2:
        # A notes page laid out like a document (a title in A1, text below), not a table.
        lines = [f"{n}: " + " | ".join(c for c in row if c != "") for n, row in matching]
        return "\n".join([f"Row 1: {header[0] if header else ''}"] + lines[:300])
    names = lambda i: header[i] if i < len(header) and header[i] else _column_letter(i)
    return [{"row": n, **{names(i): v for i, v in enumerate(row) if v != ""}}
            for n, row in matching][:200]


@tool("Add a row at the bottom of a tab of the job-search sheet.",
      tab={"type": "string"},
      values={"type": "array", "items": {"type": "string"},
              "description": "One value per column, in the tab's column order ('' to leave blank)"})
def add_sheet_row(tab, values):
    _values().values().append(spreadsheetId=config.GOOGLE_SHEET_ID, range=_tab(tab),
                              valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
                              body={"values": [values]}).execute()
    shown = ", ".join(v for v in values if v)
    notify.record(f"📊 Added to {tab}: {shown}")
    return f"Added to {tab}: {shown}"


@tool("Change one cell in the job-search sheet, e.g. update an application's status.",
      tab={"type": "string"},
      row={"type": "integer", "minimum": 2, "description": "Sheet row number from read_sheet"},
      column={"type": "string", "description": "Column heading"},
      value={"type": "string"})
def set_sheet_cell(tab, row, column, value):
    sheets = _values()
    header = sheets.values().get(spreadsheetId=config.GOOGLE_SHEET_ID,
                                 range=f"{_tab(tab)}!1:1").execute().get("values", [[]])[0]
    matches = [i for i, h in enumerate(header) if h.strip().lower() == column.strip().lower()]
    if not matches:
        return f"No column called {column!r}. Columns: {', '.join(header)}"
    cell = f"{_column_letter(matches[0])}{row}"
    sheets.values().update(spreadsheetId=config.GOOGLE_SHEET_ID, range=f"{_tab(tab)}!{cell}",
                           valueInputOption="USER_ENTERED", body={"values": [[value]]}).execute()
    notify.record(f"📊 {tab} row {row}: {header[matches[0]]} set to {value}")
    return f"Set {header[matches[0]]} in row {row} of {tab} to {value}."
