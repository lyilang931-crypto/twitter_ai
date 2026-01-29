# replay.py — 優先度付きリプレイ（失敗も再学習）
from __future__ import annotations

from typing import List, Dict, Any

def engagement_score_from_row(row: Dict[str, Any]) -> float:
    """確定スコアまたは簡易エンゲージメントを0..1で返す。"""
    try:
        s = row.get("確定") or row.get("engagement") or "0"
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def priority_for_replay(row: Dict[str, Any]) -> float:
    """優先度: 低評価・失敗ほど高く（再学習で当たり率向上）。"""
    eng = engagement_score_from_row(row)
    return max(0.0, 1.0 - eng)


def sample_for_learning(
    rows: List[Dict[str, Any]],
    k: int = 50,
    recent_first: int = 20,
) -> List[Dict[str, Any]]:
    """
    学習用サンプル: 直近 recent_first 件 + 優先度付きで過去から k 件。
    失敗・低評価を優先してサンプルし、ローカルで重み更新に使う。
    """
    if not rows:
        return []
    n = len(rows)
    # 直近は必ず含める
    recent = rows[-recent_first:] if n >= recent_first else rows
    pool = rows[:-recent_first] if n > recent_first else []
    # 優先度でソート（低い確定＝失敗に近い＝優先度高）
    with_priority = [(priority_for_replay(r), r) for r in pool]
    with_priority.sort(key=lambda x: -x[0])  # 優先度降順
    rest_k = max(0, k - len(recent))
    sampled_rest = [r for _, r in with_priority[:rest_k]]
    return recent + sampled_rest
