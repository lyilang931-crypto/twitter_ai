# analytics_utils.py
import json
from collections import Counter
from typing import List, Dict, Any, Tuple

TAG_KEYS = ["intent", "hook_type", "sentence_len", "has_question", "cta"]

def recent_mean_score(rows: List[Dict[str, Any]], window: int = 30) -> float:
    if not rows:
        return 0.02
    tail = rows[-window:] if len(rows) > window else rows
    vals = []
    for r in tail:
        try:
            vals.append(float(r.get("tweet_score", 0.0) or 0.0))
        except Exception:
            pass
    return sum(vals) / max(1, len(vals)) if vals else 0.02

def top_bottom(rows: List[Dict[str, Any]], frac: float = 0.2) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    if not rows:
        return [], []
    scored = []
    for r in rows:
        try:
            s = float(r.get("tweet_score", 0.0) or 0.0)
        except Exception:
            s = 0.0
        scored.append((s, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    k = max(1, int(len(scored) * frac))
    top = [r for _, r in scored[:k]]
    bot = [r for _, r in scored[-k:]]
    return top, bot

def tag_hist(rows: List[Dict[str, Any]]) -> Counter:
    c = Counter()
    for r in rows:
        tj = r.get("tags_json", "{}") or "{}"
        try:
            tags = json.loads(tj)
        except Exception:
            tags = {}
        for key in TAG_KEYS:
            if key in tags:
                c[f"{key}={tags[key]}"] += 1
    return c
