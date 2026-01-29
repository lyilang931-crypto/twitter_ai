# storage.py — 永続化レイヤ（原子性・耐クラッシュ）
from __future__ import annotations
import os
import json
import csv
import sqlite3
import tempfile
import shutil
from datetime import date
from typing import Dict, Any, List, Optional

DATA_DIR = "data"
DB_PATH = os.path.join(DATA_DIR, "twitter_ai.db")
USE_SQLITE = True  # True: SQLite, False: JSON+CSV（原子書き込み）


def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


# ========== 原子書き込み（JSON / CSV） ==========

def atomic_save_json(path: str, obj: Dict[str, Any]) -> None:
    """tmp に書き出してから replace で原子置換。"""
    ensure_data_dir()
    fd, tmp = tempfile.mkstemp(dir=DATA_DIR, prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


def atomic_load_json(path: str, default: Dict[str, Any]) -> Dict[str, Any]:
    if not os.path.exists(path):
        return dict(default)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def read_rows(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def append_row(path: str, row: Dict[str, Any]) -> None:
    """従来の追記（非原子）。互換用。原子版は atomic_append_row を使用推奨。"""
    ensure_data_dir()
    exists = os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def atomic_append_row(path: str, row: Dict[str, Any]) -> None:
    """読んで追記して tmp→replace で原子書き込み。"""
    ensure_data_dir()
    rows = read_rows(path)
    if rows:
        fieldnames = list(rows[0].keys())
        for k in row:
            if k not in fieldnames:
                fieldnames.append(k)
    else:
        fieldnames = list(row.keys())
    rows.append(row)
    fd, tmp = tempfile.mkstemp(dir=DATA_DIR, prefix=".tmp_", suffix=".csv")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


def load_json(path: str, default: Dict[str, Any]) -> Dict[str, Any]:
    return atomic_load_json(path, default)


def save_json(path: str, obj: Dict[str, Any]) -> None:
    atomic_save_json(path, obj)


# ========== SQLite 永続化（推奨） ==========

def _get_conn() -> sqlite3.Connection:
    ensure_data_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: Optional[sqlite3.Connection] = None) -> None:
    close = False
    if conn is None:
        conn = _get_conn()
        close = True
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS approved (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT UNIQUE, data TEXT, saved_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS pack (
                id INTEGER PRIMARY KEY CHECK (id=1), data TEXT, saved_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS weights (
                id INTEGER PRIMARY KEY CHECK (id=1), data TEXT, updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS usage (
                date TEXT PRIMARY KEY, calls INTEGER, updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS rating (
                id INTEGER PRIMARY KEY CHECK (id=1), abs_rating REAL, rel_rating REAL, updated_at TEXT DEFAULT (datetime('now'))
            );
        """)
        conn.commit()
    finally:
        if close:
            conn.close()


def db_append_log(row: Dict[str, Any]) -> int:
    conn = _get_conn()
    try:
        init_schema(conn)
        data = json.dumps(row, ensure_ascii=False, default=str)
        conn.execute("INSERT INTO logs (data) VALUES (?)", (data,))
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        conn.close()


def db_read_logs(limit: int = 5000) -> List[Dict[str, Any]]:
    conn = _get_conn()
    try:
        cur = conn.execute("SELECT id, data FROM logs ORDER BY id ASC LIMIT ?", (limit,))
        rows = cur.fetchall()
        out = []
        for r in rows:
            try:
                out.append(json.loads(r["data"]))
            except Exception:
                pass
        return out
    finally:
        conn.close()


def db_save_approved(approved: Dict[str, Any]) -> None:
    conn = _get_conn()
    try:
        init_schema(conn)
        for role, v in approved.items():
            data = json.dumps(v, ensure_ascii=False) if v is not None else "null"
            conn.execute(
                "INSERT OR REPLACE INTO approved (role, data, saved_at) VALUES (?,?,datetime('now'))",
                (role, data)
            )
        conn.commit()
    finally:
        conn.close()


def db_load_approved() -> Dict[str, Any]:
    conn = _get_conn()
    try:
        cur = conn.execute("SELECT role, data FROM approved")
        out = {}
        for row in cur.fetchall():
            try:
                out[row["role"]] = json.loads(row["data"]) if row["data"] != "null" else None
            except Exception:
                out[row["role"]] = None
        return out
    finally:
        conn.close()


def db_save_pack(pack: Dict[str, Any]) -> None:
    conn = _get_conn()
    try:
        init_schema(conn)
        # シリアライズ可能な形に（float等のみ）
        data = json.dumps(pack, ensure_ascii=False, default=str)
        conn.execute(
            "INSERT OR REPLACE INTO pack (id, data, saved_at) VALUES (1,?,datetime('now'))",
            (data,)
        )
        conn.commit()
    finally:
        conn.close()


def db_load_pack() -> Dict[str, Any]:
    conn = _get_conn()
    try:
        cur = conn.execute("SELECT data FROM pack WHERE id=1")
        row = cur.fetchone()
        if row and row["data"]:
            return json.loads(row["data"])
        return {}
    except Exception:
        return {}
    finally:
        conn.close()


def db_save_weights(w: Dict[str, float]) -> None:
    conn = _get_conn()
    try:
        init_schema(conn)
        data = json.dumps(w, ensure_ascii=False)
        conn.execute(
            "INSERT OR REPLACE INTO weights (id, data, updated_at) VALUES (1,?,datetime('now'))",
            (data,)
        )
        conn.commit()
    finally:
        conn.close()


def db_load_weights() -> Dict[str, float]:
    conn = _get_conn()
    try:
        cur = conn.execute("SELECT data FROM weights WHERE id=1")
        row = cur.fetchone()
        if row and row["data"]:
            return json.loads(row["data"])
        return {}
    except Exception:
        return {}
    finally:
        conn.close()


def db_save_usage(usage: Dict[str, Any]) -> None:
    conn = _get_conn()
    try:
        init_schema(conn)
        for dk, dv in usage.items():
            calls = dv.get("calls", 0) if isinstance(dv, dict) else 0
            conn.execute(
                "INSERT OR REPLACE INTO usage (date, calls, updated_at) VALUES (?,?,datetime('now'))",
                (dk, int(calls))
            )
        conn.commit()
    finally:
        conn.close()


def db_load_usage() -> Dict[str, Any]:
    conn = _get_conn()
    try:
        cur = conn.execute("SELECT date, calls FROM usage")
        return {row["date"]: {"calls": row["calls"]} for row in cur.fetchall()}
    except Exception:
        return {}
    finally:
        conn.close()


def db_save_rating(abs_rating: float, rel_rating: float) -> None:
    conn = _get_conn()
    try:
        init_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO rating (id, abs_rating, rel_rating, updated_at) VALUES (1,?,?,datetime('now'))",
            (abs_rating, rel_rating)
        )
        conn.commit()
    finally:
        conn.close()


def db_load_rating() -> tuple:
    conn = _get_conn()
    try:
        cur = conn.execute("SELECT abs_rating, rel_rating FROM rating WHERE id=1")
        row = cur.fetchone()
        if row and row["abs_rating"] is not None:
            return (float(row["abs_rating"]), float(row["rel_rating"]))
        return (1000.0, 1000.0)
    except Exception:
        return (1000.0, 1000.0)
    finally:
        conn.close()
