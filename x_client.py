# x_client.py — X (Twitter) API v2 薄いラッパ
# 環境変数が無い場合は自動投稿機能を無効化する設計。
from __future__ import annotations

import os
import json
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


def _get_credentials() -> Optional[Dict[str, str]]:
    """X API の認証情報を環境変数から取得する。
    必須4つが全て揃っていなければ None を返す。
    """
    keys = {
        "X_API_KEY": os.environ.get("X_API_KEY", ""),
        "X_API_SECRET": os.environ.get("X_API_SECRET", ""),
        "X_ACCESS_TOKEN": os.environ.get("X_ACCESS_TOKEN", ""),
        "X_ACCESS_SECRET": os.environ.get("X_ACCESS_SECRET", ""),
    }
    # Streamlit secrets からもフォールバック
    for k in keys:
        if not keys[k]:
            try:
                import streamlit as st
                keys[k] = st.secrets.get(k, "") or ""
            except Exception:
                pass
    if all(keys.values()):
        return keys
    return None


def is_x_api_available() -> bool:
    """X API が利用可能かどうか（認証情報が揃っているか）。"""
    return _get_credentials() is not None


def post_tweet(text: str) -> Dict[str, Any]:
    """X API v2 でツイートを投稿する。

    Returns:
        {"success": True, "tweet_id": "...", "text": "..."} on success
        {"success": False, "error": "..."} on failure
    """
    creds = _get_credentials()
    if not creds:
        return {"success": False, "error": "X API credentials not configured"}

    try:
        import tweepy
    except ImportError:
        return {"success": False, "error": "tweepy not installed. Run: pip install tweepy"}

    try:
        client = tweepy.Client(
            consumer_key=creds["X_API_KEY"],
            consumer_secret=creds["X_API_SECRET"],
            access_token=creds["X_ACCESS_TOKEN"],
            access_token_secret=creds["X_ACCESS_SECRET"],
        )
        response = client.create_tweet(text=text)
        tweet_id = str(response.data["id"]) if response.data else ""
        logger.info(f"Tweet posted: id={tweet_id}")
        return {"success": True, "tweet_id": tweet_id, "text": text}
    except Exception as e:
        logger.error(f"Tweet post failed: {e}")
        return {"success": False, "error": str(e)}


def get_tweet_metrics(tweet_id: str) -> Dict[str, Any]:
    """X API v2 でツイートのメトリクスを取得する。

    Returns:
        {"success": True, "impressions": ..., "likes": ..., ...} on success
        {"success": False, "error": "..."} on failure
    """
    creds = _get_credentials()
    if not creds:
        return {"success": False, "error": "X API credentials not configured"}

    try:
        import tweepy
    except ImportError:
        return {"success": False, "error": "tweepy not installed"}

    try:
        client = tweepy.Client(
            consumer_key=creds["X_API_KEY"],
            consumer_secret=creds["X_API_SECRET"],
            access_token=creds["X_ACCESS_TOKEN"],
            access_token_secret=creds["X_ACCESS_SECRET"],
        )
        tweet = client.get_tweet(
            tweet_id,
            tweet_fields=["public_metrics", "created_at"],
        )
        if tweet.data:
            metrics = tweet.data.get("public_metrics", {})
            return {
                "success": True,
                "impressions": metrics.get("impression_count", 0),
                "likes": metrics.get("like_count", 0),
                "rts": metrics.get("retweet_count", 0),
                "replies": metrics.get("reply_count", 0),
            }
        return {"success": False, "error": "Tweet not found"}
    except Exception as e:
        logger.error(f"Tweet metrics fetch failed: {e}")
        return {"success": False, "error": str(e)}
