# Twitter 将棋AI式 自動化（完成版）

## 目的
- 速報スコア（疑似報酬）で200案自己対局し、上位案を承認して投稿
- 実測（確定スコア）を入力すると、疑似報酬の重みが自動で学習される
- EXP枠は分散最大化（TailScore）で上振れ探索

## Secrets（Streamlit Cloud）
- Gemini_API_KEY = あなたのAPIキー

## 起動
pip install -r requirements.txt
streamlit run app.py