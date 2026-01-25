# llm_gemini.py
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, Optional

import google.generativeai as genai


def _extract_json_loose(raw: str) -> Dict[str, Any]:
    """
    返答に余計な文字が混ざっても最初の { ... } を抜く。
    """
    s = raw.find("{")
    e = raw.rfind("}")
    if s == -1 or e == -1 or e <= s:
        raise ValueError(f"JSON not found in response:\n{raw[:1200]}")
    body = raw[s : e + 1]
    return json.loads(body)


def _safe_text_from_response(resp) -> str:
    """
    resp.text が ValueError を投げるケースがあるので、
    candidates/parts から安全に抽出する。
    """
    # 1) まず resp.text を試す（ただし落ちる可能性）
    try:
        t = getattr(resp, "text", None)
        if t:
            return str(t).strip()
    except Exception:
        pass

    # 2) candidates があるなら parts/text を拾う
    cands = getattr(resp, "candidates", None) or []
    for c in cands:
        content = getattr(c, "content", None)
        if not content:
            continue
        parts = getattr(content, "parts", None) or []
        buf = []
        for p in parts:
            # p.text がある場合
            txt = getattr(p, "text", None)
            if txt:
                buf.append(str(txt))
        if buf:
            return "".join(buf).strip()

    # 3) ここまで来たら「テキストがない」
    # Safety/ブロック理由などをヒントとして含めてエラーにする
    fb = getattr(resp, "prompt_feedback", None)
    fr = None
    try:
        if cands:
            fr = getattr(cands[0], "finish_reason", None)
    except Exception:
        pass

    raise ValueError(
        "Gemini response has no extractable text. "
        f"finish_reason={fr}, prompt_feedback={fb}"
    )


def gemini_generate_json(
    prompt: str,
    api_key: Optional[str] = None,
    model: str = "gemini-flash-latest",
    temperature: float = 0.7,
    max_output_tokens: int = 2048,
    min_interval_sec: float = 2.0,
    session_key: str = "last_gemini_call_ts",
) -> Dict[str, Any]:
    """
    - レート制限回避: min_interval_sec で間隔を空ける
    - JSONだけ返す想定だが、余計なテキスト混入にも耐える
    - resp.text が落ちても candidates から拾う
    """
    api_key = api_key or os.getenv("Gemini_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Gemini_API_KEY is not set (Secrets/Env)")

    genai.configure(api_key=api_key)
    m = genai.GenerativeModel(model)

    # 最低間隔を空ける（Streamlit で st.session_state を使う場合は app.py 側でやるのが理想）
    # ここは「関数単体でも事故りにくい」保険
    now = time.time()
    # グローバル変数に保存（簡易）
    if not hasattr(gemini_generate_json, session_key):
        setattr(gemini_generate_json, session_key, 0.0)
    last = getattr(gemini_generate_json, session_key)
    wait = min_interval_sec - (now - last)
    if wait > 0:
        time.sleep(wait)
    setattr(gemini_generate_json, session_key, time.time())

    resp = m.generate_content(
        prompt,
        generation_config={
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
        },
    )

    raw = _safe_text_from_response(resp)

    # JSON抽出（余計な文が混ざっても耐える）
    return _extract_json_loose(raw)