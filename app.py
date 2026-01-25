# app.py
from __future__ import annotations

import time
from datetime import date
import random
import re

import streamlit as st
import pandas as pd

from rate_limit import RateLimiter, Limits, rough_token_count
from llm_gemini import gemini_json
from prompts import build_prompt

from safety import safety_score_01
from novelty import novelty_score
from exp_score import tail_score, exp_utility
from scoring import pseudo_reward_components, pseudo_score, 速報_score, 確定_score
from weights import load_weights, save_weights, sgd_update
from selfplay import league_score
from storage import read_rows, append_row, load_json, save_json


# =========================
# 基本設定
# =========================
st.set_page_config(page_title="Twitter 将棋AI式（超高速学習・完成版）", layout="wide")
st.title("Twitter 将棋AI式 自動化（完成版）— 1日3ツイ / 超高速学習 / 制限回避")

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
    # キー名固定（あなたの要望優先）: Gemini_API_KEY
    # ついでに GEMINI_API_KEY も fallback
    try:
        k = st.secrets.get("Gemini_API_KEY", "") or st.secrets.get("GEMINI_API_KEY", "")
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
    if t.startswith('"') and t.endswith('"') and len(t) > 2:
        t = t[1:-1].strip()

    if len(t) > 140:
        t = t[:140]
        if t and t[-1] not in ["。", "！", "?", "？"]:
            t = t.rstrip("、, ") + "。"

    if len(t) < 55:
        t = t + " 今日は何を捨てる？"
        if len(t) > 140:
            t = t[:140]
    return t


# =========================
# ローカル増殖（APIなし）
# =========================
_NUMS = ["3日", "7日", "14日", "30日", "90日", "1年", "2倍", "3倍", "10%", "20%", "80%", "90%"]

def mutate_one(t: str) -> str:
    s = (t or "").strip()

    # 数字っぽい表現差し替え
    if random.random() < 0.55:
        pick = random.choice(_NUMS)
        s = re.sub(r"(\d+%|\d+日|\d+年|\d+倍)", pick, s)

    # 冒頭フック揺らぎ
    if random.random() < 0.35:
        hooks = ["結論:", "断言:", "逆に言うと:", "残酷な話:", "本質:", ""]
        s = random.choice(hooks) + s
        s = s.replace("結論:結論:", "結論:")

    # 末尾を問い/断定に揺らす（EXP向き）
    if random.random() < 0.30:
        if s.endswith("。"):
            s = s[:-1]
        tail = random.choice(["。", "。どうする？", "。あなたはどっち？", "。今日何を捨てる？"])
        s = s + tail

    return postprocess_tweet(s)


def local_expand(seeds: list[str], target_n: int) -> list[str]:
    out: list[str] = []
    seen = set()

    for x in seeds:
        x = postprocess_tweet(x)
        if x and x not in seen:
            out.append(x)
            seen.add(x)

    if not out:
        return out

    tries = 0
    while len(out) < target_n and tries < target_n * 8:
        base = random.choice(out)
        m = mutate_one(base)
        if m and m not in seen:
            out.append(m)
            seen.add(m)
        tries += 1

    return out[:target_n]


# =========================
# API生成（roleごと）
# =========================
def api_generate(role: str, topic: str, trend_hint: str, n: int, api_key: str, model: str) -> list[str]:
    prompt = build_prompt(topic=topic, trend_hint=trend_hint, n=n, role=role)

    # TPM250目安（入力が長いなら短縮）
    if rough_token_count(prompt) > LIMITS.tpm:
        prompt = prompt[:420]

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
    return [postprocess_tweet(str(x)) for x in arr]


def build_candidates(rows, w, role, texts):
    cands = []
    for t in texts:
        saf = safety_score_01(t)
        nov = novelty_score(t, rows, window=300)
        tail = tail_score(t)

        comps = pseudo_reward_components(t, novelty=nov, safety=saf, tail=tail)
        ps = pseudo_score(comps, w)

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


def elo_update(r: float, score01: float, baseline: float = 0.50, k: float = 16.0) -> float:
    return float(r + k * (score01 - baseline))


def uniq(xs: list[str]) -> list[str]:
    seen = set()
    out = []
    for x in xs:
        if x and x not in seen:
            out.append(x)
            seen.add(x)
    return out
# =========================
# Load state（セッションに保持）
# =========================
if "rows" not in st.session_state:
    st.session_state["rows"] = read_rows(LOG_PATH)

if "usage" not in st.session_state:
    st.session_state["usage"] = load_json(U_PATH, {})

if "w" not in st.session_state:
    st.session_state["w"] = load_weights(W_PATH)

def _last_rating(rows, col: str, default: float = 1000.0) -> float:
    if not rows:
        return default
    try:
        return float(rows[-1].get(col, default) or default)
    except Exception:
        return default

if "abs_rating" not in st.session_state:
    st.session_state["abs_rating"] = _last_rating(st.session_state["rows"], "abs_rating_after", 1000.0)

if "rel_rating" not in st.session_state:
    st.session_state["rel_rating"] = _last_rating(st.session_state["rows"], "rel_rating_after", 1000.0)

# 以降は “参照用” にローカルへ落としてOK（毎回再代入されるだけ）
rows = st.session_state["rows"]
usage = st.session_state["usage"]
w = st.session_state["w"]
abs_rating = st.session_state["abs_rating"]
rel_rating = st.session_state["rel_rating"]


# =========================
# Sidebar
# =========================
with st.sidebar:
    st.subheader("運用設定（制限回避）")
    api_key = load_api_key()
    st.caption(f"RPD: {LIMITS.rpd} / RPM: {LIMITS.rpm} / TPM目安: {LIMITS.tpm}")

    today_calls = int(usage.get(today_key(), {}).get("calls", 0))
    st.metric("本日APIコール数", f"{today_calls} / {LIMITS.rpd}")

    model = "gemini-flash-latest"
    st.write(f"モデル: **{model}**（固定）")

    if not api_key:
        st.error("Secretsに Gemini_API_KEY（または GEMINI_API_KEY）が未設定です。")
        st.stop()

    st.subheader("トレンド入力（手入力・最小）")
    trend_hint = st.text_input("今日のトレンド（任意）", value="日経・AI・スタートアップ・副業・金利")

    st.subheader("候補数（API→ローカル増殖）")
    target = st.selectbox("生成モード", ["最速(各30→増殖)", "強め(各40→増殖)"], index=0)
    per_role = 30 if target.startswith("最速") else 40

    # ローカル増殖目標（速さ優先なら 120〜180 がちょうど良い）
    TARGET_MAIN = st.slider("MAIN最終候補数", 60, 180, 120, 10)
    TARGET_SUB  = st.slider("SUB最終候補数", 60, 180, 120, 10)
    TARGET_EXP  = st.slider("EXP最終候補数", 80, 220, 160, 10)

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

    if "last_gen_time" not in st.session_state:
        st.session_state["last_gen_time"] = 0.0

    if st.button("今日の3ツイ候補を生成 → 仮想自己対局"):
        now = time.time()

        # 手動連打のブレーキ（1分）
        if now - float(st.session_state["last_gen_time"]) < 60:
            st.warning("⏳ 60秒待ってから再実行してください（RPM回避）")
            st.stop()
        st.session_state["last_gen_time"] = now

        # RPDチェック（3コール分必要）
        remaining = LIMITS.rpd - int(usage.get(today_key(), {}).get("calls", 0))
        if remaining < 3:
            st.error("本日のRPD残が不足（3回必要）。明日また回してください。")
            st.stop()

        st.info("生成開始：RPM/RPDを守りつつ、候補をローカルで増殖→自己対局します。")

        # --- API生成（3回だけ） ---
        all_main = api_generate("MAIN", topic, trend_hint, per_role, api_key, model); usage = usage_inc(usage, 1); save_json(U_PATH, usage)
        all_sub  = api_generate("SUB",  topic, trend_hint, per_role, api_key, model); usage = usage_inc(usage, 1); save_json(U_PATH, usage)
        all_exp  = api_generate("EXP",  topic, trend_hint, per_role, api_key, model); usage = usage_inc(usage, 1); save_json(U_PATH, usage)
                        # 返りが少なすぎる時は安全に停止（増殖の質が死ぬ）
        if len(all_main) < 5 or len(all_sub) < 5 or len(all_exp) < 5:
            st.error("生成件数が少なすぎます（<5）。もう一度実行してください。")
            st.stop()

        # ✅✅✅ ここが「増殖を貼る場所」：生成直後／自己対局の前 ✅✅✅
        all_main = local_expand(all_main, TARGET_MAIN)
        all_sub  = local_expand(all_sub,  TARGET_SUB)
        all_exp  = local_expand(all_exp,  TARGET_EXP)

        # 重複除去
        all_main, all_sub, all_exp = uniq(all_main), uniq(all_sub), uniq(all_exp)

        # 候補 → 擬似採点 → 自己対局200局
        main_c = league_score(build_candidates(rows, w, "MAIN", all_main), rounds=200)
        sub_c  = league_score(build_candidates(rows, w, "SUB",  all_sub),  rounds=200)
        exp_c  = league_score(build_candidates(rows, w, "EXP",  all_exp),  rounds=200)

        st.session_state["pack"] = {"MAIN": main_c, "SUB": sub_c, "EXP": exp_c}
        st.success("生成完了。上位候補を表示します。")

    pack = st.session_state.get("pack")
    if pack:
        st.caption("上位は『擬似報酬×EXP分散スコア×自己対局』で選抜。Safety=0は自動で落とす想定。")

        def show_role(role, title):
            st.markdown(f"## {title}")
            cands = pack.get(role, [])[:10]
            for i, c in enumerate(cands, start=1):
                with st.expander(f"#{i} league={c.get('league',0):.3f} pseudo={c.get('pseudo',0):.3f}  {c['text'][:30]}..."):
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
        show_role("SUB",  "昼(12-13) SUB：準本命（否定×数字/比較）")
        show_role("EXP",  "夜(20-22) EXP：実験（質問×逆説 / 分散最大化）")

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

    role_pick = st.selectbox("対象（役割）", ["MAIN", "SUB", "EXP"], index=0)
    default_text = (approved.get(role_pick) or {}).get("text", "")

    st.caption("最小入力：tweet_id(任意) + インプレ/いいね/RT/返信 + フォロワー前後")
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

    saf = safety_score_01(text)
    nov = novelty_score(text, st.session_state["rows"], window=300)
    tail = tail_score(text)
    comps = pseudo_reward_components(text, novelty=nov, safety=saf, tail=tail)

    w = st.session_state["w"]
    ps = pseudo_score(comps, w)
    if role_pick == "EXP":
        ps = 0.35 * ps + 0.65 * exp_utility(tail=tail, novelty=nov, safety=saf)

    s速報 = 速報_score(int(impr), int(likes), int(rts), int(replies))
    s確定 = 確定_score(int(impr), int(likes), int(rts), int(replies), int(fol_before), int(fol_after))

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
        rows = st.session_state["rows"]
        w = st.session_state["w"]
        abs_rating = float(st.session_state["abs_rating"])
        rel_rating = float(st.session_state["rel_rating"])

        y_true = float(s確定)
        y_pred = float(ps)

        # 学習
        w = sgd_update(w, comps, y_true=y_true, y_pred=y_pred, lr=0.35, l2=0.0005)
        save_weights(W_PATH, w)
        st.session_state["w"] = w

        # レート更新
        abs_before = abs_rating
        rel_before = rel_rating

        abs_rating = elo_update(abs_rating, score01=y_true, baseline=float(baseline), k=float(k_abs))

        tail_rows = rows[-30:] if len(rows) > 30 else rows
        if tail_rows:
            vals = []
            for r in tail_rows:
                try:
                    vals.append(float(r.get("確定", 0) or 0))
                except Exception:
                    pass
            recent_mean = sum(vals) / max(1, len(vals))
        else:
            recent_mean = 0.50

        rel_rating = elo_update(rel_rating, score01=y_true, baseline=float(recent_mean), k=float(k_rel))

        st.session_state["abs_rating"] = abs_rating
        st.session_state["rel_rating"] = rel_rating

        # ログ保存
        row = {
            "date": str(date.today()),
            "role": role_pick,
            "tweet_id": tweet_id,
            "text": (text or "").strip(),
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
        st.session_state["rows"] = rows

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

        map_text = st.selectbox("text列", ["(なし)"] + cols, index=0)
        map_impr = st.selectbox("impressions列", ["(なし)"] + cols, index=0)
        map_likes = st.selectbox("likes列", ["(なし)"] + cols, index=0)
        map_rts = st.selectbox("rts列", ["(なし)"] + cols, index=0)
        map_replies = st.selectbox("replies列", ["(なし)"] + cols, index=0)
        map_fol_b = st.selectbox("followers_before列", ["(なし)"] + cols, index=0)
        map_fol_a = st.selectbox("followers_after列", ["(なし)"] + cols, index=0)

        if st.button("CSVを自動学習（擬似↔確定ズレで重み更新）"):
            w = st.session_state["w"]
            rows = st.session_state["rows"]

            learned = 0
            for _, r in df.iterrows():
                txt = str(r[map_text]) if map_text != "(なし)" else ""
                if not txt:
                    continue
                impr2 = int(r[map_impr]) if map_impr != "(なし)" else 0
                likes2 = int(r[map_likes]) if map_likes != "(なし)" else 0
                rts2 = int(r[map_rts]) if map_rts != "(なし)" else 0
                replies2 = int(r[map_replies]) if map_replies != "(なし)" else 0
                fb2 = int(r[map_fol_b]) if map_fol_b != "(なし)" else 0
                fa2 = int(r[map_fol_a]) if map_fol_a != "(なし)" else 0

                saf2 = safety_score_01(txt)
                nov2 = novelty_score(txt, rows, window=300)
                tail2 = tail_score(txt)
                comps2 = pseudo_reward_components(txt, novelty=nov2, safety=saf2, tail=tail2)
                ps2 = pseudo_score(comps2, w)
                y_true2 = float(確定_score(impr2, likes2, rts2, replies2, fb2, fa2))

                w = sgd_update(w, comps2, y_true=y_true2, y_pred=ps2, lr=0.25, l2=0.0005)
                learned += 1

            save_weights(W_PATH, w)
            st.session_state["w"] = w
            st.success(f"CSVから学習完了: {learned}件")

   


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
        for c in ["Pseudo", "速報", "確定", "novelty", "tail", "abs_rating_after", "rel_rating_after"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        st.write("### 最近の棋譜（30件）")
        st.dataframe(df.tail(30), use_container_width=True)
        st.caption("Pseudoが確定に寄ってきたら『疑似報酬が賢くなった』＝超高速学習が成立。")
    else:
        st.warning("まだ棋譜がありません。②で1件入れると学習が始まります。")