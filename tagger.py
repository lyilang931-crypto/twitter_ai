import re

def guess_tags(text: str):
    t = text.strip()

    # intent
    if "?" in t or "？" in t:
        intent = "質問"
    elif any(x in t for x in ["違う","ムダ","間違い","不要","罠","やめろ","やめた","危険"]):
        intent = "否定"
    elif any(x in t for x in ["結論","つまり","本質","原理","ルール","設計"]):
        intent = "主張"
    else:
        intent = "体験"

    # hook_type
    if re.search(r"\d", t):
        hook_type = "数字"
    elif any(x in t[:18] for x in ["でも","実は","逆に","ところが"]):
        hook_type = "逆説"
    elif intent == "否定":
        hook_type = "否定"
    else:
        hook_type = "断定"

    # sentence_len
    sents = [s for s in re.split(r"[。.!！?？\n]+", t) if s]
    avg_len = sum(len(s) for s in sents) / max(1, len(sents))
    sentence_len = "短" if avg_len <= 18 else "中" if avg_len <= 32 else "長"

    # cta
    if any(x in t for x in ["保存","試して","やってみて","コメント","教えて","RT"]):
        cta = "行動"
    else:
        cta = "なし"

    return {
        "intent": intent,
        "hook_type": hook_type,
        "sentence_len": sentence_len,
        "has_question": ("?" in t or "？" in t),
        "cta": cta,
    }
