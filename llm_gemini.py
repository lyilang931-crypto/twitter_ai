import google.generativeai as genai

def gemini_generate(
    prompt: str,
    api_key: str,
    model: str = "gemini-3-flash",
    max_output_tokens: int = 320,
    temperature: float = 0.6
) -> str:
    genai.configure(api_key=api_key)
    m = genai.GenerativeModel(model)
    resp = m.generate_content(
        prompt,
        generation_config={
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
        }
    )
    return (resp.text or "").strip()
