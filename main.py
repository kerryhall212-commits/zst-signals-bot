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
_eod_posted_day:        str            = ""
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


def _bst_mins() -> int:
    now_bst = datetime.now(ZoneInfo("Europe/London"))
    return now_bst.hour * 60 + now_bst.minute


def is_briefing_window() -> bool:
    """True on weekdays from 05:45 BST onwards (until EOD). Used for retry loop."""
    now_bst = datetime.now(ZoneInfo("Europe/London"))
    return now_bst.weekday() < 5 and _bst_mins() >= 5 * 60 + 45


def is_scan_window() -> bool:
    """True if briefing has posted today and current BST time is before 22:00."""
    now_bst = datetime.now(ZoneInfo("Europe/London"))
    if now_bst.weekday() >= 5:
        return False
    today_bst = str(now_bst.date())
    if today_bst != _briefing_posted_day:
        return False
    return _bst_mins() < 22 * 60


def near_7am_bst() -> bool:
    """True on weekdays at 07:00–07:10 BST (5-7AM pullback confirmation window)."""
    now_bst = datetime.now(ZoneInfo("Europe/London"))
    return now_bst.weekday() < 5 and now_bst.hour == 7 and now_bst.minute < 10


def near_ny_open() -> bool:
    """True on Tuesdays at 13:30–13:40 BST (NY Open, TT check)."""
    now_bst = datetime.now(ZoneInfo("Europe/London"))
    return now_bst.weekday() == 1 and now_bst.hour == 13 and 30 <= now_bst.minute < 40


def near_eod_alert() -> bool:
    """True on weekdays at 21:00–21:10 BST."""
    now_bst = datetime.now(ZoneInfo("Europe/London"))
    return now_bst.weekday() < 5 and now_bst.hour == 21 and now_bst.minute < 10


def near_friday_review() -> bool:
    """True on Friday at 20:00–20:05 BST."""
    now_bst = datetime.now(ZoneInfo("Europe/London"))
    return now_bst.weekday() == 4 and now_bst.hour == 20 and now_bst.minute < 5


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

    if not is_scan_window():
        logger.info("[TT] Scan blocked — briefing not yet posted or outside scan window.")
        return

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
    if not is_scan_window():
        logger.info("[MS] Scan blocked — briefing not yet posted or outside scan window.")
        return

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


def post_eod_alert() -> None:
    now_bst  = datetime.now(ZoneInfo("Europe/London"))
    day_str  = now_bst.strftime("%A")
    date_str = f"{now_bst.day} {now_bst.strftime('%B %Y')}"
    count    = daily_counter.get_count()
    msg = "\n".join([
        "🌙 <b>ZST END OF DAY</b>",
        f"{day_str} {date_str}",
        "",
        "Trading day complete.",
        f"Signals today: {count}/{daily_counter.MAX_DAILY}",
        "",
        "Rest well. Come back focused tomorrow. 🙏",
        "Zero Stress. Always. 🤎",
        "ZST Insider 🔐",
    ])
    if send_message(msg):
        logger.info("EOD alert posted.")
    else:
        logger.error("EOD alert send failed.")


def _current_week() -> str:
    iso = datetime.now(timezone.utc).isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def run_signals():
    key = close_key()
    if key in _scanned_closes:
        return
    _scanned_closes.add(key)

    logger.info(f"── 1H close scan: {key} ──")

    if not is_scan_window():
        logger.info("Scan blocked — briefing not yet posted or outside scan window.")
        logger.info("── Scan complete ──")
        return

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
    global _review_posted_week, _briefing_posted_day, _morning_signal_day, _eod_posted_day

    logger.info("ZST Signals Bot starting (1H wick sweep engine).")

    td_key   = os.getenv("TWELVEDATA_API_KEY")
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
    tg_chat  = os.getenv("TELEGRAM_CHANNEL_ID")
    logger.info(
        "Env check — TWELVEDATA_API_KEY: %s | TELEGRAM_BOT_TOKEN: %s | TELEGRAM_CHANNEL_ID: %s",
        f"set ({td_key[:4]}...)" if td_key else "NOT SET",
        "set" if tg_token else "NOT SET",
        tg_chat or "NOT SET",
    )
    if not td_key:
        logger.error("TWELVEDATA_API_KEY is missing — bot will fail on first API call.")

    logger.info("Entering main loop (polls every 5 min).")
    while True:
        now_bst   = datetime.now(ZoneInfo("Europe/London"))
        today_bst = str(now_bst.date())

        # ── 1. Briefing: 05:45 BST — retry every poll until success ──────────
        if is_briefing_window() and today_bst != _briefing_posted_day:
            if post_morning_briefing():
                _briefing_posted_day = today_bst
                logger.info("Briefing posted — scanning now unlocked for today.")
            else:
                logger.warning("Briefing failed — will retry next poll. Scans remain blocked.")

        # ── 2. Morning priority signal: 07:00 BST ────────────────────────────
        if near_7am_bst() and today_bst != _morning_signal_day:
            run_morning_signal()
            _morning_signal_day = today_bst

        # ── 3. TT NY session: 13:30 BST (Tuesdays only) ──────────────────────
        if near_ny_open():
            run_tt_session("NY")

        # ── 4. Hourly 1H close scan ───────────────────────────────────────────
        if near_1h_close():
            run_signals()
            check_open_trades()

        # ── 5. EOD alert: 21:00 BST ───────────────────────────────────────────
        if near_eod_alert() and today_bst != _eod_posted_day:
            post_eod_alert()
            _eod_posted_day = today_bst

        # ── 6. Friday weekly review: 20:00 BST ───────────────────────────────
        if near_friday_review():
            week = _current_week()
            if week != _review_posted_week:
                post_weekly_review()
                _review_posted_week = week

        time.sleep(300)


if __name__ == "__main__":
    main()
