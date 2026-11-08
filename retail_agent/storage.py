"""SQLite report transactions; destructive operations cannot be called by the LLM."""

import hashlib
import json
import secrets
import sqlite3
import time
from pathlib import Path

from .safety import SafeError, redact


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS reports (
          id TEXT PRIMARY KEY, owner TEXT NOT NULL, conversation TEXT NOT NULL,
          title TEXT NOT NULL, body TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
          deleted_at REAL, created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS confirmations (
          token_hash TEXT PRIMARY KEY, owner TEXT NOT NULL, conversation TEXT NOT NULL,
          snapshot TEXT NOT NULL, expires_at REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS preferences (
          owner TEXT PRIMARY KEY, format TEXT NOT NULL
        );
        """)
        path.chmod(0o600)

    def save(self, owner, conversation, title, body):
        report_id = secrets.token_hex(6)
        self.db.execute(
            "INSERT INTO reports(id,owner,conversation,title,body,created_at) VALUES(?,?,?,?,?,?)",
            (report_id, owner, conversation, redact(title), redact(body), time.time()),
        )
        return report_id

    def list(self, owner):
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT id,title,conversation,version FROM reports "
                "WHERE owner=? AND deleted_at IS NULL ORDER BY created_at",
                (owner,),
            )
        ]

    def read(self, owner, report_id):
        row = self.db.execute(
            "SELECT body FROM reports WHERE owner=? AND id=? AND deleted_at IS NULL",
            (owner, report_id),
        ).fetchone()
        if not row:
            raise SafeError("Report not found in your library.")
        return row["body"]

    def prepare_delete(self, owner, conversation, scope, term="", now=None):
        now = time.time() if now is None else now
        sql = "SELECT id,title,version FROM reports WHERE owner=? AND deleted_at IS NULL"
        args = [owner]
        if scope == "conversation":
            sql += " AND conversation=?"
            args.append(conversation)
        elif scope == "mention" and term.strip():
            # instr is literal, so '%' and '_' do not widen the match.
            sql += " AND instr(lower(title || char(10) || body), lower(?)) > 0"
            args.append(redact(term.strip()))
        else:
            raise SafeError("Choose this conversation or a literal report search term.")
        rows = [dict(r) for r in self.db.execute(sql, args)]
        if not rows:
            return None, []
        token = secrets.token_urlsafe(18)
        digest = hashlib.sha256(token.encode()).hexdigest()
        snapshot = [{"id": r["id"], "version": r["version"]} for r in rows]
        # A new preview invalidates all older previews in this session.
        self.db.execute(
            "UPDATE confirmations SET used=1 WHERE owner=? AND conversation=?",
            (owner, conversation),
        )
        self.db.execute(
            "INSERT INTO confirmations VALUES(?,?,?,?,?,0)",
            (digest, owner, conversation, json.dumps(snapshot), now + 120),
        )
        return token, rows

    def cancel(self, owner, conversation):
        self.db.execute(
            "UPDATE confirmations SET used=1 WHERE owner=? AND conversation=?",
            (owner, conversation),
        )

    def confirm_delete(self, owner, conversation, token, now=None):
        now = time.time() if now is None else now
        digest = hashlib.sha256(token.encode()).hexdigest()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT * FROM confirmations WHERE token_hash=? AND owner=? AND conversation=?",
                (digest, owner, conversation),
            ).fetchone()
            if not row or row["used"] or row["expires_at"] <= now:
                raise SafeError("Confirmation is invalid, expired, or already used. Preview again.")
            snapshot = json.loads(row["snapshot"])
            for expected in snapshot:
                current = self.db.execute(
                    "SELECT version FROM reports WHERE id=? AND owner=? AND deleted_at IS NULL",
                    (expected["id"], owner),
                ).fetchone()
                if not current or current["version"] != expected["version"]:
                    raise SafeError("A selected report changed. Preview again before deleting.")
            for expected in snapshot:
                self.db.execute(
                    "UPDATE reports SET deleted_at=?, version=version+1 WHERE id=? AND owner=?",
                    (now, expected["id"], owner),
                )
            self.db.execute("UPDATE confirmations SET used=1 WHERE token_hash=?", (digest,))
            self.db.execute("COMMIT")
            return len(snapshot)
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def preference(self, owner):
        row = self.db.execute("SELECT format FROM preferences WHERE owner=?", (owner,)).fetchone()
        return row[0] if row else "tables"

    def set_preference(self, owner, value):
        if value not in {"tables", "bullets"}:
            raise SafeError("Choose tables or bullets.")
        self.db.execute(
            "INSERT INTO preferences VALUES(?,?) ON CONFLICT(owner) "
            "DO UPDATE SET format=excluded.format",
            (owner, value),
        )
