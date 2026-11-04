from __future__ import annotations
import sqlite3, json
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
CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL COLLATE NOCASE UNIQUE, password_hash TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS test_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT, channel_name TEXT, channel_index INTEGER, message TEXT, outcome TEXT, detail TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS county_catalog (ugc TEXT PRIMARY KEY, state TEXT NOT NULL, name TEXT NOT NULL, same TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS coverage_counties (ugc TEXT PRIMARY KEY, state TEXT NOT NULL, name TEXT NOT NULL, same TEXT NOT NULL UNIQUE, selected_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS catalog_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alert_type_catalog (event TEXT PRIMARY KEY, category TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alert_type_catalog_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dispatch_previews (jti TEXT PRIMARY KEY, consumed_at TEXT NOT NULL);
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
    def bootstrap_operational_settings(self, config):
        """Seed web-managed values once; never overwrite an operator's changes."""
        from .alert_types import DEFAULT_ALERT_TYPES
        legacy_alerts=config.get("alert_types", DEFAULT_ALERT_TYPES)
        values={"auth_username":config.get("admin_username", ""),"auth_password_hash":config.get("admin_password_hash", ""),"nws_states":json.dumps(config.get("states",[])),"nws_poll_seconds":str(max(60,int(config.get("poll_seconds",60)))),"nws_alert_types":json.dumps(legacy_alerts),"transmit_enabled":"true" if config.get("transmit_enabled",True) else "false"}
        candidate=str(config.get("nws_user_agent","")).rsplit(" ",1)[-1].strip("() ")
        if "@" in candidate: values["nws_contact_email"]=candidate
        with self.connect() as c:
            for key,value in values.items():
                if value: c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",(key,value))
    def credentials(self): return self.setting("auth_username"),self.setting("auth_password_hash")
    def migrate_users(self):
        """Copy the legacy singleton only when no user rows exist."""
        with self.connect() as c:
            if c.execute("SELECT 1 FROM users LIMIT 1").fetchone(): return
            username,password_hash=self.credentials()
            if username and password_hash:
                c.execute("INSERT INTO users(username,password_hash,created_at,updated_at) VALUES(?,?,?,?)",(username,password_hash,now(),now()))
    def user(self, username, active_only=False):
        with self.connect() as c:
            return c.execute("SELECT * FROM users WHERE username=?" + (" AND disabled=0" if active_only else ""),(username,)).fetchone()
    def users(self):
        with self.connect() as c:
            return c.execute("SELECT id,username,disabled,created_at,updated_at FROM users ORDER BY username COLLATE NOCASE").fetchall()
    def active_user_count(self):
        with self.connect() as c:return c.execute("SELECT COUNT(*) FROM users WHERE disabled=0").fetchone()[0]
    def add_user(self, username, password_hash):
        with self.connect() as c:
            try: c.execute("INSERT INTO users(username,password_hash,created_at,updated_at) VALUES(?,?,?,?)",(username,password_hash,now(),now()))
            except sqlite3.IntegrityError: raise ValueError("that username is already in use")
    def update_user(self, target, username=None, password_hash=None):
        with self.connect() as c:
            row=c.execute("SELECT * FROM users WHERE username=?",(target,)).fetchone()
            if not row: raise ValueError("user was not found")
            username=username or row["username"];password_hash=password_hash or row["password_hash"]
            try: c.execute("UPDATE users SET username=?,password_hash=?,updated_at=? WHERE id=?",(username,password_hash,now(),row["id"]))
            except sqlite3.IntegrityError: raise ValueError("that username is already in use")
    def set_user_disabled(self, target, disabled):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE");row=c.execute("SELECT * FROM users WHERE username=?",(target,)).fetchone()
            if not row: c.execute("ROLLBACK");raise ValueError("user was not found")
            if bool(row["disabled"])==bool(disabled): c.execute("ROLLBACK");return
            if disabled and not row["disabled"] and c.execute("SELECT COUNT(*) FROM users WHERE disabled=0").fetchone()[0]<=1: c.execute("ROLLBACK");raise ValueError("cannot disable the last active account")
            c.execute("UPDATE users SET disabled=?,updated_at=? WHERE id=?",(int(disabled),now(),row["id"]));c.execute("COMMIT")
    def remove_user(self, target):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE");row=c.execute("SELECT * FROM users WHERE username=?",(target,)).fetchone()
            if not row: c.execute("ROLLBACK");raise ValueError("user was not found")
            if not row["disabled"] and c.execute("SELECT COUNT(*) FROM users WHERE disabled=0").fetchone()[0]<=1: c.execute("ROLLBACK");raise ValueError("cannot remove the last active account")
            c.execute("DELETE FROM users WHERE id=?",(row["id"],));c.execute("COMMIT")
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
    def consume_preview(self, jti: str) -> bool:
        """Atomically consume a server-issued confirmation preview exactly once."""
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                c.execute("INSERT INTO dispatch_previews(jti,consumed_at) VALUES(?,?)", (jti, now()))
            except sqlite3.IntegrityError:
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
    def save_settings(self, values):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE");c.executemany("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",values.items());c.execute("COMMIT")
    def setting(self, key):
        with self.connect() as c:
            row=c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone(); return row[0] if row else None
    def test_message(self, actor, channel, message, outcome, detail):
        with self.connect() as c: c.execute("INSERT INTO test_messages(actor,channel_name,channel_index,message,outcome,detail,created_at) VALUES(?,?,?,?,?,?,?)",(actor,channel["name"],channel["index"],message,outcome,detail,now()))
    def test_history(self):
        with self.connect() as c: return c.execute("SELECT * FROM test_messages ORDER BY id DESC LIMIT 100").fetchall()
    def catalog(self, state=None, query=""):
        q="%"+query.strip()+"%"
        with self.connect() as c:
            return c.execute("SELECT * FROM county_catalog WHERE (?='' OR state=?) AND (name LIKE ? OR ugc LIKE ? OR same LIKE ?) ORDER BY state,name",(state or "",state or "",q,q,q)).fetchall()
    def catalog_states(self):
        with self.connect() as c:return [r[0] for r in c.execute("SELECT DISTINCT state FROM county_catalog ORDER BY state")]
    def catalog_meta(self):
        with self.connect() as c:return {r[0]:r[1] for r in c.execute("SELECT key,value FROM catalog_metadata")}
    def replace_catalog(self, rows, metadata):
        # Validate first, then replace in one transaction; a bad import preserves the old catalog.
        if not rows: raise ValueError("empty county catalog")
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE");c.execute("CREATE TEMP TABLE incoming(ugc TEXT PRIMARY KEY,state TEXT,name TEXT,same TEXT UNIQUE)")
            c.executemany("INSERT INTO incoming VALUES(?,?,?,?)",[(r["ugc"],r["state"],r["name"],r["same"]) for r in rows])
            c.execute("DELETE FROM county_catalog");c.execute("INSERT INTO county_catalog SELECT ugc,state,name,same FROM incoming");c.execute("DROP TABLE incoming")
            c.execute("DELETE FROM catalog_metadata");c.executemany("INSERT INTO catalog_metadata VALUES(?,?)",metadata.items());c.execute("COMMIT")
    def coverage(self):
        with self.connect() as c:return c.execute("SELECT * FROM coverage_counties ORDER BY state,name").fetchall()
    def save_coverage(self, ugcs, allowed_states=None):
        with self.connect() as c:
            rows=c.execute("SELECT * FROM county_catalog WHERE ugc IN (%s)" % ",".join("?"*len(ugcs)),ugcs).fetchall() if ugcs else []
            if not rows: raise ValueError("select at least one catalog county; unrestricted monitoring is not allowed")
            if len(rows)!=len(set(ugcs)): raise ValueError("one or more selected counties are not in the catalog")
            if allowed_states is not None and any(r["state"] not in allowed_states for r in rows): raise ValueError("select only counties in configured NWS states; update NWS Settings before expanding coverage")
            c.execute("BEGIN IMMEDIATE");c.execute("DELETE FROM coverage_counties");c.executemany("INSERT INTO coverage_counties VALUES(?,?,?,?,?)",[(r["ugc"],r["state"],r["name"],r["same"],now()) for r in rows]);c.execute("COMMIT")
    def migrate_coverage(self, rows):
        if self.setting("coverage_migrated"): return
        if rows:
            with self.connect() as c:c.executemany("INSERT OR IGNORE INTO coverage_counties VALUES(?,?,?,?,?)",[(r["ugc"],r["state"],r["name"],r["same"],now()) for r in rows])
        self.save_setting("coverage_migrated","1")
    def alert_type_catalog(self):
        with self.connect() as c:
            return c.execute("SELECT event,category FROM alert_type_catalog ORDER BY category,event").fetchall()
    def alert_type_catalog_meta(self):
        with self.connect() as c:
            return {r[0]:r[1] for r in c.execute("SELECT key,value FROM alert_type_catalog_metadata")}
    def replace_alert_type_catalog(self, events, metadata):
        if not events: raise ValueError("empty alert-type catalog")
        from .alert_types import category
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("CREATE TEMP TABLE incoming_alert_types(event TEXT PRIMARY KEY,category TEXT NOT NULL)")
            c.executemany("INSERT INTO incoming_alert_types VALUES(?,?)",[(event,category(event)) for event in events])
            c.execute("DELETE FROM alert_type_catalog")
            c.execute("INSERT INTO alert_type_catalog SELECT event,category FROM incoming_alert_types")
            c.execute("DROP TABLE incoming_alert_types")
            c.execute("DELETE FROM alert_type_catalog_metadata")
            c.executemany("INSERT INTO alert_type_catalog_metadata VALUES(?,?)",metadata.items())
            c.execute("COMMIT")
    def save_alert_types(self, events, allowed_events):
        cleaned=sorted({str(event).strip() for event in events if str(event).strip()})
        allowed=set(allowed_events)
        if any(event not in allowed for event in cleaned):
            raise ValueError("one or more alert types are not in the available NWS catalog")
        self.save_setting("nws_alert_types",json.dumps(cleaned))
