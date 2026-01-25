# novelty.py
from __future__ import annotations
import re
from collections import Counter
from typing import List, Dict, Any

def _normalize(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"\s+", " ", t)
    # よくある表記ゆれ軽く吸収（必要なら増やしてOK）
    t = t.replace("，", ",").replace("．", ".").replace("　", " ")
    return t

def _char_ngrams(text: str, n: int = 3) -> Counter:
    text = _normalize(text)
    if len(text) < n:
        return Counter([text]) if text else Counter()
    grams = [text[i:i+n] for i in range(len(text) - n + 1)]
    return Counter(grams)

def _cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    # dot
    dot = 0.0
    for k, v in a.items():
        dot += v * b.get(k, 0)
    # norms
    na = sum(v*v for v in a.values()) ** 0.5
    nb = sum(v*v for v in b.values()) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return float(dot / (na * nb))

def novelty_score(text: str, rows: List[Dict[str, Any]], window: int = 300) -> float:
    """
    1.0 = 過去と全然被ってない（新規性高い）
    0.0 = 過去とほぼ同じ（新規性低い）
    """
    t = _normalize(text)
    past = [_normalize(r.get("text", "")) for r in rows if r.get("text")]
    if not past:
        return 1.0

    past = past[-window:]
    vec_t = _char_ngrams(t, n=3)

    best_sim = 0.0
    for p in past:
        sim = _cosine(vec_t, _char_ngrams(p, n=3))
        if sim > best_sim:
            best_sim = sim

    nov = 1.0 - best_sim
    # clamp
    if nov < 0.0: nov = 0.0
    if nov > 1.0: nov = 1.0
    return float(nov)