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
from safety import safety_score_01, safety_check, compute_safety_score
from novelty import novelty_score
from exp_score import tail_score, exp_utility
from scoring import pseudo_reward_components, pseudo_score, quality_score, 速報_score, 確定_score
from weights import DEFAULT_W, sgd_update
from selfplay import league_score
from storage import (
    init_db,
    get_conn,
    read_rows,
    append_row,
    append_rows,
    update_row,
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
    DRAFT_STATUS_DRAFT,
    DRAFT_STATUS_APPROVED,
    DRAFT_STATUS_POSTED,
    DRAFT_STATUS_REJECTED,
    DRAFT_STATUS_SCHEDULED,
    insert_draft,
    read_drafts,
    update_draft,
    update_draft_status,
    delete_draft,
    get_scheduled_drafts,
)
try:
    from storage import update_by_id
except ImportError:
    update_by_id = None  # 編集機能は未反映時もアプリは起動させる
from replay import sample_for_learning
from distill import is_success_row, extract_features, to_guideline_line
from bandit import arm_id_from_cand, rank_candidates_by_bandit
from vocabulary_diversity import (
    recent_word_frequency,
    get_overused_words,
    vocab_diversity_penalty,
    format_synonym_hint,
)
from x_client import is_x_api_available, post_tweet
from analytics import init_analytics, track_event
from adsense_utils import init_adsense, is_ads_enabled
from pages_content import (
    render_about_page,
    render_privacy_page,
    render_terms_page,
    render_contact_page,
    render_blog_page,
)

# =========================
# 基本設定
# =========================
st.set_page_config(page_title="TwitterAI", layout="wide")
init_analytics()  # GA4/GTM タグ挿入（ID未設定なら何もしない）
init_adsense()  # AdSense タグ挿入（ENABLE_ADS=false なら何もしない）

# =========================
# ページルーティング
# =========================
PAGE_HOME = "Home"
PAGE_ABOUT = "About"
PAGE_PRIVACY = "Privacy Policy"
PAGE_TERMS = "Terms"
PAGE_CONTACT = "Contact"
PAGE_BLOG = "Blog/Updates"

# クエリパラメータまたはセッション状態でページを決定
current_page = st.query_params.get("page", PAGE_HOME)
if current_page not in [PAGE_HOME, PAGE_ABOUT, PAGE_PRIVACY, PAGE_TERMS, PAGE_CONTACT, PAGE_BLOG]:
    current_page = PAGE_HOME

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
    diversity_hint: str = "",
) -> tuple[list[str], int]:
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
        diversity_hint=diversity_hint,
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
        # 多段フォールバック対応
        if data.get("__fallback"):
            # 完全フォールバック: 生テキストを1ツイートとして救出
            raw = data.get("raw", "") or ""
            s = postprocess_tweet(raw)
            if s and isinstance(s, str) and s.strip():
                return ([s], 1)
            return ([], 0)
        if data.get("__text_fallback") or data.get("__array_fallback"):
            # テキスト行分割/配列フォールバック
            items = data.get("items", [])
            out = []
            for x in items:
                s = postprocess_tweet(str(x))
                if s and isinstance(s, str) and s.strip():
                    out.append(s)
            return (out, len(out))  # fallback count = rescued items
        arr = data.get(role, [])
        out = []
        for x in arr:
            s = postprocess_tweet(str(x))
            if s and isinstance(s, str) and s.strip():
                out.append(s)
        return (out, 0)
    except RuntimeError as e:
        # API key expired, rate limit exceeded などのエラー時
        err_msg = str(e)
        if "api key" in err_msg.lower() or "expired" in err_msg.lower():
            st.warning(f"⚠️ Gemini API キーが無効または期限切れです: {err_msg}")
            track_event("generate_error", {"error_type": "api_key_invalid", "role": role})
        elif "rate limit" in err_msg.lower() or "quota" in err_msg.lower():
            st.warning(f"⚠️ Gemini API のレート制限に達しました: {err_msg}")
            track_event("generate_error", {"error_type": "rate_limit", "role": role})
        else:
            st.warning(f"⚠️ Gemini API エラー: {err_msg}")
            track_event("generate_error", {"error_type": "gemini_runtime", "role": role})
        return ([], 0)
    except Exception as e:
        st.warning(f"⚠️ 予期しないエラーが発生しました: {str(e)}")
        track_event("generate_error", {"error_type": "unexpected", "role": role})
        return ([], 0)

# 明確に危険で除外すべき理由（2段階safetyの第1段階）
DANGEROUS_REASONS = ["暴力/過激", "差別/属性一般化", "個人攻撃"]

def build_candidates(rows, w, role, texts):
    """
    候補を構築。textがNone/emptyは除外。
    safety<=0でも、明確に危険でない限りflaggedフラグを付けて保持。
    語彙多様性: 直近で過多になった語を含む候補にはペナルティを付与（減点のみ、禁止ではない）。
    """
    cands = []
    empty_text_dropped = 0
    safety_dropped_count = 0
    safety_flagged_count = 0

    # 直近N件で語彙頻度を算出（ペナルティ用）
    recent_n = 15
    recent_rows = (rows or [])[-20:] if (rows or []) else []
    freq_map = recent_word_frequency(recent_rows, n=recent_n) if recent_rows else {}

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

        # 語彙多様性: 直近で過多の語を含む場合は軽く減点（連呼抑制、完全禁止ではない）
        penalty = vocab_diversity_penalty(t, freq_map, threshold=3, penalty_per_word=0.08, cap=0.25)
        ps = max(0.0, ps - penalty)

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
            "json_fallback": False,
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
    # ページ選択（AdSense審査用）
    st.subheader("ページ")
    page_selected = st.radio(
        "ページを選択",
        [PAGE_HOME, PAGE_ABOUT, PAGE_PRIVACY, PAGE_TERMS, PAGE_CONTACT, PAGE_BLOG],
        index=[PAGE_HOME, PAGE_ABOUT, PAGE_PRIVACY, PAGE_TERMS, PAGE_CONTACT, PAGE_BLOG].index(current_page) if current_page in [PAGE_HOME, PAGE_ABOUT, PAGE_PRIVACY, PAGE_TERMS, PAGE_CONTACT, PAGE_BLOG] else 0,
        label_visibility="visible",
    )
    if page_selected != current_page:
        st.query_params["page"] = page_selected
        st.rerun()
    
    st.markdown("---")
    
    # Home ページ以外では運用設定を非表示（軽量化）
    if current_page == PAGE_HOME:
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
    else:
        # Home 以外のページでは変数をデフォルト値で初期化（エラー回避）
        api_key = ""
        trend_hint = ""
        per_role = 30
        calls_plan = 3
        baseline = 0.50
        k_abs = 16.0
        k_rel = 16.0

# =========================
# メインコンテンツ（ページ別）
# =========================
if current_page == PAGE_HOME:
    st.title("Twitter自動化/ 超高速学習")
    
    # =========================
    # Tabs
    # =========================
    tab1, tab2, tab3, tab4 = st.tabs(["① 生成→自己対局→承認(3ツイ)", "② 実測入力(手入力最小/CSV可)", "③ 分析・学習(重み/レート)", "④ 自動化（安全ガード付き）"])

    # =========================================================
    # ① 生成→自己対局→承認
    # =========================================================
    with tab1:
        st.subheader("今日のテーマ（あなたっぽさ：冷酷×設計×起業×経済）")
        topic = st.text_input("テーマ", value="起業で失敗する人の共通点")

        if st.button("生成（制限回避）→ 内部自己対局 → 上位を提示"):
            track_event("generate_click", {"topic_length": len(topic), "has_trend": bool(trend_hint.strip())})
            if not usage_can_call(usage):
                st.error("本日のRPD上限に到達。明日また回してください。")
                track_event("generate_error", {"error_type": "rpd_limit"})
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
            # 語彙多様性: 直近で過多の語があればプロンプトに言い換え推奨を注入
            _recent = (rows or [])[-20:]
            _freq = recent_word_frequency(_recent, n=15) if _recent else {}
            _overused = get_overused_words(_freq, threshold=3)
            diversity_hint = format_synonym_hint(_overused)

            def gen_role(
                role: str,
                n_each: int,
                s_guidelines: str = "",
                name_required: bool = False,
                req_kw: list[str] = None,
            ) -> tuple[list[str], int]:
                rl.wait_for_rpm()
                return api_generate(
                    role, topic, trend_hint, n_each, api_key, model,
                    success_guidelines=s_guidelines,
                    named_entity_required=name_required,
                    required_keywords=req_kw or [],
                    diversity_hint=diversity_hint,
                )

            # ===== 生成（calls_plan に応じて回す）=====
            rounds = 1 if calls_plan == 3 else 2
            main_fallback_count = 0
            sub_fallback_count = 0
            exp_fallback_count = 0
            for _ in range(rounds):
                if usage_can_call(usage):
                    lst, fc = gen_role("MAIN", per_role, success_guidelines, named_entity_required, required_keywords)
                    all_main.extend(lst)
                    main_fallback_count = fc
                    usage_inc(usage, 1); save_json(U_PATH, usage)
                if usage_can_call(usage):
                    lst, fc = gen_role("SUB", per_role, success_guidelines, named_entity_required, required_keywords)
                    all_sub.extend(lst)
                    sub_fallback_count = fc
                    usage_inc(usage, 1); save_json(U_PATH, usage)
                if usage_can_call(usage):
                    lst, fc = gen_role("EXP", per_role, success_guidelines, named_entity_required, required_keywords)
                    all_exp.extend(lst)
                    exp_fallback_count = fc
                    usage_inc(usage, 1); save_json(U_PATH, usage)

            # 固有名詞必須: 条件未達なら1回だけ MAIN を再生成
            if named_entity_required and not any_tweet_contains_name(all_main + all_sub + all_exp, topic):
                if usage_can_call(usage):
                    lst, fc = gen_role("MAIN", per_role, success_guidelines, True, required_keywords)
                    all_main.extend(lst)
                    main_fallback_count = fc
                    usage_inc(usage, 1); save_json(U_PATH, usage)
                if not any_tweet_contains_name(all_main + all_sub + all_exp, topic):
                    all_main.insert(0, fallback_tweet_with_name(topic))

            # 必須キーワード（テーマ/トレンド）: 条件未達なら1回だけ MAIN を再生成
            if required_keywords and not any_tweet_contains_keywords(all_main + all_sub + all_exp, required_keywords):
                if usage_can_call(usage):
                    lst, fc = gen_role("MAIN", per_role, success_guidelines, named_entity_required, required_keywords)
                    all_main.extend(lst)
                    main_fallback_count = fc
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
            # JSON フォールバックで救出した候補にフラグと安全なデフォルトを付与
            for i in range(main_fallback_count):
                if main_c and i < len(main_c):
                    c = main_c[-(i + 1)]
                    c["json_fallback"] = True
                    c.setdefault("pseudo", 0.5)
            for i in range(sub_fallback_count):
                if sub_c and i < len(sub_c):
                    c = sub_c[-(i + 1)]
                    c["json_fallback"] = True
                    c.setdefault("pseudo", 0.5)
            for i in range(exp_fallback_count):
                if exp_c and i < len(exp_c):
                    c = exp_c[-(i + 1)]
                    c["json_fallback"] = True
                    c.setdefault("pseudo", 0.5)

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
            st.session_state["_auto_topic"] = topic
            st.session_state["_auto_trend"] = trend_hint
            st.session_state["json_fallback_used"] = (main_fallback_count + sub_fallback_count + exp_fallback_count) > 0
            try:
                log_event("generate", payload={"topic": topic, "count": total_final}, meta={"roles": ["MAIN", "SUB", "EXP"]})
            except Exception:
                pass
            if main_fallback_count or sub_fallback_count or exp_fallback_count:
                st.success("生成完了（形式フォールバック）。上位候補を表示します。")
                track_event("generate_success", {"candidates": total_final, "fallback_used": True, "fallback_count": main_fallback_count + sub_fallback_count + exp_fallback_count})
            else:
                st.success("生成完了。上位候補を表示します。")
                track_event("generate_success", {"candidates": total_final, "fallback_used": False})
            if total_final == 0:
                track_event("generate_error", {"error_type": "zero_candidates"})
        

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
                track_event("confirm_click", {"tab": "tab1", "roles": 3})
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

    # 投稿済みドラフトから選択（自動入力）
    try:
        posted_drafts = read_drafts(status=DRAFT_STATUS_POSTED, limit=20)
    except Exception:
        posted_drafts = []
    if posted_drafts:
        st.caption("投稿済みの下書きから選択して実測データを入力できます。")
        draft_options = ["(手動入力)"] + [
            f"id={d.get('id')} | {d.get('role','?')} | {(d.get('text',''))[:40]}..."
            for d in posted_drafts
        ]
        selected_draft = st.selectbox("投稿済み下書きから選択", draft_options, index=0, key="posted_draft_select")
        if selected_draft != "(手動入力)":
            try:
                sel_id = int(selected_draft.split("|")[0].replace("id=", "").strip())
                sel_draft = next((d for d in posted_drafts if d.get("id") == sel_id), None)
                if sel_draft:
                    st.session_state["_tab2_prefill"] = sel_draft
            except Exception:
                pass

    prefill = st.session_state.get("_tab2_prefill", {})
    role_pick = st.selectbox("対象（役割）", ["MAIN","SUB","EXP"], index=0)
    default_text = prefill.get("text") or (approved.get(role_pick) or {}).get("text", "")

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
        track_event("confirm_click", {"tab": "tab2", "role": role_pick, "char_count": len(text.strip()), "has_metrics": int(impr) > 0})

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
            # 投稿済みドラフトに実測データを追記
            if prefill and prefill.get("id"):
                try:
                    update_draft(prefill["id"], {
                        "score_abs": float(s確定),
                        "score_rel": float(s速報),
                    })
                    log_event("draft_metrics_updated", payload={
                        "draft_id": prefill["id"],
                        "確定": float(s確定),
                        "速報": float(s速報),
                    })
                except Exception:
                    pass
                st.session_state.pop("_tab2_prefill", None)
        except Exception as e:
            st.error(f"保存に失敗しました: {e}")
        else:
            st.success('保存＆学習しました。次の生成から"擬似評価の精度"が上がります。')
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
            track_event("csv_learn_click", {"csv_rows": len(df)})
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
        track_event("replay_train_click", {"total_rows": len(rows)})
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
            track_event("replay_train_success", {"learned": learned})
        else:
            st.info("学習対象がありませんでした。")

    if rows:
        df = pd.DataFrame(rows)
        for c in ["Pseudo","速報","確定","novelty","tail","abs_rating_after","rel_rating_after"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        st.write("### 最近の棋譜（30件）")
        # ステータスで色分け表示
        if "status" in df.columns:
            st.caption("ステータス: pinned=承認済み, confirmed=実測入力済み")
        recent = df.tail(30)
        st.dataframe(recent, use_container_width=True)

        # 下書き件数を表示
        try:
            draft_count = len(read_drafts(status=DRAFT_STATUS_DRAFT, limit=100))
            posted_count = len(read_drafts(status=DRAFT_STATUS_POSTED, limit=100))
            st.caption(f"下書き: {draft_count}件 / 投稿済み: {posted_count}件")
        except Exception:
            pass
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
                track_event("delete_row", {"tab": "tab3"})
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
                track_event("edit_open", {"tab": "tab3"})
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
                                track_event("edit_save", {"tab": "tab3"})
                                if update_by_id is None:
                                    st.error("編集機能は利用できません。storage.update_by_id がインポートされていません。")
                                else:
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

    # =========================================================
    # ④ 自動化（安全ガード付き）
    # =========================================================
    with tab4:
        st.subheader("自動化ダッシュボード（安全ガード付き）")

    # --- 設定パネル ---
    auto_col1, auto_col2, auto_col3 = st.columns(3)
    with auto_col1:
        auto_post_enabled = st.toggle("自動投稿を有効にする", value=False, key="auto_post_toggle")
        x_available = is_x_api_available()
        if auto_post_enabled and not x_available:
            st.warning("X API認証情報が未設定です。手動投稿モードで動作します。")
            auto_post_enabled = False
    with auto_col2:
        safety_threshold = st.slider("安全閾値", 0.0, 1.0, 0.7, 0.05, key="safety_thresh")
    with auto_col3:
        quality_threshold = st.slider("品質閾値", 0.0, 1.0, 0.5, 0.05, key="quality_thresh")

    st.markdown("---")

    # --- 生成候補を下書きに保存 ---
    st.subheader("候補を下書きに保存")
    pack = st.session_state.get("pack")
    if pack:
        save_topic = st.session_state.get("_auto_topic", topic if "topic" in dir() else "")
        save_trend = st.session_state.get("_auto_trend", trend_hint if "trend_hint" in dir() else "")

        if st.button("生成候補を全て下書きに保存", key="save_all_drafts"):
            track_event("draft_save_all_click", {"tab": "tab4"})
            saved_count = 0
            for role_name in ["MAIN", "SUB", "EXP"]:
                cands = pack.get(role_name, [])
                if not cands or not isinstance(cands, list):
                    continue
                for c in cands[:10]:  # 上位10件ずつ
                    if not c or not isinstance(c, dict):
                        continue
                    text_val = (c.get("text") or "").strip()
                    if not text_val:
                        continue

                    # 安全・品質スコア算出
                    safety_info = compute_safety_score(text_val, save_topic)
                    q_score = quality_score(text_val, save_topic, save_trend)

                    flags = safety_info.get("flags", {})
                    if not safety_info.get("auto_post_ok", False):
                        flags["auto_blocked"] = True
                        flags["block_reason"] = "安全スコア不足/リスクトピック"

                    try:
                        insert_draft({
                            "topic": save_topic,
                            "trend_hint": save_trend,
                            "role": role_name,
                            "text": text_val,
                            "pseudo": float(c.get("pseudo", 0.0)),
                            "league": float(c.get("league", 0.0)),
                            "safety_score": safety_info.get("safety_score", 0.0),
                            "quality_score": q_score,
                            "novelty": float(c.get("novelty", 0.0)),
                            "tail": float(c.get("tail", 0.0)),
                            "flags": flags,
                            "status": DRAFT_STATUS_DRAFT,
                        })
                        saved_count += 1
                    except Exception as e:
                        st.warning(f"下書き保存エラー: {e}")
            if saved_count > 0:
                try:
                    log_event("drafts_saved", payload={"count": saved_count})
                except Exception:
                    pass
                st.success(f"{saved_count}件の候補を下書きに保存しました。")
            else:
                st.info("保存対象の候補がありません。")
    else:
        st.info("まず①タブで生成してください。生成後に候補を下書きとして保存できます。")

    st.markdown("---")

    # --- 下書き一覧 ---
    st.subheader("下書き一覧")
    filter_status = st.selectbox(
        "ステータスで絞り込み",
        ["全て", "draft", "approved", "scheduled", "posted", "rejected"],
        index=0,
        key="draft_filter",
    )

    try:
        if filter_status == "全て":
            drafts = read_drafts(status=None, limit=100)
        else:
            drafts = read_drafts(status=filter_status, limit=100)
    except Exception as e:
        st.error(f"下書き読み込みエラー: {e}")
        drafts = []

    if drafts:
        st.caption(f"表示件数: {len(drafts)}件")

        for d in drafts:
            draft_id = d.get("id", "?")
            status = d.get("status", "draft")
            role = d.get("role", "?")
            text = (d.get("text") or "")[:60]
            s_score = d.get("safety_score", 0.0)
            q_score_val = d.get("quality_score", 0.0)
            flags = d.get("flags", {})

            # ステータスごとの色分け
            status_emoji = {
                "draft": "📝", "approved": "✅", "scheduled": "⏰",
                "posted": "🚀", "rejected": "❌",
            }.get(status, "❓")

            # リスク表示
            risk_warning = ""
            if flags.get("auto_blocked"):
                risk_warning = " ⚠️ 自動投稿ブロック"
            if flags.get("topic_risk"):
                risk_warning += f" [リスク: {', '.join(flags['topic_risk'])}]"

            with st.expander(
                f"{status_emoji} [{status}] {role} | 安全={s_score:.2f} 品質={q_score_val:.2f}{risk_warning} | {text}...",
                expanded=False,
            ):
                st.write(d.get("text", ""))
                detail_col1, detail_col2 = st.columns(2)
                with detail_col1:
                    st.write({
                        "ID": draft_id,
                        "role": role,
                        "topic": d.get("topic", ""),
                        "pseudo": round(float(d.get("pseudo", 0)), 3),
                        "league": round(float(d.get("league", 0)), 3),
                        "novelty": round(float(d.get("novelty", 0)), 3),
                    })
                with detail_col2:
                    st.write({
                        "safety_score": round(s_score, 3),
                        "quality_score": round(q_score_val, 3),
                        "status": status,
                        "tweet_id": d.get("tweet_id", ""),
                        "scheduled_at": d.get("scheduled_at", ""),
                        "posted_at": d.get("posted_at", ""),
                    })
                if flags:
                    st.json(flags)

                # --- アクションボタン ---
                btn_col1, btn_col2, btn_col3, btn_col4 = st.columns(4)

                with btn_col1:
                    if status == "draft" and st.button("承認", key=f"approve_{draft_id}"):
                        track_event("draft_approve", {"tab": "tab4", "role": role})
                        try:
                            update_draft_status(draft_id, DRAFT_STATUS_APPROVED)
                            log_event("draft_approved", payload={"draft_id": draft_id})
                            st.success("承認しました。")
                            st.rerun()
                        except Exception as e:
                            st.error(f"承認エラー: {e}")

                with btn_col2:
                    if status in ("draft", "approved") and st.button("却下", key=f"reject_{draft_id}"):
                        track_event("draft_reject", {"tab": "tab4", "role": role})
                        try:
                            update_draft_status(draft_id, DRAFT_STATUS_REJECTED)
                            log_event("draft_rejected", payload={"draft_id": draft_id})
                            st.success("却下しました。")
                            st.rerun()
                        except Exception as e:
                            st.error(f"却下エラー: {e}")

                with btn_col3:
                    if status in ("draft", "approved"):
                        sched_time = st.text_input(
                            "予約日時 (YYYY-MM-DD HH:MM)",
                            key=f"sched_{draft_id}",
                            placeholder="2025-01-15 09:00",
                        )
                        if st.button("予約", key=f"schedule_{draft_id}"):
                            track_event("draft_schedule", {"tab": "tab4", "role": role})
                            if sched_time and len(sched_time) >= 10:
                                try:
                                    update_draft_status(
                                        draft_id,
                                        DRAFT_STATUS_SCHEDULED,
                                        extra={"scheduled_at": sched_time},
                                    )
                                    log_event("draft_scheduled", payload={"draft_id": draft_id, "scheduled_at": sched_time})
                                    st.success(f"予約しました: {sched_time}")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"予約エラー: {e}")
                            else:
                                st.warning("日時を入力してください。")

                with btn_col4:
                    if status in ("approved", "scheduled"):
                        if st.button("投稿", key=f"post_{draft_id}"):
                            track_event("draft_post_click", {"tab": "tab4", "role": role, "auto_post": auto_post_enabled})
                            post_text = d.get("text", "")
                            # 安全チェック再実行
                            safety_recheck = compute_safety_score(post_text, d.get("topic", ""))
                            if not safety_recheck.get("auto_post_ok", False):
                                st.error("安全チェックに失敗しました。手動確認が必要です。")
                                st.json(safety_recheck.get("flags", {}))
                            elif auto_post_enabled and x_available:
                                # X API で投稿
                                result = post_tweet(post_text)
                                if result.get("success"):
                                    tweet_id_posted = result.get("tweet_id", "")
                                    from datetime import datetime
                                    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                    update_draft_status(
                                        draft_id,
                                        DRAFT_STATUS_POSTED,
                                        extra={"posted_at": now_str, "tweet_id": tweet_id_posted},
                                    )
                                    # 棋譜にも追記
                                    try:
                                        append_row(LOG_PATH, {
                                            "status": STATUS_PINNED,
                                            "date": str(date.today()),
                                            "role": d.get("role", "MAIN"),
                                            "tweet_id": tweet_id_posted,
                                            "text": post_text,
                                            "impressions": "0", "likes": "0", "rts": "0", "replies": "0",
                                            "followers_before": "0", "followers_after": "0",
                                            "Pseudo": str(d.get("pseudo", "")),
                                            "速報": "", "確定": "",
                                            "novelty": str(d.get("novelty", "")),
                                            "safety": str(d.get("safety_score", "")),
                                            "tail": str(d.get("tail", "")),
                                            "abs_rating_before": "", "abs_rating_after": "",
                                            "rel_rating_before": "", "rel_rating_after": "",
                                        })
                                    except Exception:
                                        pass
                                    log_event("draft_posted", payload={"draft_id": draft_id, "tweet_id": tweet_id_posted})
                                    st.success(f"投稿しました! tweet_id={tweet_id_posted}")
                                    st.rerun()
                                else:
                                    st.error(f"投稿失敗: {result.get('error', '不明')}")
                            else:
                                # 手動投稿モード: テキストを表示してコピー
                                st.info("手動投稿モード: 下のテキストをコピーしてXに投稿してください。")
                                st.code(post_text, language=None)
                                if st.button("投稿済みとしてマーク", key=f"mark_posted_{draft_id}"):
                                    from datetime import datetime
                                    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                    update_draft_status(
                                        draft_id,
                                        DRAFT_STATUS_POSTED,
                                        extra={"posted_at": now_str},
                                    )
                                    log_event("draft_posted_manual", payload={"draft_id": draft_id})
                                    st.success("投稿済みとしてマークしました。")
                                    st.rerun()
    else:
        st.info("下書きがありません。①タブで生成後、「候補を全て下書きに保存」してください。")

    st.markdown("---")

    # --- スケジュール処理（擬似スケジューラ） ---
    st.subheader("スケジュール処理")
    if st.button("予約済み下書きを今すぐ処理", key="process_scheduled"):
        track_event("schedule_process_click", {"tab": "tab4"})
        try:
            scheduled = get_scheduled_drafts()
            if not scheduled:
                st.info("処理対象の予約がありません。")
            else:
                processed = 0
                for sd in scheduled:
                    sd_id = sd.get("id")
                    sd_text = sd.get("text", "")
                    sd_safety = compute_safety_score(sd_text, sd.get("topic", ""))

                    if not sd_safety.get("auto_post_ok", False):
                        st.warning(f"ID={sd_id}: 安全チェック不合格 → スキップ（手動承認へ）")
                        update_draft_status(sd_id, DRAFT_STATUS_DRAFT, extra={"flags": sd_safety.get("flags", {})})
                        continue

                    if auto_post_enabled and x_available:
                        result = post_tweet(sd_text)
                        if result.get("success"):
                            from datetime import datetime
                            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            update_draft_status(
                                sd_id,
                                DRAFT_STATUS_POSTED,
                                extra={"posted_at": now_str, "tweet_id": result.get("tweet_id", "")},
                            )
                            log_event("scheduled_posted", payload={"draft_id": sd_id, "tweet_id": result.get("tweet_id", "")})
                            processed += 1
                        else:
                            st.warning(f"ID={sd_id}: 投稿失敗 - {result.get('error', '不明')}")
                    else:
                        st.info(f"ID={sd_id}: 手動投稿モード。テキストをコピーしてください:")
                        st.code(sd_text, language=None)
                        processed += 1

                if processed > 0:
                    st.success(f"{processed}件の予約を処理しました。")
        except Exception as e:
            st.error(f"スケジュール処理エラー: {e}")

    # --- 条件付き自動投稿（Phase2） ---
    st.markdown("---")
    st.subheader("条件付き自動投稿")
    st.caption("安全スコア・品質スコアが閾値を超えた下書きのみ自動投稿候補にします。")

    if st.button("閾値超え候補を一括承認", key="auto_approve"):
        track_event("auto_approve_click", {"safety_threshold": safety_threshold, "quality_threshold": quality_threshold})
        try:
            all_drafts = read_drafts(status=DRAFT_STATUS_DRAFT, limit=50)
            auto_approved = 0
            for d in all_drafts:
                s = float(d.get("safety_score", 0))
                q = float(d.get("quality_score", 0))
                flags = d.get("flags", {})
                if s >= safety_threshold and q >= quality_threshold and not flags.get("auto_blocked"):
                    update_draft_status(d["id"], DRAFT_STATUS_APPROVED)
                    auto_approved += 1
            if auto_approved > 0:
                log_event("auto_approved", payload={"count": auto_approved, "safety_thresh": safety_threshold, "quality_thresh": quality_threshold})
                st.success(f"{auto_approved}件を自動承認しました。")
            else:
                st.info("閾値を超える候補がありませんでした。")
        except Exception as e:
            st.error(f"自動承認エラー: {e}")

elif current_page == PAGE_ABOUT:
    render_about_page()
elif current_page == PAGE_PRIVACY:
    render_privacy_page()
elif current_page == PAGE_TERMS:
    render_terms_page()
elif current_page == PAGE_CONTACT:
    render_contact_page()
elif current_page == PAGE_BLOG:
    render_blog_page()
