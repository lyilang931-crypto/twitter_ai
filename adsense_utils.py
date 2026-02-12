# adsense_utils.py — AdSense タグ埋め込みヘルパー（審査通過まで OFF 可能）
from __future__ import annotations

import os
import streamlit as st
import streamlit.components.v1 as components


def _get_adsense_client_id() -> str:
    """ADSENSE_CLIENT_ID を env → secrets の順で取得。無ければ空文字。"""
    cid = os.environ.get("ADSENSE_CLIENT_ID", "")
    if not cid:
        try:
            cid = st.secrets.get("ADSENSE_CLIENT_ID", "") or ""
        except Exception:
            cid = ""
    return cid.strip()


def is_ads_enabled() -> bool:
    """広告が有効か（ENABLE_ADS=true かつ ADSENSE_CLIENT_ID が設定されているか）。"""
    enable_ads = os.environ.get("ENABLE_ADS", "").lower()
    if enable_ads == "":
        try:
            enable_ads = st.secrets.get("ENABLE_ADS", "").lower()
        except Exception:
            enable_ads = ""
    return enable_ads == "true" and bool(_get_adsense_client_id())


def init_adsense() -> None:
    """ページ読み込み時に AdSense タグを挿入する。

    ENABLE_ADS=true かつ ADSENSE_CLIENT_ID が設定されている場合のみ実行。
    審査通過まで OFF にできる設計。
    """
    if not is_ads_enabled():
        return  # 広告無効 — 何もしない

    client_id = _get_adsense_client_id()
    if not client_id:
        return

    # AdSense 自動広告のスクリプト（head 相当に挿入）
    html = f"""
<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={client_id}"
     crossorigin="anonymous"></script>
<script>
  (adsbygoogle = window.adsbygoogle || []).push({{}});
</script>
"""
    components.html(html, height=0, width=0)
