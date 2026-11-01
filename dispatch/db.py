from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

SCHEMA = '''
CREATE TABLE IF NOT EXISTS alerts (
 cap_id TEXT PRIMARY KEY, family_id TEXT, content_hash TEXT, revision_label TEXT, event TEXT NOT NULL, headline TEXT, description TEXT,
 areas TEXT, sent TEXT, effective TEXT, expires TEXT, status TEXT NOT NULL DEFAULT 'pending',
 message TEXT NOT NULL, replaces_id TEXT, raw_json TEXT, test_only INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL, claimed_by TEXT, claimed_at TEXT
);
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY AUTOINCREMENT, cap_id TEXT, action TEXT NOT NULL, actor TEXT,
 detail TEXT, message TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alert_status ON alerts(status, sent DESC);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS test_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT, channel_name TEXT, channel_index INTEGER, message TEXT, outcome TEXT, detail TEXT, created_at TEXT NOT NULL);
'''

def now() -> str: return datetime.now(timezone.utc).isoformat()

class Database:
    def __init__(self, path: str): self.path = path
    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try: yield conn
        finally: conn.close()
    def initialize(self):
        with self.connect() as c:
            c.executescript(SCHEMA)
            columns={row[1] for row in c.execute("PRAGMA table_info(alerts)")}
            for name, kind in (("family_id","TEXT"),("content_hash","TEXT"),("revision_label","TEXT"),("test_only","INTEGER NOT NULL DEFAULT 0")):
                if name not in columns: c.execute(f"ALTER TABLE alerts ADD COLUMN {name} {kind}")
            c.execute("CREATE INDEX IF NOT EXISTS idx_alert_family ON alerts(family_id, created_at)")
    def audit(self, cap_id, action, actor=None, detail=None, message=None):
        with self.connect() as c:
            c.execute("INSERT INTO audit(cap_id,action,actor,detail,message,created_at) VALUES(?,?,?,?,?,?)", (cap_id, action, actor, detail, message, now()))
    def actionable(self):
        with self.connect() as c:
            return c.execute("SELECT * FROM alerts WHERE status='pending' ORDER BY sent DESC").fetchall()
    def family_rows(self, family_id):
        with self.connect() as c:
            return c.execute("SELECT * FROM alerts WHERE family_id=? ORDER BY created_at",(family_id,)).fetchall()
    def active_family_rows(self, family_id):
        with self.connect() as c:
            return c.execute("SELECT * FROM alerts WHERE family_id=? AND status IN ('pending','sending')",(family_id,)).fetchall()
    def history(self):
        with self.connect() as c:
            return c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 200").fetchall()
    def claim(self, cap_id: str, actor: str) -> bool:
        """The conditional UPDATE is the cross-request transmit lock."""
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            r = c.execute("UPDATE alerts SET status='sending', claimed_by=?, claimed_at=?, updated_at=? WHERE cap_id=? AND status='pending' AND (expires IS NULL OR expires > ?)", (actor, now(), now(), cap_id, now()))
            if r.rowcount != 1:
                c.execute("ROLLBACK"); return False
            c.execute("COMMIT"); return True
    def finish(self, cap_id, status, actor, detail=None, message=None):
        with self.connect() as c:
            row=c.execute("SELECT test_only FROM alerts WHERE cap_id=?",(cap_id,)).fetchone()
            c.execute("UPDATE alerts SET status=?, updated_at=? WHERE cap_id=?", (status, now(), cap_id))
        if row and row["test_only"]:
            detail=f"TEST ONLY — {detail or status}"
        self.audit(cap_id, status, actor, detail, message)
    def get(self, cap_id):
        with self.connect() as c: return c.execute("SELECT * FROM alerts WHERE cap_id=?", (cap_id,)).fetchone()
    def save_setting(self, key, value):
        with self.connect() as c: c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    def setting(self, key):
        with self.connect() as c:
            row=c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone(); return row[0] if row else None
    def test_message(self, actor, channel, message, outcome, detail):
        with self.connect() as c: c.execute("INSERT INTO test_messages(actor,channel_name,channel_index,message,outcome,detail,created_at) VALUES(?,?,?,?,?,?,?)",(actor,channel["name"],channel["index"],message,outcome,detail,now()))
    def test_history(self):
        with self.connect() as c: return c.execute("SELECT * FROM test_messages ORDER BY id DESC LIMIT 100").fetchall()
