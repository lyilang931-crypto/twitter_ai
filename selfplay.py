# selfplay.py
from __future__ import annotations
import random
from typing import List, Dict, Any, Tuple

def league_score(cands: List[Dict[str, Any]], rounds: int = 200) -> List[Dict[str, Any]]:
    """
    200局の“比較対局”をローカルで回す（APIゼロ）
    各候補の疑似スコア（pseudo）を勝率に変換して対戦
    """
    if not cands:
        return cands

    # 初期
    for c in cands:
        c["wins"] = 0
        c["games"] = 0

    n = len(cands)
    for _ in range(rounds):
        a, b = random.sample(range(n), 2)
        A, B = cands[a], cands[b]
        pa = float(A.get("pseudo", 0.0))
        pb = float(B.get("pseudo", 0.0))
        # 擬似スコアが高いほど勝ちやすい（温度）
        pwin = 0.5 + 0.45 * (pa - pb)
        pwin = max(0.05, min(0.95, pwin))
        if random.random() < pwin:
            A["wins"] += 1
        else:
            B["wins"] += 1
        A["games"] += 1
        B["games"] += 1

    for c in cands:
        g = max(1, int(c["games"]))
        c["league"] = float(c["wins"]) / g

    cands.sort(key=lambda x: (x.get("league", 0.0), x.get("pseudo", 0.0)), reverse=True)
    return cands