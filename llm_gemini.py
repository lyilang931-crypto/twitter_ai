# llm_gemini.py
from __future__ import annotations

import json
import re
import time
import traceback
from typing import Any, Dict

import google.generativeai as genai

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

def _extract_json(raw: str) -> Dict[str, Any]:
    raw = (raw or "").strip()
    m = _JSON_RE.search(raw)
    if not m:
        raise ValueError(f"JSON not found. raw={raw[:300]}")
    s = m.group(0)
    # よくある崩れを軽く補正
    s = s.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
    s = re.sub(r",\s*}", "}", s)
    s = re.sub(r",\s*]", "]", s)
    return json.loads(s)

def gemini_json(prompt: str, api_key: str, model: str, retries: int = 3, sleep_sec: float = 2.0) -> Dict[str, Any]:
    genai.configure(api_key=api_key)
    m = genai.GenerativeModel(model)

    last_err: Exception | None = None

    for i in range(retries):
        try:
            resp = m.generate_content(
                prompt,
                generation_config={
                    "temperature": 0.7,
                    "max_output_tokens": 1400,
                    # JSONを出させたいならこれが効くことが多い
                    "response_mime_type": "application/json",
                },
            )

            # ここが “候補が空” のとき落ちてた場所
            if not getattr(resp, "candidates", None):
                raise RuntimeError(f"Empty candidates. resp={resp}")

            cand0 = resp.candidates[0]
            finish = getattr(cand0, "finish_reason", None)

            # テキスト取り出し（候補があるのに text が空のこともある）
            text = getattr(resp, "text", None)
            if not text:
                raise RuntimeError(f"No text returned. finish_reason={finish}")

            return _extract_json(text)

        except Exception as e:
            last_err = e
            print("=== Gemini error ===")
            traceback.print_exc()
            time.sleep(sleep_sec)

    raise RuntimeError(f"Gemini failed: {last_err}")