import json
from collections import defaultdict
from typing import List, Dict, Any

KEYS = ["intent","hook_type","sentence_len","has_question","cta"]

def tag_key(tags: Dict[str, Any]) -> str:
    compact = {k: tags.get(k) for k in KEYS}
    return json.dumps(compact, ensure_ascii=False, sort_keys=True)

def build_priors(rows: List[Dict[str, Any]]):
    sums = defaultdict(float)
    cnts = defaultdict(int)
    for r in rows:
        tj = r.get("tags_key","") or r.get("tags_json","") or ""
        if not tj:
            continue
        try:
            s = float(r.get("tweet_score", 0.0) or 0.0)
        except Exception:
            s = 0.0
        sums[tj] += s
        cnts[tj] += 1
    priors = {}
    for k in cnts:
        priors[k] = {"count": cnts[k], "mean": sums[k]/cnts[k]}
    return priors

def expected_score(tags: Dict[str, Any], priors, global_mean: float, alpha: float = 5.0) -> float:
    k = tag_key(tags)
    if k not in priors:
        return global_mean
    c = priors[k]["count"]
    m = priors[k]["mean"]
    # スムージング（データ少の暴れ防止）
    return (m * c + global_mean * alpha) / (c + alpha)

def rank_candidates(candidates: List[Dict[str, Any]], priors, global_mean: float, alpha: float = 5.0):
    ranked = []
    for c in candidates:
        exp = expected_score(c["tags"], priors, global_mean, alpha=alpha)
        ranked.append({**c, "expected_score": exp, "tags_key": tag_key(c["tags"])})
    ranked.sort(key=lambda x: x["expected_score"], reverse=True)
    return ranked
