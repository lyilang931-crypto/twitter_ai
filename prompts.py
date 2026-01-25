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

def build_prompt_all(topic: str, trend_hint: str, n_each: int) -> str:
    """
    1回のAPIで MAIN/SUB/EXP をまとめて生成するプロンプト。
    出力は必ずJSONのみ:
      {"MAIN":[...], "SUB":[...], "EXP":[...]}
    """
    trend = f"トレンド: {trend_hint}" if trend_hint else "トレンド: なし"

    return (
        f"X投稿作成。テーマ:{topic} / {trend}\n"
        f"{STYLE_CORE}\n{SAFETY_CORE}\n"
        "制約:\n"
        "・1文=1ツイ\n"
        "・改行なし\n"
        "・140字以内\n"
        "・短すぎ禁止(目安60字以上。ただし意図的短文はOK)\n"
        "役割:\n"
        "MAIN: 否定×断定。刺さる結論→理由を一息で。\n"
        "SUB: 否定×数字/比較。具体。\n"
        "EXP: 質問×逆説。分散狙い(上振れ)。\n"
        "出力ルール:\n"
        "・JSONのみ返す(説明禁止/コードブロック禁止)\n"
        "・必ず有効なJSONとして閉じる\n"
        f'{{"MAIN":["..."],"SUB":["..."],"EXP":["..."]}}\n'
        f"各配列は{int(n_each)}件。\n"
    )