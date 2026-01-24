# llm_gemini.py
import os
import google.generativeai as genai

DEFAULT_MODEL = "gemini-flash-latest"

def _load_key(api_key: str | None = None) -> str:
    k = (api_key or os.getenv("Gemini_API_KEY") or "").strip()
    if not k:
        raise RuntimeError("Gemini_API_KEY is not set (Secrets or env).")
    return k

def gemini_generate(
    prompt: str,
    api_key: str | None = None,
    model: str = DEFAULT_MODEL,
    max_output_tokens: int = 1400,
    temperature: float = 0.7,
) -> str:
    key = _load_key(api_key)
    genai.configure(api_key=key)

    m = genai.GenerativeModel(model)
    resp = m.generate_content(
        prompt,
        generation_config={
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
        },
    )
    return (resp.text or "").strip()