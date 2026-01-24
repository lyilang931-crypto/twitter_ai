# generate.py
import json
from llm_gemini import gemini_generate


def _strip_code_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        lines = s.splitlines()
        # 先頭 ```json / ``` を落とす
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        # 末尾 ``` を落とす
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    return s


def _extract_first_json_object(raw: str) -> dict:
    """
    1) raw全体がJSONならそれを読む
    2) ```json ... ``` があれば剥がして読む
    3) balanced braces で最初に完成する { ... } を抜いて読む
    """
    raw = raw.strip()

    # 1) raw 全体が JSON の場合
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    # 2) code fence を剥がして再挑戦
    raw2 = _strip_code_fence(raw)
    try:
        obj = json.loads(raw2)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    # 3) balanced braces で「最初に閉じるdict」を抽出
    start = raw2.find("{")
    if start == -1:
        raise ValueError(f"JSON object not found:\n{raw[:500]}")

    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(raw2)):
        ch = raw2[i]

        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue

        if ch == '"':
            in_str = True
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = raw2[start : i + 1]
                try:
                    obj = json.loads(candidate)
                    if isinstance(obj, dict):
                        return obj
                except Exception as e:
                    raise ValueError(f"JSON parse failed:\n{candidate}\n\nraw:\n{raw[:800]}") from e

    raise ValueError(f"JSON not closed (truncated output?):\n{raw[:800]}")


def _normalize_text(t: str) -> str:
    # 余計な改行/空白を整えて「途中で切れた感」を減らす
    t = str(t).replace("\r", "").strip()
    t = "\n".join(line.rstrip() for line in t.split("\n")).strip()
    return t


def _enforce_constraints(data: dict, per_role_n: int, max_len: int = 120) -> dict:
    # 欠損・型チェック
    for k in ["MAIN", "SUB", "EXP"]:
        if k not in data or not isinstance(data[k], list):
            raise ValueError(f"Invalid JSON schema: missing {k}")

    # 正規化 + 空要素排除
    out = {}
    for k in ["MAIN", "SUB", "EXP"]:
        texts = [_normalize_text(x) for x in data[k]]
        texts = [x for x in texts if x]
        out[k] = texts

    # 件数チェック
    for k in ["MAIN", "SUB", "EXP"]:
        if len(out[k]) < per_role_n:
            raise ValueError(f"{k} has only {len(out[k])} items, need {per_role_n}")

    # 長さチェック（ここで「長すぎる→途中で不自然」も潰す）
    too_long = []
    for k in ["MAIN", "SUB", "EXP"]:
        for i, t in enumerate(out[k][:per_role_n]):
            if len(t) > max_len:
                too_long.append((k, i, len(t), t[:40]))

    if too_long:
        msg = "Some tweets exceed max length:\n" + "\n".join(
            [f"{k}[{i}] len={ln} head={head!r}" for (k, i, ln, head) in too_long]
        )
        raise ValueError(msg)

    # 余分があっても per_role_n に揃える（安定動作）
    for k in ["MAIN", "SUB", "EXP"]:
        out[k] = out[k][:per_role_n]

    return out


def generate_daily_pack(
    api_key: str,
    topic: str,
    per_role_n: int = 5,
    use_gemini: bool = True,
    model: str = "gemini-2.5-flash",
):
    if not use_gemini:
        raise RuntimeError("use_gemini=False is not supported yet")

    base_prompt = f"""
あなたはX(Twitter)の投稿文を作るプロです。
テーマ: {topic}

次のJSON**だけ**を返してください（説明文は禁止、コードブロック禁止）:
{{
  "MAIN": ["..."],  // 朝(7-9) 本命：否定×断定
  "SUB":  ["..."],  // 昼(12-13) 準本命：否定×数字
  "EXP":  ["..."]   // 夜(20-22) 実験：質問×逆説
}}

制約:
- 各配列は {per_role_n} 件（必ず満たす）
- 各ツイートは 120文字以内（必ず満たす）
- 絵文字は使わない
- JSONは必ず末尾の}}まで閉じる
- 文字列内に {{ }} などJSON構文に紛らわしい文字は入れない
""".strip()

    last_err = None
    for attempt in range(1, 4):  # 最大3回
        prompt = base_prompt
        if last_err:
            prompt += f"\n\n前回の出力は要件を満たしていません。次を必ず修正して再出力してください:\n{last_err}"

        raw = gemini_generate(
            prompt,
            api_key=api_key,
            model=model,
            max_output_tokens=1600,
            temperature=0.7,
        )

        try:
            data = _extract_first_json_object(raw)
            data = _enforce_constraints(data, per_role_n=per_role_n, max_len=120)
            break
        except Exception as e:
            last_err = str(e)
    else:
        raise RuntimeError(f"Failed to generate valid JSON after retries.\nLast error:\n{last_err}")

    def block(role, role_label, time_slot, time_slot_label, intent, texts):
        return {
            "role": role,
            "role_label": role_label,
            "time_slot": time_slot,
            "time_slot_label": time_slot_label,
            "intent": intent,
            "candidates": [str(t).strip() for t in texts if str(t).strip()],
        }

    return [
        block("MAIN", "本命（勝ちに行く）", "AM", "朝（7-9時）", "否定×断定", data["MAIN"]),
        block("SUB",  "準本命（微調整）", "NOON", "昼（12-13時）", "否定×数字", data["SUB"]),
        block("EXP",  "実験（学習）", "PM", "夜（20-22時）", "質問×逆説", data["EXP"]),
    ]