from typing import List, Dict
from llm_gemini import gemini_generate

SYSTEM_RULES = """
【絶対厳守】
- 個人情報・特定可能情報（固有名詞/地名/勤務先/学校/住所/予定/連絡先）を出さない
- 政治/宗教/差別/誹謗中傷/攻撃的表現/過激煽りは禁止
- 断定・短文・構造重視
- 1投稿は140字以内（目安：120字以内）
- 絵文字は原則なし（必要なら1つまで）
- 解説/前置き/番号/箇条書きは不要
"""

STYLE = "断定・短文・構造重視。最後は軽い問い or 行動。"

# 1日3局（役割固定）
TWEET_ROLES = [
    {"role": "MAIN", "label": "本命（勝ちに行く）", "intent": "否定×断定"},
    {"role": "SUB",  "label": "準本命（微調整）",   "intent": "否定×数字"},
    {"role": "EXP",  "label": "実験（学習）",       "intent": "質問×逆説"},
]

# 時間帯固定（対局条件）
TIME_SLOTS = [
    {"slot": "AM", "label": "朝（7-9時）"},
    {"slot": "NOON", "label": "昼（12-13時）"},
    {"slot": "PM", "label": "夜（20-22時）"},
]

def _prompt(topic: str, intent: str, n: int) -> str:
    return f"""
テーマ：{topic}
狙い：{intent}
口調：{STYLE}

ツイート案を{n}本。
【出力形式（厳守）】
- 各案は1行
- 空行区切り
- 番号・解説・見出しは禁止
""".strip()

def generate_candidates_gemini(
    api_key: str,
    topic: str,
    intent: str,
    n: int = 5,
    model: str = "gemini-3-flash"
) -> List[str]:
    prompt = SYSTEM_RULES + "\n\n" + _prompt(topic, intent, n)
    raw = gemini_generate(
        prompt,
        api_key=api_key,
        model=model,
        max_output_tokens=420,
        temperature=0.6
    )

    blocks = [b.strip("- \n") for b in raw.split("\n\n") if b.strip()]
    # 救済（空行区切りが崩れた場合）
    if len(blocks) < min(3, n):
        lines = [ln.strip("- \n") for ln in raw.splitlines() if ln.strip()]
        uniq = []
        seen = set()
        for ln in lines:
            if 0 < len(ln) <= 170 and ln not in seen:
                uniq.append(ln)
                seen.add(ln)
        blocks = uniq
    return blocks[:n]

def generate_candidates_free(topic: str, n: int = 5) -> List[str]:
    base = [
        f"{topic}で伸びない人は、努力を足す。正解は逆。",
        f"{topic}がうまくいかない原因は才能じゃない。設計がないだけ。",
        f"{topic}で結果が出ない人の共通点は「全部やろうとする」こと。",
        f"{topic}は根性じゃなく順番で決まる。",
        f"{topic}で迷う人は指標がない。",
    ]
    return base[:n]

def generate_daily_pack(
    api_key: str,
    topic: str,
    per_role_n: int = 5,
    use_gemini: bool = True,
    model: str = "gemini-3-flash"
) -> List[Dict]:
    """
    1日3ツイート（本命/準本命/実験）の候補セットを返す
    """
    pack = []
    for i, role in enumerate(TWEET_ROLES):
        slot = TIME_SLOTS[i]["slot"]
        slot_label = TIME_SLOTS[i]["label"]
        if use_gemini:
            texts = generate_candidates_gemini(
                api_key=api_key,
                topic=topic,
                intent=role["intent"],
                n=per_role_n,
                model=model
            )
        else:
            texts = generate_candidates_free(topic, n=per_role_n)

        pack.append({
            "role": role["role"],
            "role_label": role["label"],
            "time_slot": slot,
            "time_slot_label": slot_label,
            "intent": role["intent"],
            "candidates": texts
        })
    return pack
