"""SQLite persistence layer (system of record for structured incident state).

Semantic memory lives in Hindsight; SQLite holds the authoritative incident
records, timeline, services, and runbooks. JSON columns store list/dict fields.
A fresh connection is opened per operation (SQLite handles this well at demo
scale and keeps the threaded HTTP server simple and safe).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from .models import Incident, Runbook, Service

SCHEMA = """
CREATE TABLE IF NOT EXISTS services (
    name        TEXT PRIMARY KEY,
    tier        INTEGER NOT NULL DEFAULT 3,
    description TEXT DEFAULT '',
    dependencies TEXT DEFAULT '[]',
    oncall      TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS runbooks (
    id       TEXT PRIMARY KEY,
    title    TEXT NOT NULL,
    service  TEXT NOT NULL,
    symptoms TEXT DEFAULT '[]',
    steps    TEXT DEFAULT '[]',
    tags     TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS incidents (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id       TEXT UNIQUE NOT NULL,
    title             TEXT NOT NULL,
    service           TEXT NOT NULL,
    severity          TEXT NOT NULL,
    symptom           TEXT DEFAULT '',
    error_signature   TEXT DEFAULT '',
    tags              TEXT DEFAULT '[]',
    metrics           TEXT DEFAULT '{}',
    log_excerpt       TEXT DEFAULT '',
    status            TEXT DEFAULT 'open',
    created_at        INTEGER NOT NULL,
    resolved_at       INTEGER,
    root_cause        TEXT DEFAULT '',
    remediation_steps TEXT DEFAULT '[]',
    resolver          TEXT DEFAULT '',
    mttr_minutes      REAL,
    feedback          TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_incidents_service ON incidents(service);
CREATE INDEX IF NOT EXISTS idx_incidents_status  ON incidents(status);
CREATE INDEX IF NOT EXISTS idx_incidents_created ON incidents(created_at);

CREATE TABLE IF NOT EXISTS timeline (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL,
    ts          INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    message     TEXT DEFAULT '',
    payload     TEXT DEFAULT '{}',
    FOREIGN KEY (incident_id) REFERENCES incidents(id)
);
CREATE INDEX IF NOT EXISTS idx_timeline_incident ON timeline(incident_id);
"""

_LIST_FIELDS = {"tags", "remediation_steps"}
_JSON_FIELDS = {"tags", "metrics", "remediation_steps", "feedback"}


def connect(db_path: str) -> sqlite3.Connection:
    """Open a connection with sane defaults and dict-like rows."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(db_path: str) -> None:
    """Create tables if they do not yet exist."""
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)


def _loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


# APPEND-DB


def _row_to_incident(row: sqlite3.Row) -> Incident:
    return Incident(
        id=row["id"],
        external_id=row["external_id"],
        title=row["title"],
        service=row["service"],
        severity=row["severity"],
        symptom=row["symptom"],
        error_signature=row["error_signature"],
        tags=_loads(row["tags"], []),
        metrics=_loads(row["metrics"], {}),
        log_excerpt=row["log_excerpt"] or "",
        status=row["status"],
        created_at=row["created_at"],
        resolved_at=row["resolved_at"],
        root_cause=row["root_cause"] or "",
        remediation_steps=_loads(row["remediation_steps"], []),
        resolver=row["resolver"] or "",
        mttr_minutes=row["mttr_minutes"],
        feedback=_loads(row["feedback"], {}),
    )


class Repository:
    """Thin data-access object over SQLite. One instance per app; opens a fresh
    connection per call so it is safe under the threaded HTTP server."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        init_db(db_path)

    # --- services ---
    def upsert_service(self, svc: Service) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO services(name, tier, description, dependencies, oncall)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(name) DO UPDATE SET
                     tier=excluded.tier, description=excluded.description,
                     dependencies=excluded.dependencies, oncall=excluded.oncall""",
                (svc.name, svc.tier, svc.description,
                 json.dumps(svc.dependencies), svc.oncall),
            )

    def list_services(self) -> list[Service]:
        with connect(self.db_path) as conn:
            rows = conn.execute("SELECT * FROM services ORDER BY tier, name").fetchall()
        return [Service(name=r["name"], tier=r["tier"], description=r["description"],
                        dependencies=_loads(r["dependencies"], []), oncall=r["oncall"])
                for r in rows]

    def get_service(self, name: str) -> Optional[Service]:
        with connect(self.db_path) as conn:
            r = conn.execute("SELECT * FROM services WHERE name=?", (name,)).fetchone()
        if not r:
            return None
        return Service(name=r["name"], tier=r["tier"], description=r["description"],
                       dependencies=_loads(r["dependencies"], []), oncall=r["oncall"])

    # --- runbooks ---
    def upsert_runbook(self, rb: Runbook) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO runbooks(id, title, service, symptoms, steps, tags)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     title=excluded.title, service=excluded.service,
                     symptoms=excluded.symptoms, steps=excluded.steps, tags=excluded.tags""",
                (rb.id, rb.title, rb.service, json.dumps(rb.symptoms),
                 json.dumps(rb.steps), json.dumps(rb.tags)),
            )

    def list_runbooks(self) -> list[Runbook]:
        with connect(self.db_path) as conn:
            rows = conn.execute("SELECT * FROM runbooks ORDER BY service, id").fetchall()
        return [Runbook(id=r["id"], title=r["title"], service=r["service"],
                        symptoms=_loads(r["symptoms"], []), steps=_loads(r["steps"], []),
                        tags=_loads(r["tags"], [])) for r in rows]

    def get_runbook(self, runbook_id: str) -> Optional[Runbook]:
        with connect(self.db_path) as conn:
            r = conn.execute("SELECT * FROM runbooks WHERE id=?", (runbook_id,)).fetchone()
        if not r:
            return None
        return Runbook(id=r["id"], title=r["title"], service=r["service"],
                       symptoms=_loads(r["symptoms"], []), steps=_loads(r["steps"], []),
                       tags=_loads(r["tags"], []))

    def find_runbooks_for_service(self, service: str) -> list[Runbook]:
        return [rb for rb in self.list_runbooks() if rb.service == service]

# APPEND-DB2

    # --- incidents ---
    def add_incident(self, inc: Incident) -> Incident:
        with connect(self.db_path) as conn:
            cur = conn.execute(
                """INSERT INTO incidents(
                       external_id, title, service, severity, symptom, error_signature,
                       tags, metrics, log_excerpt, status, created_at, resolved_at,
                       root_cause, remediation_steps, resolver, mttr_minutes, feedback)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (inc.external_id, inc.title, inc.service, inc.severity, inc.symptom,
                 inc.error_signature, json.dumps(inc.tags), json.dumps(inc.metrics),
                 inc.log_excerpt, inc.status, inc.created_at, inc.resolved_at,
                 inc.root_cause, json.dumps(inc.remediation_steps), inc.resolver,
                 inc.mttr_minutes, json.dumps(inc.feedback)),
            )
            inc.id = cur.lastrowid
        return inc

    def update_incident(self, inc: Incident) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                """UPDATE incidents SET
                     title=?, service=?, severity=?, symptom=?, error_signature=?,
                     tags=?, metrics=?, log_excerpt=?, status=?, resolved_at=?,
                     root_cause=?, remediation_steps=?, resolver=?, mttr_minutes=?, feedback=?
                   WHERE id=?""",
                (inc.title, inc.service, inc.severity, inc.symptom, inc.error_signature,
                 json.dumps(inc.tags), json.dumps(inc.metrics), inc.log_excerpt,
                 inc.status, inc.resolved_at, inc.root_cause,
                 json.dumps(inc.remediation_steps), inc.resolver, inc.mttr_minutes,
                 json.dumps(inc.feedback), inc.id),
            )

    def get_incident(self, incident_id: int) -> Optional[Incident]:
        with connect(self.db_path) as conn:
            r = conn.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
        return _row_to_incident(r) if r else None

    def get_incident_by_external(self, external_id: str) -> Optional[Incident]:
        with connect(self.db_path) as conn:
            r = conn.execute("SELECT * FROM incidents WHERE external_id=?",
                             (external_id,)).fetchone()
        return _row_to_incident(r) if r else None

    def list_incidents(self, status: Optional[str] = None, service: Optional[str] = None,
                       limit: int = 200) -> list[Incident]:
        q = "SELECT * FROM incidents"
        clauses, params = [], []
        if status:
            clauses.append("status=?"); params.append(status)
        if service:
            clauses.append("service=?"); params.append(service)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY created_at DESC LIMIT ?"; params.append(limit)
        with connect(self.db_path) as conn:
            rows = conn.execute(q, params).fetchall()
        return [_row_to_incident(r) for r in rows]

    def count_resolved(self) -> int:
        with connect(self.db_path) as conn:
            r = conn.execute("SELECT COUNT(*) c FROM incidents WHERE status='resolved'").fetchone()
        return int(r["c"])

    # --- timeline ---
    def add_timeline(self, incident_id: int, kind: str, message: str,
                     payload: Optional[dict] = None, ts: Optional[int] = None) -> None:
        from .models import now_ms
        with connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO timeline(incident_id, ts, kind, message, payload) VALUES(?,?,?,?,?)",
                (incident_id, ts or now_ms(), kind, message, json.dumps(payload or {})),
            )

    def list_timeline(self, incident_id: int) -> list[dict]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT ts, kind, message, payload FROM timeline WHERE incident_id=? ORDER BY ts, id",
                (incident_id,)).fetchall()
        return [{"ts": r["ts"], "kind": r["kind"], "message": r["message"],
                 "payload": _loads(r["payload"], {})} for r in rows]

    def reset(self) -> None:
        """Drop all rows (used by the seeder for a clean, reproducible demo)."""
        with connect(self.db_path) as conn:
            for t in ("timeline", "incidents", "runbooks", "services"):
                conn.execute(f"DELETE FROM {t}")


