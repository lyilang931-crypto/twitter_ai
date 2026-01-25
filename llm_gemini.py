# llm_gemini.py
from __future__ import annotations

import time, json, re
from typing import Any, Dict
import traceback

import google.generativeai as genai

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

def gemini_json(
    prompt: str,
    api_key: str,
    model: str,
    max_output_tokens: int = 1400,
    temperature: float = 0.7,
    retries: int = 2,
    sleep_sec: float = 2.2,
) -> Dict[str, Any]:
    last_err = None

    for i in range(int(retries) + 1):
        try:
            genai.configure(api_key=api_key)
            m = genai.GenerativeModel(model)

            resp = m.generate_content(
                prompt,
                generation_config={
                    "temperature": float(temperature),
                    "max_output_tokens": int(max_output_tokens),
                },
            )

            raw = (getattr(resp, "text", None) or "").strip()

            m2 = _JSON_RE.search(raw)
            if not m2:
                raise ValueError(f"JSON not found. raw={raw[:200]}...")

            return json.loads(m2.group(0))

        except Exception as e:
            last_err = e
            print("=== Gemini error ===")
            traceback.print_exc()   # ← これが本命（Logsに完全表示）
            if i < int(retries):
                time.sleep(float(sleep_sec))

    raise RuntimeError(f"Gemini failed: {last_err}")
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

def _extract_json(raw: str) -> Dict[str, Any]:
    raw = (raw or "").strip()
    m = _JSON_RE.search(raw)
    if not m:
        raise ValueError(f"JSON not found:\n{raw}")

    s = m.group(0)

    # 軽い修復（よくある崩れ）
    s = s.replace("\u201c", '"').replace("\u201d", '"')
    s = s.replace("\u2018", "'").replace("\u2019", "'")
    s = re.sub(r",\s*}", "}", s)
    s = re.sub(r",\s*]", "]", s)

    return json.loads(s)

def gemini_json(
    prompt: str,
    api_key: str,
    model: str = "gemini-flash-latest",
    max_output_tokens: int = 1200,
    temperature: float = 0.7,
    retries: int = 3,
    base_sleep: float = 1.0,
) -> Dict[str, Any]:
    genai.configure(api_key=api_key)
    m = genai.GenerativeModel(model)

    last_err = None
    for i in range(retries + 1):
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
            msg = str(e).lower()
            # 429/limit系はより待つ（指数バックオフ）
            if "429" in msg or "rate" in msg or "limit" in msg or "resource" in msg:
                time.sleep(min(20.0, base_sleep * (2 ** i) + 0.3))
            else:
                time.sleep(min(8.0, base_sleep * (1.6 ** i) + 0.2))

    raise RuntimeError(f"Gemini failed: {last_err}")
