# データ消失バグ修正と永続化・学習改善

## 1. 「確定で消える」「時間で消える」の再現原因と修正理由

### 原因

1. **pack（生成候補）と approved（承認3ツイ）が永続化されていなかった**
   - どちらも `st.session_state` のみに保存していた。
   - ブラウザリロード・Streamlit Cloud の再起動・セッション切れで `session_state` が消えると、候補・承認一覧が消えていた。

2. **「確定」ボタンで候補や棋譜を消す処理はしていない**
   - 消えて見えていたのは、確定押下で rerun が走り、その時点で `session_state` が空だった（または別セッション）ため。
   - 真のソースが永続ストアになかったので、復元できなかった。

3. **棋譜・重み・usage の書き込みが非原子**
   - CSV 追記・JSON 上書きが途中で失敗したり、再起動で巻き戻る可能性があった。
   - Streamlit のキャッシュは使っていないが、永続層の不整合で「時間経過で消える」ように見えていた。

### 修正方針

- **真のソースを DB または原子書き込みファイルにした**
  - pack / approved / logs / weights / usage / rating をすべて永続化。
- **表示用に session_state を使うが、ロード時は永続層から読む**
  - `pack = session_state.get("pack") or _pack_from_storage` のように、永続層をフォールバックにした。
- **「確定」時は approved を永続化するだけ。pack は消さない。**
- **原子性**
  - JSON: `tmp` に書き出してから `os.replace(tmp, path)` で置換。
  - SQLite: トランザクションで `commit`。

---

## 2. 永続化の方式

### SQLite（推奨）

- **パス**: `data/twitter_ai.db`
- **スキーマ**: `storage.init_schema()` で自動作成。
  - `logs`: 棋譜（1行 = JSON 1件）
  - `approved`: 承認3ツイ（role ごと JSON）
  - `pack`: 直近生成候補（1行で全体 JSON）
  - `weights`: 重み（1行で JSON）
  - `usage`: 日付別 API コール数
  - `rating`: abs_rating / rel_rating
- **save/load**: `storage.db_append_log`, `db_read_logs`, `db_save_approved`, `db_load_approved`, `db_save_pack`, `db_load_pack`, `db_save_weights`, `db_load_weights`, `db_save_usage`, `db_load_usage`, `db_save_rating`, `db_load_rating`
- **transaction**: 各 `db_*` 内で `conn.commit()` を実行。

### ファイル（妥協案）

- `storage.USE_SQLITE = False` にすると、JSON + CSV に切り替わる。
- JSON: `atomic_save_json` / `atomic_load_json`（tmp → replace）。
- CSV: `atomic_append_row`（全行読んで追記して tmp → replace）。
- pack: `data/last_pack.json`
- approved: `data/approved.json`
- 重み: `data/weights.json`（`weights.save_weights` が原子書き込みに変更済み）。

---

## 3. 変更ファイル・関数の概要

| ファイル | 変更内容 |
|----------|----------|
| **storage.py** | 原子 JSON/CSV 書き込み追加。SQLite スキーマ・db_* 一覧追加。 |
| **weights.py** | `save_weights` を原子書き込みに変更。`sgd_update_with_gate`（インプレゲート + clip）追加。`IMPRESSION_GATE=200`, `MAX_DELTA=0.5`。 |
| **app.py** | 起動時に DB/ファイルから pack・approved・rows・usage・w・rating をロード。表示は `session_state or 永続`. 「確定」で approved を永続化。「生成完了」で pack を永続化。Tab2 保存で atomic/DB に棋譜追記・rating 保存・インプレゲート付き学習・replay で重み更新。usage 保存を DB 対応。 |

---

## 4. 学習速度改善（update gate + clip + replay）

- **インプレゲート**: `impressions < 200` の棋譜は重み更新しない（`sgd_update_with_gate`）。
- **クリップ**: 更新幅を `±MAX_DELTA` に制限（`weights.sgd_update` 内）。
- **Replay**: Tab2 で 1 件保存したあと、直近 10 件の棋譜で `_replay_weights` を実行し、重みを追加更新。

（Bandit/UCB は候補選択の変更のため、今回は未実装。必要なら `pick()` 内で ε-greedy や UCB を追加可能。）

---

## 5. ローカルでの動作確認手順

1. `cd /path/to/twitter_ai`
2. `pip install -r requirements.txt`
3. `streamlit run app.py`
4. ① タブで「生成」→ 候補表示を確認。
5. 「この3つを承認して保存」を押す → 成功メッセージと「保存先: DB approved (または data/approved.json), 件数: 3」を確認。
6. ブラウザをリロード → ② タブの「対象」で承認したツイートがデフォルトで出ることを確認。
7. ② タブで実測を入力して「保存（棋譜に追加）…」→ 成功と「保存先: DB logs (または CSV), 直近件数: …」を確認。
8. ③ タブで「最近の棋譜」「Abs/Rel Rating」「学習中の重み」が更新されていることを確認。
9. アプリを停止して再起動 → 棋譜・重み・レート・承認が復元されていることを確認。

---

## 6. Streamlit Cloud での確認手順

1. リポジトリをデプロイし、`streamlit run app.py` が動くようにする。
2. **注意**: Streamlit Cloud のデフォルトはエフェメラル FS のため、再デプロイや再起動で `data/` や SQLite が消える場合がある。
3. 永続化を保ちたい場合は、ストレージをマウントするか、外部 DB（Cloud SQL 等）を指定する必要がある。
4. 上記 5 の手順と同様に「生成 → 承認 → 実測保存 → リロード」で、同一セッション内で候補・棋譜が消えないことを確認。
5. 「保存失敗」が出た場合は、UI のキャプションにエラー種別が表示される（秘密情報は出さない）。
