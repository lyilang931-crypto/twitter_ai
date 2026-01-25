# app.py
from __future__ import annotations
import json
import time
import os
from datetime import date

import pandas as pd
import streamlit as st

from csvio import read_csv, append_csv
from weights import load_weights, update_weights_online
from rating import final_score_from_metrics, update_abs_rating, update_rel_rating
from self_play import rank_200
from generate import generate_200
from safety import safety_check

CSV_PATH = "twitter_log.csv"

st.set_page_config(page_title="Twitter 将棋AI式 自動化（200自己対局 + 学習）", layout="wide")
st.title("Twitter 将棋AI式 自動化（完成版）— 速報→自己対局200→承認→実測→学習（Gemini固定）")

# ====== ログ読み ======
rows = read_csv(CSV_PATH)
weights = load_weights(rows)

def load_secret_key() -> str:
    # Cloud: st.secrets / Local: env
    try:
        v = st.secrets.get("Gemini_API_KEY", "")  # 固定名
        if v: return str(v).strip()
    except Exception:
        pass
    v = os.getenv("Gemini_API_KEY", "") or os.getenv("Gemini_API_KEY", "")
    return str(v).strip()

def last_float(col: str, default: float) -> float:
    if not rows:
        return default
    try:
        return float(rows[-1].get(col, default) or default)
    except Exception:
        return default

abs_rating_now = last_float("abs_rating_after", 1000.0)
rel_rating_now = last_float("rel_rating_after", 1000.0)

# ====== Sidebar ======
with st.sidebar:
    st.subheader("Gemini 設定（キー名はGemini_API_KEY固定）")
    secret_key = load_secret_key()

    override = st.text_input("Gemini API Key（任意：上書き）", type="password", help="通常は空欄でOK（Secrets/Envを使用）")
    gemini_key = (override.strip() or secret_key)

    if gemini_key:
        st.caption("✅ API Key 検出済み")
    else:
        st.warning("⚠️ Gemini_API_KEY 未設定（Secrets推奨）")

    model = st.text_input("モデル", value="gemini-flash-latest")
    min_interval = st.slider("呼び出し最小間隔（秒）", 1.0, 6.0, 2.0, 0.5)
    cooldown_sec = st.slider("生成ボタンのクールダウン（秒）", 10, 120, 40, 5)

    st.subheader("あなたっぽさ（任意で追加）")
    voice_guide = st.text_area(
        "VOICE_GUIDE",
        value="根性より設計。足し算より引き算。期待値で決める。時間＝命。最後は今日の一手。",
        height=120
    )

    st.subheader("学習（重み更新）")
    lr = st.slider("学習率 lr", 0.01, 0.50, 0.15, 0.01)
    l2 = st.slider("正則化 l2", 0.0, 0.02, 0.002, 0.001)

    st.subheader("レーティング")
    baseline = st.number_input("Baseline（絶対レート基準）", value=0.50, step=0.01)
    k_abs = st.number_input("K（絶対）", value=16.0, step=1.0)
    k_rel = st.number_input("K（相対）", value=16.0, step=1.0)

tab1, tab2, tab3 = st.tabs(["① 生成→自己対局→承認", "② 実測入力（確定スコア）→学習", "③ 分析・重み・棋譜"])

# =========================================================
# ① 生成→自己対局→承認
# =========================================================
with tab1:
    st.subheader("今日のテーマ（経済・起業）")
    topic = st.text_input("テーマ", value="起業で失敗する人の共通点（経済視点）")

    if "last_gen_time" not in st.session_state:
        st.session_state["last_gen_time"] = 0.0

    colA, colB = st.columns([1,1])
    with colA:
        main_n = st.number_input("MAIN候補数", min_value=10, max_value=80, value=30, step=5)
        sub_n  = st.number_input("SUB候補数",  min_value=10, max_value=80, value=30, step=5)
    with colB:
        exp_total = st.number_input("EXP総数（内部自己対局）", min_value=50, max_value=300, value=200, step=10)
        exp_batch = st.number_input("EXPバッチ（分割生成）", min_value=10, max_value=50, value=25, step=5)

    # 生成ボタン
    if st.button("生成 → 自己対局 → ランキング（MAIN/SUB/EXP）"):
        now = time.time()
        if now - st.session_state["last_gen_time"] < cooldown_sec:
            st.warning(f"⏳ レート制限回避のため、あと {int(cooldown_sec - (now - st.session_state['last_gen_time']))} 秒待ってください")
            st.stop()

        st.session_state["last_gen_time"] = time.time()

        if not gemini_key:
            st.error("Gemini_API_KEY が未設定です（Secrets/Env推奨）。")
            st.stop()

        with st.status("生成中（分割生成＋制限回避）…", expanded=True) as status:
            # MAIN（安定・断定）
            main = generate_200(
                api_key=gemini_key,
                topic=topic,
                model=model,
                batch=min(int(exp_batch), 25),
                total=int(main_n),
                min_interval_sec=float(min_interval),
                voice_guide=voice_guide,
                role="MAIN",
                intent="否定×断定（安定して勝つ）",
            )
            st.write(f"MAIN生成: {len(main)}件")

            # SUB（安定・数字/具体）
            sub = generate_200(
                api_key=gemini_key,
                topic=topic,
                model=model,
                batch=min(int(exp_batch), 25),
                total=int(sub_n),
                min_interval_sec=float(min_interval),
                voice_guide=voice_guide,
                role="SUB",
                intent="否定×具体（数字/比較/例）",
            )
            st.write(f"SUB生成: {len(sub)}件")

            # EXP（分散最大化：200自己対局）
            exp = generate_200(
                api_key=gemini_key,
                topic=topic,
                model=model,
                batch=int(exp_batch),
                total=int(exp_total),
                min_interval_sec=float(min_interval),
                voice_guide=voice_guide,
                role="EXP",
                intent="質問×逆説（上振れ探索）",
            )
            st.write(f"EXP生成: {len(exp)}件")

            # ランキング（自己対局）
            main_ranked = rank_200(main, rows, weights, role="MAIN")[:10]
            sub_ranked  = rank_200(sub,  rows, weights, role="SUB")[:10]
            exp_ranked  = rank_200(exp,  rows, weights, role="EXP")[:20]

            st.session_state["ranked"] = {
                "MAIN": main_ranked,
                "SUB": sub_ranked,
                "EXP": exp_ranked,
            }
            st.session_state["approved"] = {}
            status.update(label="完了", state="complete")

    ranked = st.session_state.get("ranked")
    if ranked:
        st.caption("候補は『速報スコア＋Novelty＋Safety＋EXP分散スコア』で自己対局ランキング済み。承認して投稿してください。")

        def show_role(role: str, show_n: int):
            st.markdown(f"## {role}")
            for i, c in enumerate(ranked[role][:show_n], start=1):
                txt = c["text"]
                safe = safety_check(txt)
                with st.expander(f"#{i} self={c['selfplay_score']:.3f} | pseudo={c['pseudo_score']:.3f} | nov={c['novelty']:.2f} | safe={int(c['safety'])}"):
                    st.write(txt)
                    if not safe.ok:
                        st.error("安全NG: " + " / ".join(safe.reasons))
                    st.code(txt)
                    if st.button(f"この案を承認（{role}）", key=f"approve_{role}_{i}"):
                        st.session_state["approved"][role] = c
                        st.success(f"{role} 承認済み")

        c1, c2 = st.columns(2)
        with c1:
            show_role("MAIN", 10)
        with c2:
            show_role("SUB", 10)

        st.markdown("---")
        show_role("EXP", 20)

        approved = st.session_state.get("approved", {})
        if approved:
            st.markdown("## ✅ 承認済み（投稿用）")
            for role in ["MAIN","SUB","EXP"]:
                if role in approved:
                    st.markdown(f"### {role}")
                    st.code(approved[role]["text"])
                else:
                    st.warning(f"{role} 未承認")

# =========================================================
# ② 実測入力（確定スコア）→学習
# =========================================================
with tab2:
    st.subheader("実測入力（確定スコア）→ 重み学習 → レーティング更新")
    approved = st.session_state.get("approved", {})
    role_pick = st.selectbox("役割", ["MAIN","SUB","EXP"], index=0)

    default_text = approved.get(role_pick, {}).get("text", "")
    text = st.text_area("投稿文（コピペ）", value=default_text, height=120)

    # 速報スコアは、承認済みならそこから、無ければ後で再計算（簡易）
    pseudo = float(approved.get(role_pick, {}).get("pseudo_score", 0.0) or 0.0)
    novelty = float(approved.get(role_pick, {}).get("novelty", 0.0) or 0.0)
    safety01 = float(approved.get(role_pick, {}).get("safety", 0.0) or 0.0)
    selfplay = float(approved.get(role_pick, {}).get("selfplay_score", 0.0) or 0.0)

    st.caption(f"（記録用）pseudo={pseudo:.3f}, novelty={novelty:.2f}, safety={int(safety01)}, selfplay={selfplay:.3f}")

    colA, colB, colC = st.columns(3)
    with colA:
        impressions = st.number_input("インプレッション", min_value=0, value=0, step=1)
        likes = st.number_input("いいね", min_value=0, value=0, step=1)
    with colB:
        rts = st.number_input("RT", min_value=0, value=0, step=1)
        replies = st.number_input("返信", min_value=0, value=0, step=1)
    with colC:
        fol_before = st.number_input("フォロワー（前）", min_value=0, value=0, step=1)
        fol_after = st.number_input("フォロワー（後）", min_value=0, value=0, step=1)

    tweet_id = st.text_input("tweet_id（任意）", value="")

    if st.button("保存 → 学習 → レーティング更新"):
        final = final_score_from_metrics(
            impr=int(impressions),
            likes=int(likes),
            rts=int(rts),
            replies=int(replies),
            fol_before=int(fol_before),
            fol_after=int(fol_after),
        )

        # 重み更新（疑似と実測のズレで学習）
        new_w = update_weights_online(
            weights,
            text=text,
            pseudo=float(pseudo),
            final=float(final),
            lr=float(lr),
            l2=float(l2),
        )

        # レーティング更新（絶対 + 相対）
        abs_before = abs_rating_now
        rel_before = rel_rating_now

        # 相対基準：直近30件のfinal平均
        finals = []
        for r in rows[-30:]:
            try:
                finals.append(float(r.get("final_score", 0.0) or 0.0))
            except Exception:
                pass
        self_base = (sum(finals)/len(finals)) if finals else 0.5

        abs_after = update_abs_rating(abs_before, final, baseline=float(baseline), k=float(k_abs))
        rel_after = update_rel_rating(rel_before, final, self_baseline=float(self_base), k=float(k_rel))

        row = {
            "date": str(date.today()),
            "role": role_pick,
            "tweet_id": tweet_id,
            "text": text.strip(),
            "impressions": int(impressions),
             "likes": int(likes),
            "rts": int(rts),
            "replies": int(replies),
            "followers_before": int(fol_before),
            "followers_after": int(fol_after),
            "pseudo_score": f"{float(pseudo):.6f}",         # 速報
            "novelty": f"{float(novelty):.6f}",
            "safety01": f"{float(safety01):.1f}",
            "selfplay_score": f"{float(selfplay):.6f}",
            "final_score": f"{float(final):.6f}",          # 確定
            "abs_rating_before": f"{float(abs_before):.3f}",
            "abs_rating_after": f"{float(abs_after):.3f}",
            "rel_rating_before": f"{float(rel_before):.3f}",
            "rel_rating_after": f"{float(rel_after):.3f}",
            "self_baseline": f"{float(self_base):.6f}",
            "weights_json": json.dumps(new_w, ensure_ascii=False, sort_keys=True),
        }

        append_csv(CSV_PATH, row)
        st.success("保存＆学習しました。次回から疑似報酬が賢くなります。")
        st.info(f"final={final:.3f} | 絶対 {abs_before:.1f}→{abs_after:.1f} | 相対 {rel_before:.1f}→{rel_after:.1f}")
        st.rerun()

# =========================================================
# ③ 分析・重み・棋譜
# =========================================================
with tab3:
    st.subheader("棋譜・重み・推移")
    if not rows:
        st.warning("まだ棋譜がありません。②で実測を入力してください。")
    else:
        df = pd.DataFrame(rows)
        for c in ["pseudo_score","final_score","abs_rating_after","rel_rating_after","impressions","likes","rts","replies","followers_after","followers_before","novelty","selfplay_score","safety01"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        st.dataframe(df.tail(50), use_container_width=True)

        st.markdown("### 現在の重み（疑似報酬の評価関数）")
        st.json(weights)

        st.caption("ポイント：『疑似が外した誤差』で重みが動くので、投稿→実測入力を繰り返すほど精度が上がります。")