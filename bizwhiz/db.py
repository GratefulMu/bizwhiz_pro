"""
db.py -- SQLite database layer for BizWhiz
"""
import sqlite3, os
from flask_login import UserMixin

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bizwhiz.db")

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name       TEXT    NOT NULL,
    email           TEXT    NOT NULL UNIQUE,
    password_hash   TEXT    NOT NULL,
    company         TEXT    DEFAULT '',
    phone           TEXT    DEFAULT '',
    plan            TEXT    DEFAULT 'trial',
    email_verified  INTEGER DEFAULT 0,
    verify_token    TEXT    DEFAULT '',
    reset_token     TEXT    DEFAULT '',
    reset_expires   TEXT    DEFAULT '',
    created_at      TEXT    DEFAULT (datetime('now')),
    last_login      TEXT    DEFAULT ''
);

CREATE TABLE IF NOT EXISTS leads (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    website         TEXT    DEFAULT '',
    phone           TEXT    DEFAULT '',
    emails          TEXT    DEFAULT '',
    address         TEXT    DEFAULT '',
    business_type   TEXT    DEFAULT '',
    zip_code        TEXT    DEFAULT '',
    stage           TEXT    DEFAULT 'New',
    created_at      TEXT    DEFAULT (datetime('now')),
    updated_at      TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS activities (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id     INTEGER NOT NULL,
    type        TEXT    NOT NULL,
    content     TEXT    DEFAULT '',
    created_at  TEXT    DEFAULT (datetime('now')),
    FOREIGN KEY (lead_id) REFERENCES leads(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS email_templates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    subject     TEXT    DEFAULT '',
    body        TEXT    DEFAULT '',
    created_at  TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key     TEXT PRIMARY KEY,
    value   TEXT DEFAULT ''
);
"""

def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c

def init_db():
    with _conn() as c:
        c.executescript(SCHEMA)

def get_all_leads(stage=None, search=None):
    with _conn() as c:
        sql, params, conds = "SELECT * FROM leads", [], []
        if stage:
            conds.append("stage = ?"); params.append(stage)
        if search:
            conds.append("(name LIKE ? OR emails LIKE ? OR address LIKE ? OR business_type LIKE ? OR phone LIKE ?)")
            s = f"%{search}%"; params.extend([s, s, s, s, s])
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY created_at DESC"
        return [dict(r) for r in c.execute(sql, params).fetchall()]

def get_lead(lead_id):
    with _conn() as c:
        row = c.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return dict(row) if row else None

def create_lead(data):
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO leads (name,website,phone,emails,address,business_type,zip_code,stage) VALUES (?,?,?,?,?,?,?,?)",
            (data.get("name",""), data.get("website",""), data.get("phone",""),
             data.get("emails",""), data.get("address",""), data.get("business_type",""),
             data.get("zip_code",""), data.get("stage","New")),
        )
        c.commit(); return cur.lastrowid

def update_lead(lead_id, data):
    allowed = ["name","website","phone","emails","address","business_type","zip_code","stage"]
    fields, params = [], []
    for f in allowed:
        if f in data:
            fields.append(f"{f} = ?"); params.append(data[f])
    if not fields: return
    fields.append("updated_at = datetime('now')"); params.append(lead_id)
    with _conn() as c:
        c.execute(f"UPDATE leads SET {', '.join(fields)} WHERE id = ?", params); c.commit()

def delete_lead(lead_id):
    with _conn() as c:
        c.execute("DELETE FROM leads WHERE id = ?", (lead_id,)); c.commit()

def bulk_create_leads(leads_list, zip_code="", business_type=""):
    with _conn() as c:
        existing = {r[0] for r in c.execute("SELECT name FROM leads").fetchall()}
        added = 0
        for lead in leads_list:
            nm = lead.get("name","").strip()
            if not nm or nm in existing: continue
            c.execute(
                "INSERT INTO leads (name,website,phone,emails,address,business_type,zip_code,stage) VALUES (?,?,?,?,?,?,?,'New')",
                (nm, lead.get("website",""), lead.get("phone",""), lead.get("emails",""),
                 lead.get("address",""), lead.get("business_type", business_type), lead.get("zip_code", zip_code)),
            )
            existing.add(nm); added += 1
        c.commit(); return added

def bulk_delete_leads_by_id(ids):
    if not ids: return 0
    with _conn() as c:
        placeholders = ",".join("?" * len(ids))
        c.execute(f"DELETE FROM leads WHERE id IN ({placeholders})", list(ids))
        deleted = c.execute("SELECT changes()").fetchone()[0]
        c.commit(); return deleted

def delete_all_leads():
    with _conn() as c:
        c.execute("DELETE FROM leads"); c.commit()

def get_activities(lead_id):
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM activities WHERE lead_id = ? ORDER BY created_at DESC", (lead_id,)
        ).fetchall()]

def add_activity(lead_id, type_, content):
    with _conn() as c:
        c.execute("INSERT INTO activities (lead_id,type,content) VALUES (?,?,?)",
                  (lead_id, type_, content)); c.commit()

def get_recent_activities(limit=20):
    with _conn() as c:
        return [dict(r) for r in c.execute(
            """SELECT a.*, l.name AS lead_name, l.id AS lead_id
               FROM activities a JOIN leads l ON a.lead_id = l.id
               ORDER BY a.created_at DESC LIMIT ?""", (limit,)
        ).fetchall()]

def get_all_templates():
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM email_templates ORDER BY created_at DESC").fetchall()]

def get_template(tid):
    with _conn() as c:
        row = c.execute("SELECT * FROM email_templates WHERE id = ?", (tid,)).fetchone()
        return dict(row) if row else None

def create_template(name, subject, body):
    with _conn() as c:
        cur = c.execute("INSERT INTO email_templates (name,subject,body) VALUES (?,?,?)",
                        (name, subject, body)); c.commit(); return cur.lastrowid

def update_template(tid, name, subject, body):
    with _conn() as c:
        c.execute("UPDATE email_templates SET name=?,subject=?,body=? WHERE id=?",
                  (name, subject, body, tid)); c.commit()

def delete_template(tid):
    with _conn() as c:
        c.execute("DELETE FROM email_templates WHERE id = ?", (tid,)); c.commit()

def get_setting(key, default=""):
    with _conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

def set_setting(key, value):
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, value)); c.commit()

def get_all_settings():
    with _conn() as c:
        return {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings").fetchall()}

def get_stats():
    with _conn() as c:
        total = c.execute("SELECT COUNT(*) AS n FROM leads").fetchone()["n"]
        by_stage = {r["stage"]: r["cnt"] for r in c.execute(
            "SELECT stage, COUNT(*) AS cnt FROM leads GROUP BY stage").fetchall()}
        by_type = [dict(r) for r in c.execute(
            "SELECT business_type, COUNT(*) AS cnt FROM leads WHERE business_type != '' "
            "GROUP BY business_type ORDER BY cnt DESC LIMIT 10").fetchall()]
        daily = [dict(r) for r in c.execute(
            "SELECT date(created_at) AS date, COUNT(*) AS cnt FROM leads "
            "WHERE created_at >= date('now','-30 days') GROUP BY date(created_at) ORDER BY date"
        ).fetchall()]
        return {"total": total, "by_stage": by_stage, "by_type": by_type, "daily": daily}

class User(UserMixin):
    def __init__(self, data: dict):
        self.id             = data["id"]
        self.full_name      = data["full_name"]
        self.email          = data["email"]
        self.company        = data.get("company", "")
        self.phone          = data.get("phone", "")
        self.plan           = data.get("plan", "trial")
        self.email_verified = bool(data.get("email_verified", 0))

def create_user(full_name, email, password_hash, company="", phone="", verify_token="", plan="trial"):
    with _conn() as c:
        c.execute(
            """INSERT INTO users (full_name, email, password_hash, company, phone, verify_token, plan)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (full_name, email, password_hash, company, phone, verify_token, plan),
        ); c.commit()

def get_user_by_id(user_id):
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

def get_user_by_email(email):
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE email = ?", (email.lower().strip(),)).fetchone()
        return dict(row) if row else None

def get_user_by_verify_token(token):
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM users WHERE verify_token = ? AND verify_token != ''", (token,)
        ).fetchone()
        return dict(row) if row else None

def get_user_by_reset_token(token):
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM users WHERE reset_token = ? AND reset_token != ''", (token,)
        ).fetchone()
        return dict(row) if row else None

def verify_user_email(user_id):
    with _conn() as c:
        c.execute("UPDATE users SET email_verified = 1, verify_token = '' WHERE id = ?", (user_id,)); c.commit()

def update_user_last_login(user_id):
    with _conn() as c:
        c.execute("UPDATE users SET last_login = datetime('now') WHERE id = ?", (user_id,)); c.commit()

def set_reset_token(user_id, token, expires):
    with _conn() as c:
        c.execute("UPDATE users SET reset_token = ?, reset_expires = ? WHERE id = ?",
                  (token, expires, user_id)); c.commit()

def clear_reset_token(user_id):
    with _conn() as c:
        c.execute("UPDATE users SET reset_token = '', reset_expires = '' WHERE id = ?", (user_id,)); c.commit()

def update_password(user_id, password_hash):
    with _conn() as c:
        c.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id)); c.commit()
