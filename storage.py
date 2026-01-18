from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "ddn.sqlite"
SIGNATURES_DIR = DATA_DIR / "signatures"

Role = Literal["client", "transporter", "copro", "permit_holder"]


def _ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SIGNATURES_DIR.mkdir(parents=True, exist_ok=True)


def get_conn() -> sqlite3.Connection:
    _ensure_dirs()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS delivery_notes (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                delivery_note_no TEXT,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS signatures (
                note_id TEXT NOT NULL,
                role TEXT NOT NULL,
                signed_at TEXT NOT NULL,
                signer_name TEXT,
                signature_path TEXT NOT NULL,
                PRIMARY KEY (note_id, role),
                FOREIGN KEY (note_id) REFERENCES delivery_notes (id)
            )
            """
        )

        # Lightweight migration for existing DBs
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(delivery_notes)").fetchall()]
        if "delivery_note_no" not in cols:
            conn.execute("ALTER TABLE delivery_notes ADD COLUMN delivery_note_no TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_delivery_notes_delivery_note_no ON delivery_notes(delivery_note_no)"
        )


def create_note(note_id: str, delivery_note_no: str | None, payload: dict[str, Any]) -> None:
    init_db()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO delivery_notes (id, created_at, delivery_note_no, payload_json, status) VALUES (?, ?, ?, ?, ?)",
            (
                note_id,
                datetime.utcnow().isoformat(),
                delivery_note_no,
                json.dumps(payload, ensure_ascii=False),
                "pending",
            ),
        )


def get_note_by_delivery_note_no(delivery_note_no: str) -> dict[str, Any] | None:
    init_db()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM delivery_notes WHERE delivery_note_no = ? ORDER BY created_at DESC LIMIT 1",
            (delivery_note_no,),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "payload": json.loads(row["payload_json"]),
        "status": row["status"],
        "delivery_note_no": row["delivery_note_no"],
    }


def list_delivery_note_nos(status: str | None = None, limit: int = 100) -> list[str]:
    """Returns recent delivery note numbers, optionally filtered by status."""
    init_db()
    limit = max(1, min(int(limit), 1000))

    where = "WHERE delivery_note_no IS NOT NULL AND delivery_note_no != ''"
    params: list[Any] = []
    if status is not None:
        where += " AND status = ?"
        params.append(status)

    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT delivery_note_no FROM delivery_notes {where} ORDER BY created_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()

    seen: set[str] = set()
    out: list[str] = []
    for r in rows:
        dn = str(r["delivery_note_no"])
        if dn in seen:
            continue
        seen.add(dn)
        out.append(dn)
    return out


def get_note(note_id: str) -> dict[str, Any] | None:
    init_db()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM delivery_notes WHERE id = ?", (note_id,)).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "payload": json.loads(row["payload_json"]),
        "status": row["status"],
    }


def list_signatures(note_id: str) -> dict[Role, dict[str, Any]]:
    init_db()
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM signatures WHERE note_id = ?", (note_id,)).fetchall()
    out: dict[Role, dict[str, Any]] = {}
    for r in rows:
        out[r["role"]] = {
            "signed_at": r["signed_at"],
            "signer_name": r["signer_name"],
            "signature_path": r["signature_path"],
        }
    return out


def upsert_signature(note_id: str, role: Role, signer_name: str | None, signature_path: str) -> None:
    init_db()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO signatures (note_id, role, signed_at, signer_name, signature_path)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(note_id, role)
            DO UPDATE SET signed_at=excluded.signed_at, signer_name=excluded.signer_name, signature_path=excluded.signature_path
            """,
            (note_id, role, datetime.utcnow().isoformat(), signer_name, signature_path),
        )


def mark_completed(note_id: str) -> None:
    init_db()
    with get_conn() as conn:
        conn.execute("UPDATE delivery_notes SET status = ? WHERE id = ?", ("completed", note_id))


def set_status(note_id: str, status: str) -> None:
    init_db()
    with get_conn() as conn:
        conn.execute("UPDATE delivery_notes SET status = ? WHERE id = ?", (status, note_id))


def is_fully_signed(note_id: str) -> bool:
    sigs = list_signatures(note_id)
    required: list[Role] = ["client", "transporter", "copro", "permit_holder"]
    return all(role in sigs for role in required)
