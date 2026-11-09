"""The club roster: from its Google Sheet, or (after `w8lbz-minutes roster --import` and `roster: source: app`
in club.yaml) from the app's own copy of that sheet, data/roster-sheet.json. Either way it's the single source
of truth for people, and data/roster.json is built from it.

Reads columns by header name, so adding or reordering columns doesn't break it.
Columns used: callsign, full_name, email, Title(s), is_officer, is_board, Class,
minutes_editor (may lead meetings and edit minutes), calendar_editor (may change the club calendars), Status (Active / Inactive /
Silent Key / Prospect) and aliases (comma-separated spellings seen in old minutes).

Also phone, address, birthday (MM-DD, or MM for a month only) and share (which of email, phone, address,
birthday the member shows to all members; officers always see them). The cache
file is readable only by the app.

Members' own profile edits are written back through `Sheet`, which only ever
writes cell values to existing columns: never formatting, dropdowns or structure.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .club import DATA, PREVIEW, ROOT, ROSTER_CACHE as CACHE, roster_locked

KEY_FILE = Path(os.environ.get("W8LBZ_GOOGLE_KEY", ROOT / "private" / "google-service-account.json"))


def _truthy(v: str) -> bool:
    return v.strip().lower() in ("true", "yes", "y", "1", "x")


def _split(v: str) -> list[str]:
    return [x.strip() for x in v.split(",") if x.strip()]


def _cfg() -> dict:
    """`roster:` in local club.yaml, never in git."""
    import yaml
    cfg = yaml.safe_load(Path(os.environ.get("W8LBZ_CONFIG", ROOT / "club.yaml")).read_text()) or {}
    return cfg.get("roster") or {}


def in_app() -> bool:
    """The roster lives in the app (roster: source: app), not in the Google Sheet. Preview mode keeps its own copy."""
    return not PREVIEW and _cfg().get("source") == "app"


def _local() -> Path:
    return DATA / "roster-sheet.json"


def _sheet() -> tuple[str, str]:
    r = _cfg()
    if not r.get("sheet_id"):
        raise SystemExit("Set roster.sheet_id in club.yaml")
    return r["sheet_id"], r.get("tab", "Member Roster")


SHAREABLE = ("email", "phone", "address", "birthday")
EDITABLE = ("email", "phone", "address", "birthday", "share")  # what members change themselves
# What roster keepers (and admins) change: member field -> roster sheet header (lowercase). Not the callsign: it's
# the member's id in the app's records, so a change needs its own step.
COLUMNS = {"name": "full_name", "license_class": "class", "status": "status", "role": "title(s)", "officer": "is_officer",
           "board": "is_board", "minutes_editor": "minutes_editor", "calendar_editor": "calendar_editor",
           "aliases": "aliases", **{f: f for f in EDITABLE}}
FLAGS = ("officer", "board", "minutes_editor", "calendar_editor")  # checkboxes in the sheet: written as true/false


def _col(i: int) -> str:
    """0-based column index -> A1 letters."""
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


class SheetError(Exception):
    pass


class Sheet:
    """Values-only read/write of the roster tab, for members' profile edits."""

    def __init__(self, session=None):
        if session is None and PREVIEW:
            session = PreviewSheet()
        elif session is None and in_app():
            session = LocalSheet(_local())
        if session is None:
            import google.auth.transport.requests as gr
            from google.oauth2 import service_account
            creds = service_account.Credentials.from_service_account_file(
                KEY_FILE, scopes=["https://www.googleapis.com/auth/spreadsheets"])
            session = gr.AuthorizedSession(creds)
        self.s = session
        self.sid, self.tab = ("local", "Member Roster") if isinstance(self.s, LocalSheet) else _sheet()

    def _url(self, rng: str = "") -> str:
        return f"https://sheets.googleapis.com/v4/spreadsheets/{self.sid}/values{rng}"

    def read(self, tab: str | None = None) -> list[list[str]]:
        r = self.s.get(self._url(f"/'{tab or self.tab}'"), params={"valueRenderOption": "UNFORMATTED_VALUE"})
        r.raise_for_status()
        return [[str(x) for x in row] for row in r.json().get("values", [])]

    def write(self, cells: dict[str, str], tab: str | None = None) -> None:
        """cells: A1 address (e.g. D5) -> value. RAW, so nothing typed is ever parsed as a formula."""
        body = {"valueInputOption": "RAW",
                "data": [{"range": f"'{tab or self.tab}'!{a1}", "values": [[v]]} for a1, v in cells.items()]}
        r = self.s.post(self._url(":batchUpdate"), json=body)
        if r.status_code == 403:
            raise SheetError("The app can't write to the roster sheet yet (it needs Editor access).")
        r.raise_for_status()

    def update_member(self, callsign: str | None, full_name: str, changes: dict[str, str]) -> dict[str, str]:
        """Write `changes` (field -> new value) into the member's row. Returns the old values."""
        values = self.read()
        header = [h.strip().lower() for h in values[0]]
        missing = [f for f in changes if f not in header]
        if missing:
            raise SheetError(f"The roster sheet has no {', '.join(missing)} column yet.")
        key = header.index("callsign") if callsign else header.index("full_name")
        want = (callsign or full_name).strip().upper()
        rows = [i for i, row in enumerate(values[1:], start=2)
                if len(row) > key and row[key].strip().upper() == want]
        if len(rows) != 1:
            raise SheetError(f"Couldn't find exactly one roster row for {callsign or full_name}.")
        row = values[rows[0] - 1] + [""] * len(header)
        old = {f: row[header.index(f)] for f in changes}
        self.write({f"{_col(header.index(f))}{rows[0]}": v for f, v in changes.items() if old[f] != v})
        return old


    def add_row(self, values: dict[str, str], tab: str | None = None, optional: tuple[str, ...] = ()) -> int:
        """Write a new row (header name -> value) under the last row that has a full_name. Values only:
        rows are never inserted, so the sheet's formatting, dropdowns and structure are left alone.
        (Going by full_name, not "any cell", skips rows that only hold empty checkboxes or dropdowns.)"""
        grid = self.read(tab)
        header = [h.strip().lower() for h in grid[0]] if grid else []
        values = {f: v for f, v in values.items() if f in header or f not in optional}
        missing = [f for f in values if f not in header]
        if missing or "full_name" not in header:
            raise SheetError(f"The {tab or self.tab} tab has no {', '.join(missing or ['full_name'])} column.")
        key = header.index("full_name")
        row = max((i for i, r in enumerate(grid, start=1) if i > 1 and len(r) > key and str(r[key]).strip()),
                  default=1) + 1
        self.write({f"{_col(header.index(f))}{row}": v for f, v in values.items() if v}, tab)
        return row


class LocalSheet:
    """The Sheets API's values calls against a local file of grids, one per tab: the roster itself once it lives
    in the app, and preview mode's copy."""

    def __init__(self, path: Path):
        self.path = path

    def _tabs(self) -> dict:
        if not self.path.exists():
            raise SheetError(f"{self.path} is missing: run `w8lbz-minutes roster --import` (or restore a backup).")
        return json.loads(self.path.read_text())

    def save(self, tabs: dict) -> None:
        """Atomic, and readable only by the app: once the roster lives here, this file is the only copy."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(tabs))
        tmp.chmod(0o600)
        tmp.replace(self.path)

    class _Response:
        def __init__(self, data=None):
            self.data, self.status_code = data or {}, 200

        def json(self):
            return self.data

        def raise_for_status(self):
            pass

    def get(self, url, params=None):
        tab = url.rsplit("/", 1)[1].strip("'")
        return self._Response({"values": self._tabs().get(tab, [])})

    def post(self, url, json=None):
        tabs = self._tabs()
        for d in json["data"]:
            tab, a1 = d["range"].split("!")
            grid = tabs.setdefault(tab.strip("'"), [])
            col_letters = re.match(r"[A-Z]+", a1).group()
            col = sum((ord(ch) - 64) * 26 ** i for i, ch in enumerate(reversed(col_letters))) - 1
            row = int(a1[len(col_letters):]) - 1
            grid += [[] for _ in range(row + 1 - len(grid))]
            grid[row] += [""] * (col + 1 - len(grid[row]))
            grid[row][col] = d["values"][0][0]
        self.save(tabs)
        return self._Response()


class PreviewSheet(LocalSheet):
    """Preview mode's copy (preview-sheet.json), built from the roster cache, so roster edits can be tried without
    touching the real roster."""
    HEADER = ["callsign", "full_name", "email", "Phone", "Address", "Class", "Title(s)", "Status", "birthday", "share",
              "is_officer", "is_board", "minutes_editor", "calendar_editor", "aliases"]
    LOG_HEADER = ["callsign", "full_name", "date", "event", "note"]

    def __init__(self):
        super().__init__(DATA / "preview-sheet.json")
        if not self.path.exists():
            people = json.loads(CACHE.read_text()) if CACHE.exists() else []
            rows = [[p.get("callsign") or "", f"{p['first']} {p['last']}", p.get("email") or "", p.get("phone") or "",
                     p.get("address") or "", p.get("license_class") or "", p.get("role") or "",
                     p.get("status") or "Active", p.get("birthday") or "", ", ".join(p.get("share") or []),
                     bool(p.get("officer")), bool(p.get("board")), bool(p.get("minutes_editor")),
                     bool(p.get("calendar_editor")), ", ".join(p.get("aliases") or [])]
                    for p in people]
            self.save({"Member Roster": [self.HEADER] + rows, "Membership Log": [self.LOG_HEADER]})


def _google_values(tab: str | None = None) -> list[list]:
    """One tab of the Google Sheet, as the API gives it (dates as serial numbers, ticks as booleans)."""
    import google.auth.transport.requests as gr
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(
        KEY_FILE, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    r = gr.AuthorizedSession(creds).get(
        "https://sheets.googleapis.com/v4/spreadsheets/{}/values/{}".format(_sheet()[0], tab or _sheet()[1]),
        params={"valueRenderOption": "UNFORMATTED_VALUE", "dateTimeRenderOption": "SERIAL_NUMBER"})
    r.raise_for_status()
    return r.json().get("values", [])


def _dicts(values: list[list]) -> list[dict[str, str]]:
    """A grid (header row first) -> one dict per row, keyed by lowercase header."""
    if not values:
        return []
    header = [h.strip().lower() for h in values[0]]
    return [dict(zip(header, [str(x) for x in row] + [""] * (len(header) - len(row)))) for row in values[1:]]


def fetch_rows(tab: str | None = None) -> list[dict[str, str]]:
    if in_app():
        return _dicts(LocalSheet(_local())._tabs().get(tab or "Member Roster", []))
    return _dicts(_google_values(tab))


def to_members(rows: list[dict[str, str]]) -> list[dict]:
    out = []
    for r in rows:
        name, cs = r.get("full_name", "").strip(), r.get("callsign", "").strip().upper()
        if not name:
            continue
        first, _, last = name.partition(" ")
        titles = _split(r.get("title(s)", ""))
        out.append({
            # Callsign is the stable key; fall back to the name (e.g. a silent key whose call is unknown).
            "id": cs.lower() or re.sub(r"[^a-z]+", "-", name.lower()).strip("-"),
            "first": first,
            "last": last,
            "callsign": cs or None,
            "email": r.get("email", "").strip() or None,
            "role": ", ".join(titles) or None,
            "officer": _truthy(r.get("is_officer", "")),
            "board": _truthy(r.get("is_board", "")),
            "license_class": r.get("class", "").strip() or None,
            "aliases": _split(r.get("aliases", "")),
            "minutes_editor": _truthy(r.get("minutes_editor", "")),
            "calendar_editor": _truthy(r.get("calendar_editor", "")),
            "status": r.get("status", "").strip() or "Active",
            "phone": r.get("phone", "").strip() or None,
            "address": r.get("address", "").strip() or None,
            "birthday": _month_day(r.get("birthday", "")),
            "share": [x.lower() for x in _split(r.get("share", "")) if x.lower() in SHAREABLE],
        })
    return out


def _iso_date(v: str) -> str | None:
    """Sheets date serial (days since 1899-12-30) or a typed date -> YYYY-MM-DD."""
    import datetime as dt
    v = v.strip()
    if not v:
        return None
    try:
        return (dt.date(1899, 12, 30) + dt.timedelta(days=int(float(v)))).isoformat()
    except ValueError:
        for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
            try:
                return dt.datetime.strptime(v, fmt).date().isoformat()
            except ValueError:
                pass
    return v


def _month_day(v: str) -> str | None:
    """Birthday as MM-DD, or MM for a month only (the year is never kept), from those, a typed date or a Sheets date serial."""
    v = v.strip()
    if re.fullmatch(r"\d{2}-\d{2}", v):
        return v
    if re.fullmatch(r"\d{1,2}", v) and 1 <= int(v) <= 12:
        return f"{int(v):02d}"
    iso = _iso_date(v) if v else None
    return iso[5:] if iso and re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso) else None


def attach_log(members: list[dict], log: list[dict[str, str]]) -> None:
    """Membership Log tab: one row per event (Joined, Left, Silent Key, Callsign Change, ...)."""
    by_cs = {m["callsign"]: m for m in members if m["callsign"]}
    by_name = {m["first"] + " " + m["last"]: m for m in members}
    for e in log:
        m = by_cs.get(e.get("callsign", "").strip().upper()) or by_name.get(e.get("full_name", "").strip())
        if m and e.get("event"):
            m.setdefault("events", []).append(
                {"date": _iso_date(e.get("date", "")), "event": e["event"].strip(), "note": e.get("note", "").strip() or None})
    for m in members:
        joins = [e["date"] for e in m.get("events", []) if e["event"] in ("Joined", "Rejoined") and e["date"]]
        m["member_since"] = max(joins) if joins else None


@roster_locked  # from fetching to writing: a profile saved meanwhile waits, so this can't write over it with an older copy
def sync() -> list[dict]:
    members = to_members(fetch_rows())
    try:
        attach_log(members, fetch_rows("Membership Log"))
    except Exception as e:  # the log is optional
        print(f"Membership Log not read: {e}")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(members, indent=1))
    tmp.chmod(0o600)
    tmp.replace(CACHE)
    return members


@roster_locked
def import_sheet() -> list[str]:
    """Once: copy the Google Sheet's roster and Membership Log tabs into the app (data/roster-sheet.json), as the
    API gives them. Returns how the roster built from that copy differs from roster.json (expected: nothing)."""
    if in_app() or _local().exists():
        raise SystemExit(f"The roster is already in the app ({_local()}). Nothing imported.")
    tabs = {"Member Roster": _google_values(), "Membership Log": _google_values("Membership Log")}
    LocalSheet(_local()).save(tabs)
    new = to_members(_dicts(tabs["Member Roster"]))
    attach_log(new, _dicts(tabs["Membership Log"]))
    old = {m["id"]: m for m in load_cached() or []}
    out = []
    for m in new:
        o = old.pop(m["id"], None)
        if o is None:
            out.append(f"{m['id']}: only in the sheet")
        elif diff := sorted(k for k in set(m) | set(o) if m.get(k) != o.get(k)):
            out.append(f"{m['id']}: {', '.join(diff)} differ")
    return out + [f"{i}: only in roster.json" for i in old]


def load_cached() -> list[dict] | None:
    return json.loads(CACHE.read_text()) if CACHE.exists() else None
