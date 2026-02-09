# llm_gemini.py — Gemini API呼び出し + 堅牢JSONパーサ（多段フォールバック）
from __future__ import annotations
import os, time, json, re
from typing import Any, Dict, List
import google.generativeai as genai


def _extract_json(raw: str) -> Dict[str, Any]:
    """多段JSON抽出:
    1) コードブロック内のJSONを試す
    2) 先頭の { から末尾の } までを切り出して JSON パース
    3) 失敗時は ValueError / JSONDecodeError
    """
    text = (raw or "").strip()

    # Stage 1: コードブロック ```json ... ``` を抽出
    code_block = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if code_block:
        try:
            return json.loads(code_block.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            pass

    # Stage 2: 最初の { から最後の } まで
    s = text.find("{")
    e = text.rfind("}")
    if s != -1 and e != -1 and e > s:
        try:
            return json.loads(text[s : e + 1])
        except (json.JSONDecodeError, ValueError):
            pass

    # Stage 3: [ から ] の配列形式
    s = text.find("[")
    e = text.rfind("]")
    if s != -1 and e != -1 and e > s:
        try:
            arr = json.loads(text[s : e + 1])
            if isinstance(arr, list):
                return {"__array": arr}
        except (json.JSONDecodeError, ValueError):
            pass

    raise ValueError(f"JSON not found:\n{text[:200]}")


def _text_fallback(raw: str) -> List[str]:
    """JSONパース失敗時に、テキストを行分割して候補として救出する。
    短すぎ（15文字未満）/長すぎ（200文字超）の行はスキップ。
    """
    lines = (raw or "").strip().splitlines()
    candidates: List[str] = []
    for line in lines:
        cleaned = line.strip().strip('"').strip("'").strip(",").strip()
        # 番号付きリスト除去 (1. xxx, 1) xxx)
        cleaned = re.sub(r"^\d+[\.\)]\s*", "", cleaned)
        if 15 <= len(cleaned) <= 200:
            candidates.append(cleaned)
    return candidates


def _parse_or_fallback(raw: str) -> Dict[str, Any]:
    """raw を JSON としてパースする。失敗した場合は:
    1) テキスト行分割で候補救出を試みる
    2) それでもダメなら __fallback 付き dict を返す
    """
    try:
        result = _extract_json(raw)
        # 配列形式の場合、__array キーで返ってくるので処理
        if "__array" in result:
            return {"__array_fallback": True, "items": result["__array"]}
        return result
    except (ValueError, json.JSONDecodeError):
        # テキスト行分割フォールバック
        lines = _text_fallback(raw)
        if lines:
            return {"__text_fallback": True, "items": lines}
        return {"__fallback": True, "raw": (raw or "").strip()}


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
    ・多段フォールバック付き
    """
    genai.configure(api_key=api_key)
    m = genai.GenerativeModel(model)

    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = m.generate_content(
                prompt,
                generation_config={
                    "temperature": temperature,
                    "max_output_tokens": max_output_tokens,
                },
            )
            raw = (resp.text or "").strip()
            return _parse_or_fallback(raw)
        except Exception as e:
            last_err = e
            # API key expired や rate limit exceeded などのエラーを検出
            err_str = str(e).lower()
            if "api key" in err_str or "expired" in err_str or "rate limit" in err_str or "quota" in err_str:
                # 呼び出し側で処理できるように、カスタム例外として再投げ
                raise RuntimeError(f"Gemini API error: {last_err}")
            time.sleep(sleep_sec)
    raise RuntimeError(f"Gemini failed: {last_err}")
