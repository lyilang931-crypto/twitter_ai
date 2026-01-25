# storage.py
from __future__ import annotations
import os, json, csv
from datetime import date
from typing import Dict, Any, List

def ensure_data_dir():
    os.makedirs("data", exist_ok=True)

def read_rows(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)

def append_row(path: str, row: Dict[str, Any]):
    ensure_data_dir()
    exists = os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if not exists:
            writer.writeheader()
        writer.writerow(row)

def load_json(path: str, default: Dict[str, Any]) -> Dict[str, Any]:
    ensure_data_dir()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return dict(default)

def save_json(path: str, obj: Dict[str, Any]):
    ensure_data_dir()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
