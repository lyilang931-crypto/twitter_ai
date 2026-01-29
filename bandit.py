# bandit.py — 候補選択の Multi-Armed Bandit（Thompson Sampling）
from __future__ import annotations

import random
from typing import List, Dict, Any

def arm_id_from_cand(cand: Dict[str, Any]) -> str:
    """
    arm = (role, length_band, opening, cta).
    候補から arm_id 文字列を生成。
    """
    role = cand.get("role", "MAIN")
    text = (cand.get("text") or "").strip()
    n = len(text)
    if n <= 50:
        length_band = "short"
    elif n <= 100:
        length_band = "medium"
    else:
        length_band = "long"
    opening = "other"
    if text:
        start = text[:15]
        if "?" in start or "？" in start:
            opening = "question"
        elif any(x in start for x in ["結論", "本質", "正解", "原因", "結局"]):
            opening = "conclusion"
    cta = "1" if any(x in text for x in ["フォロー", "RT", "見て", "チェック"]) else "0"
    return f"{role}_{length_band}_{opening}_{cta}"


def thompson_sample(arm_id: str, pulls: int, rewards: float) -> float:
    """Beta(successes+1, failures+1) から1サンプル。rewards は累積報酬。"""
    if pulls <= 0:
        return 0.5  # 未プルは中立
    mean = rewards / pulls
    mean = max(0.0, min(1.0, mean))
    a = mean * pulls + 1.0
    b = (1.0 - mean) * pulls + 1.0
    try:
        return random.betavariate(a, b)
    except Exception:
        return mean


def select_arm_thompson(
    arms_state: Dict[str, Dict[str, Any]],
    arm_ids: List[str],
) -> str:
    """
    Thompson Sampling: 各 arm の Beta から1つサンプルし、最大の arm_id を返す。
    arms_state[arm_id] = {"pulls": n, "rewards": r}
    """
    if not arm_ids:
        return ""
    best_arm = arm_ids[0]
    best_sample = -1.0
    for aid in arm_ids:
        s = arms_state.get(aid, {"pulls": 0, "rewards": 0.0})
        p, r = s.get("pulls", 0), s.get("rewards", 0.0)
        sample = thompson_sample(aid, p, r)
        if sample > best_sample:
            best_sample = sample
            best_arm = aid
    return best_arm


def rank_candidates_by_bandit(
    candidates: List[Dict[str, Any]],
    arms_state: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    候補リストを bandit の期待値でソート（Thompson サンプルで1位を先頭に）。
    先頭が「選ばれた arm」に属する候補になるよう並べ替え。
    """
    if not candidates:
        return []
    arm_ids = list({arm_id_from_cand(c) for c in candidates})
    chosen_arm = select_arm_thompson(arms_state, arm_ids)
    # 選ばれた arm に属する候補を前に
    def key(c: Dict[str, Any]) -> tuple:
        aid = arm_id_from_cand(c)
        if aid == chosen_arm:
            return (0, -float(c.get("pseudo", 0)))
        return (1, -float(c.get("pseudo", 0)))
    return sorted(candidates, key=key)
