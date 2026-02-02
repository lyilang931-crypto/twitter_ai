# runner.py
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

# あなたの既存モジュール（存在する前提）
from rate_limit import Limits, RateLimiter
from storage import append_row, read_rows, load_json, save_json  # ←存在しないなら storage.py に合わせて変更
from safety import safety_check  # ←関数名が違う場合は修正
from scoring import pseudo_reward_components  # ←関数名が違う場合は修正
from novelty import novelty_score  # ←関数名が違う場合は修正
from prompts import build_prompt  # ←関数名が違う場合は修正
from llm_gemini import gemini_json  # ←関数名が違う場合は修正


# ====== パス（app.pyの定数をそのまま移植） ======
LOG_PATH = "data/twitter_log.csv"
USAGE_PATH = "data/usage.json"

# “投稿待ち箱”（後でTwitter API連携する時にここから出す）
OUTBOX_PATH = "data/outbox.csv"

# ====== レート制限（app.pyの値をそのまま移植） ======
LIMITS = Limits(rpm=5, tpm=250, rpd=20)
rl = RateLimiter(LIMITS)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_get_env(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        raise RuntimeError(f"Missing env var: {name}")
    return v


def generate_candidates(role: str, topic: str, trend_hint: str, n: int, api_key: str, model: str) -> List[Dict[str, Any]]:
    """
    既存のGemini呼び出しを使って候補をn件生成。
    gemini_json() の戻り値仕様に合わせて調整する。
    """
    candidates: List[Dict[str, Any]] = []
    for i in range(n):
        prompt = build_prompt(topic=topic, trend_hint=trend_hint, n=n, role=role)

        # レート制限（あなたの RateLimiter API に合わせる）
        # 例: rl.wait_for_rpm() があるならそれを使う
        try:
            rl.wait_for_rpm()
        except Exception:
            # ない場合はスリープで最低限
            time.sleep(2)

        data = gemini_json(
            prompt=prompt,
            api_key=api_key,
            model=model,
            max_output_tokens=1400,
            temperature=0.7,
        )

        # dataの形はプロジェクトにより違うので、ここで「tweet本文」を取り出す
        # よくある形式例: {"tweet": "..."} / {"text": "..."} / {"output": "..."}
        tweet = (
            data.get("tweet")
            or data.get("text")
            or data.get("output")
            or ""
        ).strip()

        if tweet:
            candidates.append({
                "tweet": tweet,
                "raw": data,
            })

    return candidates


def score_candidate(tweet: str, past_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    safety / novelty / pseudo_reward をまとめてスコア化。
    ※各関数のシグネチャはあなたの実装に合わせて調整。
    """
    # 安全性
    safe = safety_check(tweet)  # 0/1 or True/False想定

    # 新規性（過去ログ参照）
    nov = novelty_score(tweet, past_rows)  # 0.0-1.0想定

    # 擬似報酬（あなたの scoring.py に合わせて）
    # pseudo_reward_components(tweet, ...) が (score, detail) を返す想定
    pseudo, detail = pseudo_reward_components(tweet)

    # 単純に合成（必要なら weights.py を使ってもOK）
    final = (pseudo * 0.7) + (nov * 0.3)
    if not safe:
        final -= 999  # 安全でないものは落とす

    return {
        "safe": int(bool(safe)),
        "novelty": float(nov),
        "pseudo": float(pseudo),
        "final": float(final),
        "detail": detail,
    }


def pick_best(cands: List[Dict[str, Any]], past_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    scored = []
    for c in cands:
        tweet = c["tweet"]
        s = score_candidate(tweet, past_rows)
        scored.append({**c, **s})

    scored.sort(key=lambda x: x["final"], reverse=True)
    return scored[0] if scored else {}


def main() -> None:
    # ====== 入力（最小） ======
    # まずは固定でもOK。後で引数化する。
    role = os.getenv("TW_ROLE", "default")
    topic = os.getenv("TW_TOPIC", "起業×AI×生産性")
    trend_hint = os.getenv("TW_TREND", "短く刺さる1行。数字 or 対比。")
    n = int(os.getenv("TW_N", "3"))

    # Geminiのキー（あなたの変数名に合わせて）
    api_key = safe_get_env("GEMINI_API_KEY")
    model = os.getenv("GEMINI_MODEL", "gemini-1.5-pro")

    # 過去ログ読み込み（ない場合は空）
    past_rows = []
    try:
        past_rows = read_rows(LOG_PATH)
    except Exception:
        past_rows = []

    # 候補生成
    cands = generate_candidates(role=role, topic=topic, trend_hint=trend_hint, n=n, api_key=api_key, model=model)
    if not cands:
        print("No candidates generated.")
        return

    # 最良1件選ぶ
    best = pick_best(cands, past_rows)
    if not best:
        print("No best candidate.")
        return

    # 保存（outbox + log）
    row = {
        "ts": utc_now_iso(),
        "tweet": best["tweet"],
        "final": best["final"],
        "pseudo": best["pseudo"],
        "novelty": best["novelty"],
        "safe": best["safe"],
        "role": role,
        "topic": topic,
        "trend_hint": trend_hint,
    }

    # “投稿待ち”に入れる
    append_row(OUTBOX_PATH, row)

    # 学習ログにも入れる（あなたのログ項目に合わせて増やしてOK）
    append_row(LOG_PATH, row)

    print("Saved to outbox:", row["tweet"])


if __name__ == "__main__":
    main()