# 主要変更点（バグ修正・永続化・学習加速）

## 直した不具合

### 1) 「生成してから確定（承認）を押すとデータが消える」
- **原因**: 「この3つを承認して保存」は `st.session_state["approved"]` にしか保存しておらず、再描画・再起動で消えていた。
- **対応**: 承認ボタン押下時に、承認した3件を **DBへ追記**（`append_rows`）。画面表示用の session_state はそのまま。DBの tweets は上書き・全削除しない。

### 2) 「時間が経つ/再起動/再デプロイ後に最近の棋譜が消え、レートも元通り」
- **原因**: 棋譜・重み・使用量が CSV/JSON ファイル（`twitter_log.csv`, `weights.json`, `usage.json`）のみで、Streamlit Cloud の再デプロイやコンテナ再起動で消えていた。
- **対応**: 永続化を **SQLite に統一**（`data/data.db`）。テーブル: `tweets`, `weights`, `usage`, `bandit`, `success_templates`。起動時に `init_db()` でスキーマ確保＋既存CSV/JSONがあれば1回だけDBへ移行。レートは `rows[-1]` の `abs_rating_after` / `rel_rating_after` から復元するため、棋譜がDBに残っていればレートも復元される。

### 3) 「確定を押してもデータが消えない」
- **対応**: 確定（保存）時は **追記のみ**（`append_row`）。上書き・全削除は行わない。誤入力の取り消しは **論理削除**（`logical_delete_tweet(row_id)`）で対応。Tab3「最近の棋譜（30件）」で「取り消す行」を選んで「この行を取り消す（論理削除）」で論理削除可能。

---

## 永続化（SQLite）

- **DBパス**: `data/data.db`（`data/` 配下）
- **テーブル**:
  - `tweets`: 棋譜（id, deleted, date, role, text, 確定, レート等）。`deleted=0` のみ読み取り。
  - `weights`: 重み1件（key='default', value=JSON）
  - `usage`: 日付別API使用量（date_key, data=JSON）
  - `bandit`: Bandit用（arm_id, pulls, rewards）
  - `success_templates`: 成功ツイートから抽出したテンプレ（自己蒸留用）
- **マイグレーション**: 初回起動時に `data/twitter_log.csv`, `data/weights.json`, `data/usage.json` が存在すればDBへ1回だけインポート。
- **書き込み**: トランザクション＋commit。追記のみで既存データを消さない。

---

## 学習加速（3本柱）

### 1) Replay Buffer（失敗も再学習）
- **replay.py**: `sample_for_learning(rows, k=50, recent_first=20)` で、直近20件＋優先度付き（低評価を優先）でサンプル。
- **優先度**: `priority = 1 - engagement_score`（確定スコアが低いほど優先）。
- **利用**: Tab3「リプレイで学習強化」ボタンで、サンプルに対してローカルで重みをSGD更新。APIは使わない。

### 2) 成功ツイート自己蒸留（DeepMind型）
- **distill.py**: 「成功」＝フォロワー増 > 0 または likes/impressions が閾値以上。成功ツイートから `extract_features`（opening, length_band, assertive, CTA）をローカルで抽出。
- **保存**: 確定保存時に `is_success_row(row)` なら `save_success_template` でDBに保存。
- **利用**: 生成プロンプトに「成功パターン（再現を推奨）」として Top5 のガイドラインを注入（`build_prompt(..., success_guidelines=...)`）。APIは使わない。

### 3) Bandit（フォロワー増をreward）
- **bandit.py**: arm_id = (role, length_band, opening, cta)。Thompson Sampling で arm を選択。
- **報酬**: 確定保存時に `reward = 確定スコア`（フォロワー増・likes/imp を反映済み）で `bandit_update(arm_id, reward)`。状態はDBの `bandit` テーブルに保存。
- **利用**: 生成後の候補リストを `rank_candidates_by_bandit` で並べ替え、Banditが良いと学習した arm の候補を前に表示。再起動後も学習が継続。

---

## データが消えないことを確認する手順

1. **再起動**: アプリを再起動し、Tab3「最近の棋譜（30件）」に前回の棋譜が表示されること。レート（Abs/Rel）が前回の値のままであること。
2. **確定**: Tab1で生成→承認「この3つを承認して保存」→ Tab3で棋譜に3件（または該当分）が増えていること。再起動後もその3件が残っていること。
3. **時間経過**: 一定時間後に再アクセスし、同様に棋譜・レートが復元されていること（Streamlit Cloud の場合はデプロイでファイルシステムがリセットされる環境では、永続ボリュームの利用を推奨）。

---

## ファイル構成

- **storage.py**: SQLite 層（init_db, read_rows, append_row, append_rows, load_weights, save_weights, load_json, save_json, logical_delete_tweet, get_success_templates, save_success_template, bandit_get_all, bandit_update）。CSV/JSON の直接読み書きは廃止（マイグレーション時のみ参照）。
- **app.py**: 表示とイベントに集中。起動時に `init_db()`。重み・usage は storage 経由。承認時に `append_rows`。確定時に `append_row`＋成功時は `save_success_template`＋`bandit_update`。Tab3で論理削除・リプレイ学習。
- **replay.py**: 優先度付きリプレイサンプル。
- **distill.py**: 成功判定・特徴量抽出・ガイドライン文言。
- **bandit.py**: arm_id 生成・Thompson Sampling・候補の並べ替え。
- **prompts.py**: `build_prompt` に `success_guidelines` を追加。

---

## その他

- **from __future__ import annotations**: 各 .py の先頭に統一済み。
- **例外時**: Gemini 失敗時は空リストを返して処理継続。保存失敗時は `st.error` で表示し、アプリは落とさない。成功テンプレ・Bandit 更新の失敗は `try/except: pass` で握りつぶし、メインの保存は成功していればそのまま完了。
