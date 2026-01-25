# self_play.py
from __future__ import annotations
from typing import List, Dict, Any
from novelty import novelty_score
from safety import safety_score_01
from exp_score import exp_utility
from pseudo_reward import pseudo_score

def rank_200(
    candidates: List[str],
    rows: List[Dict[str, Any]],
    weights: Dict[str, float],
    role: str
) -> List[Dict[str, Any]]:
    ranked = []
    for t in candidates:
        text = (t or "").strip()
        if not text:
            continue

        saf = safety_score_01(text)
        nov = novelty_score(text, rows=rows, window=300) if rows else 1.0
        pse = pseudo_score(text, weights) if saf > 0 else 0.0

        # role別スコアリング
        if role == "EXP":
            # 分散最大化（上振れ探索）
            score = 0.30 * pse + 0.70 * exp_utility(text, novelty=nov, safety=saf)
        else:
            # 安定最大化
            score = 0.80 * pse + 0.20 * nov  # 焼き直し回避を少しだけ入れる

        ranked.append({
            "text": text,
            "pseudo_score": float(pse),
            "novelty": float(nov),
            "safety": float(saf),
            "selfplay_score": float(score),
        })

    ranked.sort(key=lambda x: x["selfplay_score"], reverse=True)
    return ranked