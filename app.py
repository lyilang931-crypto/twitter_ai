# app.py
from __future__ import annotations
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
from weights import load_weights, save_weights, sgd_update
from selfplay import league_score
from storage import read_rows, append_row, load_json, save_json

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

def api_generate(role: str, topic: str, trend_hint: str, n: int, api_key: str, model: str) -> list[str]:
    prompt = build_prompt(topic=topic, trend_hint=trend_hint, n=n, role=role)

    # TPM250目安チェック（超過しそうなら短縮）
    if rough_token_count(prompt) > LIMITS.tpm:
        prompt = prompt[:420]  # 最終手段（安全側）

    rl.wait_for_rpm()

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
        out.append(s)
    return out

def build_candidates(rows, w, role, texts):
    cands = []
    for t in texts:
        saf = safety_score_01(t)
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
        })
    return cands

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
# Load state
# =========================
rows = read_rows(LOG_PATH)
usage = load_json(U_PATH, {})
w = load_weights(W_PATH)

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
    topic = st.text_input("テーマ", value="起業で失敗する人の共通点（努力ではなく設計の問題）")

    if st.button("生成（制限回避）→ 内部自己対局 → 上位を提示"):
        if not usage_can_call(usage):
            st.error("本日のRPD上限に到達。明日また回してください。")
            st.stop()

        st.info("生成開始：途切れ防止・JSON強制・レート制限待機を自動で行います。")

        all_main: list[str] = []
        all_sub: list[str] = []
        all_exp: list[str] = []

        def gen_role(role: str, n_each: int) -> list[str]:
            # RPMを守る
            rl.wait_for_rpm()
            texts = api_generate(role, topic, trend_hint, n_each, api_key, model)
            return texts

        # ===== 生成（calls_plan に応じて回す）=====
        rounds = 1 if calls_plan == 3 else 2
        for _ in range(rounds):
            # MAIN
            if usage_can_call(usage):
                all_main.extend(gen_role("MAIN", per_role))
                usage_inc(usage, 1); save_json(U_PATH, usage)

            # SUB
            if usage_can_call(usage):
                all_sub.extend(gen_role("SUB", per_role))
                usage_inc(usage, 1); save_json(U_PATH, usage)

            # EXP
            if usage_can_call(usage):
                all_exp.extend(gen_role("EXP", per_role))
                usage_inc(usage, 1); save_json(U_PATH, usage)

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
        main_c = build_candidates(rows, w, "MAIN", all_main)
        sub_c  = build_candidates(rows, w, "SUB", all_sub)
        exp_c  = build_candidates(rows, w, "EXP", all_exp)

        # 内部自己対局（200局相当）
        main_c = league_score(main_c, rounds=200)
        sub_c  = league_score(sub_c, rounds=200)
        exp_c  = league_score(exp_c, rounds=200)

        st.session_state["pack"] = {"MAIN": main_c, "SUB": sub_c, "EXP": exp_c}
        st.success("生成完了。上位候補を表示します。")
        

    pack = st.session_state.get("pack")
    if pack:
        st.caption("上位は『擬似報酬×EXP分散スコア×自己対局』で選抜。安全が0のものは自動で落ちます。")

        def show_role(role, title):
            st.markdown(f"## {title}")
            cands = pack.get(role, [])[:10]
            for i, c in enumerate(cands, start=1):
                with st.expander(f"#{i}  league={c.get('league',0):.3f}  pseudo={c.get('pseudo',0):.3f}  {c['text'][:30]}..."):
                    st.write(c["text"])
                    st.write({
                        "safety": c["safety"],
                        "novelty": round(c["novelty"], 3),
                        "tail": round(c["tail"], 3),
                        "pseudo": round(c["pseudo"], 3),
                        "league": round(c.get("league", 0.0), 3),
                    })
                    if c["safety"] <= 0.0:
                        st.warning("Safety=0：危険判定（自動ボツ）")
                    st.code(c["text"], language=None)

        show_role("MAIN", "朝(7-9) MAIN：本命（否定×断定）")
        show_role("SUB", "昼(12-13) SUB：準本命（否定×数字/比較）")
        show_role("EXP", "夜(20-22) EXP：実験（質問×逆説 / 分散最大化）")

        st.markdown("## ✅ 承認（今日の3ツイ）")
        col1, col2, col3 = st.columns(3)

        def pick(role, idx_key):
            cands = pack.get(role, [])
            if not cands:
                return None
            idx = st.number_input(idx_key, min_value=1, max_value=min(10, len(cands)), value=1, step=1)
            return cands[int(idx)-1]

        with col1:
            a_main = pick("MAIN", "MAINの採用順位(1-10)")
        with col2:
            a_sub = pick("SUB", "SUBの採用順位(1-10)")
        with col3:
            a_exp = pick("EXP", "EXPの採用順位(1-10)")

        if st.button("この3つを承認して保存（投稿用に固定）"):
            approved = {"MAIN": a_main, "SUB": a_sub, "EXP": a_exp}
            st.session_state["approved"] = approved
            st.success("承認保存しました。②で実測入力へ。")
            for k, v in approved.items():
                st.markdown(f"**{k}**")
                st.code(v["text"])

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
        append_row(LOG_PATH, row)
        rows = read_rows(LOG_PATH)

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

    if rows:
        df = pd.DataFrame(rows)
        for c in ["Pseudo","速報","確定","novelty","tail","abs_rating_after","rel_rating_after"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        st.write("### 最近の棋譜（30件）")
        st.dataframe(df.tail(30), use_container_width=True)

        st.caption("見方：Pseudoが確定に寄ってきたら『疑似報酬が賢くなった』＝超高速学習が成立。")
    else:
        st.warning("まだ棋譜がありません。②で1件入れると学習が始まります。") # selfplay.py
import random
from typing import List, Dict, Any, Tuple

def league_score(cands: List[Dict[str, Any]], rounds: int = 200) -> List[Dict[str, Any]]:
    """
    200局の“比較対局”をローカルで回す（APIゼロ）
    各候補の疑似スコア（pseudo）を勝率に変換して対戦
    """
    if not cands:
        return cands

    # 初期
    for c in cands:
        c["wins"] = 0
        c["games"] = 0

    n = len(cands)
    for _ in range(rounds):
        a, b = random.sample(range(n), 2)
        A, B = cands[a], cands[b]
        pa = float(A.get("pseudo", 0.0))
        pb = float(B.get("pseudo", 0.0))
        # 擬似スコアが高いほど勝ちやすい（温度）
        pwin = 0.5 + 0.45 * (pa - pb)
        pwin = max(0.05, min(0.95, pwin))
        if random.random() < pwin:
            A["wins"] += 1
        else:
            B["wins"] += 1
        A["games"] += 1
        B["games"] += 1

    for c in cands:
        g = max(1, int(c["games"]))
        c["league"] = float(c["wins"]) / g

    cands.sort(key=lambda x: (x.get("league", 0.0), x.get("pseudo", 0.0)), reverse=True)
    return cands