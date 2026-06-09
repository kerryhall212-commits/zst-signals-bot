"""
ZST Signals Bot — runs on every 4H candle close (UTC).
4H closes: 00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC.
The loop checks every 5 minutes and scans only when within
10 minutes of a 4H close, so API quota is never wasted.
"""

import logging
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

_last_signals:      dict[str, str] = {}
_scanned_closes:    set[str]       = set()
_review_posted_week: str           = ""

LIMIT_REACHED_MSG = (
    "🔒 ZST SIGNAL LIMIT REACHED\n"
    "\n"
    "Maximum trades for today hit.\n"
    "No more signals until tomorrow.\n"
    "\n"
    "Quality over quantity.\n"
    "Zero Stress. Always. 🙏\n"
    "ZST Insider 🔐"
)


def near_4h_close() -> bool:
    """Returns True during the 10-minute window after a 4H UTC candle close."""
    now = datetime.now(timezone.utc)
    return now.hour % 4 == 0 and now.minute < 10


def close_key() -> str:
    now = datetime.now(timezone.utc)
    return f"{now.date()}-{now.hour:02d}"


def near_friday_review() -> bool:
    """Returns True on Friday at 20:00 BST (within a 5-minute window)."""
    now_london = datetime.now(ZoneInfo("Europe/London"))
    return now_london.weekday() == 4 and now_london.hour == 20 and now_london.minute < 5


def _current_week() -> str:
    iso = datetime.now(timezone.utc).isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _notify_limit_if_needed() -> None:
    if not daily_counter.is_limit_notified():
        send_message(LIMIT_REACHED_MSG)
        daily_counter.mark_limit_notified()
        logger.info("Daily limit reached — limit notification sent.")


def run_signals():
    key = close_key()
    if key in _scanned_closes:
        return
    _scanned_closes.add(key)

    logger.info(f"── 4H close scan: {key} ──")

    if daily_counter.is_limit_reached():
        _notify_limit_if_needed()
        logger.info("Daily signal limit already reached — skipping scan.")
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
            _notify_limit_if_needed()
            break

        priority = signal.get("priority", 3)
        logger.info(f"[{sym_key}] Sending Rank {priority} signal.")

        msg = format_smc_message(info, signal)
        if send_message(msg):
            count, just_hit = daily_counter.increment()
            trade_log.record_signal(sym_key, info, signal)
            _last_signals[sym_key] = f"{signal['direction']}_{signal['entry']:.2f}"
            logger.info(f"[{sym_key}] Signal posted. Daily count: {count}/{daily_counter.MAX_DAILY}")
            if just_hit:
                _notify_limit_if_needed()
        else:
            logger.error(f"[{sym_key}] Telegram send failed.")

    logger.info("── Scan complete ──")


def main():
    global _review_posted_week

    logger.info("ZST Signals Bot starting (4H wick sweep engine).")
    run_signals()
    check_open_trades()

    logger.info("Entering 4H candle close watch loop (polls every 5 min).")
    while True:
        time.sleep(300)
        if near_4h_close():
            run_signals()
            check_open_trades()
        if near_friday_review():
            week = _current_week()
            if week != _review_posted_week:
                post_weekly_review()
                _review_posted_week = week


if __name__ == "__main__":
    main()
