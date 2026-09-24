"""File sources: CSV exports and JSON / JSONL files.

Works with *any* export - Apollo people/company exports, Sales Navigator /
Clay lists, job lists, spreadsheets, Apify dataset downloads - because column
headers are auto-detected (case/space/underscore-insensitive, see
``mapping.detect_mapping``) and every field can be remapped by hand.

Both sources never touch the network (``offline = True``), so they also run
in ``--dry-run``.

Config keys (both sources)
--------------------------
path (required)
    File to read. Relative paths resolve against the working directory, then
    the playbook's directory (``Playbook.resolve_path``).
encoding
    Text encoding, default ``utf-8-sig`` (strips an Excel BOM). When the
    default fails to decode, ``cp1252`` then ``latin-1`` are tried.
mapping
    Overrides: canonical field -> column header (CSV) or key / dotted path
    (JSON); a list means "first non-empty". ``null`` disables a detected
    field. Canonical fields are listed in ``leadgen.sources.mapping``.
auto_detect
    Guess the mapping from headers/keys (default true). With false only
    ``mapping`` is used.
defaults
    Field -> constant for records without a value (e.g. ``{country: DE}``).
signal_type
    Type of the per-row signal (default ``job_posting``; a ``signal_type``
    column wins per row).
signal_title_template
    Build the signal title from the row, e.g. ``"Hiring {Role} in {City}"``.
signal_requires
    Columns / paths that must be non-empty for a row's own signal to be created.
default_signal
    ``{type, title, description?, url?, date?}`` attached to rows without a
    signal of their own - use it for static lists (e.g. an event attendee list).
people
    ``{path, mapping}``: a list of people inside each JSON record.
limit
    Max companies returned (0 = all).
label
    Label written to ``Company.sources`` / ``Signal.source`` (default: type).

CSV only: ``delimiter`` (auto-sniffed among ``,`` ``;`` tab ``|``; also
accepts ``tab``/``comma``/``semicolon``/``pipe``) and ``skip_rows`` (lines to
skip before the header row, for exports with a title line).

JSON only: ``records_path`` - dotted path to the list of records inside the
document. Without it the file may be a JSON array, an object wrapping the
list under ``items`` / ``data`` / ``results`` / ``records`` / ``rows`` / ... , a
single object, or JSON Lines (one object per line; ``.jsonl`` / ``.ndjson``).
"""
from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..models import Company, SignalType
from .base import Source
from .mapping import (
    detect_mapping,
    lookup_path,
    merge_mappings,
    normalize_defaults,
    record_keys,
    records_to_companies,
    resolve_columns,
)

_DELIMITER_NAMES = {"tab": "\t", "\\t": "\t", "comma": ",", "semicolon": ";", "pipe": "|"}
_DELIMITER_CANDIDATES = (",", ";", "\t", "|")
_FALLBACK_ENCODINGS = ("cp1252", "latin-1")
_WRAPPER_KEYS = ("items", "data", "results", "records", "rows", "companies", "leads", "jobs",
                 "organizations", "accounts", "people", "contacts")


def sniff_delimiter(text: str) -> str:
    """Pick the delimiter that occurs most often in the header line (outside quotes)."""
    header = next((line for line in text.splitlines() if line.strip()), "")
    unquoted = re.sub(r'"[^"]*"', "", header)
    counts = {d: unquoted.count(d) for d in _DELIMITER_CANDIDATES}
    best = max(_DELIMITER_CANDIDATES, key=lambda d: counts[d])
    return best if counts[best] > 0 else ","


class _FileSource(Source):
    """Shared plumbing: path resolution, decoding, mapping assembly."""

    offline = True
    default_signal_type = SignalType.JOB_POSTING

    def _path(self) -> Path:
        raw = self.config.get("path") or self.config.get("file")
        if not raw:
            raise ValueError(f"source {self.label}: 'path' is required")
        path = self.ctx.playbook.resolve_path(str(raw))
        if not path.exists():
            raise FileNotFoundError(f"source {self.label}: file not found: {raw}")
        if path.is_dir():
            raise ValueError(f"source {self.label}: {raw} is a directory, expected a file")
        return path

    def _read_text(self, path: Path) -> str:
        configured = self.config.get("encoding")
        data = path.read_bytes()
        encodings = [str(configured)] if configured else ["utf-8-sig", *_FALLBACK_ENCODINGS]
        last: Optional[Exception] = None
        for enc in encodings:
            try:
                text = data.decode(enc)
            except LookupError:
                raise ValueError(f"source {self.label}: unknown encoding {enc!r}") from None
            except UnicodeDecodeError as e:
                last = e
                continue
            if enc != encodings[0]:
                self.log.warning("source %s: %s is not %s; decoded as %s", self.label, path.name,
                                 encodings[0], enc)
            return text.lstrip("﻿")
        raise ValueError(f"source {self.label}: cannot decode {path.name} as {encodings[0]}: {last}")

    def _mapping(self, headers: Sequence[str], *, warn_missing: bool) -> Tuple[Dict[str, List[str]], Dict[str, Any]]:
        info: Dict[str, Any] = {"mode": "manual", "signal_type": None, "unmapped": []}
        detected: Dict[str, List[str]] = {}
        if self.config.get("auto_detect", True):
            detected, info = detect_mapping(headers)
        overrides = resolve_columns(self.config.get("mapping"), headers,
                                    log=self.log if warn_missing else None, label=self.label)
        mapping = merge_mappings(detected, overrides)
        self.log.debug("source %s: mode=%s mapping=%s", self.label, info.get("mode"), mapping)
        if info.get("unmapped"):
            self.log.debug("source %s: ignored columns: %s", self.label, ", ".join(info["unmapped"]))
        return mapping, info

    def _defaults(self) -> Dict[str, Any]:
        defaults = normalize_defaults(self.config.get("defaults"))
        stype = self.config.get("signal_type") or defaults.get("signal_type") or self.default_signal_type
        defaults["signal_type"] = stype
        return defaults

    def _to_companies(self, records: List[Dict[str, Any]], headers: Sequence[str], *,
                      warn_missing: bool) -> List[Company]:
        mapping, info = self._mapping(headers, warn_missing=warn_missing)
        if not any(k in mapping for k in ("name", "domain", "website", "email")):
            self.log.warning("source %s: no company name/domain column found (headers: %s); "
                             "set 'mapping: {name: <column>}'", self.label, ", ".join(headers[:30]))
        return records_to_companies(
            records, mapping, label=self.label, defaults=self._defaults(), today=self.ctx.today,
            limit=self.limit, signal_title_template=self.config.get("signal_title_template"),
            default_signal=self.config.get("default_signal"), people=self.config.get("people"),
            location_from_signal=bool(self.config.get("location_from_signal", True)),
            signal_requires=self.config.get("signal_requires"), log=self.log,
        )


class CsvSource(_FileSource):
    """Companies (+ signals + contacts) from a CSV export. See module docstring for config."""

    name = "csv"

    def _delimiter(self, text: str) -> str:
        raw = self.config.get("delimiter")
        if raw is None or raw == "":
            return sniff_delimiter(text)
        d = _DELIMITER_NAMES.get(str(raw).lower(), str(raw))
        if len(d) != 1:
            raise ValueError(f"source {self.label}: delimiter must be a single character, got {raw!r}")
        return d

    def read_rows(self) -> Tuple[List[str], List[Dict[str, str]]]:
        """Return (headers, rows as dicts). Blank rows are skipped, duplicate headers renamed."""
        path = self._path()
        text = self._read_text(path)
        try:
            skip = max(0, int(self.config.get("skip_rows") or 0))
        except (TypeError, ValueError):
            raise ValueError(f"source {self.label}: skip_rows must be an integer") from None
        if skip:
            text = "\n".join(text.splitlines()[skip:])
        reader = csv.reader(io.StringIO(text), delimiter=self._delimiter(text))
        headers: List[str] = []
        rows: List[Dict[str, str]] = []
        try:
            for raw in reader:
                if not any((cell or "").strip() for cell in raw):
                    continue
                if not headers:
                    headers = self._headers(raw)
                    continue
                cells = list(raw) + [""] * (len(headers) - len(raw))
                rows.append({h: (cells[i] or "").strip() for i, h in enumerate(headers)})
        except csv.Error as e:
            raise ValueError(f"source {self.label}: malformed CSV in {path.name} "
                             f"(line {reader.line_num}): {e}") from None
        return headers, rows

    @staticmethod
    def _headers(raw: Sequence[str]) -> List[str]:
        headers: List[str] = []
        seen: Dict[str, int] = {}
        for i, h in enumerate(raw):
            h = (h or "").strip() or f"column_{i + 1}"
            if h in seen:
                seen[h] += 1
                h = f"{h} ({seen[h]})"
            else:
                seen[h] = 1
            headers.append(h)
        return headers

    def fetch(self) -> List[Company]:
        headers, rows = self.read_rows()
        if not headers:
            self.log.warning("source %s: file is empty", self.label)
            return []
        companies = self._to_companies(rows, headers, warn_missing=True)
        self.log.info("source %s: %d rows -> %d companies", self.label, len(rows), len(companies))
        return companies


class JsonSource(_FileSource):
    """Companies from a JSON array, a wrapped list ({"items": [...]}) or JSON Lines."""

    name = "json"

    def read_records(self) -> List[Dict[str, Any]]:
        path = self._path()
        text = self._read_text(path).strip()
        if not text:
            return []
        if path.suffix.lower() in (".jsonl", ".ndjson"):
            return self._jsonl(text, path)
        try:
            doc = json.loads(text)
        except json.JSONDecodeError as e:
            if "\n" in text:
                try:
                    return self._jsonl(text, path)
                except ValueError:
                    pass
            raise ValueError(f"source {self.label}: {path.name} is not valid JSON/JSONL: {e}") from None
        return self._extract(doc)

    def _jsonl(self, text: str, path: Path) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for n, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"source {self.label}: {path.name} line {n} is not valid JSON: {e}") from None
            if isinstance(obj, list):
                out.extend(o for o in obj if isinstance(o, dict))
            elif isinstance(obj, dict):
                out.append(obj)
        return out

    def _extract(self, doc: Any) -> List[Dict[str, Any]]:
        rp = self.config.get("records_path")
        if rp:
            found = lookup_path(doc, str(rp))
            if isinstance(found, dict):
                found = [found]
            if not isinstance(found, list):
                raise ValueError(f"source {self.label}: records_path {rp!r} does not point to a list")
            return [r for r in found if isinstance(r, dict)]
        if isinstance(doc, list):
            return [r for r in doc if isinstance(r, dict)]
        if isinstance(doc, dict):
            for key in _WRAPPER_KEYS:
                if isinstance(doc.get(key), list):
                    return [r for r in doc[key] if isinstance(r, dict)]
            return [doc]
        raise ValueError(f"source {self.label}: expected a JSON array or object, got {type(doc).__name__}")

    def fetch(self) -> List[Company]:
        records = self.read_records()
        if not records:
            self.log.warning("source %s: no records found", self.label)
            return []
        companies = self._to_companies(records, record_keys(records), warn_missing=False)
        self.log.info("source %s: %d records -> %d companies", self.label, len(records), len(companies))
        return companies


__all__ = ["CsvSource", "JsonSource", "sniff_delimiter"]
