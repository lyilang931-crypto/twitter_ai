# scoring.py
from __future__ import annotations
import math
from typing import Dict

def pseudo_reward_components(text: str, novelty: float, safety: float, tail: float) -> Dict[str, float]:
    """
    擬似報酬のための特徴量（0..1）
    """
    text = (text or "").strip()
    n = len(text)

    # 長さ評価：短すぎで刺さらない問題を抑える
    if 55 <= n <= 140:
        length_ok = 1.0
    elif 40 <= n <= 160:
        length_ok = 0.6
    else:
        length_ok = 0.2

    # 断定感（簡易）
    assertive = 1.0 if any(x in text for x in ["結論", "本質", "正解", "原因", "結局", "だ。", "だ"]) else 0.6

    return {
        "novelty": float(novelty),
        "safety": float(safety),
        "tail": float(tail),
        "length_ok": float(length_ok),
        "assertive": float(assertive),
    }

def pseudo_score(components: Dict[str, float], w: Dict[str, float]) -> float:
    """
    線形 → シグモイドで0..1
    """
    z = 0.0
    for k, v in components.items():
        z += float(w.get(k, 0.0)) * float(v)
    z += float(w.get("bias", 0.0))
    return 1.0 / (1.0 + math.exp(-z))

def fast_score(impr: int, likes: int, rts: int, replies: int) -> float:
    """
    速報スコア（早期に取れる指標だけ）
    """
    x = (
        0.35 * math.log1p(max(0, impr)) +
        0.30 * math.log1p(max(0, likes)) +
        0.25 * math.log1p(max(0, rts)) +
        0.10 * math.log1p(max(0, replies))
    )
    return max(0.0, min(1.0, x / 6.5))

def final_score(impr: int, likes: int, rts: int, replies: int, fol_before: int, fol_after: int) -> float:
    """
    確定スコア（フォロワー増を重視）
    """
    delta = max(0, fol_after - fol_before)
    x = (
        0.25 * math.log1p(max(0, impr)) +
        0.20 * math.log1p(max(0, likes)) +
        0.20 * math.log1p(max(0, rts)) +
        0.10 * math.log1p(max(0, replies)) +
        0.25 * math.log1p(delta)
    )
    return max(0.0, min(1.0, x / 6.0))

def quality_score(text: str, topic: str = "", trend_hint: str = "") -> float:
    """品質スコア（伸びやすさ）を算出する（ルールベース）。0.0..1.0。

    加点:
    - 冒頭フック（疑問/逆説/短文）
    - 二人称「あなた」含有（過多は減点）
    - 適切な文長（60〜130字近辺）
    減点:
    - 冗長/抽象語連発（例「設計」「本質」が2回以上）
    - 改行が多い
    - 文長が短すぎ/長すぎ
    - 必須キーワード未達（topic/trendの語が無い）
    """
    t = (text or "").strip()
    if not t:
        return 0.0

    score = 0.5  # baseline

    n = len(t)

    # --- 文長評価 ---
    if 60 <= n <= 130:
        score += 0.10  # sweet spot
    elif 55 <= n <= 140:
        score += 0.05
    elif n < 30:
        score -= 0.15
    elif n > 140:
        score -= 0.10

    # --- 冒頭フック ---
    first_10 = t[:10]
    # 疑問で始まる
    if any(c in first_10 for c in ["？", "?", "なぜ", "どう", "何"]):
        score += 0.08
    # 逆説で始まる
    if any(w in t[:15] for w in ["でも", "しかし", "ただ", "実は", "本当は", "むしろ"]):
        score += 0.06
    # 短文冒頭（最初の句点が20字以内）
    first_period = -1
    for i, c in enumerate(t):
        if c in ["。", "！", "？", ".", "!"]:
            first_period = i
            break
    if 0 < first_period <= 20:
        score += 0.05

    # --- 二人称「あなた」---
    anata_count = t.count("あなた")
    if anata_count == 1:
        score += 0.06
    elif anata_count >= 3:
        score -= 0.05  # 過多

    # --- CTA（行動喚起）---
    if any(w in t for w in ["やってみて", "試して", "考えてみて", "始めよう", "やめよう", "手放そう"]):
        score += 0.05

    # --- 抽象語連発ペナルティ ---
    abstract_words = ["設計", "本質", "構造", "努力", "結論", "結局", "正解", "原因"]
    abstract_count = sum(t.count(w) for w in abstract_words)
    if abstract_count >= 3:
        score -= 0.10
    elif abstract_count >= 2:
        score -= 0.04

    # --- 同一語2回以上ペナルティ ---
    for w in abstract_words:
        if t.count(w) >= 2:
            score -= 0.06
            break

    # --- 改行ペナルティ ---
    newline_count = t.count("\n")
    if newline_count >= 2:
        score -= 0.05

    # --- 必須キーワード評価 ---
    keywords = []
    if topic:
        keywords.append(topic.strip())
    if trend_hint:
        for sep in [",", " ", "　", "・", "、"]:
            for part in trend_hint.split(sep):
                p = part.strip()
                if len(p) >= 2:
                    keywords.append(p)
    if keywords:
        has_any = any(kw in t for kw in keywords)
        if has_any:
            score += 0.05
        else:
            score -= 0.05

    return max(0.0, min(1.0, round(score, 3)))


def diversity_penalty(text: str, other_texts: list) -> float:
    """同一バッチ内での候補間の多様性ペナルティ。類似候補が多いほど減点。"""
    if not other_texts or not text:
        return 0.0
    t = (text or "").strip()
    overlap_count = 0
    for other in other_texts:
        o = (other or "").strip()
        if not o or o == t:
            continue
        # 冒頭20文字が一致 → 重複感
        if t[:20] == o[:20]:
            overlap_count += 1
    return min(0.15, overlap_count * 0.05)


# 互換用（日本語名で呼んでる箇所があっても壊れないように）
速報_score = fast_score
確定_score = final_score