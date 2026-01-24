import json
from datetime import date

import pandas as pd
import streamlit as st

from analytics_utils import recent_mean_score, top_bottom, tag_hist
from generate import generate_daily_pack
from tagger import guess_tags
from ab_rank import build_priors, rank_candidates, tag_key
from rating import tweet_score, update_abs_rating, update_rel_rating
from csvio import read_csv, append_csv
# app.py 冒頭付近
import os

# Secrets を最優先
gemini_key = (
    st.secrets.get("Gemini_API_KEY")
    or os.getenv("Gemini_API_KEY")
    or ""
)

with st.sidebar:
    
 CSV_PATH = "twitter_log.csv"

st.set_page_config(page_title="Twitter 将棋AI式 自動化（1日3ツイート）", layout="wide")
st.title("Twitter 将棋AI式 自動化（完成版）— 1日3ツイート固定（本命/準本命/実験）")

rows = read_csv(CSV_PATH)

def last_float(col: str, default: float) -> float:
    if not rows:
        return default
    try:
        return float(rows[-1].get(col, default) or default)
    except Exception:
        return default

abs_rating_now = last_float("abs_rating_after", 1000.0)
rel_rating_now = last_float("rel_rating_after", 1000.0)

scores = []
for r in rows:
    try:
        scores.append(float(r.get("tweet_score", 0.0) or 0.0))
    except Exception:
        pass
global_mean = (sum(scores)/len(scores)) if scores else 0.02
priors = build_priors(rows) if rows else {}

with st.sidebar:
    st.subheader("Gemini 3 設定（無料最適化）")
    use_gemini = st.toggle("Geminiを使う", value=True, key="use_gemini")

    # 1) まず Secrets から読む（Cloud用）
    secret_key = st.secrets.get("Gemini_API_KEY", "")

    # 2) 必要ならUIで上書き（テスト用）
    override = st.text_input("Gemini_API_KEY（任意：上書き）", type="password", key="gemini_key_input")

    gemini_key = override.strip() or secret_key

    model = st.selectbox("モデル", ["gemini-3-flash", "gemini-3-pro"], index=0)
    per_role_n = st.slider("各役割の候補数", 3, 8, 5, 1)

    st.subheader("仮想自己対局")
    alpha = st.slider("スムージングα（大=安定）", 1, 20, 5, 1)

    st.subheader("レーティング")
    baseline = st.number_input("Baseline（絶対レート）", value=1000.0, step=10.0)
    k_abs = st.number_input("K（絶対）", value=16.0, step=1.0)
    k_rel = st.number_input("K（相対）", value=16.0, step=1.0)

tab1, tab2, tab3 = st.tabs(["① 今日の3ツイ生成→承認", "② 実測入力（棋譜）", "③ 分析（勝ち構造）"])

# =========================================================
# ① 今日の3ツイ生成→承認
# =========================================================
with tab1:
    st.subheader("今日のテーマ（1セット＝3ツイ）")
    topic = st.text_input("テーマ", value="努力しても結果が出ない理由")

    if st.button("今日の3ツイ候補を生成 → 仮想自己対局"):
        if use_gemini and not gemini_key:
            st.error("Geminiを使う場合は Gemini_API_KEY を設定してください（Secrets推奨）")
            st.stop()

        pack = generate_daily_pack(
            api_key=gemini_key,
            topic=topic,
            per_role_n=int(per_role_n),
            use_gemini=bool(use_gemini),
            model=model
        )

        # 役割ごとにランキングを作る
        ranked_pack = []
        for block in pack:
            candidates = []
            for t in block["candidates"]:
                tags = guess_tags(t)
                candidates.append({
                    "text": t,
                    "tags": tags,
                    "role": block["role"],
                    "role_label": block["role_label"],
                    "time_slot": block["time_slot"],
                    "time_slot_label": block["time_slot_label"],
                    "intent_hint": block["intent"],
                })
            ranked = rank_candidates(candidates, priors, global_mean=global_mean, alpha=float(alpha))
            ranked_pack.append({"meta": block, "ranked": ranked})

        st.session_state["ranked_pack"] = ranked_pack
        st.session_state["approved"] = {}  # role -> chosen

    ranked_pack = st.session_state.get("ranked_pack")

    if ranked_pack:
        st.caption("各役割で“勝ちやすい順”に並びます。各役割から1本ずつ承認してください。")
        for group in ranked_pack:
            meta = group["meta"]
            ranked = group["ranked"]

            st.markdown(f"### {meta['role_label']} / {meta['time_slot_label']}（狙い：{meta['intent']}）")
            for i, c in enumerate(ranked, start=1):
                with st.expander(f"#{i} 期待Score {c['expected_score']:.3f} | {c['text'][:26]}..."):
                    st.write(c["text"])
                    st.json(c["tags"])
                    st.code("（投稿用）\n" + c["text"])
                    if st.button(f"この案を承認（{meta['role']})", key=f"approve_{meta['role']}_{i}"):
                        st.session_state["approved"][meta["role"]] = {
                            "role": meta["role"],
                            "role_label": meta["role_label"],
                            "time_slot": meta["time_slot"],
                            "time_slot_label": meta["time_slot_label"],
                            "text": c["text"],
                            "tags": c["tags"],
                            "tags_key": tag_key(c["tags"]),
                        }
                        st.success(f"{meta['role_label']} を承認しました")

        approved = st.session_state.get("approved", {})
        if approved:
            st.markdown("## 承認済み（今日の3ツイ）")
            for role in ["MAIN","SUB","EXP"]:
                if role in approved:
                    a = approved[role]
                    st.markdown(f"**{a['role_label']} / {a['time_slot_label']}**")
                    st.code(a["text"])
                else:
                    st.warning(f"{role} が未承認です（3つ揃えると最強）")

# =========================================================
# ② 実測入力（棋譜）
# =========================================================
with tab2:
    st.subheader("実測を入力して棋譜に保存（1ツイ＝1対局）")

    approved = st.session_state.get("approved", {})
    role_pick = st.selectbox("入力するツイート（役割）", ["MAIN","SUB","EXP"], index=0)

    default_text = approved.get(role_pick, {}).get("text", "")
    default_tags = approved.get(role_pick, {}).get("tags", None)
    default_slot = approved.get(role_pick, {}).get("time_slot", "")
    default_slot_label = approved.get(role_pick, {}).get("time_slot_label", "")

    st.caption("※できれば『承認したツイ』を選んで入力。手入力でもOK。")

    time_slot = st.selectbox("時間帯", ["AM","NOON","PM"], index=["AM","NOON","PM"].index(default_slot) if default_slot in ["AM","NOON","PM"] else 0)
    st.write(f"時間帯メモ：{ {'AM':'朝(7-9)','NOON':'昼(12-13)','PM':'夜(20-22)'}[time_slot] }")

    text = st.text_area("投稿文（コピペ）", value=default_text, height=120)
    tags = default_tags if default_tags else (guess_tags(text) if text.strip() else {})
    tags_k = tag_key(tags) if tags else ""

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

    self_base = recent_mean_score(rows, window=30)

    if st.button("棋譜に保存（レーティング更新）"):
        s = tweet_score(
            impr=int(impressions),
            likes=int(likes),
            rts=int(rts),
            replies=int(replies),
            fol_before=int(fol_before),
            fol_after=int(fol_after),
        )

        abs_before = abs_rating_now
        rel_before = rel_rating_now

        abs_after = update_abs_rating(abs_before, s, baseline=float(baseline), k=float(k_abs))
        rel_after = update_rel_rating(rel_before, s, self_baseline=float(self_base), k=float(k_rel))

        row = {
            "date": str(date.today()),
            "time_slot": time_slot,
            "role": role_pick,
            "tweet_id": tweet_id,
            "text": text.strip(),
            "impressions": int(impressions),
            "likes": int(likes),
            "rts": int(rts),
            "replies": int(replies),
            "followers_before": int(fol_before),
            "followers_after": int(fol_after),
            "tweet_score": f"{s:.6f}",
            "abs_rating_before": f"{abs_before:.3f}",
            "abs_rating_after": f"{abs_after:.3f}",
            "rel_rating_before": f"{rel_before:.3f}",
            "rel_rating_after": f"{rel_after:.3f}",
            "self_baseline": f"{self_base:.6f}",
            "tags_key": tags_k,
            "tags_json": json.dumps(tags, ensure_ascii=False, sort_keys=True),
        }

        append_csv(CSV_PATH, row)
        st.success("保存しました。次回から役割別・時間帯別の学習が効きます。")
        st.info(f"TweetScore: {s:.3f} | 絶対: {abs_before:.1f}→{abs_after:.1f} | 相対: {rel_before:.1f}→{rel_after:.1f}")
        st.rerun()

# =========================================================
# ③ 分析（勝ち構造）
# =========================================================
with tab3:
    st.subheader("勝ち構造（上位20%）／負け構造（下位20%）")
    if not rows:
        st.warning("まだ棋譜がありません。②で実測を入力してください。")
    else:
        top, bot = top_bottom(rows, frac=0.2)
        top_hist = tag_hist(top)
        bot_hist = tag_hist(bot)

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("### 勝ち構造（上位20%）")
            st.write(top_hist.most_common(20))
        with c2:
            st.markdown("### 負け構造（下位20%）")
            st.write(bot_hist.most_common(20))

        st.subheader("最近の棋譜（30行）")
        df = pd.DataFrame(rows)
        for c in ["tweet_score","abs_rating_after","rel_rating_after","impressions","likes","rts","replies","followers_after","followers_before"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        st.dataframe(df.tail(30), use_container_width=True)

        st.caption("※成長は『底値の上昇』『大崩れの減少』『勝ちタグ再現率』に出ます。")
