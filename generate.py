# generate.py
import json
from llm_gemini import gemini_generate

def _extract_json(raw: str) -> dict:
    s = raw.find("{")
    e = raw.rfind("}")
    if s == -1 or e == -1 or e <= s:
        raise ValueError(f"JSON not found in response:\n{raw}")
    return json.loads(raw[s:e+1])

def generate_daily_pack(
    api_key: str,
    topic: str,
    per_role_n: int = 5,
    use_gemini: bool = True,
    model: str = "gemini-2.5-flash",
):
    # Geminiを使わない場合のフォールバック（必要なら後で実装）
    if not use_gemini:
        raise RuntimeError("use_gemini=False is not supported yet")

    prompt = f"""
あなたはX(Twitter)の投稿文を作るプロです。
テーマ: {topic}

次のJSONだけを返してください（説明文は禁止、コードブロック禁止）:
{{
  "MAIN": ["..."],  // 朝(7-9) 本命：否定×断定
  "SUB":  ["..."],  // 昼(12-13) 準本命：否定×数字
  "EXP":  ["..."]   // 夜(20-22) 実験：質問×逆説
}}

制約:
- 各配列は {per_role_n} 件
- 各ツイートは 120文字以内
- 絵文字は使わない
- 出力は必ず有効なJSONとして閉じる（末尾の括弧まで）
"""

    raw = gemini_generate(
        prompt,
        api_key=api_key,
        model=model,
        max_output_tokens=1600,
        temperature=0.7,
    )

    data = _extract_json(raw)
    for k in ["MAIN", "SUB", "EXP"]:
        if k not in data or not isinstance(data[k], list):
            raise ValueError(f"Invalid JSON schema. Missing {k}.\n{raw}")

    def block(role, role_label, time_slot, time_slot_label, intent, texts):
        return {
            "role": role,
            "role_label": role_label,
            "time_slot": time_slot,
            "time_slot_label": time_slot_label,
            "intent": intent,
            "candidates": [str(t).strip() for t in texts if str(t).strip()],
        }

    return [
        block("MAIN", "本命（勝ちに行く）", "AM", "朝（7-9時）", "否定×断定", data["MAIN"]),
        block("SUB",  "準本命（微調整）", "NOON", "昼（12-13時）", "否定×数字", data["SUB"]),
        block("EXP",  "実験（学習）", "PM", "夜（20-22時）", "質問×逆説", data["EXP"]),
    ]