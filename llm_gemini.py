import os
import google.generativeai as genai

def gemini_generate(
    prompt: str,
    api_key: str | None = None,
    model: str = "gemini-3-flash",
    max_output_tokens: int = 320,
    temperature: float = 0.6,
) -> str:
    api_key = api_key or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")

    genai.configure(api_key=api_key)
    m = genai.GenerativeModel(model)

    resp = m.generate_content(
        prompt,
        generation_config={
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
        },
    )
    return (resp.text or "").strip()