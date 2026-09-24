"""SQLite persistence: the engine's memory between runs.

Holds run history, signal history (for repost / "still open" detection), an
email-verification cache, every lead with its funnel stage, replies,
follow-ups and the suppression list. One file per workspace; many playbooks
can share it (rows are tagged with the playbook name).
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .models import Lead, Reply, Signal, Stage
from .utils import normalize_domain

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    playbook TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    meta TEXT,
    counts TEXT
);
CREATE TABLE IF NOT EXISTS signal_history (
    company_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    external_id TEXT NOT NULL DEFAULT '',
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    PRIMARY KEY (company_key, fingerprint, external_id)
);
CREATE TABLE IF NOT EXISTS verifications (
    email TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    provider TEXT,
    checked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS leads (
    id TEXT PRIMARY KEY,
    playbook TEXT NOT NULL,
    run_id TEXT,
    company_key TEXT NOT NULL,
    company_name TEXT,
    email TEXT,
    score INTEGER,
    tier TEXT,
    stage TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    exported_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(email);
CREATE INDEX IF NOT EXISTS idx_leads_run ON leads(run_id);
CREATE INDEX IF NOT EXISTS idx_leads_playbook ON leads(playbook);
CREATE TABLE IF NOT EXISTS lead_runs (
    lead_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    PRIMARY KEY (lead_id, run_id)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    note TEXT,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_lead ON events(lead_id);
CREATE TABLE IF NOT EXISTS suppression (
    value TEXT NOT NULL,
    kind TEXT NOT NULL,
    reason TEXT,
    added_at TEXT NOT NULL,
    PRIMARY KEY (value, kind)
);
CREATE TABLE IF NOT EXISTS replies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    playbook TEXT,
    lead_id TEXT,
    from_email TEXT NOT NULL,
    category TEXT,
    payload TEXT NOT NULL,
    received_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS followups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    playbook TEXT,
    lead_id TEXT,
    email TEXT,
    due TEXT NOT NULL,
    reason TEXT,
    done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""

_STAGE_RANK = {s: i for i, s in enumerate(Stage.ORDER)}


def _now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat()


def _iso(d: Optional[date]) -> Optional[str]:
    return d.isoformat() if d else None


class Store:
    def __init__(self, path: Any = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # --- runs -----------------------------------------------------------------
    def start_run(self, playbook: str, meta: Optional[Dict[str, Any]] = None) -> str:
        run_id = datetime.utcnow().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.conn.execute("INSERT INTO runs (id, playbook, started_at, meta) VALUES (?,?,?,?)",
                          (run_id, playbook, _now(), json.dumps(meta or {})))
        self.conn.commit()
        return run_id

    def finish_run(self, run_id: str, counts: Dict[str, Any]) -> None:
        self.conn.execute("UPDATE runs SET finished_at=?, counts=? WHERE id=?",
                          (_now(), json.dumps(counts), run_id))
        self.conn.commit()

    def list_runs(self, playbook: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
        q = "SELECT * FROM runs"
        args: List[Any] = []
        if playbook:
            q += " WHERE playbook=?"
            args.append(playbook)
        q += " ORDER BY started_at DESC, rowid DESC LIMIT ?"
        args.append(limit)
        out = []
        for r in self.conn.execute(q, args):
            d = dict(r)
            d["meta"] = json.loads(d["meta"] or "{}")
            d["counts"] = json.loads(d["counts"] or "{}")
            out.append(d)
        return out

    def latest_run_id(self, playbook: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT id FROM runs WHERE playbook=? AND finished_at IS NOT NULL "
            "ORDER BY started_at DESC, rowid DESC LIMIT 1", (playbook,)).fetchone()
        return row["id"] if row else None

    # --- signal history ---------------------------------------------------------
    def observe_signals(self, company_key: str, signals: Iterable[Signal], today: date) -> None:
        """Record signals and annotate them in place.

        * ``first_seen`` = earliest day this company+signal title was ever seen
          (falls back to today), so undated signals still age across runs.
        * ``reposted`` = the same title was seen before under a different
          external id (a re-posted job), or was first seen well before its
          current posting date.
        """
        t = today.isoformat()
        signals = list(signals)
        # read history for the whole batch BEFORE inserting, so siblings in this
        # batch (same role in two locations, same ad from two sources) are not
        # mistaken for re-posts of each other.
        prior: Dict[str, List[sqlite3.Row]] = {}
        batch_ids: Dict[str, set] = {}
        for s in signals:
            fp = s.fingerprint
            batch_ids.setdefault(fp, set()).add(s.external_id or "")
            if fp not in prior:
                prior[fp] = self.conn.execute(
                    "SELECT external_id, first_seen FROM signal_history WHERE company_key=? AND fingerprint=?",
                    (company_key, fp)).fetchall()
        for s in signals:
            fp = s.fingerprint
            ext = s.external_id or ""
            rows = prior[fp]
            earliest = min((r["first_seen"] for r in rows), default=None)
            own = next((r["first_seen"] for r in rows if ext and r["external_id"] == ext), None)
            if earliest:
                s.first_seen = date.fromisoformat(earliest)
            elif s.first_seen is None:
                s.first_seen = min(s.posted_at, today) if s.posted_at else today
            # the same title was posted before under a different id that is no longer live => re-posted
            gone = {r["external_id"] for r in rows if r["external_id"] and r["external_id"] not in batch_ids[fp]}
            if ext and gone:
                s.reposted = True
            ref_seen = date.fromisoformat(own) if own else (s.first_seen if not ext else None)
            if s.posted_at and ref_seen and (s.posted_at - ref_seen).days >= 7:
                s.reposted = True
            self.conn.execute(
                "INSERT INTO signal_history (company_key, fingerprint, external_id, first_seen, last_seen) "
                "VALUES (?,?,?,?,?) ON CONFLICT(company_key, fingerprint, external_id) "
                "DO UPDATE SET last_seen=excluded.last_seen",
                (company_key, fp, ext, _iso(min(filter(None, [s.first_seen, today]))), t))
        self.conn.commit()

    # --- verification cache -------------------------------------------------------
    def get_verification(self, email: str, max_age_days: int = 30,
                         today: Optional[date] = None) -> Optional[str]:
        row = self.conn.execute("SELECT status, checked_at FROM verifications WHERE email=?",
                                (email.lower(),)).fetchone()
        if not row:
            return None
        checked = datetime.fromisoformat(row["checked_at"]).date()
        if ((today or date.today()) - checked).days > max_age_days:
            return None
        return row["status"]

    def put_verification(self, email: str, status: str, provider: str = "") -> None:
        self.conn.execute(
            "INSERT INTO verifications (email, status, provider, checked_at) VALUES (?,?,?,?) "
            "ON CONFLICT(email) DO UPDATE SET status=excluded.status, provider=excluded.provider, "
            "checked_at=excluded.checked_at", (email.lower(), status, provider, _now()))
        self.conn.commit()

    # --- leads ------------------------------------------------------------------------
    def save_lead(self, lead: Lead) -> None:
        """Insert or update a lead. The stored stage never moves backwards."""
        existing = self.conn.execute("SELECT stage, created_at, exported_at FROM leads WHERE id=?",
                                     (lead.id,)).fetchone()
        stage = lead.stage
        if existing:
            old = existing["stage"]
            if _STAGE_RANK.get(old, -1) > _STAGE_RANK.get(stage, -1):
                stage = old
            # LOST is sticky: a later pipeline run (stage <= exported) must not revive it
            if old == Stage.LOST and _STAGE_RANK.get(stage, -1) < _STAGE_RANK[Stage.REPLIED]:
                stage = old
            lead.stage = stage
        email = lead.contact.email if lead.contact else ""
        payload = json.dumps(lead.to_dict())
        now = _now()
        self.conn.execute(
            "INSERT INTO leads (id, playbook, run_id, company_key, company_name, email, score, tier, "
            "stage, payload, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET run_id=excluded.run_id, company_name=excluded.company_name, "
            "email=excluded.email, score=excluded.score, tier=excluded.tier, stage=excluded.stage, "
            "payload=excluded.payload, updated_at=excluded.updated_at",
            (lead.id, lead.playbook, lead.run_id, lead.company.key, lead.company.name, email,
             lead.score, lead.tier, stage, payload, existing["created_at"] if existing else now, now))
        if lead.run_id:
            self.conn.execute("INSERT OR IGNORE INTO lead_runs (lead_id, run_id) VALUES (?,?)",
                              (lead.id, lead.run_id))
        if not existing or existing["stage"] != stage:
            self.conn.execute("INSERT INTO events (lead_id, stage, note, at) VALUES (?,?,?,?)",
                              (lead.id, stage, "", now))
        self.conn.commit()

    def _row_to_lead(self, row: sqlite3.Row) -> Lead:
        lead = Lead.from_dict(json.loads(row["payload"]))
        lead.stage = row["stage"]
        return lead

    def get_lead(self, lead_id: str) -> Optional[Lead]:
        row = self.conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        return self._row_to_lead(row) if row else None

    def find_lead_by_email(self, email: str, playbook: Optional[str] = None) -> Optional[Lead]:
        q, args = "SELECT * FROM leads WHERE email=?", [email.strip().lower()]
        if playbook:
            q += " AND playbook=?"
            args.append(playbook)
        row = self.conn.execute(q + " ORDER BY updated_at DESC LIMIT 1", args).fetchone()
        return self._row_to_lead(row) if row else None

    def find_leads_by_domain(self, domain: str, playbook: Optional[str] = None) -> List[Lead]:
        d = normalize_domain(domain)
        q, args = "SELECT * FROM leads WHERE (company_key=? OR email LIKE ?)", [d, f"%@{d}"]
        if playbook:
            q += " AND playbook=?"
            args.append(playbook)
        return [self._row_to_lead(r) for r in self.conn.execute(q + " ORDER BY updated_at DESC", args)]

    def leads_for_run(self, run_id: str) -> List[Lead]:
        rows = self.conn.execute(
            "SELECT l.* FROM leads l JOIN lead_runs r ON r.lead_id = l.id WHERE r.run_id=? "
            "ORDER BY l.score DESC", (run_id,)).fetchall()
        return [self._row_to_lead(r) for r in rows]

    def list_leads(self, playbook: Optional[str] = None, stage: Optional[str] = None,
                   limit: int = 1000) -> List[Lead]:
        q, args = "SELECT * FROM leads WHERE 1=1", []
        if playbook:
            q += " AND playbook=?"
            args.append(playbook)
        if stage:
            q += " AND stage=?"
            args.append(stage)
        q += " ORDER BY score DESC LIMIT ?"
        args.append(limit)
        return [self._row_to_lead(r) for r in self.conn.execute(q, args)]

    def set_stage(self, lead_id: str, stage: str, note: str = "", force: bool = False) -> bool:
        """Move a lead to ``stage``. Forward-only unless ``force`` or stage is LOST.
        Returns True if the stored stage changed."""
        if stage not in Stage.ALL:
            raise ValueError(f"unknown stage {stage!r}")
        row = self.conn.execute("SELECT stage, payload FROM leads WHERE id=?", (lead_id,)).fetchone()
        if not row:
            return False
        cur = row["stage"]
        move = force or stage == Stage.LOST or _STAGE_RANK.get(stage, -1) > _STAGE_RANK.get(cur, -1)
        now = _now()
        self.conn.execute("INSERT INTO events (lead_id, stage, note, at) VALUES (?,?,?,?)",
                          (lead_id, stage, note, now))
        if move and cur != stage:
            payload = json.loads(row["payload"])
            payload["stage"] = stage
            self.conn.execute("UPDATE leads SET stage=?, payload=?, updated_at=? WHERE id=?",
                              (stage, json.dumps(payload), now, lead_id))
        self.conn.commit()
        return move and cur != stage

    def mark_exported(self, lead_id: str, exporter: str = "") -> None:
        self.conn.execute("UPDATE leads SET exported_at=? WHERE id=?", (_now(), lead_id))
        self.conn.commit()
        self.set_stage(lead_id, Stage.EXPORTED, note=exporter)

    def recently_contacted(self, email: str, days: int, today: Optional[date] = None,
                           exclude_lead_id: str = "") -> bool:
        """True if this email was exported (handed to a sender) in the last ``days`` days."""
        if not email or days <= 0:
            return False
        cutoff = datetime.combine((today or date.today()) - timedelta(days=days), datetime.min.time())
        row = self.conn.execute(
            "SELECT 1 FROM leads WHERE email=? AND exported_at IS NOT NULL AND exported_at >= ? "
            "AND id != ? LIMIT 1", (email.lower(), cutoff.isoformat(), exclude_lead_id)).fetchone()
        return row is not None

    def company_engagement(self, company_key: str, playbook: str, exclude_run_id: str = "") -> Dict[str, Any]:
        """What already happened with this company in this playbook.

        Returns ``{"stages": set of lead stages, "last_exported": date|None}``,
        ignoring leads that only belong to ``exclude_run_id`` (the current run).
        A lead lost because its address bounced is reported as ``"lost_bounce"``.
        """
        q = "SELECT id, stage, exported_at, run_id FROM leads WHERE company_key=? AND playbook=?"
        stages, last = set(), None
        for r in self.conn.execute(q, (company_key, playbook)).fetchall():
            if exclude_run_id and r["run_id"] == exclude_run_id and not r["exported_at"]:
                continue
            stage = r["stage"]
            if stage == Stage.LOST and self._lost_by_bounce(r["id"]):
                stage = "lost_bounce"  # a dead address, not a "no": colleagues stay reachable (cooldown applies)
            stages.add(stage)
            if r["exported_at"]:
                d = datetime.fromisoformat(r["exported_at"]).date()
                last = d if last is None or d > last else last
        return {"stages": stages, "last_exported": last}

    def _lost_by_bounce(self, lead_id: str) -> bool:
        row = self.conn.execute("SELECT note FROM events WHERE lead_id=? AND stage=? ORDER BY id DESC LIMIT 1",
                                (lead_id, Stage.LOST)).fetchone()
        return bool(row and "bounce" in (row["note"] or "").lower())

    def was_exported(self, lead_id: str) -> bool:
        row = self.conn.execute("SELECT exported_at FROM leads WHERE id=?", (lead_id,)).fetchone()
        return bool(row and row["exported_at"])

    # --- suppression ----------------------------------------------------------------
    def suppress(self, value: str, kind: str = "email", reason: str = "") -> None:
        if kind not in ("email", "domain"):
            raise ValueError("kind must be 'email' or 'domain'")
        v = value.strip().lower() if kind == "email" else normalize_domain(value)
        if not v:
            return
        self.conn.execute("INSERT OR REPLACE INTO suppression (value, kind, reason, added_at) "
                          "VALUES (?,?,?,?)", (v, kind, reason, _now()))
        self.conn.commit()

    def unsuppress(self, value: str, kind: Optional[str] = None) -> int:
        """Remove one suppression entry. ``kind`` defaults to email if the value has an '@'."""
        v = value.strip().lower()
        k = kind or ("email" if "@" in v else "domain")
        v = v if k == "email" else normalize_domain(v)
        cur = self.conn.execute("DELETE FROM suppression WHERE value=? AND kind=?", (v, k))
        self.conn.commit()
        return cur.rowcount

    def is_suppressed(self, email: str = "", domain: str = "") -> bool:
        checks: List[tuple] = []
        if email:
            checks.append((email.strip().lower(), "email"))
            checks.append((normalize_domain(email), "domain"))
        if domain:
            checks.append((normalize_domain(domain), "domain"))
        for v, k in checks:
            if v and self.conn.execute("SELECT 1 FROM suppression WHERE value=? AND kind=?",
                                       (v, k)).fetchone():
                return True
        return False

    def list_suppressed(self) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM suppression ORDER BY added_at DESC")]

    # --- replies + follow-ups ---------------------------------------------------------
    def save_reply(self, reply: Reply, playbook: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO replies (playbook, lead_id, from_email, category, payload, received_at, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (playbook, reply.lead_id, reply.from_email, reply.category, json.dumps(reply.to_dict()),
             reply.received_at, _now()))
        self.conn.commit()
        return int(cur.lastrowid)

    def find_reply(self, playbook: str, from_email: str, received_at: str) -> List[Reply]:
        """Replies already stored for this sender + timestamp (for de-duplication)."""
        rows = self.conn.execute(
            "SELECT payload FROM replies WHERE playbook=? AND from_email=? AND received_at=?",
            (playbook, from_email.strip().lower(), received_at)).fetchall()
        out = []
        for r in rows:
            d = json.loads(r["payload"])
            out.append(Reply(**{k: v for k, v in d.items() if k in Reply.__dataclass_fields__}))
        return out

    def list_replies(self, playbook: Optional[str] = None, category: Optional[str] = None) -> List[Reply]:
        q, args = "SELECT payload FROM replies WHERE 1=1", []
        if playbook:
            q += " AND playbook=?"
            args.append(playbook)
        if category:
            q += " AND category=?"
            args.append(category)
        return [Reply(**{k: v for k, v in json.loads(r["payload"]).items() if k in Reply.__dataclass_fields__})
                for r in self.conn.execute(q + " ORDER BY id", args)]

    def schedule_followup(self, due: date, reason: str, lead_id: str = "", email: str = "",
                          playbook: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO followups (playbook, lead_id, email, due, reason, created_at) VALUES (?,?,?,?,?,?)",
            (playbook, lead_id, email.lower(), due.isoformat(), reason, _now()))
        self.conn.commit()
        return int(cur.lastrowid)

    def due_followups(self, today: date, playbook: Optional[str] = None,
                      include_suppressed: bool = False) -> List[Dict[str, Any]]:
        q, args = "SELECT * FROM followups WHERE done=0 AND due<=?", [today.isoformat()]
        if playbook:
            q += " AND playbook=?"
            args.append(playbook)
        rows = [dict(r) for r in self.conn.execute(q + " ORDER BY due", args)]
        # someone suppressed after the follow-up was scheduled is never due
        if include_suppressed:
            return rows
        return [r for r in rows if not (r.get("email") and self.is_suppressed(email=r["email"]))]

    def complete_followup(self, followup_id: int) -> None:
        self.conn.execute("UPDATE followups SET done=1 WHERE id=?", (followup_id,))
        self.conn.commit()

    # --- stats ---------------------------------------------------------------------------
    def funnel(self, playbook: Optional[str] = None, since: Optional[date] = None) -> Dict[str, Any]:
        """Cumulative funnel: a lead at stage X counts for X and every earlier stage."""
        q, args = "SELECT id, stage, tier FROM leads WHERE 1=1", []
        if playbook:
            q += " AND playbook=?"
            args.append(playbook)
        if since:
            q += " AND created_at >= ?"
            args.append(since.isoformat())
        rows = self.conn.execute(q, args).fetchall()
        reached = {s: 0 for s in Stage.ORDER}
        lost = 0
        tiers: Dict[str, int] = {}
        for r in rows:
            tiers[r["tier"]] = tiers.get(r["tier"], 0) + 1
            stage = r["stage"]
            if stage == Stage.LOST:
                lost += 1
                # a lost lead still reached the furthest stage recorded in its events
                ev = self.conn.execute("SELECT stage FROM events WHERE lead_id=?", (r["id"],)).fetchall()
                ranks = [_STAGE_RANK[e["stage"]] for e in ev if e["stage"] in _STAGE_RANK]
                top = max(ranks) if ranks else 0
            else:
                top = _STAGE_RANK.get(stage, 0)
            for s in Stage.ORDER[: top + 1]:
                reached[s] += 1
        rq, rargs = "SELECT category, COUNT(*) AS n FROM replies WHERE 1=1", []
        if playbook:
            rq += " AND playbook=?"
            rargs.append(playbook)
        if since:
            rq += " AND created_at >= ?"
            rargs.append(since.isoformat())
        replies = {r["category"]: r["n"] for r in self.conn.execute(rq + " GROUP BY category", rargs)}
        return {"total_leads": len(rows), "stages": reached, "lost": lost, "tiers": tiers,
                "replies": replies}
