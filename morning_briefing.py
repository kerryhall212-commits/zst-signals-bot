"""
Daily morning briefing — posted once at 05:45 BST.
Shows PDH/PDL/PWH/PWL for Gold, PDH/PDL for US30.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import levels_store
from telegram_sender import send_message

logger = logging.getLogger(__name__)

_SCRIPTURE = (
    '🙏 "For God has not given us a spirit of fear, but of power, '
    'love and a sound mind." — 2 Timothy 1:7'
)


def _fmt(value) -> str:
    if value is None:
        return "N/A"
    return f"{round(float(value)):,}"


def build_briefing_message() -> str:
    now      = datetime.now(ZoneInfo("Europe/London"))
    day_str  = now.strftime("%A")
    date_str = f"{now.day} {now.strftime('%B %Y')}"
    levels   = levels_store.load()

    gold = levels.get("GOLD", {})
    us30 = levels.get("US30", {})

    lines = [
        "🌅 <b>ZST DAILY BRIEFING</b>",
        f"{day_str} {date_str}",
        "",
        _SCRIPTURE,
        "",
        "📍 <b>KEY LEVELS</b>",
        "<b>GOLD</b>",
        f"PDH: <code>{_fmt(gold.get('pdh'))}</code>  |  PDL: <code>{_fmt(gold.get('pdl'))}</code>",
        f"PWH: <code>{_fmt(gold.get('pwh'))}</code>  |  PWL: <code>{_fmt(gold.get('pwl'))}</code>",
        "",
        "<b>US30</b>",
        f"PDH: <code>{_fmt(us30.get('pdh'))}</code>  |  PDL: <code>{_fmt(us30.get('pdl'))}</code>",
        "",
        "Trade the levels. Trust the process.",
        "Zero Stress. Always. 🤎",
        "ZST Insider 🔐",
    ]
    return "\n".join(lines)


def post_morning_briefing() -> bool:
    logger.info("Posting morning briefing...")
    try:
        msg = build_briefing_message()
        if send_message(msg):
            logger.info("Morning briefing posted.")
            return True
        logger.error("Morning briefing send failed.")
        return False
    except Exception as e:
        logger.exception("Morning briefing error: %s", e)
        return False
