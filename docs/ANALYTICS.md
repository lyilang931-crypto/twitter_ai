# GA4 / GTM 計測ガイド

## 概要

Streamlit アプリに Google Analytics 4 (GA4) または Google Tag Manager (GTM) を組み込み、
ユーザー行動を計測する仕組みです。`st.components.v1.html` で JS タグを挿入します。

**個人情報は一切送信しません。** 送信するのは文字数・件数・role・エラー種別などのメタ情報のみです。

---

## セットアップ

### 方法 1: GA4 直接（推奨・簡単）

1. [GA4 管理画面](https://analytics.google.com/) でプロパティを作成
2. 測定 ID (`G-XXXXXXXXXX`) を取得
3. Streamlit Cloud の場合: `.streamlit/secrets.toml` に追記

```toml
GA4_MEASUREMENT_ID = "G-XXXXXXXXXX"
```

4. ローカルの場合: 環境変数を設定

```bash
export GA4_MEASUREMENT_ID="G-XXXXXXXXXX"
```

### 方法 2: GTM 経由（高度なカスタマイズ向け）

1. [GTM](https://tagmanager.google.com/) でコンテナを作成
2. コンテナ ID (`GTM-XXXXXXX`) を取得
3. secrets または env に設定

```toml
GTM_ID = "GTM-XXXXXXX"
```

### 無効化

`GA4_MEASUREMENT_ID` も `GTM_ID` も設定しなければ計測は完全に無効になります。
エラーにはならず、アプリは通常どおり動作します。

---

## イベント一覧

| イベント名 | 発火タイミング | パラメータ |
|---|---|---|
| `generate_click` | 生成ボタン押下 | `topic_length`, `has_trend` |
| `generate_success` | 生成完了（候補が出た） | `candidates`, `fallback_used`, `fallback_count` |
| `generate_error` | 生成エラー | `error_type` (`rpd_limit`, `api_key_invalid`, `rate_limit`, `gemini_runtime`, `unexpected`, `zero_candidates`) |
| `confirm_click` | 承認/保存ボタン押下 | `tab`, `role`, `roles`, `char_count`, `has_metrics` |
| `replay_train_click` | リプレイ学習ボタン押下 | `total_rows` |
| `replay_train_success` | リプレイ学習完了 | `learned` |
| `csv_learn_click` | CSV 学習ボタン押下 | `csv_rows` |
| `edit_open` | 編集フォームを開いた | `tab` |
| `edit_save` | 編集保存ボタン押下 | `tab` |
| `delete_row` | 行の論理削除 | `tab` |
| `draft_save_all_click` | 全候補を下書き保存 | `tab` |
| `draft_approve` | 下書き承認 | `tab`, `role` |
| `draft_reject` | 下書き却下 | `tab`, `role` |
| `draft_schedule` | 下書き予約 | `tab`, `role` |
| `draft_post_click` | 下書き投稿 | `tab`, `role`, `auto_post` |
| `auto_approve_click` | 閾値超え一括承認 | `safety_threshold`, `quality_threshold` |
| `schedule_process_click` | 予約処理実行 | `tab` |

---

## アーキテクチャ

```
analytics.py
  init_analytics()   ... ページ読み込み時に <script> タグを埋め込み
  track_event()      ... 各操作時に dataLayer.push / gtag('event') を埋め込み
  _sanitize_params() ... 個人情報をフィルタリング

app.py
  init_analytics() を st.set_page_config 直後に呼び出し
  各ボタン/操作の直後に track_event() を呼び出し
```

### 個人情報保護

`_sanitize_params()` が以下を自動的にブロックします:

- **ブロックキー**: `text`, `tweet`, `content`, `input`, `query`, `prompt`, `api_key`, `password`, `secret`, `token`, `email`, `name`, `address`, `phone`
- **長文**: 50 文字を超える文字列値は自動除去

---

## GA4 での確認手順

1. GA4 管理画面 > リアルタイム で即時確認
2. GA4 > レポート > エンゲージメント > イベント で集計確認
3. GTM の場合: GTM プレビューモードで `dataLayer` を確認

## トラブルシューティング

- **イベントが送信されない**: `GA4_MEASUREMENT_ID` が secrets/env に正しく設定されているか確認
- **Streamlit Cloud で動かない**: `st.components.v1.html` は Streamlit Cloud で動作します。secrets.toml の設定を確認してください
- **GTM で GA4 が計測されない**: GTM コンテナに GA4 タグを設定し、トリガーを「All Pages」にしてください
