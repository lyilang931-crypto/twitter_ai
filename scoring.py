# scoring.py
from __future__ import annotations
import math
from typing import Dict

def pseudo_reward_components(text: str, novelty: float, safety: float, tail: float) -> Dict[str, float]:
    """
    擬似報酬のための特徴量（0..1）
    """
    text = (text or "").strip()
    n = len(text)

    # 長さ評価：短すぎで刺さらない問題を抑える
    if 55 <= n <= 140:
        length_ok = 1.0
    elif 40 <= n <= 160:
        length_ok = 0.6
    else:
        length_ok = 0.2

    # 断定感（簡易）
    assertive = 1.0 if any(x in text for x in ["結論", "本質", "正解", "原因", "結局", "だ。", "だ"]) else 0.6

    return {
        "novelty": float(novelty),
        "safety": float(safety),
        "tail": float(tail),
        "length_ok": float(length_ok),
        "assertive": float(assertive),
    }

def pseudo_score(components: Dict[str, float], w: Dict[str, float]) -> float:
    """
    線形 → シグモイドで0..1
    """
    z = 0.0
    for k, v in components.items():
        z += float(w.get(k, 0.0)) * float(v)
    z += float(w.get("bias", 0.0))
    return 1.0 / (1.0 + math.exp(-z))

def fast_score(impr: int, likes: int, rts: int, replies: int) -> float:
    """
    速報スコア（早期に取れる指標だけ）
    """
    x = (
        0.35 * math.log1p(max(0, impr)) +
        0.30 * math.log1p(max(0, likes)) +
        0.25 * math.log1p(max(0, rts)) +
        0.10 * math.log1p(max(0, replies))
    )
    return max(0.0, min(1.0, x / 6.5))

def final_score(impr: int, likes: int, rts: int, replies: int, fol_before: int, fol_after: int) -> float:
    """
    確定スコア（フォロワー増を重視）
    """
    delta = max(0, fol_after - fol_before)
    x = (
        0.25 * math.log1p(max(0, impr)) +
        0.20 * math.log1p(max(0, likes)) +
        0.20 * math.log1p(max(0, rts)) +
        0.10 * math.log1p(max(0, replies)) +
        0.25 * math.log1p(delta)
    )
    return max(0.0, min(1.0, x / 6.0))

# 互換用（日本語名で呼んでる箇所があっても壊れないように）
速報_score = fast_score
確定_score = final_score
