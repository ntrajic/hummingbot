"""
Minimal Telegram notifier for Hummingbot strategies.
Sends messages to a Telegram chat via the Bot API.

Setup:
  1. Message @BotFather on Telegram → /newbot → copy the API token
  2. Message @userinfobot on Telegram → copy your chat_id
  3. Set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID in your environment,
     or pass them directly when constructing TelegramNotifier.
"""
import logging
import os
from typing import Optional

import aiohttp

from hummingbot.notifier.notifier_base import NotifierBase


class TelegramNotifier(NotifierBase):
    """
    Sends Hummingbot notifications to a Telegram chat.
    Inherits the async queue from NotifierBase — messages are fire-and-forget.
    """

    TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self, token: Optional[str] = None, chat_id: Optional[str] = None):
        super().__init__()
        self._token = token or os.environ.get("TELEGRAM_TOKEN", "")
        self._chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
        self._logger = logging.getLogger(__name__)

        if not self._token or not self._chat_id:
            self._logger.warning(
                "TelegramNotifier: token or chat_id not set. "
                "Notifications will be silently dropped. "
                "Set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID env vars or pass them explicitly."
            )

    @property
    def is_configured(self) -> bool:
        return bool(self._token and self._chat_id)

    async def _send_message(self, message: str):
        if not self.is_configured:
            return
        url = self.TELEGRAM_API_URL.format(token=self._token)
        payload = {"chat_id": self._chat_id, "text": message, "parse_mode": "HTML"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        self._logger.warning(f"Telegram send failed [{resp.status}]: {body}")
        except Exception as e:
            self._logger.warning(f"Telegram send error: {e}")
