# generate.py
from __future__ import annotations
import time
from typing import List, Dict, Any

from llm_gemini import gemini_generate_json

SYSTEM_RULES = """
【絶対厳守】
- 個人情報・特定可能情報（固有名詞/地名/勤務先/学校/住所/予定/連絡先）を出さない
- 政治/宗教/差別/誹謗中傷/個人攻撃/過激煽りは禁止
- 断定・短文・構造重視
- 絵文字は使わない
- 1投稿は140字以内（目安：120字以内）
- 出力は必ず有効なJSONで閉じる（説明文/コードブロック禁止）
"""

def build_voice(voice_guide: str) -> str:
    # あなたっぽさ（ただし個人特定はしない）
    base = """
【口調（あなたっぽさ）】
- 断定・短文・構造重視
- 根性より設計、足し算より引き算
- 期待値・再現性・時間＝命
- 最後は行動1つ、または軽い問いで締める
- テーマは経済・起業・成長・習慣・意思決定
""".strip()
    if voice_guide and voice_guide.strip():
        return base + "\n\n【追加の声】\n" + voice_guide.strip()
    return base

def _prompt(topic: str, n: int, role: str, intent: str, voice: str) -> str:
    return f"""
{SYSTEM_RULES}

{voice}

テーマ: {topic}
役割: {role}
狙い: {intent}

次のJSONだけを返す:
{{
  "tweets": ["...","..."]
}}

制約:
- tweetsは必ず {n} 件
- 各ツイートは140字以内
- 日本語
""".strip()

def generate_200(
    api_key: str,
    topic: str,
    model: str = "gemini-flash-latest",
    batch: int = 25,
    total: int = 200,
    min_interval_sec: float = 2.0,
    voice_guide: str = "",
    role: str = "EXP",
    intent: str = "質問×逆説（上振れ探索）",
) -> List[str]:
    voice = build_voice(voice_guide)

    out: List[str] = []
    rounds = (total + batch - 1) // batch

    for i in range(rounds):
        need = min(batch, total - len(out))
        if need <= 0:
            break

        p = _prompt(topic, need, role=role, intent=intent, voice=voice)
        data = gemini_generate_json(
            p,
            api_key=api_key,
            model=model,
            max_output_tokens=2048,
            temperature=0.75,
            min_interval_sec=min_interval_sec,
        )
        tweets = data.get("tweets", [])
        if not isinstance(tweets, list):
            tweets = []

        for t in tweets:
            s = str(t).strip()
            if not s:
                continue
            # 140字超えを切る（最後の保険）
            if len(s) > 140:
                s = s[:140]
            out.append(s)

        # RPM回避のため小休止（分割生成の間）
        time.sleep(0.4)

    # 重複を軽く除去
    uniq = []
    seen = set()
    for t in out:
        if t not in seen:
            uniq.append(t)
            seen.add(t)

    return uniq[:total]