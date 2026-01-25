# exp_score.py
from __future__ import annotations
import re

NEG = ["違う","間違い","無理","ダメ","捨てろ","やめろ"]
ASSERT = ["結論","本質","正解","原因","事実","結局"]
CONTRAST = ["でも","しかし","逆に","実は","なのに"]
TENSION = ["なぜ","理由","唯一","たった","本当は"]
QMARK = ["?","？"]

def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))

def tail_score(text: str) -> float:
    t = text or ""
    s = 0.0
    if any(w in t for w in NEG): s += 0.22
    if any(w in t for w in ASSERT): s += 0.18
    if any(w in t for w in CONTRAST): s += 0.22
    if any(w in t for w in TENSION): s += 0.18
    if any(w in t for w in QMARK): s += 0.20
    # 長さの“刺さる帯”を評価（短すぎ/長すぎを下げる）
    n = len(t)
    if 60 <= n <= 120: s += 0.20
    elif 45 <= n <= 140: s += 0.10
    return clamp01(s)

def exp_utility(tail: float, novelty: float, safety: float) -> float:
    # safetyは0/1想定、0なら即死
    if safety <= 0.0:
        return 0.0
    return clamp01(0.60 * tail + 0.25 * novelty + 0.15 * safety)