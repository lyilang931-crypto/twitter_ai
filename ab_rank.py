# ab_rank.py
import json
from collections import defaultdict
from typing import Dict, Any, List

def tag_key(tags: Dict[str, Any]) -> str:
    # 重要タグだけキー化
    keys = ["intent", "hook_type", "sentence_len", "has_question", "cta"]
    parts = []
    for k in keys:
        parts.append(f"{k}={tags.get(k)}")
    return "|".join(parts)

def build_priors(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    """
    tags_keyごとに平均スコアを学習（軽量な事前分布）
    """
    sums = defaultdict(float)
    cnts = defaultdict(int)

    for r in rows:
        k = r.get("tags_key", "") or ""
        if not k:
            continue
        try:
            s = float(r.get("tweet_score", 0.0) or 0.0)
        except Exception:
            s = 0.0
        sums[k] += s
        cnts[k] += 1

    pri = {}
    for k in sums:
        pri[k] = {"mean": sums[k] / max(1, cnts[k]), "n": float(cnts[k])}
    return pri

def rank_candidates(
    candidates: List[Dict[str, Any]],
    priors: Dict[str, Dict[str, float]],
    global_mean: float = 0.02,
    alpha: float = 5.0,
) -> List[Dict[str, Any]]:
    """
    expected = (alpha*global_mean + n*prior_mean)/(alpha+n)
    """
    ranked = []
    for c in candidates:
        k = tag_key(c.get("tags", {}))
        prior = priors.get(k)
        if prior:
            n = prior["n"]
            m = prior["mean"]
            exp = (alpha * global_mean + n * m) / (alpha + n)
        else:
            exp = global_mean
        out = dict(c)
        out["expected_score"] = float(exp)
        ranked.append(out)

    ranked.sort(key=lambda x: x["expected_score"], reverse=True)
    return ranked