# app.py
from __future__ import annotations

import json
import time
from datetime import date
import streamlit as st
import pandas as pd

from rate_limit import RateLimiter, Limits, rough_token_count
from llm_gemini import gemini_json
from prompts import build_prompt
from safety import safety_score_01, safety_check
from novelty import novelty_score
from exp_score import tail_score, exp_utility
from scoring import pseudo_reward_components, pseudo_score,速報_score,確定_score
from weights import DEFAULT_W, sgd_update
from selfplay import league_score
from storage import (
    init_db,
    get_conn,
    read_rows,
    append_row,
    append_rows,
    update_row,
    update_by_id,
    load_json,
    save_json,
    load_weights,
    save_weights,
    logical_delete_tweet,
    get_success_templates,
    save_success_template,
    bandit_get_all,
    bandit_update,
    STATUS_PINNED,
    log_event,
)
from replay import sample_for_learning
from distill import is_success_row, extract_features, to_guideline_line
from bandit import arm_id_from_cand, rank_candidates_by_bandit

# =========================
# 基本設定
# =========================
st.set_page_config(page_title="TwitterAI", layout="wide")
st.title("Twitter自動化/ 超高速学習")

# =========================
# Paths
# =========================
LOG_PATH = "data/twitter_log.csv"
W_PATH = "data/weights.json"
U_PATH = "data/usage.json"

# =========================
# Limits（ユーザー指定）
# =========================
LIMITS = Limits(rpm=5, tpm=250, rpd=20)
rl = RateLimiter(LIMITS)

def today_key() -> str:
    return str(date.today())

def load_api_key() -> str:
    # キー名固定：Gemini_API_KEY
    k = ""
    try:
        k = st.secrets.get("Gemini_API_KEY", "")
    except Exception:
        k = ""
    return (k or "").strip()

def usage_can_call(usage: dict) -> bool:
    d = usage.get(today_key(), {})
    return int(d.get("calls", 0)) < LIMITS.rpd

def usage_inc(usage: dict, n: int = 1) -> dict:
    d = usage.get(today_key(), {})
    d["calls"] = int(d.get("calls", 0)) + n
    usage[today_key()] = d
    return usage

def postprocess_tweet(t: str) -> str:
    """本文を正規化。固有名詞は削除しない（名前を壊さない）。"""
    t = (t or "").strip()
    t = t.replace("\n", " ")
    # 二重引用を雑に除去
    if t.startswith('"') and t.endswith('"') and len(t) > 2:
        t = t[1:-1].strip()

    # 長すぎは切る（途切れ防止：句読点で切り、最後に“。”で締める）
    if len(t) > 140:
        t = t[:140]
        # 中途半端に終わるのを軽減
        if t[-1] not in ["。", "！", "?", "？"]:
            t = t.rstrip("、, ") + "。"

    # 短すぎ対策：最低ラインを維持（ただし意図的短文があるので軽い補正）
    if len(t) < 55:
        pass
    
    return t  # 戻り値を明示的に返す（None を返さないように）


def extract_name_candidates(topic: str) -> list[str]:
    """テーマから固有名詞の候補を抽出（本文含有チェック用）。"""
    t = (topic or "").strip()
    if not t:
        return []
    candidates = [t]
    for sep in ["・", " ", "　", "、"]:
        if sep in t:
            for part in t.split(sep):
                p = part.strip()
                if len(p) >= 2:
                    candidates.append(p)
    return list(dict.fromkeys(candidates))


def contains_name(text: str, topic: str) -> bool:
    """本文にテーマの固有名詞（またはその一部）が含まれるか。"""
    if not text or not topic:
        return False
    for cand in extract_name_candidates(topic):
        if cand and cand in text:
            return True
    return False


def any_tweet_contains_name(texts: list[str], topic: str) -> bool:
    """いずれか1ツイート以上にテーマの名前が含まれるか。"""
    return any(contains_name(t, topic) for t in (texts or []))


def is_named_entity_required(topic: str) -> bool:
    """テーマが人物名っぽいか（短く、文節接続が少ない）。"""
    t = (topic or "").strip()
    if len(t) > 25:
        return False
    particles = ("で", "の", "と", "を", "が", "は", "に", "について", "共通点", "考え")
    return not any(p in t for p in particles)


def fallback_tweet_with_name(topic: str) -> str:
    """名前入り汎用ツイ（API追加呼び出しなし）。観察・学びスタイル。"""
    t = (topic or "").strip() or "テーマ"
    return f"{t}についての観察。設計と行動を大切にしたい。"


def collect_required_keywords(topic: str, trend_hint: str) -> list[str]:
    """テーマとトレンドから必須キーワードを収集（重複除去・2文字以上）。"""
    out: list[str] = []
    t = (topic or "").strip()
    if t and len(t) >= 2:
        out.append(t)
    hint = (trend_hint or "").strip()
    if not hint:
        return list(dict.fromkeys(out))
    for sep in [",", " ", "　", "・", "、"]:
        for part in hint.split(sep):
            p = part.strip()
            if len(p) >= 2 and p not in out:
                out.append(p)
    return list(dict.fromkeys(out))


def contains_keywords(text: str, required_keywords: list[str]) -> bool:
    """本文に必須キーワードのいずれかが含まれるか（部分一致・前後空白や記号は許容）。"""
    if not text or not required_keywords:
        return False
    normalized = (text or "").strip()
    for kw in required_keywords:
        if kw and kw in normalized:
            return True
    return False


def any_tweet_contains_keywords(texts: list[str], required_keywords: list[str]) -> bool:
    """いずれか1ツイート以上に必須キーワードが含まれるか。"""
    return any(contains_keywords(t, required_keywords) for t in (texts or []))


def fallback_tweet_with_keywords(required_keywords: list[str]) -> str:
    """必須キーワード入り汎用ツイ（API呼び出しなし）。観察・設計スタイル。"""
    kw = (required_keywords or [""])[0].strip() or "テーマ"
    return f"{kw}についての観察。設計と行動を大切にしたい。"


def api_generate(
    role: str,
    topic: str,
    trend_hint: str,
    n: int,
    api_key: str,
    model: str,
    success_guidelines: str = "",
    named_entity_required: bool = False,
    required_keywords: list[str] = None,
) -> list[str]:
    if required_keywords is None:
        required_keywords = []
    prompt = build_prompt(
        topic=topic,
        trend_hint=trend_hint,
        n=n,
        role=role,
        success_guidelines=success_guidelines,
        named_entity_required=named_entity_required,
        required_keywords=required_keywords,
    )

    # TPM250目安チェック（超過しそうなら短縮）
    if rough_token_count(prompt) > LIMITS.tpm:
        prompt = prompt[:420]  # 最終手段（安全側）

    rl.wait_for_rpm()

    try:
        data = gemini_json(
            prompt,
            api_key=api_key,
            model=model,
            max_output_tokens=1400,
            temperature=0.7,
            retries=2,
            sleep_sec=2.2,
        )
        arr = data.get(role, [])
        out = []
        for x in arr:
            s = postprocess_tweet(str(x))
            # None や空文字列を除外
            if s and isinstance(s, str) and s.strip():
                out.append(s)
        return out
    except RuntimeError as e:
        # API key expired, rate limit exceeded などのエラー時
        err_msg = str(e)
        if "api key" in err_msg.lower() or "expired" in err_msg.lower():
            st.warning(f"⚠️ Gemini API キーが無効または期限切れです: {err_msg}")
        elif "rate limit" in err_msg.lower() or "quota" in err_msg.lower():
            st.warning(f"⚠️ Gemini API のレート制限に達しました: {err_msg}")
        else:
            st.warning(f"⚠️ Gemini API エラー: {err_msg}")
        # 空リストを返して処理を続行（UIは落とさない）
        return []
    except Exception as e:
        # その他の予期しないエラー
        st.warning(f"⚠️ 予期しないエラーが発生しました: {str(e)}")
        return []

# 明確に危険で除外すべき理由（2段階safetyの第1段階）
DANGEROUS_REASONS = ["暴力/過激", "差別/属性一般化", "個人攻撃"]

def build_candidates(rows, w, role, texts):
    """
    候補を構築。textがNone/emptyは除外。
    safety<=0でも、明確に危険でない限りflaggedフラグを付けて保持。
    """
    cands = []
    empty_text_dropped = 0
    safety_dropped_count = 0
    safety_flagged_count = 0
    
    for t in texts:
        # None や空文字列、非文字列を安全に除外
        if not t or not isinstance(t, str) or not t.strip():
            empty_text_dropped += 1
            continue
        
        # safety_checkで詳細な理由を取得
        safety_result = safety_check(t)
        saf = safety_score_01(t)
        
        # 2段階safety: 明確に危険な理由がある場合のみ除外
        has_dangerous_reason = any(reason in DANGEROUS_REASONS for reason in safety_result.reasons)
        if has_dangerous_reason:
            safety_dropped_count += 1
            continue  # 除外
        
        # safety<=0の候補はflaggedフラグを付けて保持
        flagged = (saf <= 0.0)
        flagged_reason = ", ".join(safety_result.reasons) if safety_result.reasons else "safety<=0"
        if flagged:
            safety_flagged_count += 1
        nov = novelty_score(t, rows, window=300)
        tail = tail_score(t)

        comps = pseudo_reward_components(t, novelty=nov, safety=saf, tail=tail)
        ps = pseudo_score(comps, w)

        # EXPは分散最大化を上乗せ（“上振れ確率”）
        if role == "EXP":
            ps = 0.35 * ps + 0.65 * exp_utility(tail=tail, novelty=nov, safety=saf)

        cands.append({
            "role": role,
            "text": t,
            "safety": saf,
            "novelty": nov,
            "tail": tail,
            "pseudo": ps,
            "components": comps,
            "flagged": flagged,
            "flagged_reason": flagged_reason,
        })
    
    return cands, {
        "empty_text_dropped": empty_text_dropped,
        "safety_dropped_count": safety_dropped_count,
        "safety_flagged_count": safety_flagged_count,
        "final_count": len(cands),
    }

def choose_top3(main_c, sub_c, exp_c):
    main_best = main_c[0] if main_c else None
    sub_best  = sub_c[0] if sub_c else None
    exp_best  = exp_c[0] if exp_c else None
    return main_best, sub_best, exp_best

def elo_update(r: float, score01: float, baseline: float = 0.50, k: float = 16.0) -> float:
    # score01がbaselineより高ければ上がる（簡易Elo）
    expected = baseline
    return float(r + k * (score01 - expected))

# =========================
# 永続化初期化（1回だけ・既存を消さない）
# =========================
init_db()

# =========================
# Load state（DBから復元）
# =========================
rows = read_rows(LOG_PATH)
usage = load_json(U_PATH, {})
w = load_weights(W_PATH)
if not w:
    w = dict(DEFAULT_W)

def last_rating(col: str, default: float = 1000.0) -> float:
    if not rows:
        return default
    try:
        return float(rows[-1].get(col, default) or default)
    except Exception:
        return default

abs_rating = last_rating("abs_rating_after", 1000.0)
rel_rating = last_rating("rel_rating_after", 1000.0)

# =========================
# Sidebar
# =========================
with st.sidebar:
    st.subheader("運用設定（制限回避）")
    api_key = load_api_key()
    st.caption(f"RPD: {LIMITS.rpd} / RPM: {LIMITS.rpm} / TPM目安: {LIMITS.tpm}")
    today_calls = int(usage.get(today_key(), {}).get("calls", 0))
    st.metric("本日APIコール数", f"{today_calls} / {LIMITS.rpd}")

    model = 'gemini-2.5-flash-lite'
    st.write(f"モデル: **{model}**（固定）")

    if not api_key:
        st.error("Secretsに Gemini_API_KEY が未設定です。")
        st.stop()

    st.subheader("トレンド入力（手入力・最小）")
    trend_hint = st.text_input("今日のトレンド（任意）", value="日経・AI・スタートアップ・副業・金利")

    st.subheader("候補数（自動最適）")
    # 制限と速度を最優先。デフォは90案（3回×30）
    target = st.selectbox("候補規模", ["最速(90案)", "強め(120案)", "重め(150案)"], index=0)
    if target == "最速(90案)":
        per_role = 30
        calls_plan = 3
    elif target == "強め(120案)":
        per_role = 40
        calls_plan = 3
    else:
        per_role = 25
        calls_plan = 6  # 2回に分割して150相当（RPD的にギリ余裕）

    st.caption("※『200案』は“内部リーグ200局”で代替（速い＆制限踏まない）。")

    st.subheader("レーティング")
    baseline = st.slider("Baseline（勝率基準）", 0.40, 0.70, 0.50, 0.01)
    k_abs = st.slider("K（絶対）", 4.0, 32.0, 16.0, 1.0)
    k_rel = st.slider("K（相対）", 4.0, 32.0, 16.0, 1.0)

# =========================
# Tabs
# =========================
tab1, tab2, tab3 = st.tabs(["① 生成→自己対局→承認(3ツイ)", "② 実測入力(手入力最小/CSV可)", "③ 分析・学習(重み/レート)"])

# =========================================================
# ① 生成→自己対局→承認
# =========================================================
with tab1:
    st.subheader("今日のテーマ（あなたっぽさ：冷酷×設計×起業×経済）")
    topic = st.text_input("テーマ", value="起業で失敗する人の共通点")

    if st.button("生成（制限回避）→ 内部自己対局 → 上位を提示"):
        if not usage_can_call(usage):
            st.error("本日のRPD上限に到達。明日また回してください。")
            st.stop()

        st.info("生成開始：途切れ防止・JSON強制・レート制限待機を自動で行います。")

        all_main: list[str] = []
        all_sub: list[str] = []
        all_exp: list[str] = []

        # 成功テンプレTopNをガイドラインとして注入（自己蒸留）
        try:
            templates = get_success_templates(5)
            success_guidelines = " / ".join(
                to_guideline_line(t.get("data", {})) for t in templates if t.get("data")
            ) if templates else ""
        except Exception:
            success_guidelines = ""

        named_entity_required = is_named_entity_required(topic)
        required_keywords = collect_required_keywords(topic, trend_hint)

        def gen_role(
            role: str,
            n_each: int,
            s_guidelines: str = "",
            name_required: bool = False,
            req_kw: list[str] = None,
        ) -> list[str]:
            rl.wait_for_rpm()
            return api_generate(
                role, topic, trend_hint, n_each, api_key, model,
                success_guidelines=s_guidelines,
                named_entity_required=name_required,
                required_keywords=req_kw or [],
            )

        # ===== 生成（calls_plan に応じて回す）=====
        rounds = 1 if calls_plan == 3 else 2
        for _ in range(rounds):
            if usage_can_call(usage):
                all_main.extend(gen_role("MAIN", per_role, success_guidelines, named_entity_required, required_keywords))
                usage_inc(usage, 1); save_json(U_PATH, usage)
            if usage_can_call(usage):
                all_sub.extend(gen_role("SUB", per_role, success_guidelines, named_entity_required, required_keywords))
                usage_inc(usage, 1); save_json(U_PATH, usage)
            if usage_can_call(usage):
                all_exp.extend(gen_role("EXP", per_role, success_guidelines, named_entity_required, required_keywords))
                usage_inc(usage, 1); save_json(U_PATH, usage)

        # 固有名詞必須: 条件未達なら1回だけ MAIN を再生成
        if named_entity_required and not any_tweet_contains_name(all_main + all_sub + all_exp, topic):
            if usage_can_call(usage):
                retry_main = gen_role("MAIN", per_role, success_guidelines, True, required_keywords)
                all_main.extend(retry_main)
                usage_inc(usage, 1); save_json(U_PATH, usage)
            if not any_tweet_contains_name(all_main + all_sub + all_exp, topic):
                all_main.insert(0, fallback_tweet_with_name(topic))

        # 必須キーワード（テーマ/トレンド）: 条件未達なら1回だけ MAIN を再生成
        if required_keywords and not any_tweet_contains_keywords(all_main + all_sub + all_exp, required_keywords):
            if usage_can_call(usage):
                retry_main = gen_role("MAIN", per_role, success_guidelines, named_entity_required, required_keywords)
                all_main.extend(retry_main)
                usage_inc(usage, 1); save_json(U_PATH, usage)
            if not any_tweet_contains_keywords(all_main + all_sub + all_exp, required_keywords):
                all_main.insert(0, fallback_tweet_with_keywords(required_keywords))

        # 重複除去
        def uniq(xs: list[str]) -> list[str]:
            seen = set()
            out = []
            for x in xs:
                if x not in seen:
                    out.append(x)
                    seen.add(x)
            return out

        all_main, all_sub, all_exp = uniq(all_main), uniq(all_sub), uniq(all_exp)

        # 候補 → 擬似採点
        main_c, main_stats = build_candidates(rows, w, "MAIN", all_main)
        sub_c, sub_stats = build_candidates(rows, w, "SUB", all_sub)
        exp_c, exp_stats = build_candidates(rows, w, "EXP", all_exp)

        # デバッグカウンターを表示
        generated_count = len(all_main) + len(all_sub) + len(all_exp)
        total_empty_dropped = main_stats["empty_text_dropped"] + sub_stats["empty_text_dropped"] + exp_stats["empty_text_dropped"]
        total_safety_dropped = main_stats["safety_dropped_count"] + sub_stats["safety_dropped_count"] + exp_stats["safety_dropped_count"]
        total_safety_flagged = main_stats["safety_flagged_count"] + sub_stats["safety_flagged_count"] + exp_stats["safety_flagged_count"]
        total_final = main_stats["final_count"] + sub_stats["final_count"] + exp_stats["final_count"]
        
        st.caption(f"📊 生成統計: 生成={generated_count}, 空文字除外={total_empty_dropped}, 危険除外={total_safety_dropped}, 警告付き={total_safety_flagged}, 最終候補={total_final}")
        
        # fallback: 最終候補が0の場合、flagged候補も含めて表示
        if total_final == 0:
            st.warning("⚠️ 最終候補が0件です。警告付き候補も含めて再評価します。")
            # flagged候補も含めて再構築（危険なもの以外は全て保持）
            main_c_fallback, _ = build_candidates(rows, w, "MAIN", all_main)
            sub_c_fallback, _ = build_candidates(rows, w, "SUB", all_sub)
            exp_c_fallback, _ = build_candidates(rows, w, "EXP", all_exp)
            if len(main_c_fallback) > 0 or len(sub_c_fallback) > 0 or len(exp_c_fallback) > 0:
                main_c = main_c_fallback
                sub_c = sub_c_fallback
                exp_c = exp_c_fallback
                st.info("警告付き候補を含めて表示します。")

        # 内部自己対局（200局相当）
        main_c = league_score(main_c, rounds=200)
        sub_c  = league_score(sub_c, rounds=200)
        exp_c  = league_score(exp_c, rounds=200)
        # Bandit で提示順位を最適化（フォロワー増を reward に学習）
        try:
            arms = bandit_get_all()
            main_c = rank_candidates_by_bandit(main_c, arms)
            sub_c  = rank_candidates_by_bandit(sub_c, arms)
            exp_c  = rank_candidates_by_bandit(exp_c, arms)
        except Exception:
            pass

        st.session_state["pack"] = {"MAIN": main_c, "SUB": sub_c, "EXP": exp_c}
        try:
            log_event("generate", payload={"topic": topic, "count": total_final}, meta={"roles": ["MAIN", "SUB", "EXP"]})
        except Exception:
            pass
        st.success("生成完了。上位候補を表示します。")
        

    pack = st.session_state.get("pack")
    if pack:
        st.caption("上位は『擬似報酬×EXP分散スコア×自己対局』で選抜。警告付き候補（政治/経済など）も表示されます。")

        def show_role(role, title):
            st.markdown(f"## {title}")
            raw_cands = pack.get(role, [])
            # None や非リストを安全に処理
            if not raw_cands or not isinstance(raw_cands, list):
                st.info(f"{title}: 候補がありません")
                return
            
            # None や非 dict の要素を除外
            cands = [c for c in raw_cands[:10] if c and isinstance(c, dict)]
            
            if not cands:
                st.info(f"{title}: 有効な候補がありません")
                return
            
            for i, c in enumerate(cands, start=1):
                # text を安全に取得
                t = c.get("text") if isinstance(c, dict) else None
                if not t or not isinstance(t, str):
                    t = "(no text)"
                preview = t.replace("\n", " ")[:30]

                # flagged フラグを取得
                flagged = c.get("flagged", False)
                flagged_reason = c.get("flagged_reason", "")

                # 数値を安全に取得（デフォルト値付き）
                league_val = c.get("league", 0.0)
                pseudo_val = c.get("pseudo", 0.0)
                if not isinstance(league_val, (int, float)):
                    league_val = 0.0
                if not isinstance(pseudo_val, (int, float)):
                    pseudo_val = 0.0

                # expanderタイトルに警告マークを追加
                title_prefix = "⚠️ " if flagged else ""
                with st.expander(f"{title_prefix}#{i} league={league_val:.3f} pseudo={pseudo_val:.3f} {preview}..."):
                    st.write(t)
                    # 安全に dict の値を取得
                    safety_val = c.get("safety", 0.0)
                    novelty_val = c.get("novelty", 0.0)
                    tail_val = c.get("tail", 0.0)
                    st.write({
                        "safety": safety_val if isinstance(safety_val, (int, float)) else 0.0,
                        "novelty": round(novelty_val, 3) if isinstance(novelty_val, (int, float)) else 0.0,
                        "tail": round(tail_val, 3) if isinstance(tail_val, (int, float)) else 0.0,
                        "pseudo": round(pseudo_val, 3),
                        "league": round(league_val, 3),
                    })
                    # flagged候補を警告表示（黄色）
                    if flagged:
                        st.warning(f"⚠️ 警告: {flagged_reason}（人間承認が必要）")
                    st.code(t, language=None)

        show_role("MAIN", "朝(7-9) MAIN：本命（否定×断定）")
        show_role("SUB", "昼(12-13) SUB：準本命（否定×数字/比較）")
        show_role("EXP", "夜(20-22) EXP：実験（質問×逆説 / 分散最大化）")

        st.markdown("## ✅ 承認（今日の3ツイ）")
        col1, col2, col3 = st.columns(3)

        def pick(role, idx_key):
            pack = st.session_state.get("pack") or {}
            raw_cands = pack.get(role, [])
            # None や非リストを安全に処理
            if not raw_cands or not isinstance(raw_cands, list):
                return None
            # None や非 dict の要素を除外
            cands = [c for c in raw_cands if c and isinstance(c, dict)]
            if not cands:
                return None
            idx = st.number_input(idx_key, min_value=1, max_value=min(10, len(cands)), value=1, step=1)
            selected = cands[int(idx)-1]
            return selected if selected and isinstance(selected, dict) else None

        with col1:
            a_main = pick("MAIN", "MAINの採用順位(1-10)")
        with col2:
            a_sub = pick("SUB", "SUBの採用順位(1-10)")
        with col3:
            a_exp = pick("EXP", "EXPの採用順位(1-10)")

        if st.button("この3つを承認して保存（投稿用に固定）"):
            approved = {"MAIN": a_main, "SUB": a_sub, "EXP": a_exp}
            st.session_state["approved"] = approved
            # 永続化：承認した3件をDBへ追記（確定で消えない）
            today_str = str(date.today())
            to_append = []
            for role_name, cand in approved.items():
                if cand and isinstance(cand, dict):
                    text_val = (cand.get("text") or "").strip()
                    to_append.append({
                        "status": STATUS_PINNED,
                        "date": today_str,
                        "role": role_name,
                        "tweet_id": "",
                        "text": text_val,
                        "impressions": "0",
                        "likes": "0",
                        "rts": "0",
                        "replies": "0",
                        "followers_before": "0",
                        "followers_after": "0",
                        "Pseudo": str(cand.get("pseudo", "")),
                        "速報": "",
                        "確定": "",
                        "novelty": str(cand.get("novelty", "")),
                        "safety": str(cand.get("safety", "")),
                        "tail": str(cand.get("tail", "")),
                        "abs_rating_before": f"{abs_rating:.2f}",
                        "abs_rating_after": f"{abs_rating:.2f}",
                        "rel_rating_before": f"{rel_rating:.2f}",
                        "rel_rating_after": f"{rel_rating:.2f}",
                    })
            if to_append:
                try:
                    append_rows(to_append)
                    log_event("pinned", payload={"roles": list(approved.keys()), "count": len(to_append)}, meta={"today": today_str})
                except Exception as e:
                    st.warning(f"DB追記でエラー（承認は画面に保持）: {e}")
            st.success("承認保存しました（DBに記録済み）。②で実測入力へ。")
            for k, v in approved.items():
                if v and isinstance(v, dict):
                    text_val = v.get("text", "")
                    flagged = v.get("flagged", False)
                    flagged_reason = v.get("flagged_reason", "")
                    st.markdown(f"**{k}**")
                    if flagged:
                        st.warning(f"⚠️ 警告: {flagged_reason}（人間承認が必要）")
                    st.code(text_val if text_val else "(no text)", language=None)
                else:
                    st.markdown(f"**{k}**")
                    st.info("候補が選択されていません")

# =========================================================
# ② 実測入力（手入力最小/CSV可）
# =========================================================
with tab2:
    st.subheader("実測入力：速報→確定（手入力最小）")
    approved = st.session_state.get("approved", {})

    role_pick = st.selectbox("対象（役割）", ["MAIN","SUB","EXP"], index=0)
    default_text = (approved.get(role_pick) or {}).get("text", "")

    st.caption("最小入力：tweet_id(任意) + インプレ/いいね/RT/返信 + フォロワー前後（確定スコア用）")
    text = st.text_area("投稿文（コピペ）", value=default_text, height=120)
    tweet_id = st.text_input("tweet_id（任意）", value="")

    c1, c2, c3 = st.columns(3)
    with c1:
        impr = st.number_input("インプレッション", min_value=0, value=0, step=1)
        likes = st.number_input("いいね", min_value=0, value=0, step=1)
    with c2:
        rts = st.number_input("RT", min_value=0, value=0, step=1)
        replies = st.number_input("返信", min_value=0, value=0, step=1)
    with c3:
        fol_before = st.number_input("フォロワー（前）", min_value=0, value=0, step=1)
        fol_after = st.number_input("フォロワー（後）", min_value=0, value=0, step=1)

    # 自動採点（擬似）
    saf = safety_score_01(text)
    nov = novelty_score(text, rows, window=300)
    tail = tail_score(text)
    comps = pseudo_reward_components(text, novelty=nov, safety=saf, tail=tail)
    ps = pseudo_score(comps, w)
    if role_pick == "EXP":
        ps = 0.35 * ps + 0.65 * exp_utility(tail=tail, novelty=nov, safety=saf)

    s速報 =速報_score(int(impr), int(likes), int(rts), int(replies))
    s確定 =確定_score(int(impr), int(likes), int(rts), int(replies), int(fol_before), int(fol_after))

    st.write("### 自動採点")
    st.write({
        "Pseudo(擬似)": round(ps, 3),
        "速報": round(s速報, 3),
        "確定": round(s確定, 3),
        "novelty": round(nov, 3),
        "safety": saf,
        "tail": round(tail, 3),
    })

    if st.button("保存（棋譜に追加）＋ 重み学習（擬似↔確定ズレ）＋ レート更新"):

        # 学習：擬似→確定のズレで重み更新
        y_true = float(s確定)
        y_pred = float(ps)
        w = sgd_update(w, comps, y_true=y_true, y_pred=y_pred, lr=0.35, l2=0.0005)
        save_weights(W_PATH, w)

        # レート更新（絶対/相対）
        abs_before = abs_rating
        rel_before = rel_rating

        abs_rating = elo_update(abs_rating, score01=y_true, baseline=float(baseline), k=float(k_abs))
        # 相対：自分の最近平均（簡易）
        tail_rows = rows[-30:] if len(rows) > 30 else rows
        if tail_rows:
            recent_mean = sum(float(r.get("確定", 0) or 0) for r in tail_rows) / max(1, len(tail_rows))
        else:
            recent_mean = 0.50
        rel_rating = elo_update(rel_rating, score01=y_true, baseline=float(recent_mean), k=float(k_rel))

        row = {
            "date": str(date.today()),
            "role": role_pick,
            "tweet_id": tweet_id,
            "text": text.strip(),
            "impressions": int(impr),
            "likes": int(likes),
            "rts": int(rts),
            "replies": int(replies),
            "followers_before": int(fol_before),
            "followers_after": int(fol_after),
            "Pseudo": f"{ps:.6f}",
            "速報": f"{s速報:.6f}",
            "確定": f"{s確定:.6f}",
            "novelty": f"{nov:.6f}",
            "safety": f"{saf:.0f}",
            "tail": f"{tail:.6f}",
            "abs_rating_before": f"{abs_before:.2f}",
            "abs_rating_after": f"{abs_rating:.2f}",
            "rel_rating_before": f"{rel_before:.2f}",
            "rel_rating_after": f"{rel_rating:.2f}",
        }
        try:
            append_row(LOG_PATH, row)
            rows = read_rows(LOG_PATH)
            # 成功ツイート自己蒸留：成功なら特徴量をテンプレに保存
            row_for_success = {k: str(v) for k, v in row.items()}
            if is_success_row(row_for_success):
                try:
                    feats = extract_features(text)
                    save_success_template(json.dumps(feats, ensure_ascii=False), float(s確定))
                except Exception:
                    pass
            # Bandit 更新（reward = 確定スコア、フォロワー増を反映済み）
            try:
                arm_id = arm_id_from_cand({"role": role_pick, "text": text})
                bandit_update(arm_id, float(s確定))
            except Exception:
                pass
        except Exception as e:
            st.error(f"保存に失敗しました: {e}")
        else:
            st.success("保存＆学習しました。次の生成から“擬似評価の精度”が上がります。")
            st.info(f"Abs: {abs_before:.1f} → {abs_rating:.1f} / Rel: {rel_before:.1f} → {rel_rating:.1f}")

    st.markdown("---")
    st.subheader("CSV一括取り込み（手入力削減）")
    up = st.file_uploader("Xの分析CSV（任意）", type=["csv"])
    if up is not None:
        df = pd.read_csv(up)
        st.dataframe(df.head(20), use_container_width=True)
        st.caption("列名が違ってもOK。必要列を選んでマッピングしてください。")
        cols = list(df.columns)

        map_id = st.selectbox("tweet_id列", ["(なし)"] + cols, index=0)
        map_text = st.selectbox("text列", ["(なし)"] + cols, index=0)
        map_impr = st.selectbox("impressions列", ["(なし)"] + cols, index=0)
        map_likes = st.selectbox("likes列", ["(なし)"] + cols, index=0)
        map_rts = st.selectbox("rts列", ["(なし)"] + cols, index=0)
        map_replies = st.selectbox("replies列", ["(なし)"] + cols, index=0)
        map_fol_b = st.selectbox("followers_before列", ["(なし)"] + cols, index=0)
        map_fol_a = st.selectbox("followers_after列", ["(なし)"] + cols, index=0)
        map_role = st.selectbox("role列(任意)", ["(なし)"] + cols, index=0)

        if st.button("CSVを自動学習（擬似↔確定ズレで重み更新）"):
            learned = 0
            for _, r in df.iterrows():
                txt = str(r[map_text]) if map_text != "(なし)" else ""
                if not txt:
                    continue
                impr = int(r[map_impr]) if map_impr != "(なし)" else 0
                likes = int(r[map_likes]) if map_likes != "(なし)" else 0
                rts = int(r[map_rts]) if map_rts != "(なし)" else 0
                replies = int(r[map_replies]) if map_replies != "(なし)" else 0
                fb = int(r[map_fol_b]) if map_fol_b != "(なし)" else 0
                fa = int(r[map_fol_a]) if map_fol_a != "(なし)" else 0

                saf = safety_score_01(txt)
                nov = novelty_score(txt, rows, window=300)
                tail = tail_score(txt)
                comps = pseudo_reward_components(txt, novelty=nov, safety=saf, tail=tail)
                ps = pseudo_score(comps, w)
                y_true = float(確定_score(impr, likes, rts, replies, fb, fa))
                w = sgd_update(w, comps, y_true=y_true, y_pred=ps, lr=0.25, l2=0.0005)
                learned += 1

            save_weights(W_PATH, w)
            st.success(f"CSVから学習完了: {learned}件")
            st.info("次回生成から精度が上がります。")

# =========================================================
# ③ 分析・学習（重み/レート）
# =========================================================
with tab3:
    st.subheader("レーティング & 学習状態")
    colA, colB, colC = st.columns(3)
    colA.metric("Abs Rating", f"{abs_rating:.1f}")
    colB.metric("Rel Rating", f"{rel_rating:.1f}")
    colC.metric("Weights更新日", str(date.today()))

    st.write("### 学習中の重み（擬似→確定の当たり方）")
    st.json(w)

    # リプレイバッファで学習強化（失敗・低評価を優先サンプル）
    if rows and st.button("リプレイで学習強化（直近＋低評価優先で重み更新）"):
        sampled = sample_for_learning(rows, k=50, recent_first=20)
        learned = 0
        for r in sampled:
            try:
                txt = (r.get("text") or "").strip()
                if not txt:
                    continue
                nov = float(r.get("novelty") or 0)
                saf = float(r.get("safety") or 0)
                tail = float(r.get("tail") or 0)
                comps = pseudo_reward_components(txt, novelty=nov, safety=saf, tail=tail)
                y_true = float(r.get("確定") or 0)
                ps = float(r.get("Pseudo") or 0)
                w = sgd_update(w, comps, y_true=y_true, y_pred=ps, lr=0.2, l2=0.0005)
                learned += 1
            except Exception:
                continue
        if learned > 0:
            save_weights(W_PATH, w)
            st.success(f"リプレイ学習: {learned}件で重みを更新しました。")
        else:
            st.info("学習対象がありませんでした。")

    if rows:
        df = pd.DataFrame(rows)
        for c in ["Pseudo","速報","確定","novelty","tail","abs_rating_after","rel_rating_after"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        st.write("### 最近の棋譜（30件）")
        recent = df.tail(30)
        st.dataframe(recent, use_container_width=True)
        # 論理削除：誤入力した行を取り消し
        if "id" in df.columns:
            st.caption("誤入力した行は下の「取り消し」で論理削除できます。")
            ids_in_recent = [(r.get("id"), r.get("text", "")) for _, r in recent.iterrows() if r.get("id") is not None]
            to_delete = st.selectbox(
                "取り消す行（ID=行番号）",
                options=["(選択しない)"] + [f"id={rid} | {str(txt)[:40]}..." for rid, txt in ids_in_recent],
                index=0,
                key="delete_row_select",
            )
            if to_delete != "(選択しない)" and st.button("この行を取り消す（論理削除）"):
                try:
                    rid = int(to_delete.split("|")[0].replace("id=", "").strip())
                    if logical_delete_tweet(row_id=rid):
                        st.success("取り消しました。")
                        st.rerun()
                    else:
                        st.warning("取り消しに失敗しました。")
                except Exception as e:
                    st.error(f"取り消しエラー: {e}")

        # 編集：1行選んでフォームで編集 → 保存
        st.markdown("---")
        st.subheader("棋譜の編集")
        if "id" in df.columns:
            recent_list = recent.to_dict("records")
            to_edit = st.selectbox(
                "編集する行（ID=行番号）",
                options=["(選択しない)"] + [f"id={r.get('id')} | {str(r.get('text', ''))[:40]}..." for r in recent_list if r.get("id") is not None],
                index=0,
                key="edit_row_select",
            )
            if to_edit != "(選択しない)":
                try:
                    rid = int(to_edit.split("|")[0].replace("id=", "").strip())
                    row_to_edit = next((r for r in recent_list if r.get("id") == rid), None)
                    if row_to_edit is not None:
                        with st.form("edit_tweet_form"):
                            st.caption("編集後「保存」でDBに反映します。")
                            edit_text = st.text_area("投稿文", value=(row_to_edit.get("text") or ""), height=100, key="edit_text")
                            _r = (row_to_edit.get("role") or "MAIN").strip()
                            _idx = ["MAIN", "SUB", "EXP"].index(_r) if _r in ["MAIN", "SUB", "EXP"] else 0
                            edit_role = st.selectbox("role", ["MAIN", "SUB", "EXP"], index=_idx, key="edit_role")
                            def _safe_int(v, default: int = 0) -> int:
                                try:
                                    return int(float(v)) if v is not None and str(v).strip() != "" else default
                                except (TypeError, ValueError):
                                    return default
                            c1, c2, c3 = st.columns(3)
                            with c1:
                                edit_impr = st.number_input("インプレッション", min_value=0, value=_safe_int(row_to_edit.get("impressions")), step=1, key="edit_impr")
                                edit_likes = st.number_input("いいね", min_value=0, value=_safe_int(row_to_edit.get("likes")), step=1, key="edit_likes")
                            with c2:
                                edit_rts = st.number_input("RT", min_value=0, value=_safe_int(row_to_edit.get("rts")), step=1, key="edit_rts")
                                edit_replies = st.number_input("返信", min_value=0, value=_safe_int(row_to_edit.get("replies")), step=1, key="edit_replies")
                            with c3:
                                edit_fol_b = st.number_input("フォロワー（前）", min_value=0, value=_safe_int(row_to_edit.get("followers_before")), step=1, key="edit_fol_b")
                                edit_fol_a = st.number_input("フォロワー（後）", min_value=0, value=_safe_int(row_to_edit.get("followers_after")), step=1, key="edit_fol_a")
                            edit_tweet_id = st.text_input("tweet_id（任意）", value=(row_to_edit.get("tweet_id") or ""), key="edit_tweet_id")
                            if st.form_submit_button("保存して反映"):
                                patch = {
                                    "text": edit_text,
                                    "role": edit_role,
                                    "impressions": edit_impr,
                                    "likes": edit_likes,
                                    "rts": edit_rts,
                                    "replies": edit_replies,
                                    "followers_before": edit_fol_b,
                                    "followers_after": edit_fol_a,
                                    "tweet_id": edit_tweet_id,
                                }
                                if update_by_id(rid, patch):
                                    st.success("更新しました。")
                                    st.rerun()
                                else:
                                    st.warning("更新に失敗しました。")
                except Exception as e:
                    st.error(f"編集エラー: {e}")
        st.caption("見方：Pseudoが確定に寄ってきたら『疑似報酬が賢くなった』＝超高速学習が成立。")
    else:
        st.warning("まだ棋譜がありません。②で1件入れると学習が始まります。")
