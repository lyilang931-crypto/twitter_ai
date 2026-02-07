# 語彙多様性（単一語彙過剰収束の解消）設計

## 目的

- 「設計」「本質」「構造」などの語が勝ちツイート学習・自己蒸留で過学習し、表現が単一語彙に収束する問題を解消する。
- 完全禁止ではなく「連続・高頻度使用のみ減点」とし、同義語ローテーションを促す。
- 長期フォロー増加に不利な“思想の連呼AI”を防ぎ、抽象×具体×感情×行動が混ざった出力に寄せる。

## 要件（必須）

| # | 要件 | 対応 |
|---|------|------|
| 1 | 直近Nツイート（10〜20件）での単語頻度を計測 | `recent_word_frequency(rows, n)` で頻度マップを構築 |
| 2 | 特定語彙がしきい値を超えたらスコアにペナルティ | `vocab_diversity_penalty(text, freq_map)` で 0〜約0.25 を返し、pseudo から減算 |
| 3 | 連続・高頻度使用のみ減点（完全禁止ではない） | しきい値超えの語を「含む」候補のみ減点 |
| 4 | 同義語ローテーションを促す | プロンプトに「言い換え推奨: 設計→仕組み/方針/型」を注入 |
| 5 | scoring / reward / build_prompt の思想を壊さない | 既存関数のシグネチャは変えず、app 側でペナルティを後から適用 |
| 6 | secrets / APIキーに触れない | ロジックのみ |

## 構成

```
vocabulary_diversity.py (新規)
  - OVERUSED_CANDIDATES: 過学習しやすい語のリスト
  - SYNONYM_HINTS: 同義語ローテーション用（プロンプト用）
  - recent_word_frequency(rows, n) -> Dict[str, int]
  - vocab_diversity_penalty(text, freq_map, threshold, cap) -> float
  - get_overused_words(freq_map, threshold) -> List[str]
  - format_synonym_hint(overused) -> str

scoring.py
  - 変更なし（既存のまま）

exp_score.py
  - 変更なし（既存のまま）

prompts.py
  - STYLE_CORE 付近に1行追加: 「直近で多用した語は避け、同義語・言い換えで多様に。」
  - build_prompt に optional: diversity_hint: str = ""
  - diversity_hint が非空ならプロンプトに1行追加

app.py
  - build_candidates: 直近N件をスライス → freq_map 計算 → 各候補に penalty 適用（ps = max(0, ps - penalty)）
  - 生成開始時: overused = get_overused_words(freq_map), hint = format_synonym_hint(overused)
  - api_generate / build_prompt に diversity_hint を渡す
```

## パラメータ（目安）

- 直近件数 N: 15（10〜20の中央）
- しきい値: 直近N件中でその語が **3回以上** 出現したら「過多」
- ペナルティ: 候補本文に過多語を1語含むごとに 0.08、最大 0.25（約3語でキャップ）
- 同義語ヒント: 設計→仕組み/方針/型/判断基準、本質→核心/根っこ/要点、構造→仕組み/枠/流れ など

## やってはいけないこと

- 単語の完全BAN
- 表現の自由度を著しく下げる固定テンプレ化
- UI変更（今回はロジックのみ）

## ゴール

- 「設計」が10ツイート中2〜3回程度に自然収束
- 抽象×具体×感情×行動が混ざった出力
- 長期フォロー増に不利な連呼を防ぐ

## 変更ファイル一覧（実装後）

| ファイル | 変更内容 |
|----------|----------|
| `vocabulary_diversity.py` | **新規**。頻度計測・ペナルティ・同義語ヒント |
| `prompts.py` | STYLE_DIVERSITY 追加、build_prompt に diversity_hint 追加 |
| `app.py` | build_candidates で freq_map とペナルティ適用、生成時に diversity_hint を api_generate に渡す |
| `scoring.py` / `exp_score.py` | 変更なし |
