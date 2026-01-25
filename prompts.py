# prompts.py
from __future__ import annotations

STYLE_CORE = (
    "口調: 断定/短文/構造/冷酷に真実。"
    "努力より設計。最後は行動1つ or 問い。"
    "余計な絵文字/前置き/箇条書き/番号禁止。"
)

SAFETY_CORE = (
    "禁止: 個人情報(固有名詞,地名,勤務先,学校,住所,予定,連絡先),"
    "政治/宗教/差別/誹謗中傷/個人攻撃/過激煽り。"
)

def build_prompt_all(topic: str, trend_hint: str, n: int, role: str) -> str:
    # TPM250を守るため短い。出力はJSON限定。
    # role別に“尖り方”を変える
    if role == "MAIN":
        intent = "否定×断定。刺さる結論→理由を一息で。"
    elif role == "SUB":
        intent = "否定×数字/比較。具体。"
    else:
        intent = "質問×逆説。分散狙い(上振れ)。"

    trend = f"トレンド: {trend_hint}" if trend_hint else "トレンド: なし"

    return (
        f"X投稿作成。テーマ:{topic} / {trend}\n"
        f"{STYLE_CORE}\n{SAFETY_CORE}\n"
        f"制約: 1文=1ツイ。改行なし。140字以内。短すぎ禁止(目安60字以上,ただし意図的短文OK)。\n"
        f"狙い({role}): {intent}\n"
        f"JSONのみ返す。説明禁止。schema厳守:\n"
        f'{{"{role}":[ "...", "...", "..."]}}\n'
        f"{role}は{n}件。必ずJSONを閉じる。"
    )