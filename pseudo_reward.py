# pseudo_reward.py
from __future__ import annotations
import math
from typing import Dict

def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))

def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))

def pseudo_features(text: str) -> Dict[str, float]:
    t = (text or "").strip()
    n = len(t)

    # 読みやすさ（短文・断定・構造寄りを優遇）
    length_good = 1.0 if 60 <= n <= 120 else (0.7 if n <= 140 else 0.2)

    # 断定感
    assertive = 1.0 if any(w in t for w in ["結論", "本質", "正解", "原因", "結局"]) else 0.6

    # 行動誘導（あなたっぽさ：最後に行動1つ）
    cta = 1.0 if any(w in t for w in ["やれ", "捨てろ", "やめろ", "決めろ", "今日", "今"]) else 0.6

    # 経済/起業寄り（あなたの軸）
    biz = 1.0 if any(w in t for w in ["起業", "ビジネス", "利益", "売上", "市場", "投資", "時間", "期待値"]) else 0.7

    # 摩擦（問い・逆説）
    tension = 1.0 if ("？" in t or "?" in t or "実は" in t or "逆に" in t) else 0.6

    return {
        "length_good": length_good,
        "assertive": assertive,
        "cta": cta,
        "biz": biz,
        "tension": tension,
    }

def pseudo_score(text: str, w: Dict[str, float]) -> float:
    f = pseudo_features(text)
    # 線形→sigmoid（0..1）
    z = 0.0
    for k, v in f.items():
        z += float(w.get(k, 0.0)) * float(v)
    # バイアス
    z += float(w.get("_bias", 0.0))
    return clamp01(sigmoid(z))