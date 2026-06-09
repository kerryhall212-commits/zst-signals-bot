import requests
import logging
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID

logger = logging.getLogger(__name__)


def send_message(text: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "text": text,
        "parse_mode": "HTML",
    }
    try:
        response = requests.post(url, json=payload, timeout=30)
        if not response.ok:
            logger.error(f"Telegram error {response.status_code}: {response.text}")
        return response.ok
    except requests.RequestException as e:
        logger.error(f"Telegram request failed: {e}")
        return False
