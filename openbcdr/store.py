"""Persistence + the immutable audit trail.

SQLite rather than Postgres for the core: the schema is the shape section 9
asks for, and swapping the driver later is a smaller job than standing up
Postgres to find out whether the analysis is any good.

The audit trail is append-only and hash-chained. Every event carries the hash of
the one before it, so editing or removing an event in the MIDDLE breaks
verification.

⚠️ THE CHAIN ALONE DOES NOT CATCH A DELETED TAIL. Removing the most recent
events leaves a shorter chain that verifies perfectly — the rows that remain
still link correctly (Codex review 2026-09-08, finding 17). That is why `log()`
also writes a head checkpoint (seq + hash) to a file beside the database, and
`verify_chain()` compares against it.

Be precise about what that buys: it makes the log **tamper evident against
accident and casual edit** — truncation, a partial restore, a buggy migration, a
swapped .db file. It is NOT immutability, and it is NOT proof against someone
who can write both the database and the sidecar. A real guarantee needs the
checkpoint under a different trust boundary: another host, append-only storage,
or a signed external service. Do not describe this log to an examiner as
immutable until that exists.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

GENESIS = "0" * 64

SCHEMA = """
CREATE TABLE IF NOT EXISTS plan_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id TEXT NOT NULL,
    version TEXT,
    ingested_at TEXT NOT NULL,
    mode TEXT NOT NULL,
    source_path TEXT,
    source_sha256 TEXT NOT NULL,
    extract_json TEXT NOT NULL,
    raw_text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_plan_versions_plan ON plan_versions(plan_id);

CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_version_id INTEGER NOT NULL REFERENCES plan_versions(id),
    req_id TEXT NOT NULL,
    coverage TEXT NOT NULL,
    rationale TEXT,
    evidence_quote TEXT,
    evidence_verified INTEGER NOT NULL DEFAULT 0,
    plan_section TEXT,
    recommended_action TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_findings_pv ON findings(plan_version_id);

CREATE TABLE IF NOT EXISTS gaps (
    gap_id TEXT PRIMARY KEY,
    identified_date TEXT,
    identified_by TEXT,
    plan_id TEXT,
    plan_section TEXT,
    title TEXT,
    description TEXT,
    standard_ref TEXT,
    regulatory_ref TEXT,
    severity TEXT,
    status TEXT,
    assigned_to TEXT,
    deadline TEXT,
    resolution_notes TEXT,
    closed_date TEXT,
    exception_approved INTEGER DEFAULT 0,
    examiner_visible INTEGER DEFAULT 1,
    source TEXT,
    origin_key TEXT,
    fingerprint TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS feed_items (
    item_id TEXT PRIMARY KEY,
    source TEXT, title TEXT, published TEXT, summary TEXT, url TEXT,
    relevance_score INTEGER, disposition TEXT, scored_at TEXT
);

-- Analytics projection of risk-owner decisions. The canonical, tamper-evident
-- record is audit_events; this table exists so calibration can aggregate without
-- parsing audit payloads. If the two ever disagree, audit_events is right.
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    gap_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason_code TEXT,
    note TEXT,
    severity_at_decision TEXT,
    origin_key TEXT,
    gap_source TEXT
);
CREATE INDEX IF NOT EXISTS ix_decisions_gap ON decisions(gap_id);

CREATE TABLE IF NOT EXISTS audit_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    subject TEXT,
    detail_json TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
"""


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._migrate()
        self.db.commit()

    def _migrate(self) -> None:
        """Add columns introduced after a database was first created."""
        have = {r["name"] for r in self.db.execute("PRAGMA table_info(gaps)")}
        if "origin_key" not in have:
            self.db.execute("ALTER TABLE gaps ADD COLUMN origin_key TEXT")

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------ audit trail
    def log(self, actor: str, action: str, subject: str = "", **detail: Any) -> str:
        row = self.db.execute("SELECT hash FROM audit_events ORDER BY seq DESC LIMIT 1").fetchone()
        prev = row["hash"] if row else GENESIS
        ts = _now()
        payload = _canonical(
            {"ts": ts, "actor": actor, "action": action, "subject": subject, "detail": detail}
        )
        digest = hashlib.sha256((prev + payload).encode("utf-8")).hexdigest()
        self.db.execute(
            "INSERT INTO audit_events (ts, actor, action, subject, detail_json, prev_hash, hash)"
            " VALUES (?,?,?,?,?,?,?)",
            (ts, actor, action, subject, _canonical(detail), prev, digest),
        )
        self.db.commit()
        seq = self.db.execute("SELECT seq FROM audit_events ORDER BY seq DESC LIMIT 1").fetchone()["seq"]
        self._write_head(seq, digest)
        return digest

    # -------------------------------------------------- audit head checkpoint
    # WHAT THIS DOES AND DOES NOT GUARANTEE — read before relying on it.
    #
    # Codex review 2026-09-08, finding 17: verify_chain() only ever checked that
    # the rows STILL PRESENT link to each other. Deleting the most recent event
    # leaves a shorter chain that verifies perfectly, so "the log is tamper
    # evident" was a broader claim than the code supported.
    #
    # The head checkpoint records (seq, hash) OUTSIDE the database, so a tail
    # deletion — or a wholesale database swap — no longer verifies clean.
    #
    # ⚠️ It is NOT proof against a determined attacker. Anyone who can write the
    # database can usually write a sidecar next to it. What this DOES catch:
    # accidental truncation, a partial restore, a buggy migration, a naive
    # tamper, and a replaced .db file. To make it hold against a real adversary
    # the checkpoint has to live under a different trust boundary — different
    # host, append-only storage, or a signed external service. Until it does,
    # say "tamper evident against accident and casual edit", not "immutable".
    HEAD_SUFFIX = ".audit-head"

    def _head_path(self) -> Optional[Path]:
        if str(self.path) == ":memory:":
            return None      # nowhere to anchor; verify_chain says so explicitly
        return self.path.with_name(self.path.name + self.HEAD_SUFFIX)

    def _write_head(self, seq: int, digest: str) -> None:
        p = self._head_path()
        if p is None:
            return
        tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
        try:
            tmp.write_text(json.dumps({"seq": seq, "hash": digest}), encoding="utf-8")
            os.replace(tmp, p)
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass
            # A checkpoint that cannot be written must not pass silently: the
            # next verify would then trust a log with no anchor at all.
            raise

    def read_head(self) -> Optional[dict]:
        p = self._head_path()
        if p is None or not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"seq": -1, "hash": "unreadable"}
        if not isinstance(data, dict) or "seq" not in data or "hash" not in data:
            return {"seq": -1, "hash": "malformed"}
        return data

    def verify_chain(self) -> tuple[bool, Optional[int]]:
        """Return (ok, first_broken_seq). A break means the log was altered.

        Checks two things: that the rows present link to each other, AND that the
        last row matches the external head checkpoint. The second is what makes a
        DELETED TAIL detectable — see the note above for the limits of that.
        """
        prev = GENESIS
        for row in self.db.execute("SELECT * FROM audit_events ORDER BY seq"):
            payload = _canonical({
                "ts": row["ts"],
                "actor": row["actor"],
                "action": row["action"],
                "subject": row["subject"],
                "detail": json.loads(row["detail_json"]),
            })
            expect = hashlib.sha256((prev + payload).encode("utf-8")).hexdigest()
            if expect != row["hash"] or row["prev_hash"] != prev:
                return False, row["seq"]
            prev = row["hash"]

        # The chain is internally consistent. Now: is it the WHOLE chain?
        head = self.read_head()
        if head is None:
            return True, None          # no anchor (in-memory, or never written)
        last = self.db.execute(
            "SELECT seq, hash FROM audit_events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        last_seq = last["seq"] if last else 0
        last_hash = last["hash"] if last else GENESIS
        if head.get("seq") != last_seq or head.get("hash") != last_hash:
            # Events after head["seq"] are gone, or the database was replaced.
            return False, head.get("seq")
        return True, None

    # ------------------------------------------------------------------ plans
    def save_plan_version(self, plan_id: str, version: str, mode: str, source_path: str,
                          raw_text: str, extract: dict[str, Any]) -> int:
        sha = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
        cur = self.db.execute(
            "INSERT INTO plan_versions (plan_id, version, ingested_at, mode, source_path,"
            " source_sha256, extract_json, raw_text) VALUES (?,?,?,?,?,?,?,?)",
            (plan_id, version, _now(), mode, source_path, sha, _canonical(extract), raw_text),
        )
        self.db.commit()
        pv_id = int(cur.lastrowid)
        self.log("agent", "plan_ingested", "plan_version:" + str(pv_id),
                 plan_id=plan_id, version=version, sha256=sha, mode=mode)
        return pv_id

    def get_plan_version(self, pv_id: int) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM plan_versions WHERE id=?", (pv_id,)).fetchone()
        if row is None:
            raise KeyError("no plan_version " + str(pv_id))
        return row

    def latest_plan_version(self, plan_id: str) -> sqlite3.Row:
        row = self.db.execute(
            "SELECT * FROM plan_versions WHERE plan_id=? ORDER BY id DESC LIMIT 1", (plan_id,)
        ).fetchone()
        if row is None:
            raise KeyError("no plan versions for " + plan_id)
        return row

    # --------------------------------------------------------------- findings
    def save_findings(self, pv_id: int, findings: Iterable[Any]) -> int:
        n = 0
        for f in findings:
            self.db.execute(
                "INSERT INTO findings (plan_version_id, req_id, coverage, rationale,"
                " evidence_quote, evidence_verified, plan_section, recommended_action, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (pv_id, f.req_id, f.coverage, f.rationale, f.evidence_quote,
                 int(f.evidence_verified), f.plan_section, f.recommended_action, _now()),
            )
            n += 1
        self.db.commit()
        return n

    def findings_for(self, pv_id: int) -> list[sqlite3.Row]:
        return list(self.db.execute(
            "SELECT * FROM findings WHERE plan_version_id=? ORDER BY req_id", (pv_id,)))

    # ------------------------------------------------------------------- gaps
    def next_gap_id(self, year: int) -> str:
        row = self.db.execute(
            "SELECT COUNT(*) AS n FROM gaps WHERE gap_id LIKE ?", ("GAP-" + str(year) + "-%",)
        ).fetchone()
        return "GAP-" + str(year) + "-" + str(row["n"] + 1).zfill(4)

    def upsert_gap(self, gap: Any, fingerprint: str) -> tuple[str, bool]:
        """Insert, or return the existing gap for this fingerprint. -> (id, created)."""
        row = self.db.execute(
            "SELECT gap_id FROM gaps WHERE fingerprint=?", (fingerprint,)).fetchone()
        if row:
            return row["gap_id"], False
        self.db.execute(
            "INSERT INTO gaps (gap_id, identified_date, identified_by, plan_id, plan_section,"
            " title, description, standard_ref, regulatory_ref, severity, status, assigned_to,"
            " deadline, resolution_notes, closed_date, exception_approved, examiner_visible,"
            " source, origin_key, fingerprint) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (gap.gap_id, str(gap.identified_date or ""), gap.identified_by, gap.plan_id,
             gap.plan_section, gap.title, gap.description, gap.standard_ref, gap.regulatory_ref,
             gap.severity, gap.status, gap.assigned_to, str(gap.deadline or ""),
             gap.resolution_notes, str(gap.closed_date or ""), int(gap.exception_approved),
             int(gap.examiner_visible), gap.source, gap.origin_key, fingerprint),
        )
        self.db.commit()
        self.log("agent", "gap_opened", gap.gap_id, severity=gap.severity,
                 plan_id=gap.plan_id, standard_ref=gap.standard_ref)
        return gap.gap_id, True

    def open_gaps(self, plan_id: str | None = None) -> list[sqlite3.Row]:
        order = (" ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1"
                 " WHEN 'medium' THEN 2 ELSE 3 END, gap_id")
        if plan_id:
            return list(self.db.execute(
                "SELECT * FROM gaps WHERE status='open' AND plan_id=?" + order, (plan_id,)))
        return list(self.db.execute("SELECT * FROM gaps WHERE status='open'" + order))

    # `reject` is what makes calibration possible. Without it a risk owner who
    # thinks a finding is simply wrong has to record `close`, which is
    # indistinguishable in the data from "we fixed the underlying problem" - and
    # the precision signal is destroyed at the point of capture, unrecoverably.
    DECISIONS = {"accept", "modify", "defer", "exception", "close", "reject"}

    REJECTION_REASONS = {
        "wrong_requirement": "The requirement does not say what the agent claimed.",
        "misread_plan": "The plan does cover this; the agent missed it.",
        "not_applicable": "Out of scope at this institution's tier or charter.",
        "severity_too_high": "Real issue, but overstated.",
        "duplicate": "Already tracked under another gap.",
        "other": "Something else - the note explains.",
    }

    def decide_gap(self, gap_id: str, actor: str, decision: str, note: str = "",
                   new_deadline: str = "", reason_code: str = "") -> None:
        """Risk-owner decision. `reject` requires a reason_code and a note."""
        if decision not in self.DECISIONS:
            raise ValueError("decision must be one of " + str(sorted(self.DECISIONS)))
        if decision == "reject":
            if reason_code not in self.REJECTION_REASONS:
                raise ValueError(
                    "reject requires reason_code, one of "
                    + str(sorted(self.REJECTION_REASONS)))
            if not note.strip():
                raise ValueError(
                    "reject requires a note saying what the agent got wrong - a "
                    "reason code with no detail cannot be acted on later")
        elif reason_code:
            raise ValueError("reason_code applies only to `reject`")

        row = self.db.execute("SELECT * FROM gaps WHERE gap_id=?", (gap_id,)).fetchone()
        if row is None:
            raise KeyError(gap_id)
        status = {"close": "closed", "defer": "deferred", "reject": "rejected",
                  "exception": "exception_approved"}.get(decision, "in_progress")
        self.db.execute(
            "UPDATE gaps SET status=?, resolution_notes=?,"
            " deadline=COALESCE(NULLIF(?,''), deadline),"
            " exception_approved=?, closed_date=? WHERE gap_id=?",
            (status, note, new_deadline, int(decision == "exception"),
             _now()[:10] if decision in ("close", "reject") else row["closed_date"], gap_id),
        )
        self.db.execute(
            "INSERT INTO decisions (ts, gap_id, actor, decision, reason_code, note,"
            " severity_at_decision, origin_key, gap_source) VALUES (?,?,?,?,?,?,?,?,?)",
            (_now(), gap_id, actor, decision, reason_code or None, note,
             row["severity"], row["origin_key"], row["source"]),
        )
        self.db.commit()
        self.log(actor, "gap_" + decision, gap_id, note=note,
                 new_deadline=new_deadline, reason_code=reason_code)
