# storage.py — 永続層（app.py が import する名前を全て提供する互換レイヤー）
from __future__ import annotations

import os
import json
import csv
import sqlite3
from typing import Dict, Any, List, Optional

# -----------------------------------------------------------------------------
# app.py が from storage import する名前を全てここに列挙（ImportError ゼロ）
# -----------------------------------------------------------------------------
__all__ = [
    "init_db",
    "get_conn",
    "read_rows",
    "append_row",
    "append_rows",
    "update_row",
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
]

# DB パス（安定パスに統一）
DB_DIR = "data"
DB_PATH = os.path.join(DB_DIR, "data.db")

# status: candidate=候補, pinned=固定（承認済み）, confirmed=確定済み, deleted=論理削除
STATUS_CONFIRMED = "confirmed"
STATUS_PINNED = "pinned"
STATUS_CANDIDATE = "candidate"
STATUS_DELETED = "deleted"

# 既存CSV/JSONパス（マイグレーション用）
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


def get_conn() -> sqlite3.Connection:
    """永続層への接続。Streamlit 対応: check_same_thread=False, timeout=5, row_factory=Row。"""
    ensure_data_dir()
    conn = sqlite3.connect(
        DB_PATH,
        timeout=5,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    return conn


def _get_conn() -> sqlite3.Connection:
    return get_conn()


def init_db() -> None:
    """起動時1回。PRAGMA busy_timeout / WAL + CREATE TABLE IF NOT EXISTS。"""
    conn = get_conn()
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_mode=WAL")
        _ensure_schema(conn)
        _migrate_csv_to_db_once(conn)
        _migrate_weights_to_db_once(conn)
        _migrate_usage_to_db_once(conn)
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
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
    conn.commit()


def _migrate_csv_to_db_once(conn: sqlite3.Connection) -> None:
    if not os.path.exists(LOG_PATH_LEGACY):
        return
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM tweets")
    if cur.fetchone()[0] > 0:
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


def _insert_tweet_row_safe(cur: sqlite3.Cursor, row: Dict[str, Any]) -> None:
    status = str(row.get("status", STATUS_CONFIRMED))
    if status not in (STATUS_CONFIRMED, STATUS_PINNED, STATUS_CANDIDATE, STATUS_DELETED):
        status = STATUS_CONFIRMED
    all_cols = ["deleted", "status"] + TWEETS_COLUMNS
    all_vals = [0, status] + [str(row.get(c, "")) for c in TWEETS_COLUMNS]
    cur.execute(
        "INSERT INTO tweets (" + ",".join(all_cols) + ") VALUES (" + ",".join(["?"] * len(all_vals)) + ")",
        all_vals
    )


def read_rows(path: str = "", status: Optional[str] = None) -> List[Dict[str, Any]]:
    """棋譜を取得。deleted=0。status 指定時はその status に絞る。"""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        if status is not None:
            cur.execute("SELECT * FROM tweets WHERE deleted = 0 AND status = ? ORDER BY id ASC", (status,))
        else:
            cur.execute("SELECT * FROM tweets WHERE deleted = 0 ORDER BY id ASC")
        rows = cur.fetchall()
        out = []
        for r in rows:
            d = dict(r)
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
    """棋譜を1行追記。トランザクションで atomic。"""
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
    """棋譜を複数行追記。トランザクションで atomic。"""
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
    """指定 id の行を更新（例: status を pinned に）。"""
    if not kwargs:
        return False
    conn = _get_conn()
    try:
        cur = conn.cursor()
        set_parts = []
        vals = []
        for k, v in kwargs.items():
            set_parts.append(f"{k} = ?")
            vals.append(v)
        vals.append(row_id)
        cur.execute(
            "UPDATE tweets SET " + ", ".join(set_parts) + " WHERE id = ? AND deleted = 0",
            vals
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def logical_delete_tweet(tweet_id: Optional[int] = None, row_id: Optional[int] = None) -> bool:
    """論理削除（deleted=1, status=deleted）。行は消さない。"""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        rid = row_id if row_id is not None else tweet_id
        if rid is None:
            return False
        cur.execute("UPDATE tweets SET deleted = 1, status = ? WHERE id = ? AND deleted = 0", (STATUS_DELETED, rid))
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# delete_row: app.py は logical_delete_tweet を使用。互換のため alias を用意（必要なら利用可）
delete_row = logical_delete_tweet


def load_json(path: str, default: Dict[str, Any]) -> Dict[str, Any]:
    """usage 用。DB の usage テーブルから日付別を返す。"""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT date_key, data FROM usage")
        out = dict(default)
        for r in cur.fetchall():
            out[r[0]] = json.loads(r[1])
        return out
    finally:
        conn.close()


def save_json(path: str, obj: Dict[str, Any]) -> None:
    """usage 用。日付キーごとに UPSERT。"""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        for k, v in obj.items():
            cur.execute(
                "INSERT OR REPLACE INTO usage (date_key, data) VALUES (?, ?)",
                (str(k), json.dumps(v, ensure_ascii=False))
            )
        conn.commit()
    finally:
        conn.close()


def load_weights(path: str = "") -> Dict[str, float]:
    """重みを DB から取得。無ければ空 dict。"""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT value FROM weights WHERE key = 'default'")
        r = cur.fetchone()
        if r:
            return json.loads(r[0])
        return {}
    finally:
        conn.close()


def save_weights(path: str, w: Dict[str, float]) -> None:
    """重みを DB に保存。"""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO weights (key, value) VALUES ('default', ?)",
            (json.dumps(w, ensure_ascii=False),)
        )
        conn.commit()
    finally:
        conn.close()


def _migrate_weights_to_db_once(conn: sqlite3.Connection) -> None:
    if not os.path.exists(W_PATH_LEGACY):
        return
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM weights WHERE key = 'default'")
    if cur.fetchone()[0] > 0:
        return
    try:
        with open(W_PATH_LEGACY, "r", encoding="utf-8") as f:
            w = json.load(f)
        cur.execute("INSERT OR REPLACE INTO weights (key, value) VALUES ('default', ?)", (json.dumps(w),))
        conn.commit()
    except Exception:
        conn.rollback()


def _migrate_usage_to_db_once(conn: sqlite3.Connection) -> None:
    if not os.path.exists(U_PATH_LEGACY):
        return
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM usage")
    if cur.fetchone()[0] > 0:
        return
    try:
        with open(U_PATH_LEGACY, "r", encoding="utf-8") as f:
            u = json.load(f)
        for k, v in u.items():
            cur.execute("INSERT OR REPLACE INTO usage (date_key, data) VALUES (?, ?)", (str(k), json.dumps(v)))
        conn.commit()
    except Exception:
        conn.rollback()


def save_success_template(template_json: str, engagement_score: float = 0.0) -> None:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO success_templates (template_json, engagement_score) VALUES (?, ?)",
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
            "SELECT template_json, engagement_score FROM success_templates ORDER BY engagement_score DESC LIMIT ?",
            (top_n,),
        )
        out = []
        for r in cur.fetchall():
            try:
                out.append({"data": json.loads(r[0]), "score": float(r[1])})
            except Exception:
                pass
        return out
    finally:
        conn.close()


def bandit_get_all() -> Dict[str, Dict[str, Any]]:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT arm_id, pulls, rewards FROM bandit")
        out = {}
        for r in cur.fetchall():
            out[r[0]] = {"pulls": int(r[1]), "rewards": float(r[2])}
        return out
    finally:
        conn.close()


def bandit_update(arm_id: str, reward: float) -> None:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT pulls, rewards FROM bandit WHERE arm_id = ?", (arm_id,))
        row = cur.fetchone()
        if row:
            p, r = int(row[0]), float(row[1])
            cur.execute(
                "UPDATE bandit SET pulls = ?, rewards = ?, updated_at = datetime('now','localtime') WHERE arm_id = ?",
                (p + 1, r + reward, arm_id),
            )
        else:
            cur.execute(
                "INSERT INTO bandit (arm_id, pulls, rewards) VALUES (?, 1, ?)",
                (arm_id, reward),
            )
        conn.commit()
    finally:
        conn.close()
