# vocabulary_diversity.py — 直近ツイートの語彙頻度に基づく多様性ペナルティ（過学習語の連呼抑制）
from __future__ import annotations
from typing import Dict, List, Any

# 過学習しやすい語（勝ちツイート学習・自己蒸留で偏りがち）
OVERUSED_CANDIDATES = [
    "設計", "本質", "構造", "努力", "行動", "結論", "正解", "原因", "結局",
    "事実", "唯一", "本当は", "仕組み", "型", "判断",
]

# 同義語ローテーション用（プロンプトの「言い換え推奨」で使用）
SYNONYM_HINTS: Dict[str, List[str]] = {
    "設計": ["仕組み", "方針", "型", "判断基準"],
    "本質": ["核心", "根っこ", "要点"],
    "構造": ["仕組み", "枠", "流れ"],
    "努力": ["積み上げ", "やり方", "工夫"],
    "結論": ["着地", "答え", "まとめ"],
    "行動": ["動き", "一手", "やること"],
}


def recent_word_frequency(rows: List[Dict[str, Any]], n: int = 15) -> Dict[str, int]:
    """直近 n 件のツイート本文から、OVERUSED_CANDIDATES 各語の出現回数を集計する。"""
    recent = (rows or [])[-n:]
    freq: Dict[str, int] = {w: 0 for w in OVERUSED_CANDIDATES}
    for r in recent:
        text = (r.get("text") or "").strip()
        if not text:
            continue
        for w in OVERUSED_CANDIDATES:
            if w in text:
                freq[w] = freq.get(w, 0) + 1
    return freq


def get_overused_words(freq_map: Dict[str, int], threshold: int = 3) -> List[str]:
    """しきい値（直近N件中 threshold 回以上）を超えた語のリストを返す。"""
    return [w for w, c in (freq_map or {}).items() if c >= threshold]


def vocab_diversity_penalty(
    text: str,
    freq_map: Dict[str, int],
    threshold: int = 3,
    penalty_per_word: float = 0.08,
    cap: float = 0.25,
) -> float:
    """
    候補本文が「直近で過多になった語」を含む場合にペナルティを返す。
    完全禁止ではなく、含むごとに減点（最大 cap）。
    """
    if not text or not freq_map:
        return 0.0
    penalty = 0.0
    for w, count in freq_map.items():
        if count >= threshold and w in text:
            penalty += penalty_per_word
            if penalty >= cap:
                return cap
    return min(penalty, cap)


def format_synonym_hint(overused_words: List[str]) -> str:
    """プロンプト用の言い換え推奨文を1行で返す。過多語が無い場合は空文字。"""
    if not overused_words:
        return ""
    parts = []
    for w in overused_words[:5]:  # 最大5語
        syns = SYNONYM_HINTS.get(w, [])
        if syns:
            parts.append(f"{w}→{'/'.join(syns[:3])}")
    if not parts:
        return ""
    return "直近頻出のため言い換え推奨: " + "、".join(parts)
