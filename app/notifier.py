"""通知推播抽象層。

支援多個 backend，由 config.yaml 的 `notify.channel` 或環境變數 `NOTIFY_CHANNEL` 決定。
目前實作：discord。未來可加 ntfy、telegram、email 等。

config.yaml 範例：
    notify:
      channel: discord
      discord:
        webhook_url: "https://discord.com/api/webhooks/..."

環境變數覆寫：
    NOTIFY_CHANNEL          覆寫 channel（"none" 會關掉推播）
    DISCORD_WEBHOOK_URL     覆寫 discord.webhook_url
"""
from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)


def _get_channel_config() -> tuple[str, dict]:
    """讀 config.yaml + env var，回傳 (channel, channel_cfg)。"""
    channel = ""
    channel_cfg: dict = {}
    try:
        from app.config import Config
        cfg = Config.load()
        notify_cfg = getattr(cfg, "notify", None) or {}
        channel = str(notify_cfg.get("channel", "")).lower()
        channel_cfg = dict(notify_cfg.get(channel, {})) if channel else {}
    except Exception as e:
        logger.debug("讀取 config 失敗，回退到 env var：%s", e)

    env_channel = os.environ.get("NOTIFY_CHANNEL")
    if env_channel:
        channel = env_channel.lower()
        channel_cfg = {}  # 以 env 為主時從頭組

    # 各 channel 專屬的 env 覆寫
    if channel == "discord":
        env_url = os.environ.get("DISCORD_WEBHOOK_URL")
        if env_url:
            channel_cfg["webhook_url"] = env_url

    return channel, channel_cfg


def notify(message: str, title: str | None = None) -> bool:
    """發一則通知。失敗不拋，只記 log，回傳成功與否。"""
    channel, channel_cfg = _get_channel_config()
    if channel in ("", "none"):
        logger.info("notify channel 未設定，跳過推播")
        return False
    if channel == "discord":
        return _send_discord(message, title, channel_cfg)
    logger.warning("未知的 notify channel: %s", channel)
    return False


def _send_discord(message: str, title: str | None, cfg: dict) -> bool:
    url = cfg.get("webhook_url")
    if not url:
        logger.warning("Discord webhook_url 未設定")
        return False
    # Discord 單則 content 上限 2000 字
    body_parts = []
    if title:
        body_parts.append(f"**{title}**")
    body_parts.append(message)
    content = "\n".join(body_parts)[:1990]
    try:
        r = requests.post(url, json={"content": content}, timeout=10)
        if r.status_code in (200, 204):
            logger.info("Discord 推播成功")
            return True
        logger.warning("Discord 回 %s: %s", r.status_code, r.text[:200])
        return False
    except Exception as e:
        logger.warning("Discord 推播失敗: %s", e)
        return False
