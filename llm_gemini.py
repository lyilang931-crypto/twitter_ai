# llm_gemini.py
from __future__ import annotations
import os, time, json, re
from typing import Any, Dict
import google.generativeai as genai

def _extract_json(raw: str) -> Dict[str, Any]:
    s = raw.find("{")
    e = raw.rfind("}")
    if s == -1 or e == -1 or e <= s:
        raise ValueError(f"JSON not found:\n{raw}")
    return json.loads(raw[s:e+1])

def gemini_json(
    prompt: str,
    api_key: str,
    model: str = "gemini-2.5-flash-lite",
    max_output_tokens: int = 1400,
    temperature: float = 0.7,
    retries: int = 2,
    sleep_sec: float = 2.5,
) -> Dict[str, Any]:
    """
    ・JSONのみ返させる
    ・途切れや schema崩れはリトライ
    """
    genai.configure(api_key=api_key)
    m = genai.GenerativeModel(model)

    last_err = None
    for _ in range(retries + 1):
        try:
            resp = m.generate_content(
                prompt,
                generation_config={
                    "temperature": temperature,
                    "max_output_tokens": max_output_tokens,
                },
            )
            raw = (resp.text or "").strip()
            return _extract_json(raw)
        except Exception as e:
            last_err = e
            time.sleep(sleep_sec)
    raise RuntimeError(f"Gemini failed: {last_err}")