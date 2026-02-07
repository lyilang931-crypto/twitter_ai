# 動作確認手順（品質・学習ログ運用改善）

## 変更したファイル一覧

| ファイル | 変更内容 |
|----------|----------|
| `prompts.py` | `required_keywords` 引数追加、必須キーワードルール（kw_rule）追加 |
| `app.py` | 必須キーワード収集・検証・再生成・フォールバック、`contains_keywords` / `any_tweet_contains_keywords` / `fallback_tweet_with_keywords`、編集フォーム（タブ3）、`update_by_id` 利用 |
| `storage.py` | `update_by_id` 追加、`TWEETS_EDITABLE_COLUMNS`、保存層コメント（load→merge→save・Supabase切替） |

## 追加した関数

- **app.py**
  - `collect_required_keywords(topic, trend_hint)` … テーマ＋トレンドから必須キーワードを収集（2文字以上・重複除去）
  - `contains_keywords(text, required_keywords)` … 本文にいずれかのキーワードが含まれるか（部分一致）
  - `any_tweet_contains_keywords(texts, required_keywords)` … いずれか1ツイート以上にキーワードが含まれるか
  - `fallback_tweet_with_keywords(required_keywords)` … 必須キーワード入り汎用ツイ（APIなし）
- **storage.py**
  - `update_by_id(row_id, patch)` … 指定 id の行を patch で更新（許可列のみ・永続保存）

## ローカル / Streamlit での動作確認手順

### 前提

- 仮想環境で `pip install -r requirements.txt` 済み
- `streamlit run app.py` で起動

---

### ケース1: トレンドに「衆議院選挙」を入れた場合（必須キーワード）

1. タブ①を開く。
2. **テーマ**: そのまま（例: 「起業で失敗する人の共通点」）または任意。
3. **今日のトレンド（任意）**: 「衆議院選挙」と入力。
4. 「生成（制限回避）→ 内部自己対局 → 上位を提示」をクリック。
5. **確認**:
   - 生成されたツイートのうち、**少なくとも1本**の本文に「衆議院選挙」が含まれていること。
   - 含まれていない場合は、MAIN が1回再生成され、それでも無ければ「衆議院選挙についての観察。設計と行動を大切にしたい。」のようなフォールバックが先頭に1本追加されていること。
6. トーンは断定より「観察・問い・設計」寄りで、キーワード自体は本文に残っていること。

---

### ケース2: 通常テーマ（必須キーワードなし／人物名なし）

1. タブ①を開く。
2. **テーマ**: 「起業で失敗する人の共通点」のまま。
3. **今日のトレンド（任意）**: 空欄または「日経・AI・スタートアップ」など（「衆議院選挙」のような単一キーワードにしない）。
4. 「生成（制限回避）→ 内部自己対局 → 上位を提示」をクリック。
5. **確認**:
   - これまで通り候補が表示されること。
   - 必須キーワード未達による再生成やフォールバックは不要（トレンドが空 or 複数で、必須キーワードリストが空または多数のため、実質「最低1本に含める」が効きにくい設計の場合は、挙動が従来に近いこと）。

---

### ケース3: 棋譜の編集

1. タブ③「分析・学習」を開く。
2. 棋譜が1件以上ある状態で、「最近の棋譜（30件）」を表示する。
3. 「編集する行（ID=行番号）」で 1 行を選択する。
4. フォームで **投稿文** / **role** / **インプレッション** / **いいね** / **RT** / **返信** / **フォロワー前後** / **tweet_id** を編集し、「保存して反映」をクリック。
5. **確認**:
   - 「更新しました。」と表示され、画面が更新されること。
   - 同じ行を再度選ぶと、編集した内容が反映されていること。

---

### ケース4: 棋譜の取り消し（既存）

1. タブ③で「取り消す行」から 1 行選び、「この行を取り消す（論理削除）」をクリック。
2. **確認**: 「取り消しました。」と表示され、その行が一覧から消えること（論理削除）。

---

## データが消える / レートが戻る問題の再発防止

- **保存層**: `storage.py` 冒頭に「上書き時は必ず load→merge→save」とコメントを追加済み。usage は `load_json` → `usage_inc`（merge）→ `save_json` のみで更新しているため、他日のデータを上書きしていない。
- **Supabase 切替**: 環境変数 `SUPABASE_URL` と `SUPABASE_KEY` が設定されている場合にのみ Supabase に切替可能とする設計コメントを追加。実装は任意（`storage_supabase` を用意する場合は同じインターフェースで read_rows / append_row / update_by_id / logical_delete_tweet を実装）。

## 注意

- `secrets.toml` / `.env*` / APIキー・パスワードは読まない・触らない・表示しない方針で変更。
- 政治テーマでも、断定ではなく「観察・問い・設計」に寄せつつ、**キーワード自体は本文に残す**ようにプロンプトとフォールバックで制御している。

---

## ImportError `update_by_id` 復旧（Streamlit Cloud 等）

### 事象

- 起動時に `ImportError: cannot import name 'update_by_id' from storage` で落ちる。

### 対応内容

1. **まず起動させる**  
   - `app.py` で `update_by_id` を `try/except` で import。失敗時は `update_by_id = None` とし、編集以外の機能はそのまま利用可能。
2. **編集時のガード**  
   - 編集フォームで「保存して反映」時に `update_by_id is None` なら `st.error` を表示して更新処理は行わない。
3. **本修正**  
   - `storage.py` に `update_by_id(row_id, patch)` を実装し、`__all__` に含める。同一リポジトリ内の `storage.py` に既に実装済みであれば、デプロイ対象に含まれるようにする。

### ローカル確認コマンド

```bash
python -m py_compile app.py storage.py
streamlit run app.py
```

- 起動後: タブ③で棋譜を1行編集 → 「保存して反映」→ 再読み込みで編集が残ること。

### Streamlit Cloud で反映されない場合のチェック

- **ブランチ**  
  - Cloud のデプロイ元ブランチが、`update_by_id` を追加したコミットを含むブランチになっているか確認。
- **キャッシュ**  
  - Streamlit Cloud の「Clear cache」や「Reboot app」を実行してから再デプロイ。
- **storage.py の存在**  
  - デプロイに含まれるファイルに `storage.py` が含まれ、`update_by_id` と `TWEETS_EDITABLE_COLUMNS` が定義されているか確認。

---

## 「Gemini failed: JSON not found」フォールバック

### 事象

- Gemini API は文章を返すが、JSON 形式で返らないことがあり、`json.loads` 失敗で例外になり UI にエラー表示される。

### 対応内容（設計）

1. **llm_gemini.py**  
   - `_parse_or_fallback(raw)`: JSON パースを try/except で実施。失敗時は例外にせず `{"__fallback": True, "raw": raw}` を返す。  
   - `gemini_json` は `_extract_json` の代わりに `_parse_or_fallback` を呼ぶ。
2. **app.py api_generate**  
   - `data.get("__fallback")` のとき、`raw` を 1 ツイートとして `postprocess_tweet` し、`([s], 1)` を返す。通常時は `(out, 0)`。  
   - 戻り値を `tuple[list[str], int]`（本文リストとフォールバック件数）に統一。
3. **app.py 生成フロー**  
   - `gen_role` は `(lst, fc)` を返す。各 role で `fc` を集計し、`build_candidates` 後にその role の「末尾 fc 件」の候補に `json_fallback=True` と `pseudo` のデフォルトを付与。  
   - フォールバックが 1 件以上あれば「生成完了（形式フォールバック）」と表示。
4. **runner.py**  
   - `data.get("__fallback")` のときは `tweet = data.get("raw", "")` で救出。

### ゴール

- 文章が生成されていれば、ツイートは必ず 1 本以上 UI に出る。
- 「JSON not found」でアプリが止まらない。
- エラーではなく「生成成功（形式フォールバック）」として扱う。
