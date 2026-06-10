"""
Builds and posts the daily morning briefing to Telegram.
Posted at 07:00 UTC Monday–Friday (London Open prep, ~08:00 BST in summer).
"""

import logging
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from signal_engine import _fetch
from key_levels import prev_day_levels, asian_session_levels
from telegram_sender import send_message
from config import SYMBOLS, H1_BARS, DAY_BARS

logger = logging.getLogger(__name__)

_SCRIPTURE = (
    '🙏 "For God has not given us a spirit of fear, but of power, '
    'love and a sound mind." — 2 Timothy 1:7'
)
_SEP = "━" * 21


def _fmt(value, decimals: int) -> str:
    if value is None:
        return "N/A"
    if decimals == 0:
        return f"{round(value):,}"
    return f"{value:,.{decimals}f}"


def _bias(current, pdh, pdl) -> str:
    if None in (current, pdh, pdl):
        return "N/A"
    if current > pdh:
        return "Bullish ↑"
    if current < pdl:
        return "Bearish ↓"
    mid = (pdh + pdl) / 2
    return "Neutral (Bullish)" if current > mid else "Neutral (Bearish)"


def _fetch_levels() -> dict:
    result = {}
    for sym_key, cfg in SYMBOLS.items():
        try:
            h1        = _fetch(cfg, "1h",   H1_BARS)
            daily     = _fetch(cfg, "1day", DAY_BARS)
            pdl_dict  = prev_day_levels(daily)
            asl_dict  = asian_session_levels(h1)
            result[sym_key] = {
                "pdh":      pdl_dict.get("prev_day_high", {}).get("value"),
                "pdl":      pdl_dict.get("prev_day_low",  {}).get("value"),
                "ash":      asl_dict.get("asian_high",    {}).get("value"),
                "asl":      asl_dict.get("asian_low",     {}).get("value"),
                "current":  float(daily.iloc[-1]["close"]) if not daily.empty else None,
                "decimals": cfg.get("decimals", 2),
            }
        except Exception as e:
            logger.warning("[%s] Level fetch failed: %s", sym_key, e)
            result[sym_key] = None
    return result


def _fetch_todays_news() -> list:
    try:
        resp = requests.get(
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json", timeout=10
        )
        resp.raise_for_status()
        events    = resp.json()
        today_ldn = datetime.now(ZoneInfo("Europe/London")).date()
        return [
            e for e in events
            if e.get("impact") == "High"
            and e.get("country") == "USD"
            and _event_london_date(e) == today_ldn
        ][:5]
    except Exception as e:
        logger.warning("News fetch failed: %s", e)
        return []


def _event_london_date(event: dict):
    try:
        return (
            datetime.fromisoformat(event["date"])
            .astimezone(ZoneInfo("Europe/London"))
            .date()
        )
    except Exception:
        return None


def _format_news_event(event: dict) -> str:
    try:
        dt_ldn   = datetime.fromisoformat(event["date"]).astimezone(ZoneInfo("Europe/London"))
        time_str = dt_ldn.strftime(f"%H:%M {dt_ldn.strftime('%Z')}")
        return f"📰 {time_str} — {event.get('title', '—')}"
    except Exception:
        return f"📰 — {event.get('title', '—')}"


def build_briefing_message() -> str:
    now_ldn  = datetime.now(ZoneInfo("Europe/London"))
    day_str  = now_ldn.strftime("%A")
    date_str = f"{now_ldn.day} {now_ldn.strftime('%B %Y')}"
    levels   = _fetch_levels()
    news     = _fetch_todays_news()

    # ── Key levels — one line per symbol ────────────────────────────────────
    level_lines = []
    for sym_key in ("GOLD", "US30"):
        lv      = levels.get(sym_key)
        display = "GOLD" if sym_key == "GOLD" else "US30"
        if lv:
            level_lines.append(
                f"{display}  PDH: {_fmt(lv['pdh'], 0)}  |  PDL: {_fmt(lv['pdl'], 0)}"
            )
        else:
            level_lines.append(f"{display}  Data unavailable")

    # ── Bias ─────────────────────────────────────────────────────────────────
    bias_parts = []
    for sym_key in ("GOLD", "US30"):
        lv      = levels.get(sym_key)
        display = "GOLD" if sym_key == "GOLD" else "US30"
        b       = _bias(lv["current"], lv["pdh"], lv["pdl"]) if lv else "N/A"
        bias_parts.append(f"{display}: {b}")

    # ── News — deduplicate by time, group titles ──────────────────────────────
    if news:
        news_lines = [_format_news_event(e) for e in news]
    else:
        news_lines = ["📰 No high-impact USD events today"]

    return "\n".join([
        "🌅 ZST DAILY BRIEFING",
        f"{day_str} {date_str} | London Open Prep",
        "",
        _SCRIPTURE,
        "",
        "📍 KEY LEVELS",
        *level_lines,
        "",
        f"⚡ BIAS: {' | '.join(bias_parts)}",
        *news_lines,
        "",
        "Trade the levels. Trust the process.",
        "Zero Stress. Always. 🤎",
        "ZST Insider 🔐",
    ])


def post_morning_briefing() -> bool:
    logger.info("Posting morning briefing...")
    try:
        msg = build_briefing_message()
        if send_message(msg):
            logger.info("Morning briefing posted successfully.")
            return True
        logger.error("Morning briefing Telegram send failed.")
        return False
    except Exception as e:
        logger.exception("Morning briefing error: %s", e)
        return False
