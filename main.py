"""
ZST Signals Bot — Session-based sweep / OB / CE signal engine.

Sessions (BST):
  Asian  — 21:00–07:59  |  Gold only  |  1 signal max
  London — 08:00–12:59  |  Gold only  |  1 signal max
  NY     — 13:00–20:59  |  Gold + US30  |  1 signal max per symbol

Total: 3 signals max per day (1 per session).

Level schedule:
  21:00 BST (weekdays) → compute PDH/PDL/PWH/PWL → save levels.json
  05:45 BST (weekdays) → post morning briefing (once, file-locked)
  07:00 BST (weekdays) → add Asian H/L to levels.json
"""

import glob as _glob
import logging
import os
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from config import SYMBOLS
from sweep_engine import check_session
from formatter import format_signal
from telegram_sender import send_message
import session_counter
import trade_log
from price_monitor import check_open_trades
from morning_briefing import post_morning_briefing
from news_filter import is_news_blackout
import levels_store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

_OB_INVALIDATION_MSG = (
    "⚠️ <b>ZST UPDATE</b>\n\n"
    "Signal invalidated — OB broken\n"
    "Waiting for next setup 🎯\n\n"
    "ZST Insider 🔐"
)

_levels_computed_day:    str = ""
_asian_levels_added_day: str = ""
_briefing_posted_day:    str = ""
_briefing_cleanup_day:   str = ""


# ── Time helpers ───────────────────────────────────────────────────────────────

def _bst_now() -> datetime:
    return datetime.now(ZoneInfo("Europe/London"))


def _bst_mins() -> int:
    n = _bst_now()
    return n.hour * 60 + n.minute


def get_current_session() -> str | None:
    now = _bst_now()
    wd  = now.weekday()   # 0=Mon … 6=Sun
    m   = _bst_mins()

    if wd == 5:                       # Saturday — no session
        return None
    if wd == 6 and m < 21 * 60:      # Sunday before 21:00 — no session
        return None
    if wd == 4 and m >= 21 * 60:     # Friday after 21:00 — weekend starts
        return None

    if m >= 21 * 60 or m < 8 * 60:
        return "asian"
    if 8 * 60 <= m < 13 * 60:
        return "london"
    if 13 * 60 <= m < 21 * 60:
        return "ny"
    return None


# ── Briefing file lock (survives restarts) ─────────────────────────────────────

def _briefing_lock_path(today: str) -> str:
    return f"briefing_sent_{today}.txt"


def _briefing_lock_exists(today: str) -> bool:
    return os.path.exists(_briefing_lock_path(today))


def _write_briefing_lock(today: str) -> None:
    with open(_briefing_lock_path(today), "w") as f:
        f.write(today)


def _clean_old_briefing_files(today: str) -> None:
    keep = _briefing_lock_path(today)
    for path in _glob.glob("briefing_sent_*.txt"):
        if not path.endswith(keep):
            try:
                os.remove(path)
                logger.info("Removed old briefing lock: %s", path)
            except Exception as e:
                logger.warning("Could not remove old briefing lock %s: %s", path, e)


# ── Signal dispatch ────────────────────────────────────────────────────────────

def _post_signal(sym_key: str, symbol_config: dict, signal: dict,
                 session: str) -> bool:
    # Bug 3 safety gate: reject any signal where SL distance exceeds per-symbol max
    pip         = symbol_config.get("pip_size", 1.0)
    max_sl_pips = symbol_config.get("intraday_sl_pips", 15)
    sl_dist     = abs(signal["entry"] - signal["sl"]) / pip
    if sl_dist > max_sl_pips:
        logger.info("[%s][%s] Signal rejected — SL %.1f pips exceeds max %d.",
                    sym_key, session, sl_dist, max_sl_pips)
        return False

    msg = format_signal(symbol_config, signal)
    if not send_message(msg):
        logger.error("[%s][%s] Telegram send failed.", sym_key, session)
        return False
    # Record ONLY after Telegram confirms delivery
    count = session_counter.mark_session_fired(sym_key, session)
    trade_log.record_signal(sym_key, symbol_config, signal)
    logger.info("[%s][%s] Signal posted. Daily total: %d/%d",
                sym_key, session, count, session_counter.MAX_DAILY)
    return True


# ── Session scan ───────────────────────────────────────────────────────────────

def run_session_scan(session: str) -> None:
    if session_counter.is_limit_reached():
        logger.info("[%s] Daily limit (%d) reached.",
                    session, session_counter.MAX_DAILY)
        return

    if is_news_blackout():
        logger.info("[%s] News blackout — skipping.", session)
        return

    levels = levels_store.load()
    if not levels:
        logger.info("[%s] No levels loaded yet.", session)
        return

    logger.info("── %s session scan ──", session.capitalize())

    for sym_key, symbol_config in SYMBOLS.items():
        if session_counter.is_limit_reached():
            break
        if session_counter.is_session_fired(sym_key, session):
            logger.debug("[%s][%s] Already fired this session.", sym_key, session)
            continue

        # US30 only trades in NY session
        if symbol_config.get("display") == "US30" and session != "ny":
            continue

        lvl    = levels.get(sym_key, {})
        signal = check_session(symbol_config, session, lvl)
        if signal is None:
            continue

        _post_signal(sym_key, symbol_config, signal, session)

    logger.info("── %s scan complete ──", session.capitalize())


# ── Main loop ──────────────────────────────────────────────────────────────────

def main():
    global _levels_computed_day, _asian_levels_added_day, _briefing_posted_day, _briefing_cleanup_day

    td_key   = os.getenv("TWELVEDATA_API_KEY")
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
    tg_chat  = os.getenv("TELEGRAM_CHANNEL_ID")

    logger.info("ZST Signals Bot starting (session sweep engine).")
    logger.info(
        "Env — TWELVEDATA_API_KEY: %s | TELEGRAM_BOT_TOKEN: %s | TELEGRAM_CHANNEL_ID: %s",
        f"set ({td_key[:4]}...)" if td_key else "NOT SET",
        "set" if tg_token else "NOT SET",
        tg_chat or "NOT SET",
    )

    _heartbeat_hour = -1
    logger.info("Entering main loop (polls every 5 min).")

    while True:
        now_bst   = _bst_now()
        today_bst = str(now_bst.date())
        m         = _bst_mins()

        # ── Hourly heartbeat ──────────────────────────────────────────────
        if now_bst.hour != _heartbeat_hour:
            _heartbeat_hour = now_bst.hour
            logger.info("Heartbeat %s BST | signals today: %d/%d",
                        now_bst.strftime("%H:%M"),
                        session_counter.get_count(),
                        session_counter.MAX_DAILY)

        # ── 0. Midnight cleanup of old briefing lock files ────────────────
        if now_bst.hour == 0 and now_bst.minute < 10:
            if today_bst != _briefing_cleanup_day:
                _clean_old_briefing_files(today_bst)
                _briefing_cleanup_day = today_bst

        # ── 1. Compute levels at 21:00 BST (weekdays) ─────────────────────
        if now_bst.weekday() < 5 and now_bst.hour == 21 and now_bst.minute < 10:
            if today_bst != _levels_computed_day:
                logger.info("Computing daily levels...")
                levels_store.compute_and_save()
                _levels_computed_day = today_bst

        # ── 2. Morning briefing at 05:45 BST (once, file-locked) ──────────
        if now_bst.weekday() < 5 and m >= 5 * 60 + 45 and today_bst != _briefing_posted_day:
            if _briefing_lock_exists(today_bst):
                _briefing_posted_day = today_bst
                logger.info("Briefing lock found — already posted today.")
            elif post_morning_briefing():
                _write_briefing_lock(today_bst)
                _briefing_posted_day = today_bst

        # ── 3. Add Asian H/L at 07:00 BST ─────────────────────────────────
        if now_bst.weekday() < 5 and now_bst.hour == 7 and now_bst.minute < 10:
            if today_bst != _asian_levels_added_day:
                logger.info("Adding Asian H/L to levels...")
                levels_store.add_asian_levels()
                _asian_levels_added_day = today_bst

        # ── 4. Session scan ────────────────────────────────────────────────
        session = get_current_session()
        if session:
            run_session_scan(session)

        # ── 5. Hourly TP/SL monitor ────────────────────────────────────────
        if now_bst.minute < 10:
            check_open_trades()

        time.sleep(300)


if __name__ == "__main__":
    main()
