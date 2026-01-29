# storage.py — SQLite永続化（データが消えない防御コード）
from __future__ import annotations

import os
import json
import csv
import sqlite3
from datetime import date
from typing import Dict, Any, List, Optional

# DBは data/ 配下に固定（既存を消さない・追記のみ）
DB_DIR = "data"
DB_PATH = os.path.join(DB_DIR, "data.db")

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


def _get_conn() -> sqlite3.Connection:
    ensure_data_dir()
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    # tweets: 棋譜（論理削除フラグ付き）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tweets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deleted INTEGER NOT NULL DEFAULT 0,
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
    # 列追加マイグレーション（既存DBに列がない場合のみ）
    try:
        cur.execute("ALTER TABLE tweets ADD COLUMN created_at TEXT DEFAULT (datetime('now','localtime'))")
    except sqlite3.OperationalError:
        pass  # 既に存在

    # weights: 重み1件（key='default'）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS weights (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    # usage: 日付別API使用量
    cur.execute("""
        CREATE TABLE IF NOT EXISTS usage (
            date_key TEXT PRIMARY KEY,
            data TEXT NOT NULL
        )
    """)

    # bandit: 学習加速用（arm → pulls, rewards）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bandit (
            arm_id TEXT PRIMARY KEY,
            pulls INTEGER NOT NULL DEFAULT 0,
            rewards REAL NOT NULL DEFAULT 0.0,
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # success_templates: 成功ツイートから抽出したテンプレ（自己蒸留用）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS success_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            template_json TEXT NOT NULL,
            engagement_score REAL NOT NULL DEFAULT 0.0,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    conn.commit()


def init_db() -> None:
    """起動時に1回呼ぶ。スキーマ確保＋既存CSVがあれば1回だけ移行。"""
    conn = _get_conn()
    try:
        _ensure_schema(conn)
        _migrate_csv_to_db_once(conn)
        _migrate_weights_to_db_once(conn)
        _migrate_usage_to_db_once(conn)
    finally:
        conn.close()


def _migrate_csv_to_db_once(conn: sqlite3.Connection) -> None:
    if not os.path.exists(LOG_PATH_LEGACY):
        return
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM tweets")
    if cur.fetchone()[0] > 0:
        return  # 既に移行済み
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
    """全列を揃えてINSERT。"""
    all_cols = ["deleted"] + TWEETS_COLUMNS
    all_vals = [0] + [str(row.get(c, "")) for c in TWEETS_COLUMNS]
    cur.execute(
        "INSERT INTO tweets (" + ",".join(all_cols) + ") VALUES (" + ",".join(["?"] * len(all_vals)) + ")",
        all_vals
    )


def read_rows(path: str = "") -> List[Dict[str, Any]]:
    """棋譜を取得。deleted=0 のみ。pathは互換用で無視。"""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM tweets WHERE deleted = 0 ORDER BY id ASC")
        rows = cur.fetchall()
        out = []
        for r in rows:
            d = dict(r)
            row = {k: d[k] for k in TWEETS_COLUMNS if k in d}
            if "id" in d:
                row["id"] = d["id"]
            out.append(row)
        return out
    finally:
        conn.close()


def append_row(path: str, row: Dict[str, Any]) -> None:
    """棋譜を1行だけ追記。上書き・全削除は行わない。"""
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
    """棋譜を複数行まとめて追記（承認時の3件など）。上書き・全削除は行わない。"""
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


def logical_delete_tweet(tweet_id: Optional[int] = None, row_id: Optional[int] = None) -> bool:
    """論理削除。id または row の id を指定。"""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        if row_id is not None:
            cur.execute("UPDATE tweets SET deleted = 1 WHERE id = ?", (row_id,))
        elif tweet_id is not None:
            cur.execute("UPDATE tweets SET deleted = 1 WHERE id = ?", (tweet_id,))
        else:
            return False
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def load_json(path: str, default: Dict[str, Any]) -> Dict[str, Any]:
    """usage用。DBのusageテーブルから日付別を組み立てて返す。"""
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
    """usage用。日付キーごとにusageテーブルへUPSERT。"""
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
    """重みをDBから取得。無ければ空で返し呼び出し側でデフォルト適用。"""
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
    """重みをDBに保存（atomic）。"""
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


# ---------- 成功テンプレ（自己蒸留用） ----------
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


# ---------- Bandit（arm → pulls, rewards） ----------
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
