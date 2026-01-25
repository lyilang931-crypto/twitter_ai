# novelty.py
from __future__ import annotations
import re
from typing import List, Dict, Any
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

def _norm(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s.replace("！","!").replace("？","?")

def novelty_score(text: str, rows: List[Dict[str, Any]], window: int = 300) -> float:
    t = _norm(text)
    past = [_norm(r.get("text","")) for r in rows if r.get("text")]
    if not past:
        return 1.0
    past = past[-window:]
    corpus = past + [t]
    vec = TfidfVectorizer(analyzer="char", ngram_range=(3,5), min_df=1)
    X = vec.fit_transform(corpus)
    sims = cosine_similarity(X[-1], X[:-1]).ravel()
    best = float(np.max(sims)) if sims.size else 0.0
    return max(0.0, min(1.0, 1.0 - best))