# storage.py — 永続層（Postgres / SQLite 切替、event_log 永続化）
# 保存層: デフォルトは SQLite/Postgres。環境変数 SUPABASE_URL + SUPABASE_KEY が設定されている
# 場合のみ Supabase に切替可能（storage_supabase を利用）。上書き時は必ず load→merge→save とすること。
from __future__ import annotations

import os
import json
import csv
import sqlite3
from typing import Dict, Any, List, Optional, Union

# DATABASE_URL: 環境変数優先。未設定時のみ st.secrets を参照（secrets は読まない方針のため env 推奨）
_database_url = os.environ.get("DATABASE_URL", "")
if not _database_url:
    try:
        import streamlit as _st
        _database_url = _st.secrets.get("DATABASE_URL", "") or ""
    except Exception:
        pass
_backend: str = "postgres" if _database_url else "sqlite"

def _ph() -> str:
    return "%s" if _backend == "postgres" else "?"

def _sql(s: str) -> str:
    if _backend == "postgres":
        return s.replace("?", "%s")
    return s


def _row_to_dict(cur, r) -> Dict[str, Any]:
    """cursor の 1 行を dict に。Postgres/sqlite 両対応。"""
    if hasattr(r, "keys"):
        return dict(r)
    if cur.description:
        return {cur.description[i][0]: r[i] for i in range(len(r))}
    return {}

# -----------------------------------------------------------------------------
# app.py が from storage import する名前を全てここに列挙（ImportError ゼロ）
# -----------------------------------------------------------------------------
# 編集可能な列（update_by_id で許可）
TWEETS_EDITABLE_COLUMNS = [
    "date", "role", "tweet_id", "text", "impressions", "likes", "rts", "replies",
    "followers_before", "followers_after", "Pseudo", "速報", "確定", "novelty", "safety", "tail",
    "abs_rating_before", "abs_rating_after", "rel_rating_before", "rel_rating_after",
]

__all__ = [
    "init_db",
    "get_conn",
    "read_rows",
    "append_row",
    "append_rows",
    "update_row",
    "update_by_id",
    "load_json",
    "save_json",
    "load_weights",
    "save_weights",
    "logical_delete_tweet",
    "get_success_templates",
    "save_success_template",
    "bandit_get_all",
    "bandit_update",
    "STATUS_PINNED",
    "STATUS_CONFIRMED",
    "STATUS_CANDIDATE",
    "STATUS_DELETED",
    "DRAFT_STATUS_DRAFT",
    "DRAFT_STATUS_APPROVED",
    "DRAFT_STATUS_POSTED",
    "DRAFT_STATUS_REJECTED",
    "DRAFT_STATUS_SCHEDULED",
    "insert_draft",
    "read_drafts",
    "update_draft",
    "update_draft_status",
    "delete_draft",
    "get_scheduled_drafts",
    "log_event",
    "read_events",
]

# DB パス（SQLite 時のみ使用）
DB_DIR = "data"
DB_PATH = os.path.join(DB_DIR, "data.db")

STATUS_CONFIRMED = "confirmed"
STATUS_PINNED = "pinned"
STATUS_CANDIDATE = "candidate"
STATUS_DELETED = "deleted"

# Draft statuses
DRAFT_STATUS_DRAFT = "draft"
DRAFT_STATUS_APPROVED = "approved"
DRAFT_STATUS_POSTED = "posted"
DRAFT_STATUS_REJECTED = "rejected"
DRAFT_STATUS_SCHEDULED = "scheduled"

LOG_PATH_LEGACY = "data/twitter_log.csv"
W_PATH_LEGACY = "data/weights.json"
U_PATH_LEGACY = "data/usage.json"

TWEETS_COLUMNS = [
    "date", "role", "tweet_id", "text", "impressions", "likes", "rts", "replies",
    "followers_before", "followers_after", "Pseudo", "速報", "確定", "novelty", "safety", "tail",
    "abs_rating_before", "abs_rating_after", "rel_rating_before", "rel_rating_after",
]


def ensure_data_dir() -> None:
    os.makedirs(DB_DIR, exist_ok=True)


def get_conn() -> Union[sqlite3.Connection, Any]:
    """永続層への接続。DATABASE_URL があれば Postgres、なければ SQLite。"""
    if _backend == "postgres":
        import psycopg
        return psycopg.connect(_database_url)
    ensure_data_dir()
    conn = sqlite3.connect(DB_PATH, timeout=5, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _get_conn():
    return get_conn()


def init_db() -> None:
    """冪等。PRAGMA(WAL) / CREATE TABLE IF NOT EXISTS。event_log 含む。"""
    conn = get_conn()
    try:
        if _backend == "sqlite":
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA journal_mode=WAL")
        _ensure_schema(conn)
        if _backend == "sqlite":
            _migrate_csv_to_db_once(conn)
            _migrate_weights_to_db_once(conn)
            _migrate_usage_to_db_once(conn)
    finally:
        conn.close()


def _ensure_schema(conn) -> None:
    cur = conn.cursor()
    if _backend == "postgres":
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tweets (
                id BIGSERIAL PRIMARY KEY,
                deleted INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'confirmed',
                date TEXT,
                role TEXT,
                tweet_id TEXT,
                text TEXT,
                impressions TEXT,
                likes TEXT,
                rts TEXT,
                replies TEXT,
                followers_before TEXT,
                followers_after TEXT,
                Pseudo TEXT,
                速報 TEXT,
                確定 TEXT,
                novelty TEXT,
                safety TEXT,
                tail TEXT,
                abs_rating_before TEXT,
                abs_rating_after TEXT,
                rel_rating_before TEXT,
                rel_rating_after TEXT,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS weights (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usage (
                date_key TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bandit (
                arm_id TEXT PRIMARY KEY,
                pulls INTEGER NOT NULL DEFAULT 0,
                rewards REAL NOT NULL DEFAULT 0.0,
                updated_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS success_templates (
                id BIGSERIAL PRIMARY KEY,
                template_json TEXT NOT NULL,
                engagement_score REAL NOT NULL DEFAULT 0.0,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS event_log (
                id BIGSERIAL PRIMARY KEY,
                created_at TIMESTAMPTZ DEFAULT now(),
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                meta_json TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS drafts (
                id BIGSERIAL PRIMARY KEY,
                created_at TIMESTAMPTZ DEFAULT now(),
                topic TEXT NOT NULL DEFAULT '',
                trend_hint TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL DEFAULT 'MAIN',
                text TEXT NOT NULL DEFAULT '',
                score_abs REAL NOT NULL DEFAULT 0.0,
                score_rel REAL NOT NULL DEFAULT 0.0,
                pseudo REAL NOT NULL DEFAULT 0.0,
                league REAL NOT NULL DEFAULT 0.0,
                safety_score REAL NOT NULL DEFAULT 0.0,
                quality_score REAL NOT NULL DEFAULT 0.0,
                novelty REAL NOT NULL DEFAULT 0.0,
                tail REAL NOT NULL DEFAULT 0.0,
                flags TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'draft',
                scheduled_at TIMESTAMPTZ,
                posted_at TIMESTAMPTZ,
                tweet_id TEXT,
                deleted INTEGER NOT NULL DEFAULT 0
            )
        """)
    else:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tweets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deleted INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'confirmed',
                date TEXT,
                role TEXT,
                tweet_id TEXT,
                text TEXT,
                impressions TEXT,
                likes TEXT,
                rts TEXT,
                replies TEXT,
                followers_before TEXT,
                followers_after TEXT,
                Pseudo TEXT,
                速報 TEXT,
                確定 TEXT,
                novelty TEXT,
                safety TEXT,
                tail TEXT,
                abs_rating_before TEXT,
                abs_rating_after TEXT,
                rel_rating_before TEXT,
                rel_rating_after TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        for col_def in [
            "ALTER TABLE tweets ADD COLUMN created_at TEXT DEFAULT (datetime('now','localtime'))",
            "ALTER TABLE tweets ADD COLUMN status TEXT NOT NULL DEFAULT 'confirmed'",
        ]:
            try:
                cur.execute(col_def)
            except sqlite3.OperationalError:
                pass
        cur.execute("""
            CREATE TABLE IF NOT EXISTS weights (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usage (
                date_key TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bandit (
                arm_id TEXT PRIMARY KEY,
                pulls INTEGER NOT NULL DEFAULT 0,
                rewards REAL NOT NULL DEFAULT 0.0,
                updated_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS success_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                template_json TEXT NOT NULL,
                engagement_score REAL NOT NULL DEFAULT 0.0,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS event_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                meta_json TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                topic TEXT NOT NULL DEFAULT '',
                trend_hint TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL DEFAULT 'MAIN',
                text TEXT NOT NULL DEFAULT '',
                score_abs REAL NOT NULL DEFAULT 0.0,
                score_rel REAL NOT NULL DEFAULT 0.0,
                pseudo REAL NOT NULL DEFAULT 0.0,
                league REAL NOT NULL DEFAULT 0.0,
                safety_score REAL NOT NULL DEFAULT 0.0,
                quality_score REAL NOT NULL DEFAULT 0.0,
                novelty REAL NOT NULL DEFAULT 0.0,
                tail REAL NOT NULL DEFAULT 0.0,
                flags TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'draft',
                scheduled_at TEXT,
                posted_at TEXT,
                tweet_id TEXT,
                deleted INTEGER NOT NULL DEFAULT 0
            )
        """)
    conn.commit()


def _migrate_csv_to_db_once(conn) -> None:
    if _backend != "sqlite" or not os.path.exists(LOG_PATH_LEGACY):
        return
    cur = conn.cursor()
    cur.execute(_sql("SELECT COUNT(*) FROM tweets"))
    row = cur.fetchone()
    if row and (row[0] if isinstance(row, (list, tuple)) else row["count"]) > 0:
        return
    try:
        with open(LOG_PATH_LEGACY, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                norm = {c: row.get(c, "") for c in TWEETS_COLUMNS}
                _insert_tweet_row_safe(cur, norm)
        conn.commit()
    except Exception:
        conn.rollback()


def _insert_tweet_row_safe(cur, row: Dict[str, Any]) -> None:
    status = str(row.get("status", STATUS_CONFIRMED))
    if status not in (STATUS_CONFIRMED, STATUS_PINNED, STATUS_CANDIDATE, STATUS_DELETED):
        status = STATUS_CONFIRMED
    all_cols = ["deleted", "status"] + TWEETS_COLUMNS
    all_vals = [0, status] + [str(row.get(c, "")) for c in TWEETS_COLUMNS]
    placeholders = ",".join([_ph()] * len(all_vals))
    cur.execute(
        _sql("INSERT INTO tweets (" + ",".join(all_cols) + ") VALUES (" + placeholders + ")"),
        all_vals
    )


# ---------- event_log（再起動/再デプロイでも消えない） ----------
def log_event(event_type: str, payload: Optional[Dict[str, Any]] = None, meta: Optional[Dict[str, Any]] = None) -> None:
    """イベントを必ず DB に保存。payload/meta は dict → json.dumps で TEXT 保存。"""
    conn = get_conn()
    try:
        cur = conn.cursor()
        payload_json = json.dumps(payload or {}, ensure_ascii=False)
        meta_json = json.dumps(meta or {}, ensure_ascii=False)
        cur.execute(
            _sql("INSERT INTO event_log (event_type, payload_json, meta_json) VALUES (?, ?, ?)"),
            (event_type, payload_json, meta_json)
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def read_events(limit: int = 100, event_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """event_log を取得。event_type 指定時は絞り込み。"""
    conn = get_conn()
    try:
        cur = conn.cursor()
        if event_type is not None:
            cur.execute(
                _sql("SELECT id, created_at, event_type, payload_json, meta_json FROM event_log WHERE event_type = ? ORDER BY id DESC LIMIT ?"),
                (event_type, limit),
            )
        else:
            cur.execute(
                _sql("SELECT id, created_at, event_type, payload_json, meta_json FROM event_log ORDER BY id DESC LIMIT ?"),
                (limit,),
            )
        rows = cur.fetchall()
        out = []
        for r in rows:
            d = _row_to_dict(cur, r)
            if not d:
                d = {"id": r[0], "created_at": r[1], "event_type": r[2], "payload_json": r[3], "meta_json": r[4]} if isinstance(r, (list, tuple)) else {}
            try:
                out.append({
                    "id": d.get("id"),
                    "created_at": d.get("created_at"),
                    "event_type": d.get("event_type"),
                    "payload": json.loads(d.get("payload_json") or "{}"),
                    "meta": json.loads(d.get("meta_json") or "{}"),
                })
            except Exception:
                pass
        return out
    finally:
        conn.close()


# ---------- 既存 API（互換レイヤー） ----------
def read_rows(path: str = "", status: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        if status is not None:
            cur.execute(_sql("SELECT * FROM tweets WHERE deleted = 0 AND status = ? ORDER BY id ASC"), (status,))
        else:
            cur.execute(_sql("SELECT * FROM tweets WHERE deleted = 0 ORDER BY id ASC"))
        rows = cur.fetchall()
        out = []
        for r in rows:
            d = _row_to_dict(cur, r)
            row = {k: d[k] for k in TWEETS_COLUMNS if k in d}
            if "id" in d:
                row["id"] = d["id"]
            if "status" in d:
                row["status"] = d["status"]
            out.append(row)
        return out
    finally:
        conn.close()


def append_row(path: str, row: Dict[str, Any]) -> None:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        _insert_tweet_row_safe(cur, row)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def append_rows(rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    conn = _get_conn()
    try:
        cur = conn.cursor()
        for row in rows:
            _insert_tweet_row_safe(cur, row)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_row(row_id: int, **kwargs: Any) -> bool:
    if not kwargs:
        return False
    conn = _get_conn()
    try:
        cur = conn.cursor()
        set_parts = [f"{k} = {_ph()}" for k in kwargs]
        vals = list(kwargs.values()) + [row_id]
        cur.execute(
            _sql("UPDATE tweets SET " + ", ".join(set_parts) + " WHERE id = ? AND deleted = 0"),
            vals
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_by_id(row_id: int, patch: Dict[str, Any]) -> bool:
    """指定 id の行を patch のキーで更新。許可列のみ反映し永続保存。"""
    if not patch or row_id is None:
        return False
    allowed = {k: v for k, v in patch.items() if k in TWEETS_EDITABLE_COLUMNS}
    if not allowed:
        return False
    # 数値列は文字列で保存されているため str に統一
    for col in ["impressions", "likes", "rts", "replies", "followers_before", "followers_after"]:
        if col in allowed and allowed[col] is not None:
            allowed[col] = str(allowed[col])
    return update_row(row_id, **allowed)


def logical_delete_tweet(tweet_id: Optional[int] = None, row_id: Optional[int] = None) -> bool:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        rid = row_id if row_id is not None else tweet_id
        if rid is None:
            return False
        cur.execute(_sql("UPDATE tweets SET deleted = 1, status = ? WHERE id = ? AND deleted = 0"), (STATUS_DELETED, rid))
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


delete_row = logical_delete_tweet


def load_json(path: str, default: Dict[str, Any]) -> Dict[str, Any]:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(_sql("SELECT date_key, data FROM usage"))
        out = dict(default)
        for r in cur.fetchall():
            k, v = (r[0], r[1]) if not hasattr(r, "keys") else (r["date_key"], r["data"])
            out[k] = json.loads(v)
        return out
    finally:
        conn.close()


def save_json(path: str, obj: Dict[str, Any]) -> None:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        for k, v in obj.items():
            if _backend == "postgres":
                cur.execute(
                    "INSERT INTO usage (date_key, data) VALUES (%s, %s) ON CONFLICT (date_key) DO UPDATE SET data = EXCLUDED.data",
                    (str(k), json.dumps(v, ensure_ascii=False))
                )
            else:
                cur.execute(
                    _sql("INSERT OR REPLACE INTO usage (date_key, data) VALUES (?, ?)"),
                    (str(k), json.dumps(v, ensure_ascii=False))
                )
        conn.commit()
    finally:
        conn.close()


def load_weights(path: str = "") -> Dict[str, float]:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(_sql("SELECT value FROM weights WHERE key = 'default'"))
        r = cur.fetchone()
        if r:
            return json.loads(r[0] if not hasattr(r, "keys") else r["value"])
        return {}
    finally:
        conn.close()


def save_weights(path: str, w: Dict[str, float]) -> None:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        j = json.dumps(w, ensure_ascii=False)
        if _backend == "postgres":
            cur.execute("INSERT INTO weights (key, value) VALUES ('default', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (j,))
        else:
            cur.execute(_sql("INSERT OR REPLACE INTO weights (key, value) VALUES ('default', ?)"), (j,))
        conn.commit()
    finally:
        conn.close()


def _migrate_weights_to_db_once(conn) -> None:
    if _backend != "sqlite" or not os.path.exists(W_PATH_LEGACY):
        return
    cur = conn.cursor()
    cur.execute(_sql("SELECT COUNT(*) FROM weights WHERE key = 'default'"))
    row = cur.fetchone()
    if row and (row[0] if isinstance(row, (list, tuple)) else row["count"]) > 0:
        return
    try:
        with open(W_PATH_LEGACY, "r", encoding="utf-8") as f:
            w = json.load(f)
        cur.execute(_sql("INSERT OR REPLACE INTO weights (key, value) VALUES ('default', ?)"), (json.dumps(w),))
        conn.commit()
    except Exception:
        conn.rollback()


def _migrate_usage_to_db_once(conn) -> None:
    if _backend != "sqlite" or not os.path.exists(U_PATH_LEGACY):
        return
    cur = conn.cursor()
    cur.execute(_sql("SELECT COUNT(*) FROM usage"))
    row = cur.fetchone()
    if row and (row[0] if isinstance(row, (list, tuple)) else row["count"]) > 0:
        return
    try:
        with open(U_PATH_LEGACY, "r", encoding="utf-8") as f:
            u = json.load(f)
        for k, v in u.items():
            cur.execute(_sql("INSERT OR REPLACE INTO usage (date_key, data) VALUES (?, ?)"), (str(k), json.dumps(v)))
        conn.commit()
    except Exception:
        conn.rollback()


def save_success_template(template_json: str, engagement_score: float = 0.0) -> None:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            _sql("INSERT INTO success_templates (template_json, engagement_score) VALUES (?, ?)"),
            (template_json, engagement_score),
        )
        conn.commit()
    finally:
        conn.close()


def get_success_templates(top_n: int = 10) -> List[Dict[str, Any]]:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            _sql("SELECT template_json, engagement_score FROM success_templates ORDER BY engagement_score DESC LIMIT ?"),
            (top_n,),
        )
        out = []
        for r in cur.fetchall():
            try:
                a, b = (r[0], r[1]) if not hasattr(r, "keys") else (r["template_json"], r["engagement_score"])
                out.append({"data": json.loads(a), "score": float(b)})
            except Exception:
                pass
        return out
    finally:
        conn.close()


def bandit_get_all() -> Dict[str, Dict[str, Any]]:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(_sql("SELECT arm_id, pulls, rewards FROM bandit"))
        out = {}
        for r in cur.fetchall():
            if hasattr(r, "keys"):
                out[r["arm_id"]] = {"pulls": int(r["pulls"]), "rewards": float(r["rewards"])}
            else:
                out[r[0]] = {"pulls": int(r[1]), "rewards": float(r[2])}
        return out
    finally:
        conn.close()


def bandit_update(arm_id: str, reward: float) -> None:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(_sql("SELECT pulls, rewards FROM bandit WHERE arm_id = ?"), (arm_id,))
        row = cur.fetchone()
        if row:
            p = int(row[1] if isinstance(row, (list, tuple)) else row["pulls"])
            r = float(row[2] if isinstance(row, (list, tuple)) else row["rewards"])
            if _backend == "postgres":
                cur.execute("UPDATE bandit SET pulls = %s, rewards = %s, updated_at = now() WHERE arm_id = %s", (p + 1, r + reward, arm_id))
            else:
                cur.execute(_sql("UPDATE bandit SET pulls = ?, rewards = ?, updated_at = datetime('now','localtime') WHERE arm_id = ?"), (p + 1, r + reward, arm_id))
        else:
            cur.execute(_sql("INSERT INTO bandit (arm_id, pulls, rewards) VALUES (?, 1, ?)"), (arm_id, reward))
        conn.commit()
    finally:
        conn.close()


# ---------- drafts CRUD ----------

def insert_draft(draft: Dict[str, Any]) -> Optional[int]:
    """下書きを1件挿入し、挿入されたIDを返す。"""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cols = [
            "topic", "trend_hint", "role", "text",
            "score_abs", "score_rel", "pseudo", "league",
            "safety_score", "quality_score", "novelty", "tail",
            "flags", "status", "scheduled_at", "tweet_id",
        ]
        vals = [
            str(draft.get("topic", "")),
            str(draft.get("trend_hint", "")),
            str(draft.get("role", "MAIN")),
            str(draft.get("text", "")),
            float(draft.get("score_abs", 0.0)),
            float(draft.get("score_rel", 0.0)),
            float(draft.get("pseudo", 0.0)),
            float(draft.get("league", 0.0)),
            float(draft.get("safety_score", 0.0)),
            float(draft.get("quality_score", 0.0)),
            float(draft.get("novelty", 0.0)),
            float(draft.get("tail", 0.0)),
            json.dumps(draft.get("flags", {}), ensure_ascii=False) if isinstance(draft.get("flags"), dict) else str(draft.get("flags", "{}")),
            str(draft.get("status", DRAFT_STATUS_DRAFT)),
            draft.get("scheduled_at") or None,
            draft.get("tweet_id") or None,
        ]
        placeholders = ",".join([_ph()] * len(vals))
        sql = _sql("INSERT INTO drafts (" + ",".join(cols) + ") VALUES (" + placeholders + ")")
        cur.execute(sql, vals)
        conn.commit()
        if _backend == "postgres":
            cur.execute("SELECT lastval()")
            row = cur.fetchone()
            return int(row[0]) if row else None
        else:
            return cur.lastrowid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def read_drafts(
    status: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """下書き一覧を取得。status指定時は絞り込み。deleted=0のみ。"""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        if status is not None:
            cur.execute(
                _sql("SELECT * FROM drafts WHERE deleted = 0 AND status = ? ORDER BY id DESC LIMIT ?"),
                (status, limit),
            )
        else:
            cur.execute(
                _sql("SELECT * FROM drafts WHERE deleted = 0 ORDER BY id DESC LIMIT ?"),
                (limit,),
            )
        rows = cur.fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            d = _row_to_dict(cur, r)
            # flags を dict にパース
            try:
                d["flags"] = json.loads(d.get("flags") or "{}")
            except (json.JSONDecodeError, TypeError):
                d["flags"] = {}
            out.append(d)
        return out
    finally:
        conn.close()


def update_draft(draft_id: int, patch: Dict[str, Any]) -> bool:
    """下書きの任意フィールドを更新する。"""
    if not patch or draft_id is None:
        return False
    allowed_cols = {
        "topic", "trend_hint", "role", "text",
        "score_abs", "score_rel", "pseudo", "league",
        "safety_score", "quality_score", "novelty", "tail",
        "flags", "status", "scheduled_at", "posted_at", "tweet_id",
    }
    filtered = {}
    for k, v in patch.items():
        if k in allowed_cols:
            if k == "flags" and isinstance(v, dict):
                filtered[k] = json.dumps(v, ensure_ascii=False)
            else:
                filtered[k] = v
    if not filtered:
        return False
    conn = _get_conn()
    try:
        cur = conn.cursor()
        set_parts = [f"{k} = {_ph()}" for k in filtered]
        vals = list(filtered.values()) + [draft_id]
        cur.execute(
            _sql("UPDATE drafts SET " + ", ".join(set_parts) + " WHERE id = ? AND deleted = 0"),
            vals,
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_draft_status(draft_id: int, new_status: str, extra: Optional[Dict[str, Any]] = None) -> bool:
    """下書きのステータスを変更する。extraでposted_at/tweet_id等も同時更新可。"""
    patch = {"status": new_status}
    if extra:
        patch.update(extra)
    return update_draft(draft_id, patch)


def delete_draft(draft_id: int) -> bool:
    """下書きを論理削除する。"""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            _sql("UPDATE drafts SET deleted = 1 WHERE id = ? AND deleted = 0"),
            (draft_id,),
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_scheduled_drafts() -> List[Dict[str, Any]]:
    """scheduled_at <= now() の未投稿ドラフトを取得する（スケジューラ用）。"""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        if _backend == "postgres":
            cur.execute(
                "SELECT * FROM drafts WHERE deleted = 0 AND status = %s AND scheduled_at <= now() ORDER BY scheduled_at ASC LIMIT 10",
                (DRAFT_STATUS_SCHEDULED,),
            )
        else:
            cur.execute(
                _sql("SELECT * FROM drafts WHERE deleted = 0 AND status = ? AND scheduled_at <= datetime('now','localtime') ORDER BY scheduled_at ASC LIMIT 10"),
                (DRAFT_STATUS_SCHEDULED,),
            )
        rows = cur.fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            d = _row_to_dict(cur, r)
            try:
                d["flags"] = json.loads(d.get("flags") or "{}")
            except (json.JSONDecodeError, TypeError):
                d["flags"] = {}
            out.append(d)
        return out
    finally:
        conn.close()
