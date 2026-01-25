# llm_gemini.py
from __future__ import annotations
import os
import time
import json
import random
from typing import List, Dict, Any

import google.generativeai as genai

def load_key() -> str:
    # Streamlit secrets優先は app.py 側で渡す想定だが、保険としてEnvも読む
    return (os.getenv("Gemini_API_KEY") or os.getenv("Gemini_API_KEY") or "").strip()

def _sleep_jitter(base: float):
    time.sleep(base + random.uniform(0, 0.25))

def gemini_generate_json(
    prompt: str,
    api_key: str,
    model: str = "gemini-flash-latest",
    max_output_tokens: int = 2048,
    temperature: float = 0.7,
    min_interval_sec: float = 2.0,
    max_retries: int = 6,
) -> Dict[str, Any]:
    """
    途切れないJSON取得（説明文禁止）
    """
    if not api_key:
        raise RuntimeError("Gemini_API_KEY is not set")

    # RPM回避：呼び出し間隔を強制
    now = time.time()
    last = getattr(gemini_generate_json, "_last_call", 0.0)
    wait = min_interval_sec - (now - last)
    if wait > 0:
        _sleep_jitter(wait)
    gemini_generate_json._last_call = time.time()

    genai.configure(api_key=api_key)
    m = genai.GenerativeModel(model)

    # リトライ（429/5xx/変な返答）
    backoff = 1.5
    for attempt in range(max_retries):
        try:
            resp = m.generate_content(
                prompt,
                generation_config={
                    "temperature": temperature,
                    "max_output_tokens": max_output_tokens,
                },
            )
            text = (resp.text or "").strip()

            # JSONだけ抽出（前後に余計な文が混ざっても拾う）
            s = text.find("{")
            e = text.rfind("}")
            if s == -1 or e == -1 or e <= s:
                raise ValueError(f"JSON not found:\n{text}")

            data = json.loads(text[s:e+1])
            return data

        except Exception as e:
            # 429/RateLimitっぽい場合もここに落ちることがある
            if attempt == max_retries - 1:
                raise
            _sleep_jitter(backoff)
            backoff *= 1.8

    raise RuntimeError("unreachable")