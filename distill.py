# distill.py — 成功ツイート自己蒸留（DeepMind型）
from __future__ import annotations

import re
from typing import Dict, Any, List

# 成功条件: フォロワー増 > 0 または likes/imp が閾値超え（設定で変更可）
SUCCESS_FOLLOWERS_DELTA_MIN = 0
SUCCESS_LIKES_PER_IMPR_MIN = 0.001  # likes/impressions >= これで成功とみなす


def is_success_row(
    row: Dict[str, Any],
    followers_delta_min: int = SUCCESS_FOLLOWERS_DELTA_MIN,
    likes_per_impr_min: float = SUCCESS_LIKES_PER_IMPR_MIN,
) -> bool:
    """1行が「成功」かどうか。"""
    try:
        fol_b = int(row.get("followers_before") or 0)
        fol_a = int(row.get("followers_after") or 0)
        impr = int(row.get("impressions") or 0)
        likes = int(row.get("likes") or 0)
    except (TypeError, ValueError):
        return False
    delta = fol_a - fol_b
    if delta > followers_delta_min:
        return True
    if impr > 0 and (likes / impr) >= likes_per_impr_min:
        return True
    return False


def extract_features(text: str) -> Dict[str, Any]:
    """
    成功ツイートから「型」を抽出（ローカル・ヒューリスティック、API使わない）。
    opening(掴み), assertiveness, length_band, structure, topic, CTA など。
    """
    text = (text or "").strip()
    n = len(text)

    # 長さ帯
    if n <= 50:
        length_band = "short"
    elif n <= 100:
        length_band = "medium"
    else:
        length_band = "long"

    # 掴み: 冒頭5〜15字が疑問/数字/結論か
    opening = "other"
    start = text[:15]
    if "?" in start or "？" in start or "なぜ" in start or "どう" in start:
        opening = "question"
    elif re.search(r"^[0-9０-９一二三四五六七八九十]+", start):
        opening = "number"
    elif any(x in start for x in ["結論", "本質", "正解", "原因", "結局", "だ。", "。"]):
        opening = "conclusion"

    # 断定感
    assertive = 1.0 if any(x in text for x in ["結論", "本質", "正解", "原因", "結局", "だ。", "だ"]) else 0.6

    # CTA有無（フォロー/RT/見て など）
    cta = 1.0 if any(x in text for x in ["フォロー", "RT", "見て", "チェック", "試して"]) else 0.0

    return {
        "opening": opening,
        "length_band": length_band,
        "assertive": assertive,
        "cta": cta,
        "length": n,
    }


def to_guideline_line(features: Dict[str, Any]) -> str:
    """特徴量を1行のガイドライン文言に。"""
    parts = []
    if features.get("opening") != "other":
        parts.append(f"掴み:{features['opening']}")
    parts.append(f"長さ:{features.get('length_band','medium')}")
    if features.get("assertive", 0) >= 0.8:
        parts.append("断定強め")
    if features.get("cta"):
        parts.append("CTAあり")
    return " / ".join(parts) if parts else "標準"
