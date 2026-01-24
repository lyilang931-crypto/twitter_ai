# tagger.py
import re
from typing import Dict

def guess_tags(text: str) -> Dict:
    t = (text or "").strip()
    has_q = "?" in t or "？" in t
    # 文の長さ区分
    n = len(t)
    if n <= 60:
        sentence_len = "short"
    elif n <= 100:
        sentence_len = "mid"
    else:
        sentence_len = "long"

    # CTA推定
    cta = "none"
    if re.search(r"(やれ|しろ|やって|試して|保存|見直|今すぐ)", t):
        cta = "action"
    if has_q:
        cta = "question"

    # intent推定（超ラフ）
    intent = "assert"
    if has_q:
        intent = "question"
    if re.search(r"(違う|間違い|勘違い|やめろ|不要|無意味)", t):
        intent = "negation"

    hook_type = "断定"
    if re.search(r"(結論|要するに|本質|真実)", t):
        hook_type = "結論"
    if re.search(r"(数字|％|円|倍|年|月|日|万|億|兆)", t):
        hook_type = "数字"

    return {
        "intent": intent,
        "hook_type": hook_type,
        "sentence_len": sentence_len,
        "has_question": bool(has_q),
        "cta": cta,
    }