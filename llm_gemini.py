# llm_gemini.py
import os
from typing import Optional
from google import genai

DEFAULT_MODEL = "gemini-flash-latest"

def _load_key(api_key: Optional[str] = None) -> str:
    k = api_key or os.getenv("Gemini_API_KEY", "")
    k = str(k).strip()
    if not k:
        raise RuntimeError("Gemini_API_KEY is not set (Secrets or env).")
    return k

def gemini_generate(
    prompt: str,
    api_key: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    max_output_tokens: int = 1400,
    temperature: float = 0.7,
) -> str:
    key = _load_key(api_key)
    client = genai.Client(api_key=key)

    resp = client.models.generate_content(
        model=model,
        contents=prompt,
        config={
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
        },
    )
    text = getattr(resp, "text", "") or ""
    return text.strip()

def list_models(api_key: Optional[str] = None) -> list[str]:
    key = _load_key(api_key)
    client = genai.Client(api_key=key)
    ms = client.models.list()
    out = []
    for m in ms:
        name = getattr(m, "name", "")
        if name:
            out.append(name)
    return out