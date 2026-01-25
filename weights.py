# weights.py
from __future__ import annotations
import json, os
from typing import Dict

DEFAULT_W = {
    "bias": -1.2,
    "novelty": 1.2,
    "safety": 2.0,
    "tail": 1.6,
    "length_ok": 0.9,
    "assertive": 0.7,
}

def load_weights(path: str) -> Dict[str, float]:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return dict(DEFAULT_W)

def save_weights(path: str, w: Dict[str, float]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(w, f, ensure_ascii=False, indent=2)

def sgd_update(w: Dict[str, float], x: Dict[str, float], y_true: float, y_pred: float, lr: float = 0.35, l2: float = 0.0005):
    # logistic回帰のSGDっぽく更新（簡易）
    err = (y_true - y_pred)
    for k, v in x.items():
        w[k] = float(w.get(k, 0.0) + lr * err * float(v) - l2 * w.get(k, 0.0))
    w["bias"] = float(w.get("bias", 0.0) + lr * err - l2 * w.get("bias", 0.0))
    return w
