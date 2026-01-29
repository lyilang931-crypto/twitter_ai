# weights.py — 重みの読込・保存（原子）・学習（ゲート・クリップ・replay）
from __future__ import annotations
import json
import os
import tempfile
from typing import Dict

DEFAULT_W = {
    "bias": -1.2,
    "novelty": 1.2,
    "safety": 2.0,
    "tail": 1.6,
    "length_ok": 0.9,
    "assertive": 0.7,
}

# 学習ゲート: インプレ未満は重み更新しない（初期ノイズ対策）
IMPRESSION_GATE = 200
# 更新幅クリップ
MAX_DELTA = 0.5


def load_weights(path: str) -> Dict[str, float]:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return dict(DEFAULT_W)


def save_weights(path: str, w: Dict[str, float]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(w, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


def _clip_delta(delta: float) -> float:
    return max(-MAX_DELTA, min(MAX_DELTA, delta))


def sgd_update(
    w: Dict[str, float],
    x: Dict[str, float],
    y_true: float,
    y_pred: float,
    lr: float = 0.35,
    l2: float = 0.0005,
) -> Dict[str, float]:
    """SGD更新（クリップ付き）。"""
    err = (y_true - y_pred)
    err = _clip_delta(err)
    for k, v in x.items():
        delta = lr * err * float(v) - l2 * w.get(k, 0.0)
        w[k] = float(w.get(k, 0.0) + _clip_delta(delta))
    w["bias"] = float(w.get("bias", 0.0) + _clip_delta(lr * err - l2 * w.get("bias", 0.0)))
    return w


def sgd_update_with_gate(
    w: Dict[str, float],
    x: Dict[str, float],
    y_true: float,
    y_pred: float,
    impressions: int,
    lr: float = 0.35,
    l2: float = 0.0005,
) -> tuple[Dict[str, float], bool]:
    """
    インプレゲート付き更新。impressions < IMPRESSION_GATE なら更新せず (w, False) を返す。
    更新した場合 (w, True)。
    """
    if impressions < IMPRESSION_GATE:
        return w, False
    return sgd_update(w, x, y_true, y_pred, lr=lr, l2=l2), True


