# weights.py
from __future__ import annotations
import json
from typing import Dict, Any, List
from pseudo_reward import pseudo_features

DEFAULT_W = {
    "_bias": -0.2,
    "length_good": 0.8,
    "assertive": 0.6,
    "cta": 0.5,
    "biz": 0.6,
    "tension": 0.4,
}

def load_weights(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    # CSVに保存していれば最新を読む（なければデフォルト）
    for r in reversed(rows):
        wj = r.get("weights_json", "")
        if wj:
            try:
                return json.loads(wj)
            except Exception:
                pass
    return dict(DEFAULT_W)

def update_weights_online(
    w: Dict[str, float],
    text: str,
    pseudo: float,
    final: float,
    lr: float = 0.15,
    l2: float = 0.002
) -> Dict[str, float]:
    """
    誤差 = final - pseudo
    w <- w + lr * error * feature - l2*w
    """
    f = pseudo_features(text)
    err = float(final) - float(pseudo)

    nw = dict(w)
    for k, fv in f.items():
        nw[k] = float(nw.get(k, 0.0)) + lr * err * float(fv) - l2 * float(nw.get(k, 0.0))
    nw["_bias"] = float(nw.get("_bias", 0.0)) + lr * err - l2 * float(nw.get("_bias", 0.0))

    # 暴走防止（クリップ）
    for k in list(nw.keys()):
        nw[k] = max(-3.0, min(3.0, float(nw[k])))
    return nw