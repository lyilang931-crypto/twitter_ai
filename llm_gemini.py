# llm_gemini.py
import os
import time
import random
import google.generativeai as genai

def gemini_generate(
    prompt: str,
    api_key: str | None = None,
    model: str = "gemini-2.5-flash",
    max_output_tokens: int = 1400,
    temperature: float = 0.7,
    retries: int = 4,
) -> str:
    api_key = (api_key or os.getenv("Gemini_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("Gemini_API_KEY is not set")

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
            return (resp.text or "").strip()
        except Exception as e:
            last_err = e
            # Rate limit / transient 用の雑リトライ（指数バックオフ）
            if i < retries:
                time.sleep((2 ** i) + random.random())
                continue
            raise last_err