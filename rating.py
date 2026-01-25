# rating.py
from __future__ import annotations

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def update_abs_rating(r: float, final_score: float, baseline: float = 0.5, k: float = 16.0) -> float:
    # baseline=0.5（普通）より上なら上がる
    diff = float(final_score) - float(baseline)
    return r + k * diff

def update_rel_rating(r: float, final_score: float, self_baseline: float = 0.5, k: float = 16.0) -> float:
    diff = float(final_score) - float(self_baseline)
    return r + k * diff

def final_score_from_metrics(impr:int, likes:int, rts:int, replies:int, fol_before:int, fol_after:int) -> float:
    """
    確定スコア（0..1）：フォロー増を最重視、次にRT、いいね
    """
    impr = max(1, int(impr))
    like_rate = likes / impr
    rt_rate = rts / impr
    reply_rate = replies / impr
    fol_gain = max(0, fol_after - fol_before)

    # フォロー増はスケールが違うので飽和関数へ
    fol_term = 1.0 - (2.71828 ** (-fol_gain / 3.0))  # 3人で~0.63

    s = (
        0.55 * fol_term +
        0.25 * (1.0 - (2.71828 ** (-rt_rate * 120))) +
        0.15 * (1.0 - (2.71828 ** (-like_rate * 60))) +
        0.05 * (1.0 - (2.71828 ** (-reply_rate * 80)))
    )
    return clamp(s, 0.0, 1.0)