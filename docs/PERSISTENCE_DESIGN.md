# データが消えなくなる設計（最終）

## 目的
候補ツイート・固定ツイート・最近の棋譜/ログが、**再実行・再起動・再デプロイ**でも消えないようにする。

## 設計方針（最小変更で最大改善）

### A) 同一デプロイ中の rerun で消えない
- **session_state**: 生成候補（pack）は一時表示用に保持。rerun で消えるのは仕様。
- **DB**: 承認（固定）した3件は **status='pinned'** で DB に追記。確定した棋譜は **status='confirmed'**。行は消さず状態だけ更新。
- **読み込み**: 起動時・保存後に `read_rows()` で DB から復元。deleted=0 の行のみ取得。

### B) デプロイ再起動でも残る（現状）
- **SQLite**: `data/data.db` に保存。Streamlit Cloud ではコンテナ再デプロイでファイルシステムがリセットされるため、**永続ボリュームのマウント** または **外部DB（Postgres 等）** が必要。
- **現実装**: SQLite のまま、保存パスを `data/data.db` に統一。全書き込みをトランザクション化。将来、環境変数で Postgres に切替可能にできるよう `get_conn()` で接続を抽象化。

### C) 固定を押したら候補が消える挙動の変更
- **変更前**: 承認で session_state にだけ保持し、DB には追記していたが「候補」としての状態がなかった。
- **変更後**: 承認した3件を **status='pinned'** で DB に追記。行は消さず、状態遷移（candidate → pinned → confirmed / deleted）のみ。論理削除は `deleted=1` と `status='deleted'` に更新。

## 永続層（storage.py）責務

| 関数 | 役割 |
|------|------|
| `get_conn()` | 接続方式を統一。将来 Postgres 対応時はここだけ差し替え。 |
| `init_db()` | スキーマ確保・既存CSV/JSONの1回移行。 |
| `read_rows(path, status=None)` | 棋譜取得。deleted=0。status 指定で絞り込み可。 |
| `append_row(path, row)` | 1行追記。トランザクションで atomic。 |
| `append_rows(rows)` | 複数行追記。トランザクションで atomic。 |
| `update_row(row_id, **kwargs)` | 指定行を更新（例: status を pinned に）。 |
| `logical_delete_tweet(row_id)` | 論理削除（deleted=1, status='deleted'）。行は消さない。 |

## status の取りうる値
- `candidate`: 候補（将来、生成時に保存する場合に利用）
- `pinned`: 固定（承認済み・投稿用に確定前）
- `confirmed`: 確定済み（実測入力済みの棋譜）
- `deleted`: 論理削除済み

## データが消えないようにするための運用
1. **同一デプロイ**: 承認・確定はすべて DB に追記。rerun 後も `read_rows()` で復元。
2. **再起動**: `data/data.db` が残っていればそのまま復元。Streamlit Cloud では永続ボリュームまたは外部DBを推奨。
3. **再デプロイ**: ファイルシステムがリセットされる環境では、永続ストレージのマウントまたは Supabase/Neon 等の利用を検討。
