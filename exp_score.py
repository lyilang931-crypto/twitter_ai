# exp_score.py
from __future__ import annotations

NEG = ["違う", "間違い", "無理", "ダメ", "危険", "捨てろ", "やめろ"]
ASS = ["結論", "本質", "正解", "原因", "事実", "結局", "断言"]
CON = ["でも", "しかし", "逆に", "実は", "なのに", "一方"]
QMK = ["？", "?"]

def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))

def _has(text: str, words) -> bool:
    return any(w in text for w in words)

def _digits(text: str) -> int:
    return sum(ch.isdigit() for ch in text)

def polarity_score(text: str) -> float:
    s = 0.0
    if _has(text, NEG): s += 0.35
    if _has(text, ASS): s += 0.25
    if _has(text, CON): s += 0.20
    return clamp01(s)

def surprise_score(text: str) -> float:
    s = 0.0
    if _has(text, ["実は", "多くの人", "みんな誤解", "逆"]): s += 0.45
    if _has(text, CON): s += 0.25
    if _has(text, ["才能じゃない", "根性じゃない", "努力じゃない"]): s += 0.30
    return clamp01(s)

def specificity_score(text: str) -> float:
    d = _digits(text)
    s = 0.0
    if d >= 1: s += 0.35
    if d >= 2: s += 0.15
    if _has(text, ["例えば", "たとえば", "例"]): s += 0.25
    if _has(text, ["3つ", "1つ", "二択", "上位", "下位"]): s += 0.25
    return clamp01(s)

def memorability_score(text: str) -> float:
    n = len(text)
    s = 0.0
    if 60 <= n <= 120: s += 0.45
    elif 45 <= n <= 140: s += 0.30
    if text.count("、") <= 3: s += 0.15
    if _has(text[-8:], ["。", "だ", "です", "結局"]): s += 0.25
    return clamp01(s)

def tension_score(text: str) -> float:
    s = 0.0
    if any(q in text for q in QMK): s += 0.55
    if _has(text, ["なぜ", "理由", "唯一", "たった1つ"]): s += 0.30
    return clamp01(s)

def tail_score(text: str) -> float:
    return clamp01(
        0.25 * polarity_score(text) +
        0.20 * surprise_score(text) +
        0.20 * specificity_score(text) +
        0.20 * memorability_score(text) +
        0.15 * tension_score(text)
    )

def exp_utility(text: str, novelty: float, safety: float) -> float:
    if safety <= 0.0:
        return 0.0
    ts = tail_score(text)
    return clamp01(0.55 * ts + 0.25 * novelty + 0.20 * safety)