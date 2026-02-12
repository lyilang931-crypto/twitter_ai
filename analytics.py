# analytics.py — GA4 / GTM 計測ヘルパー（Streamlit Cloud 対応）
# 個人情報は一切送信しない。送信するのはメタ情報（文字数/件数/role/エラー種別 等）のみ。
from __future__ import annotations

import os
import json
from typing import Dict, Any, Optional

import streamlit as st
import streamlit.components.v1 as components


def _get_ga4_id() -> str:
    """GA4_MEASUREMENT_ID を env → secrets の順で取得。無ければ空文字。"""
    mid = os.environ.get("GA4_MEASUREMENT_ID", "")
    if not mid:
        try:
            mid = st.secrets.get("GA4_MEASUREMENT_ID", "") or ""
        except Exception:
            mid = ""
    return mid.strip()


def _get_gtm_id() -> str:
    """GTM_ID を env → secrets の順で取得。無ければ空文字。"""
    gid = os.environ.get("GTM_ID", "")
    if not gid:
        try:
            gid = st.secrets.get("GTM_ID", "") or ""
        except Exception:
            gid = ""
    return gid.strip()


def is_analytics_enabled() -> bool:
    """計測が有効か（GA4 or GTM の ID が設定されているか）。"""
    return bool(_get_ga4_id() or _get_gtm_id())


def init_analytics() -> None:
    """ページ読み込み時に GA4 / GTM タグを挿入する。

    st.components.v1.html で <script> を埋め込む。
    GA4_MEASUREMENT_ID も GTM_ID も無い場合は何もしない（エラーにしない）。
    """
    ga4_id = _get_ga4_id()
    gtm_id = _get_gtm_id()

    if not ga4_id and not gtm_id:
        return  # 計測無効 — 何もしない

    snippets: list[str] = []

    # --- GTM 優先 ---
    if gtm_id:
        snippets.append(f"""
<!-- Google Tag Manager -->
<script>
(function(w,d,s,l,i){{w[l]=w[l]||[];w[l].push({{'gtm.start':
new Date().getTime(),event:'gtm.js'}});var f=d.getElementsByTagName(s)[0],
j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src=
'https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);
}})(window,document,'script','dataLayer','{gtm_id}');
</script>
<!-- End Google Tag Manager -->
""")

    # --- GA4 (gtag.js) ---
    if ga4_id and not gtm_id:
        # GTM がある場合は GTM 側で GA4 を管理するので gtag.js は不要
        snippets.append(f"""
<!-- Global site tag (gtag.js) - Google Analytics 4 -->
<script async src="https://www.googletagmanager.com/gtag/js?id={ga4_id}"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){{dataLayer.push(arguments);}}
  gtag('js', new Date());
  gtag('config', '{ga4_id}', {{
    'send_page_view': true,
    'cookie_flags': 'SameSite=None;Secure'
  }});
</script>
""")
    elif ga4_id and gtm_id:
        # GTM + GA4 両方ある場合: dataLayer の初期化だけ確保
        snippets.append("""
<script>
  window.dataLayer = window.dataLayer || [];
</script>
""")

    html = "\n".join(snippets)
    components.html(html, height=0, width=0)


def track_event(
    event_name: str,
    params: Optional[Dict[str, Any]] = None,
) -> None:
    """GA4 カスタムイベントを送信する。

    dataLayer.push で GTM に送るか、gtag('event', ...) で GA4 に直接送る。
    ID が無い場合は何もしない。

    個人情報禁止:
    - tweet 本文やユーザー入力の全文は送らない
    - 送っていいのは文字数/件数/role/エラー種別 等のメタ情報のみ
    """
    ga4_id = _get_ga4_id()
    gtm_id = _get_gtm_id()

    if not ga4_id and not gtm_id:
        return  # 計測無効

    safe_params = _sanitize_params(params or {})
    params_json = json.dumps(safe_params, ensure_ascii=False)

    if gtm_id:
        # GTM dataLayer.push
        js = f"""
<script>
  window.dataLayer = window.dataLayer || [];
  window.dataLayer.push({{
    'event': '{event_name}',
    'event_params': {params_json}
  }});
</script>
"""
    else:
        # GA4 gtag.js 直接
        js = f"""
<script>
  if (typeof gtag === 'function') {{
    gtag('event', '{event_name}', {params_json});
  }}
</script>
"""

    components.html(js, height=0, width=0)


def _sanitize_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """パラメータから個人情報・長文テキストを除去する。

    許可するキー:
    - 数値系: count, length, char_count, candidates, learned, error_count 等
    - カテゴリ系: role, status, error_type, tab, action 等
    - 真偽値: has_input, success, fallback_used 等

    禁止:
    - text, tweet, content, input, query 等のテキスト全文
    """
    BLOCKED_KEYS = {
        "text", "tweet", "content", "input", "query", "prompt",
        "api_key", "password", "secret", "token", "email",
        "name", "address", "phone",
    }

    sanitized: Dict[str, Any] = {}
    for k, v in params.items():
        k_lower = k.lower()
        # ブロックキーに一致 → スキップ
        if k_lower in BLOCKED_KEYS:
            continue
        # 文字列が長すぎる場合は切り捨て（50文字以上は怪しい）
        if isinstance(v, str) and len(v) > 50:
            continue
        sanitized[k] = v

    return sanitized
