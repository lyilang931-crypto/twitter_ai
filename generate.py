# generate.py
import json
from llm_gemini import gemini_generate

def _extract_json(raw: str) -> dict:
    s = raw.find("{")
    e = raw.rfind("}")
    if s == -1 or e == -1 or e <= s:
        raise ValueError(f"JSON not found:\n{raw}")
    body = raw[s:e+1]
    return json.loads(body)

def generate_daily_pack(
    api_key: str,
    topic: str,
    trend_context: str = "",
    per_role_n: int = 5,
    model: str = "gemini-flash-latest",
):
    trend_block = ""
    tc = (trend_context or "").strip()
    if tc:
        trend_block = f"""
【トレンド情報（最優先で活用）】
{tc}
""".strip()

    prompt = f"""
あなたはX(Twitter)の投稿文を作るプロです。

テーマ:
{topic}

{trend_block}

次のJSONだけを返してください（説明文は禁止、コードブロック禁止、前置き禁止）:
{{
  "MAIN": ["..."],  // 朝(7-9) 本命：否定×断定（刺す）
  "SUB":  ["..."],  // 昼(12-13) 準本命：否定×数字（冷静に分解）
  "EXP":  ["..."]   // 夜(20-22) 実験：質問×逆説（学習用）
}}

制約（絶対）:
- 各配列は {per_role_n} 件
- 各ツイートは 120文字以内
- 絵文字は使わない
- 固有名詞/住所/勤務先/学校/予定/連絡先など個人特定情報は禁止
- 政治/宗教/差別/誹謗中傷/攻撃語/過激煽りは禁止
- 「今っぽさ」は、流行語の羅列ではなく“切り口”で表現
- 出力は必ず有効なJSONとして閉じる（末尾の括弧まで）
""".strip()

    raw = gemini_generate(
        prompt,
        api_key=api_key,
        model=model,
        max_output_tokens=1800,
        temperature=0.75,
    )

    data = _extract_json(raw)
    for k in ["MAIN", "SUB", "EXP"]:
        if k not in data or not isinstance(data[k], list):
            raise ValueError(f"Invalid schema. Missing {k}.\n{raw}")

    def block(role, role_label, time_slot, time_slot_label, intent, texts):
        cleaned = []
        for t in texts:
            s = str(t).strip().replace("\n", " ")
            if 0 < len(s) <= 140:  # 念のため140まで許容
                cleaned.append(s)
        return {
            "role": role,
            "role_label": role_label,
            "time_slot": time_slot,
            "time_slot_label": time_slot_label,
            "intent": intent,
            "candidates": cleaned[:per_role_n],
        }

    return [
        block("MAIN", "本命（勝ちに行く）", "AM", "朝（7-9時）", "否定×断定", data["MAIN"]),
        block("SUB",  "準本命（微調整）", "NOON", "昼（12-13時）", "否定×数字", data["SUB"]),
        block("EXP",  "実験（学習）", "PM", "夜（20-22時）", "質問×逆説", data["EXP"]),
    ]