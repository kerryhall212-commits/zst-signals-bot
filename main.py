"""
ZST Signals Bot — runs on every 4H candle close (UTC).
4H closes: 00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC.
The loop checks every 5 minutes and scans only when within
10 minutes of a 4H close, so API quota is never wasted.
"""

import logging
import os
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from config import SYMBOLS, SKIP_DUPLICATE_SIGNALS
from signal_engine import generate_smc_signal
from formatter import format_smc_message
from telegram_sender import send_message
import daily_counter
import trade_log
from price_monitor import check_open_trades
from weekly_review import post_weekly_review
from morning_briefing import post_morning_briefing
from textbook_tuesday import (
    generate_tt_signal, format_tt_message,
    is_tuesday_bst, current_session,
)
from morning_signal import generate_morning_signal, format_morning_signal
from news_filter import is_news_blackout

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

_last_signals:          dict[str, str] = {}
_scanned_closes:        set[str]       = set()
_review_posted_week:    str            = ""
_briefing_posted_day:   str            = ""
_morning_signal_day:    str            = ""
_tt_scanned:            set[str]       = set()   # "{date}-{session}" keys

_LIMIT_FOOTER = (
    "\n\n🔒 Final signal for the day.\n"
    "No more signals until tomorrow.\n\n"
    "Quality over quantity.\n"
    "Zero Stress. Always. 🙏"
)


def near_1h_close() -> bool:
    """Returns True during the 10-minute window after each UTC hourly candle close."""
    now = datetime.now(timezone.utc)
    return now.minute < 10


def close_key() -> str:
    now = datetime.now(timezone.utc)
    return f"{now.date()}-{now.hour:02d}"


def near_7am_bst() -> bool:
    """Returns True on weekdays at 07:00–07:10 BST (morning priority signal window)."""
    now_bst = datetime.now(ZoneInfo("Europe/London"))
    return now_bst.weekday() < 5 and now_bst.hour == 7 and now_bst.minute < 10


def near_morning_briefing() -> bool:
    """Returns True on weekdays at 07:00–07:10 UTC (London Open prep)."""
    now = datetime.now(timezone.utc)
    return now.weekday() < 5 and now.hour == 7 and now.minute < 10


def near_friday_review() -> bool:
    """Returns True on Friday at 20:00 BST (within a 5-minute window)."""
    now_london = datetime.now(ZoneInfo("Europe/London"))
    return now_london.weekday() == 4 and now_london.hour == 20 and now_london.minute < 5


def near_ny_open() -> bool:
    """Returns True on Tuesdays at 13:30–13:40 BST (NY Open, TT check)."""
    now_bst = datetime.now(ZoneInfo("Europe/London"))
    return now_bst.weekday() == 1 and now_bst.hour == 13 and 30 <= now_bst.minute < 40


def _tt_session_key(session: str) -> str:
    today = str(datetime.now(ZoneInfo("Europe/London")).date())
    return f"{today}-{session}"


def run_tt_session(session_override: str | None = None) -> None:
    """Run Textbook Tuesday detection for the given (or current) session."""
    if not is_tuesday_bst():
        return

    session = session_override or current_session()
    if session is None:
        return

    key = _tt_session_key(session)
    if key in _tt_scanned:
        return
    _tt_scanned.add(key)

    if daily_counter.is_limit_reached():
        logger.info("[TT] Daily limit reached — skipping %s scan.", session)
        return

    if is_news_blackout():
        logger.info("[TT] News blackout active — skipping %s scan.", session)
        return

    logger.info("── TT scan: %s session ──", session)

    for sym_key, info in SYMBOLS.items():
        if daily_counter.is_limit_reached():
            break

        signal = generate_tt_signal(info, session)
        if signal is None:
            continue

        is_final = (daily_counter.get_count() + 1 >= daily_counter.MAX_DAILY)
        msg = format_tt_message(info, signal)
        if is_final:
            msg += _LIMIT_FOOTER

        if send_message(msg):
            count, just_hit = daily_counter.increment()
            trade_log.record_signal(sym_key, info, signal)
            logger.info("[TT][%s] Signal posted. Daily count: %d/%d",
                        sym_key, count, daily_counter.MAX_DAILY)
            if just_hit:
                daily_counter.mark_limit_notified()
        else:
            logger.error("[TT][%s] Send failed.", sym_key)

    logger.info("── TT scan complete ──")


def run_morning_signal() -> None:
    """Run the 5-7AM continuation pullback check for all symbols."""
    if daily_counter.is_limit_reached():
        logger.info("[MS] Daily limit reached — skipping morning signal.")
        return

    if is_news_blackout():
        logger.info("[MS] News blackout active — skipping morning signal.")
        return

    logger.info("── Morning signal scan (5-7AM pullback) ──")

    for sym_key, info in SYMBOLS.items():
        if daily_counter.is_limit_reached():
            break

        signal = generate_morning_signal(info)
        if signal is None:
            continue

        is_final = (daily_counter.get_count() + 1 >= daily_counter.MAX_DAILY)
        msg = format_morning_signal(info, signal)
        if is_final:
            msg += _LIMIT_FOOTER

        if send_message(msg):
            count, just_hit = daily_counter.increment()
            trade_log.record_signal(sym_key, info, signal)
            logger.info("[MS][%s] Signal posted. Daily count: %d/%d",
                        sym_key, count, daily_counter.MAX_DAILY)
            if just_hit:
                daily_counter.mark_limit_notified()
        else:
            logger.error("[MS][%s] Send failed.", sym_key)

    logger.info("── Morning signal scan complete ──")


def _current_week() -> str:
    iso = datetime.now(timezone.utc).isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def run_signals():
    key = close_key()
    if key in _scanned_closes:
        return
    _scanned_closes.add(key)

    logger.info(f"── 1H close scan: {key} ──")

    if daily_counter.is_limit_reached():
        logger.info("Daily signal limit already reached — skipping scan.")
        logger.info("── Scan complete ──")
        return

    if is_news_blackout():
        logger.info("News blackout active — skipping 4H scan.")
        logger.info("── Scan complete ──")
        return

    # Collect valid signals from all symbols
    candidates = []
    for sym_key, info in SYMBOLS.items():
        signal = generate_smc_signal(info)
        if signal is None:
            continue
        sig_id = f"{signal['direction']}_{signal['entry']:.2f}"
        if SKIP_DUPLICATE_SIGNALS and _last_signals.get(sym_key) == sig_id:
            logger.info(f"[{sym_key}] Duplicate — skipping.")
            continue
        candidates.append((sym_key, info, signal))

    if not candidates:
        logger.info("── Scan complete ──")
        return

    # Rank 1 (Asian sweep) first, then PDH/PDL, then PWH/PWL
    candidates.sort(key=lambda x: x[2].get("priority", 3))

    remaining = daily_counter.MAX_DAILY - daily_counter.get_count()
    logger.info(f"{len(candidates)} setup(s) found, {remaining} slot(s) remaining today.")

    for sym_key, info, signal in candidates:
        if daily_counter.is_limit_reached():
            break

        priority = signal.get("priority", 3)
        logger.info(f"[{sym_key}] Sending Rank {priority} signal.")

        is_final = (daily_counter.get_count() + 1 >= daily_counter.MAX_DAILY)
        msg = format_smc_message(info, signal)
        if is_final:
            msg += _LIMIT_FOOTER

        if send_message(msg):
            count, just_hit = daily_counter.increment()
            trade_log.record_signal(sym_key, info, signal)
            _last_signals[sym_key] = f"{signal['direction']}_{signal['entry']:.2f}"
            logger.info(f"[{sym_key}] Signal posted. Daily count: {count}/{daily_counter.MAX_DAILY}")
            if just_hit:
                daily_counter.mark_limit_notified()
        else:
            logger.error(f"[{sym_key}] Telegram send failed.")

    logger.info("── Scan complete ──")

    # Textbook Tuesday check at each 1H close (covers Asian + London sessions)
    if is_tuesday_bst():
        run_tt_session()


def main():
    global _review_posted_week, _briefing_posted_day, _morning_signal_day

    logger.info("ZST Signals Bot starting (4H wick sweep engine).")

    # Validate required env vars at startup so failures are obvious in Railway logs
    td_key = os.getenv("TWELVEDATA_API_KEY")
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
    tg_chat = os.getenv("TELEGRAM_CHANNEL_ID")
    logger.info(
        "Env check — TWELVEDATA_API_KEY: %s | TELEGRAM_BOT_TOKEN: %s | TELEGRAM_CHANNEL_ID: %s",
        f"set ({td_key[:4]}...)" if td_key else "NOT SET",
        "set" if tg_token else "NOT SET",
        tg_chat or "NOT SET",
    )
    if not td_key:
        logger.error("TWELVEDATA_API_KEY is missing — bot will fail on first API call.")

    run_signals()
    check_open_trades()

    logger.info("Entering 4H candle close watch loop (polls every 5 min).")
    while True:
        time.sleep(300)
        if near_morning_briefing():
            today = str(datetime.now(timezone.utc).date())
            if today != _briefing_posted_day:
                post_morning_briefing()
                _briefing_posted_day = today
        if near_7am_bst():
            today_bst = str(datetime.now(ZoneInfo("Europe/London")).date())
            if today_bst != _morning_signal_day:
                run_morning_signal()
                _morning_signal_day = today_bst
        if near_ny_open():
            run_tt_session("NY")
        if near_1h_close():
            run_signals()
            check_open_trades()
        if near_friday_review():
            week = _current_week()
            if week != _review_posted_week:
                post_weekly_review()
                _review_posted_week = week


if __name__ == "__main__":
    main()
