# AdSense 審査対応ガイド

## 目的

- AdSense審査に通るための基本ページと広告表示の安全な切替機能を実装
- 審査通過まで広告を OFF にできる設計

## 実装内容

### 1. 追加されたページ

| ページ | パス | 内容 |
|--------|------|------|
| Home | `/` (デフォルト) | 既存のTwitterAIアプリ |
| About | `?page=About` | 運営者情報、サービス概要、免責事項 |
| Privacy Policy | `?page=Privacy Policy` | プライバシーポリシー、Cookie使用、データ保存 |
| Terms | `?page=Terms` | 利用規約、禁止事項、免責事項 |
| Contact | `?page=Contact` | お問い合わせ方法、FAQ |
| Blog/Updates | `?page=Blog/Updates` | 使い方、更新履歴 |

### 2. AdSense タグの埋め込み

- **ファイル**: `adsense_utils.py`
- **関数**: `init_adsense()` - ページ読み込み時に自動実行
- **条件**: `ENABLE_ADS=true` かつ `ADSENSE_CLIENT_ID` が設定されている場合のみ広告タグを挿入

### 3. 設定方法

#### Streamlit Cloud の場合

`.streamlit/secrets.toml` に以下を追加：

```toml
# AdSense 設定
ENABLE_ADS = "false"  # 審査通過まで false にしておく
ADSENSE_CLIENT_ID = "ca-pub-xxxxxxxxxxxxxxxx"  # 審査通過後に設定
```

#### ローカル環境の場合

環境変数で設定：

```bash
export ENABLE_ADS=false
export ADSENSE_CLIENT_ID=ca-pub-xxxxxxxxxxxxxxxx
```

または `.streamlit/secrets.toml` に上記と同じ内容を追加。

## 審査までの手順チェックリスト

### 必須ページの確認

- [ ] About ページが表示される（運営者情報、免責事項が明記されている）
- [ ] Privacy Policy ページが表示される（Cookie、Analytics、AdSense に触れている）
- [ ] Terms ページが表示される（利用規約、禁止事項が明記されている）
- [ ] Contact ページが表示される（お問い合わせ方法が明記されている）
- [ ] 各ページが400〜800文字程度の内容になっている（薄すぎない）

### コンテンツの確認

- [ ] AI生成ツールであることが明記されている
- [ ] 免責事項が明記されている
- [ ] 禁止事項（誹謗中傷、差別、個人攻撃等）が明記されている
- [ ] 個人情報の販売・提供をしない旨が明記されている
- [ ] Cookie / Analytics / AdSense の使用について触れている

### 広告設定の確認

- [ ] `ENABLE_ADS=false` で広告が表示されないことを確認
- [ ] 審査申請前に `ENABLE_ADS=true` に変更する
- [ ] `ADSENSE_CLIENT_ID` が正しく設定されていることを確認

### 技術的な確認

- [ ] ページが正常に読み込まれる（エラーが出ない）
- [ ] サイドバーのページ選択で各ページに遷移できる
- [ ] AdSense タグが正しく埋め込まれている（審査申請時）

## 審査中にやってはいけないこと

### コンテンツ関連

- ❌ **薄いページ**: 各ページが100文字未満など、内容が薄すぎる
- ❌ **リンクだらけ**: 外部リンクが多すぎる（スパムと判断される）
- ❌ **未完成ページ**: 「工事中」「準備中」などの未完成ページがある
- ❌ **著作権侵害**: 他者の著作物を無断で使用している
- ❌ **煽り・炎上誘導**: 過激な表現や炎上を誘導する内容

### 技術的な問題

- ❌ **広告の誤配置**: 広告がコンテンツの上に重なっている
- ❌ **自動クリック**: 広告の自動クリック機能がある
- ❌ **誤解を招く広告**: 広告がコンテンツと区別できない

### その他

- ❌ **個人情報の収集**: ユーザーの個人情報を不適切に収集している
- ❌ **違法コンテンツ**: 違法な内容やサービスを提供している
- ❌ **重複コンテンツ**: 他のサイトと完全に同じ内容

## 審査申請の流れ

1. **必須ページの作成・確認**
   - About, Privacy Policy, Terms, Contact がすべて表示されることを確認
   - 各ページの内容が適切であることを確認

2. **広告設定**
   - `ENABLE_ADS=true` に変更
   - `ADSENSE_CLIENT_ID` を設定
   - AdSense タグが正しく埋め込まれていることを確認

3. **サイトの動作確認**
   - すべてのページが正常に読み込まれることを確認
   - 広告が適切に表示されることを確認（審査申請時）

4. **AdSense に申請**
   - Google AdSense のサイトから申請
   - サイトURL、コンテンツの種類等を入力

5. **審査結果の確認**
   - 通常1〜2週間程度で審査結果が通知される
   - 不承認の場合は、指摘事項を修正して再申請

## トラブルシューティング

### 広告が表示されない

- `ENABLE_ADS=true` になっているか確認
- `ADSENSE_CLIENT_ID` が正しく設定されているか確認
- ブラウザのコンソールでエラーが出ていないか確認

### ページが表示されない

- `pages_content.py` が正しくインポートされているか確認
- `app.py` のページルーティングが正しく実装されているか確認

### ImportError が発生する

- `adsense_utils.py` と `pages_content.py` がプロジェクトルートにあることを確認
- Streamlit Cloud の場合、デプロイに含まれていることを確認

## 参考リンク

- [Google AdSense ヘルプ](https://support.google.com/adsense/)
- [AdSense プログラム ポリシー](https://support.google.com/adsense/answer/48182)
- [Streamlit Cloud ドキュメント](https://docs.streamlit.io/streamlit-community-cloud)
