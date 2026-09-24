"""Google Sheets review sheet (``type: gsheets``): the ``opportunities.csv`` columns, live in a Sheet.

Writes every scored lead (``scope = "all"``) with the same columns and order
as the CSV exporter (``leadgen.outbound.csv_export.lead_rows``: highest score
first, ``followup_1..N``, ``score_reasons`` ...) to one worksheet.

Needs the optional ``gspread`` package (``pip install gspread`` or
``pip install 'leadgen[sheets]'``); it is imported only when the exporter
runs, so the rest of the engine works without it.

Setup
-----
1. Google Cloud console -> create a service account -> add a JSON key.
2. Enable the Google Sheets API (and Drive API) for that project.
3. Share the spreadsheet with the service account's e-mail as **Editor**.

Values are written with ``value_input_option=RAW``, so Sheets never
evaluates a cell as a formula (scraped text like ``=HYPERLINK(..)`` stays
text) and numbers (score, employees, signal age) stay sortable numbers.

Modes
-----
replace (default)  Clear the worksheet, then write header + all rows (the
                   grid is grown first if it is too small).
append             Add rows under the existing ones. An empty sheet gets
                   the header first. Rows are re-ordered to match the
                   sheet's existing header (columns the sheet lacks are
                   dropped with a warning) and, unless ``dedupe: false``,
                   leads whose ``lead_id`` is already in column A are skipped.

In ``ctx.dry_run`` no Google API call is made (``detail="dry-run"``).

Config keys
-----------
spreadsheet_id        The id from the sheet URL (``/spreadsheets/d/<id>/``), or
spreadsheet_url       the full URL. One of the two is required.
worksheet             Tab name (default: the playbook name). Created if missing.
mode                  ``replace`` (default) | ``append``.
dedupe                append mode: skip lead ids already in column A (default true).
service_account_file  Path to the service-account JSON key; default: env
                      ``GOOGLE_APPLICATION_CREDENTIALS`` (``service_account_file_env``
                      names another variable).
service_account_json  Alternatively the key itself (JSON string or mapping), or
                      env ``GOOGLE_SERVICE_ACCOUNT_JSON`` - handy in CI / cloud.
freeze_header         Freeze row 1 (default true).
label                 Name reported in ``ExportResult.exporter`` (default ``gsheets``).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Tuple

from ..context import MissingCredentialError
from ..models import Lead
from .base import Exporter, ExportResult
from .csv_export import _bool, lead_rows

MODES = ("replace", "append")
JSON_ENV = "GOOGLE_SERVICE_ACCOUNT_JSON"
FILE_ENV = "GOOGLE_APPLICATION_CREDENTIALS"


class GoogleSheetsError(RuntimeError):
    """The spreadsheet could not be opened or written."""


def _import_gspread() -> Any:
    try:
        import gspread  # type: ignore[import-not-found]
    except ImportError as e:
        raise RuntimeError("gsheets: the Google Sheets exporter needs gspread - "
                           "pip install gspread  (or: pip install 'leadgen[sheets]')") from e
    return gspread


def _is_exc(exc: BaseException, gspread: Any, name: str) -> bool:
    """isinstance check against ``gspread.exceptions.<name>`` that tolerates missing classes."""
    cls = getattr(getattr(gspread, "exceptions", None), name, None) or getattr(gspread, name, None)
    if isinstance(cls, type) and isinstance(exc, cls):
        return True
    return type(exc).__name__ == name


class GoogleSheetsExporter(Exporter):
    """Write the review sheet to a Google Sheets worksheet (see module docstring)."""

    name = "gsheets"
    env_key = FILE_ENV
    scope = "all"
    offline = False

    # --- config ---------------------------------------------------------------------
    @property
    def label(self) -> str:
        return str(self.config.get("label") or self.name)

    @property
    def mode(self) -> str:
        mode = str(self.config.get("mode") or "replace").strip().lower()
        if mode not in MODES:
            raise ValueError(f"gsheets: mode must be 'replace' or 'append', got {mode!r}")
        return mode

    @property
    def worksheet_title(self) -> str:
        title = str(self.config.get("worksheet") or self.ctx.playbook.name or "leads").strip()
        return title[:100] or "leads"

    def _spreadsheet_ref(self) -> Tuple[str, str]:
        """('key', id) or ('url', url); raises ValueError when neither is configured."""
        sid = str(self.config.get("spreadsheet_id") or "").strip()
        url = str(self.config.get("spreadsheet_url") or "").strip()
        if sid.startswith(("http://", "https://")):
            sid, url = "", url or sid
        if sid:
            return "key", sid
        if url:
            return "url", url
        raise ValueError("gsheets: set 'spreadsheet_id' (from the sheet URL .../spreadsheets/d/<id>/) "
                         "or 'spreadsheet_url' in the exporter config")

    # --- client ---------------------------------------------------------------------
    def _client(self, gspread: Any) -> Any:
        raw = self.config.get("service_account_json") or self.ctx.env.get(JSON_ENV)
        if raw:
            if isinstance(raw, dict):
                info = raw
            else:
                try:
                    info = json.loads(str(raw))
                except ValueError as e:
                    raise GoogleSheetsError(f"gsheets: service_account_json is not valid JSON: {e}") from None
            if not isinstance(info, dict):
                raise GoogleSheetsError("gsheets: service_account_json must be a JSON object")
            return gspread.service_account_from_dict(info)
        path = self.secret("service_account_file", FILE_ENV, required=False)
        if not path:
            raise MissingCredentialError(
                f"gsheets: missing credentials - set 'service_account_file' (path to a service-account "
                f"JSON key) or ${FILE_ENV}, or put the key itself in ${JSON_ENV}")
        resolved = self.ctx.playbook.resolve_path(path)
        if not resolved.exists():
            raise MissingCredentialError(f"gsheets: service account key file not found: {resolved}")
        return gspread.service_account(filename=str(resolved))

    @staticmethod
    def _account_email(client: Any) -> str:
        """The service account's e-mail (gspread 5: ``client.auth``; 6: ``client.http_client.auth``)."""
        for auth in (getattr(client, "auth", None),
                     getattr(getattr(client, "http_client", None), "auth", None)):
            email = getattr(auth, "service_account_email", "")
            if isinstance(email, str) and email:
                return email
        return ""

    def _open(self, gspread: Any, client: Any) -> Any:
        kind, ref = self._spreadsheet_ref()
        try:
            return client.open_by_key(ref) if kind == "key" else client.open_by_url(ref)
        except Exception as e:  # noqa: BLE001 - translate gspread's errors into one clear message
            who = self._account_email(client)
            share = f" and shared with {who} as Editor" if who else " and shared with the service account as Editor"
            if _is_exc(e, gspread, "SpreadsheetNotFound") or _is_exc(e, gspread, "NoValidUrlKeyFound"):
                raise GoogleSheetsError(f"gsheets: spreadsheet {ref!r} not found - check the id/url{share}") from e
            if _is_exc(e, gspread, "APIError"):
                raise GoogleSheetsError(f"gsheets: could not open spreadsheet {ref!r} ({e}); make sure the "
                                        f"Sheets API is enabled{share}") from e
            raise

    def _worksheet(self, gspread: Any, sh: Any, title: str, rows: int, cols: int) -> Any:
        try:
            return sh.worksheet(title)
        except Exception as e:  # noqa: BLE001
            if not _is_exc(e, gspread, "WorksheetNotFound"):
                raise
        self.log.info("gsheets: creating worksheet '%s'", title)
        return sh.add_worksheet(title=title, rows=max(rows, 100), cols=max(cols, 26))

    @staticmethod
    def _ensure_size(ws: Any, rows: int, cols: int) -> None:
        """Grow the grid when it is smaller than what we write (Sheets rejects out-of-grid updates)."""
        try:
            cur_rows, cur_cols = int(ws.row_count), int(ws.col_count)
        except (AttributeError, TypeError, ValueError):
            return
        if cur_rows < rows or cur_cols < cols:
            ws.resize(rows=max(cur_rows, rows), cols=max(cur_cols, cols))

    # --- export ---------------------------------------------------------------------
    def export(self, leads: List[Lead], out_dir: Path) -> ExportResult:
        header, rows = lead_rows(leads, self.ctx.today)
        mode = self.mode
        title = self.worksheet_title
        if self.ctx.dry_run:
            self.log.info("gsheets: dry-run - not writing %d row(s) to worksheet '%s' (%s)",
                          len(rows), title, mode)
            return ExportResult(exporter=self.label, count=0, detail="dry-run")
        self._spreadsheet_ref()  # fail on missing config before touching credentials
        gspread = _import_gspread()
        client = self._client(gspread)
        sh = self._open(gspread, client)
        ws = self._worksheet(gspread, sh, title, len(rows) + 1, len(header))
        if mode == "replace":
            written, ids = self._replace(ws, header, rows)
        else:
            written, ids = self._append(ws, header, rows)
        if _bool(self.config.get("freeze_header"), True):
            try:
                ws.freeze(rows=1)
            except Exception as e:  # noqa: BLE001 - cosmetic only
                self.log.debug("gsheets: could not freeze header row: %s", e)
        url = str(getattr(sh, "url", "") or "")
        if not url:
            sid = getattr(sh, "id", "") or self._spreadsheet_ref()[1]
            url = f"https://docs.google.com/spreadsheets/d/{sid}"
        verb = "replaced with" if mode == "replace" else "appended"
        detail = f"worksheet '{title}' {verb} {written} rows"
        self.log.info("gsheets: %s (%s)", detail, url)
        return ExportResult(exporter=self.label, count=written, path=url, detail=detail, exported_ids=ids)

    def _replace(self, ws: Any, header: List[str], rows: List[List[Any]]) -> Tuple[int, List[str]]:
        values = [header] + [[("" if v is None else v) for v in r] for r in rows]
        ws.clear()
        self._ensure_size(ws, len(values), len(header))
        ws.update(range_name="A1", values=values, value_input_option="RAW")
        return len(rows), [str(r[0]) for r in rows]

    def _append(self, ws: Any, header: List[str], rows: List[List[Any]]) -> Tuple[int, List[str]]:
        existing = [str(h) for h in (ws.row_values(1) or [])]
        if not any(h.strip() for h in existing):
            values = [header] + [[("" if v is None else v) for v in r] for r in rows]
            if values:
                ws.append_rows(values, value_input_option="RAW", table_range="A1")
            return len(rows), [str(r[0]) for r in rows]
        # re-order our rows to the sheet's existing header
        missing = [h for h in header if h not in existing]
        if missing:
            self.log.warning("gsheets: worksheet header lacks column(s) %s - those values are not "
                             "appended (use mode: replace to rebuild the sheet)", ", ".join(missing))
        dicts = [dict(zip(header, r)) for r in rows]
        if _bool(self.config.get("dedupe"), True) and "lead_id" in existing:
            col = existing.index("lead_id") + 1
            seen = {str(v) for v in (ws.col_values(col) or [])[1:]}
            before = len(dicts)
            dicts = [d for d in dicts if str(d.get("lead_id")) not in seen]
            if before != len(dicts):
                self.log.info("gsheets: %d lead(s) already in the sheet, not appended", before - len(dicts))
        values = [[("" if d.get(h) is None else d.get(h, "")) for h in existing] for d in dicts]
        if values:
            ws.append_rows(values, value_input_option="RAW", table_range="A1")
        return len(values), [str(d.get("lead_id")) for d in dicts]


__all__ = ["GoogleSheetsExporter", "GoogleSheetsError", "MODES"]
