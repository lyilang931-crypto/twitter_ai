# csvio.py
import csv
import os
from typing import List, Dict, Any

def read_csv(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)

def append_csv(path: str, row: Dict[str, Any]) -> None:
    exists = os.path.exists(path)
    fieldnames = list(row.keys())
    if exists:
        # 既存headerを優先
        with open(path, "r", encoding="utf-8", newline="") as f:
            r = csv.reader(f)
            header = next(r, None)
        if header:
            fieldnames = header

    with open(path, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        # 欠けた列は空で埋める
        out = {k: row.get(k, "") for k in fieldnames}
        writer.writerow(out)