"""
ZST Signals Bot — 6-slot SMC signal engine.

Slot schedule (BST):
  Slot 1 — Tokyo PDH/PDL Sweep   00:00–03:00  Gold only    SWING
  Slot 2 — 6AM Continuation      06:00–07:30  Gold + US30  INTRADAY
  Slot 3 — London ORB             08:00–11:00  Gold + US30  LONDON
  Slot 5 — NY Open Sweep         13:30–15:00  Gold + US30  SWING
  Slot 6 — Guaranteed Daily      13:00        Gold only    INTRADAY (if <3 signals today)

Plus:
  Morning briefing  05:45 BST (weekdays)
  Textbook Tuesday  runs at London + NY open on Tuesdays (replaces Slot 3)
  EOD alert         21:00 BST
  Weekly review     Friday 20:00 BST
  1H wick sweep     every hour (existing engine, no slot tag)
  Intraday momentum every 30M during London/NY (existing engine)
"""

import logging
import os
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from config import SYMBOLS, SKIP_DUPLICATE_SIGNALS
from signal_engine import generate_smc_signal
from formatter import format_swing_signal, format_intraday_signal, format_london_signal, format_us30_signal
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
from intraday_momentum import generate_intraday_signal
from daily_guaranteed import generate_daily_signal
from news_filter import is_news_blackout

# Slot engines
from slot1_tokyo_sweep      import generate_slot1_signal
from slot2_6am_continuation import generate_slot2_signal
from slot3_london_sweep     import generate_slot3_signal
from slot5_ny_sweep         import generate_slot5_signal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

_last_signals:           dict[str, str] = {}
_scanned_closes:         set[str]       = set()
_intraday_scanned:       set[str]       = set()
_last_intraday_signals:  dict[str, str] = {}
_review_posted_week:     str            = ""
_briefing_posted_day:    str            = ""
_morning_signal_day:     str            = ""
_eod_posted_day:         str            = ""
_tt_scanned:             set[str]       = set()
_daily_g_fired_day:      str            = ""
_daily_g_13_tried:       str            = ""

# Per-slot scan dedup keys
_slot_scanned:           dict[int, set] = {i: set() for i in range(1, 6)}

_LIMIT_FOOTER = (
    "\n\n🔒 Final signal for the day.\n"
    "No more signals until tomorrow.\n\n"
    "Quality over quantity.\n"
    "Zero Stress. Always. 🙏"
)

_OB_INVALIDATION_MSG = (
    "⚠️ <b>ZST SIGNAL UPDATE</b>\n\n"
    "Previous signal invalidated.\n"
    "OB broken — no entry.\n"
    "Waiting for next setup.\n\n"
    "ZST Insider 🔐"
)

# Tracks which slot+candle combos already posted an OB invalidation
_ob_invalidation_posted: set[str] = set()


# ── Time-window helpers ────────────────────────────────────────────────────────

def _bst_mins() -> int:
    now = datetime.now(ZoneInfo("Europe/London"))
    return now.hour * 60 + now.minute


def near_1h_close() -> bool:
    return datetime.now(timezone.utc).minute < 10


def close_key() -> str:
    now = datetime.now(timezone.utc)
    return f"{now.date()}-{now.hour:02d}"


def near_15m_close() -> bool:
    """True within 5 min after each 15M candle close (XX:00, XX:15, XX:30, XX:45 UTC)."""
    return datetime.now(timezone.utc).minute % 15 < 5


def m15_close_key() -> str:
    now  = datetime.now(timezone.utc)
    slot = (now.minute // 15) * 15
    return f"{now.date()}-{now.hour:02d}-{slot:02d}"


def near_30m_close() -> bool:
    now = datetime.now(timezone.utc)
    return now.minute < 10 or 30 <= now.minute < 40


def intraday_close_key() -> str:
    now  = datetime.now(timezone.utc)
    half = "30" if now.minute >= 30 else "00"
    return f"{now.date()}-{now.hour:02d}-{half}"


def is_briefing_window() -> bool:
    now = datetime.now(ZoneInfo("Europe/London"))
    return now.weekday() < 5 and _bst_mins() >= 5 * 60 + 45


def is_scan_window() -> bool:
    now = datetime.now(ZoneInfo("Europe/London"))
    if now.weekday() >= 5:
        return False
    return 5 * 60 + 45 <= _bst_mins() < 16 * 60


def near_7am_bst() -> bool:
    now = datetime.now(ZoneInfo("Europe/London"))
    return now.weekday() < 5 and now.hour == 7 and now.minute < 10


def near_ny_open() -> bool:
    now = datetime.now(ZoneInfo("Europe/London"))
    return now.weekday() == 1 and now.hour == 13 and 30 <= now.minute < 40


def near_eod_alert() -> bool:
    now = datetime.now(ZoneInfo("Europe/London"))
    return now.weekday() < 5 and now.hour == 21 and now.minute < 10


def near_friday_review() -> bool:
    now = datetime.now(ZoneInfo("Europe/London"))
    return now.weekday() == 4 and now.hour == 20 and now.minute < 5


def near_13_bst() -> bool:
    now = datetime.now(ZoneInfo("Europe/London"))
    return now.weekday() < 5 and now.hour == 13 and now.minute < 10


def near_1430_bst() -> bool:
    now = datetime.now(ZoneInfo("Europe/London"))
    return now.weekday() < 5 and now.hour == 14 and 25 <= now.minute < 35


def is_intraday_session_active() -> bool:
    now = datetime.now(ZoneInfo("Europe/London"))
    if now.weekday() >= 5:
        return False
    m = _bst_mins()
    return (8 * 60 <= m < 11 * 60) or (13 * 60 + 30 <= m < 16 * 60)


def _slot_window_active(slot: int) -> bool:
    """True if current BST time is within the given slot's window."""
    m = _bst_mins()
    now_bst = datetime.now(ZoneInfo("Europe/London"))
    if now_bst.weekday() >= 5:
        return False
    return {
        1: 0             <= m < 5 * 60 + 45,   # 00:00–05:44 Tokyo
        2: 5 * 60 + 45   <= m < 8 * 60,         # 05:45–07:59 Pre-London
        3: 8 * 60        <= m < 11 * 60,         # 08:00–10:59 London ORB
        5: 13 * 60       <= m < 16 * 60,         # 13:00–15:59 NY
    }.get(slot, False)


def _slot_close_key(slot: int) -> str:
    """Unique key per candle close per slot."""
    if slot in (3, 5):
        return m15_close_key()
    return intraday_close_key()


def _near_close_for_slot(slot: int) -> bool:
    if slot in (3, 5):
        return near_15m_close()
    return near_30m_close()


def _tt_session_key(session: str) -> str:
    today = str(datetime.now(ZoneInfo("Europe/London")).date())
    return f"{today}-{session}"


# ── Signal dispatch helpers ────────────────────────────────────────────────────

def _post_signal(sym_key: str, info: dict, signal: dict, label: str) -> bool:
    """Format, send, increment counter and log. Returns True on success."""
    sig_type = signal.get("signal_type", "")
    if sig_type == "us30_ny":
        msg = format_us30_signal(info, signal)
    elif sig_type == "london_orb":
        msg = format_london_signal(info, signal)
    elif label == "swing" or signal.get("slot") in (1, 5):
        msg = format_swing_signal(info, signal)
    else:
        msg = format_intraday_signal(info, signal)

    is_final = (daily_counter.get_count() + 1 >= daily_counter.MAX_DAILY)
    if is_final:
        msg += _LIMIT_FOOTER

    if not send_message(msg):
        logger.error("[%s] Telegram send failed.", sym_key)
        return False

    count, just_hit = daily_counter.increment()
    trade_log.record_signal(sym_key, info, signal)
    logger.info("[%s] Signal posted (slot=%s). Daily: %d/%d",
                sym_key, signal.get("slot", "?"), count, daily_counter.MAX_DAILY)
    if just_hit:
        daily_counter.mark_limit_notified()
    return True


# ── Slot runners ───────────────────────────────────────────────────────────────

_SLOT_GENERATORS = {
    1: generate_slot1_signal,
    2: generate_slot2_signal,
    3: generate_slot3_signal,
    5: generate_slot5_signal,
}

_SLOT_SESSIONS = {
    1: "Tokyo",
    2: "Pre-London",
    3: "London ORB",
    5: "NY",
    6: "NY",
}

_SLOT_GOLD_ONLY        = {1}  # slots that only run on Gold
_BRIEFING_EXEMPT_SLOTS = {1}  # slots that run before the morning briefing is posted


def run_slot(slot: int) -> None:
    """Generic slot runner. Checks dedup key, slot-fired guard, news, daily limit."""
    if not _slot_window_active(slot):
        return
    if not _near_close_for_slot(slot):
        return

    close_k = _slot_close_key(slot)
    if close_k in _slot_scanned[slot]:
        return
    _slot_scanned[slot].add(close_k)

    if daily_counter.is_slot_fired(slot):
        logger.debug("[S%d] Already fired today — skipping.", slot)
        return
    if slot not in _BRIEFING_EXEMPT_SLOTS and not is_scan_window():
        logger.info("[S%d] Scan blocked — outside trading window.", slot)
        return
    if daily_counter.is_limit_reached():
        logger.info("[S%d] Daily limit reached.", slot)
        return
    if is_news_blackout():
        logger.info("[S%d] News blackout — skipping.", slot)
        return

    logger.info("── Slot %d scan ──", slot)
    gen = _SLOT_GENERATORS[slot]

    for sym_key, info in SYMBOLS.items():
        if slot in _SLOT_GOLD_ONLY and info.get("display") != "GOLD":
            continue

        signal = gen(info)
        if signal is None:
            continue

        now_bst_t = datetime.now(ZoneInfo("Europe/London"))
        signal["session"] = _SLOT_SESSIONS.get(slot, "")
        signal["signal_time_bst"] = now_bst_t.strftime("%H:%M BST")

        # OB invalidation — post cancellation message, don't count as signal
        if signal.get("signal_type") == "ob_invalidated":
            inv_key = f"inv-s{slot}-{sym_key}-{_slot_close_key(slot)}"
            if inv_key not in _ob_invalidation_posted:
                _ob_invalidation_posted.add(inv_key)
                if send_message(_OB_INVALIDATION_MSG):
                    logger.info("[S%d][%s] OB invalidation posted.", slot, sym_key)
                else:
                    logger.error("[S%d][%s] OB invalidation send failed.", slot, sym_key)
            continue

        if daily_counter.is_limit_reached():
            break

        if _post_signal(sym_key, info, signal,
                        "swing" if slot in (1, 5) else "intraday"):
            daily_counter.mark_slot_fired(slot)
            break  # max 1 signal per slot

    logger.info("── Slot %d scan complete ──", slot)


# ── Existing engine runners ────────────────────────────────────────────────────

def run_tt_session(session_override: str | None = None) -> None:
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
        logger.info("[TT] Scan blocked.")
        return
    if daily_counter.is_limit_reached():
        logger.info("[TT] Daily limit reached — skipping %s.", session)
        return
    if is_news_blackout():
        logger.info("[TT] News blackout — skipping %s.", session)
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
            logger.info("[TT][%s] Signal posted. Daily: %d/%d", sym_key, count, daily_counter.MAX_DAILY)
            if just_hit:
                daily_counter.mark_limit_notified()
        else:
            logger.error("[TT][%s] Send failed.", sym_key)

    logger.info("── TT scan complete ──")


def run_morning_signal() -> None:
    if not is_scan_window():
        logger.info("[MS] Scan blocked.")
        return
    if daily_counter.is_limit_reached():
        logger.info("[MS] Daily limit reached.")
        return
    if is_news_blackout():
        logger.info("[MS] News blackout.")
        return

    logger.info("── Morning signal scan ──")

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
            logger.info("[MS][%s] Signal posted. Daily: %d/%d", sym_key, count, daily_counter.MAX_DAILY)
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


def run_daily_guaranteed(ignore_news: bool = False) -> None:
    """
    Slot 6 — Guaranteed daily signal at 13:00 BST.
    Fires only if fewer than 3 signals have been sent today.
    Gold only (aligns with spec).
    """
    global _daily_g_fired_day

    today_bst = str(datetime.now(ZoneInfo("Europe/London")).date())

    if today_bst == _daily_g_fired_day:
        return

    count_now = daily_counter.get_count()
    if count_now >= 3:
        logger.info("[S6] %d signals already sent today — guaranteed daily not needed.", count_now)
        _daily_g_fired_day = today_bst
        return

    if not ignore_news and is_news_blackout():
        logger.info("[S6] News blackout — will retry at 14:30 BST.")
        return

    if not is_scan_window():
        logger.info("[S6] Scan blocked — briefing not posted or outside window.")
        return

    logger.info("── Slot 6 — Guaranteed daily scan ──")

    for sym_key, info in SYMBOLS.items():
        if info.get("display") != "GOLD":
            continue  # Slot 6 is Gold only per spec
        if daily_counter.is_limit_reached():
            break

        signal = generate_daily_signal(info)
        if signal is None:
            logger.info("[S6][%s] No signal generated.", sym_key)
            continue

        is_final = (daily_counter.get_count() + 1 >= daily_counter.MAX_DAILY)
        msg = format_intraday_signal(info, signal)
        if is_final:
            msg += _LIMIT_FOOTER

        now_bst_t = datetime.now(ZoneInfo("Europe/London"))
        signal["session"] = "NY"
        signal["signal_time_bst"] = now_bst_t.strftime("%H:%M BST")

        if send_message(msg):
            count, just_hit = daily_counter.increment()
            trade_log.record_signal(sym_key, info, signal)
            _daily_g_fired_day = today_bst
            daily_counter.mark_slot_fired(6)
            logger.info("[S6][%s] Daily guaranteed posted. Daily: %d/%d",
                        sym_key, count, daily_counter.MAX_DAILY)
            if just_hit:
                daily_counter.mark_limit_notified()
        else:
            logger.error("[S6][%s] Send failed.", sym_key)

    logger.info("── Slot 6 scan complete ──")


def run_intraday_signals() -> None:
    key = intraday_close_key()
    if key in _intraday_scanned:
        return
    _intraday_scanned.add(key)

    logger.info("── Intraday 30M scan: %s ──", key)

    if not is_scan_window():
        logger.info("[IM] Scan blocked.")
        return
    if daily_counter.is_limit_reached():
        logger.info("[IM] Daily limit reached.")
        return
    if is_news_blackout():
        logger.info("[IM] News blackout.")
        return

    for sym_key, info in SYMBOLS.items():
        if daily_counter.is_limit_reached():
            break

        signal = generate_intraday_signal(info)
        if signal is None:
            continue

        sig_id = f"{signal['direction']}_{signal['entry']:.2f}"
        if _last_intraday_signals.get(sym_key) == sig_id:
            logger.info("[IM][%s] Duplicate — skipping.", sym_key)
            continue

        is_final = (daily_counter.get_count() + 1 >= daily_counter.MAX_DAILY)
        msg = format_intraday_signal(info, signal)
        if is_final:
            msg += _LIMIT_FOOTER

        if send_message(msg):
            count, just_hit = daily_counter.increment()
            trade_log.record_signal(sym_key, info, signal)
            _last_intraday_signals[sym_key] = sig_id
            logger.info("[IM][%s] Signal posted. Daily: %d/%d", sym_key, count, daily_counter.MAX_DAILY)
            if just_hit:
                daily_counter.mark_limit_notified()
        else:
            logger.error("[IM][%s] Send failed.", sym_key)

    logger.info("── Intraday 30M scan complete ──")


def run_signals() -> None:
    key = close_key()
    if key in _scanned_closes:
        return
    _scanned_closes.add(key)

    logger.info("── 1H close scan: %s ──", key)

    if not is_scan_window():
        logger.info("Scan blocked.")
        logger.info("── Scan complete ──")
        return
    if daily_counter.is_limit_reached():
        logger.info("Daily limit reached.")
        logger.info("── Scan complete ──")
        return
    if is_news_blackout():
        logger.info("News blackout — skipping 1H scan.")
        logger.info("── Scan complete ──")
        return

    candidates = []
    for sym_key, info in SYMBOLS.items():
        signal = generate_smc_signal(info)
        if signal is None:
            continue
        sig_id = f"{signal['direction']}_{signal['entry']:.2f}"
        if SKIP_DUPLICATE_SIGNALS and _last_signals.get(sym_key) == sig_id:
            logger.info("[%s] Duplicate — skipping.", sym_key)
            continue
        candidates.append((sym_key, info, signal))

    if not candidates:
        logger.info("── Scan complete ──")
        return

    candidates.sort(key=lambda x: x[2].get("priority", 3))
    remaining = daily_counter.MAX_DAILY - daily_counter.get_count()
    logger.info("%d setup(s) found, %d slot(s) remaining.", len(candidates), remaining)

    for sym_key, info, signal in candidates:
        if daily_counter.is_limit_reached():
            break

        logger.info("[%s] Sending Rank %s signal.", sym_key, signal.get("priority", "?"))

        is_final = (daily_counter.get_count() + 1 >= daily_counter.MAX_DAILY)
        msg = format_swing_signal(info, signal)
        if is_final:
            msg += _LIMIT_FOOTER

        if send_message(msg):
            count, just_hit = daily_counter.increment()
            trade_log.record_signal(sym_key, info, signal)
            _last_signals[sym_key] = f"{signal['direction']}_{signal['entry']:.2f}"
            logger.info("[%s] Signal posted. Daily: %d/%d", sym_key, count, daily_counter.MAX_DAILY)
            if just_hit:
                daily_counter.mark_limit_notified()
        else:
            logger.error("[%s] Telegram send failed.", sym_key)

    logger.info("── Scan complete ──")

    if is_tuesday_bst():
        run_tt_session()


# ── Main loop ──────────────────────────────────────────────────────────────────

def main():
    global _review_posted_week, _briefing_posted_day, _morning_signal_day, _eod_posted_day

    logger.info("ZST Signals Bot starting (6-slot SMC engine).")

    td_key   = os.getenv("TWELVEDATA_API_KEY")
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
    tg_chat  = os.getenv("TELEGRAM_CHANNEL_ID")
    logger.info(
        "Env — TWELVEDATA_API_KEY: %s | TELEGRAM_BOT_TOKEN: %s | TELEGRAM_CHANNEL_ID: %s",
        f"set ({td_key[:4]}...)" if td_key else "NOT SET",
        "set" if tg_token else "NOT SET",
        tg_chat or "NOT SET",
    )
    if not td_key:
        logger.error("TWELVEDATA_API_KEY missing — bot will fail on first API call.")

    logger.info("Entering main loop (polls every 5 min).")
    _heartbeat_hour = -1
    while True:
        now_bst   = datetime.now(ZoneInfo("Europe/London"))
        today_bst = str(now_bst.date())

        if now_bst.hour != _heartbeat_hour:
            _heartbeat_hour = now_bst.hour
            logger.info("Heartbeat %s BST | signals today: %d",
                        now_bst.strftime("%H:%M"), daily_counter.get_count())

        # ── 1. Morning briefing: 05:45 BST ───────────────────────────────
        if is_briefing_window() and today_bst != _briefing_posted_day:
            if post_morning_briefing():
                _briefing_posted_day = today_bst
                logger.info("Briefing posted — scans unlocked.")
            else:
                logger.warning("Briefing failed — will retry next poll.")

        # ── 2. Slot 1 — Tokyo (00:00–05:44 BST, 1H) ─────────────────────
        if _slot_window_active(1) and near_1h_close():
            run_slot(1)

        # ── 3. Slot 2 — Pre-London (05:45–07:59 BST, 1H) ────────────────
        if _slot_window_active(2) and near_1h_close():
            run_slot(2)

        # ── 4. Slot 3 — London ORB (08:00–10:59 BST, 15M) ───────────────
        if _slot_window_active(3) and near_15m_close():
            run_slot(3)

        # ── 6. Slot 6 — Guaranteed daily: 13:00 BST (primary) ────────────
        if near_13_bst() and today_bst != _daily_g_13_tried:
            _daily_g_13_tried = today_bst
            run_daily_guaranteed(ignore_news=False)

        # ── 7. TT NY session: 13:30 BST (Tuesdays only) ──────────────────
        if near_ny_open():
            run_tt_session("NY")

        # ── 8. Slot 5 — NY (13:00–15:59 BST, 15M) ───────────────────────
        if _slot_window_active(5) and near_15m_close():
            run_slot(5)

        # ── 9. Slot 6 fallback: 14:30 BST (if 13:00 news-blocked) ────────
        if near_1430_bst() and today_bst != _daily_g_fired_day:
            run_daily_guaranteed(ignore_news=True)

        # ── 11. Intraday 30M scan (London + NY) ──────────────────────────
        if near_30m_close() and is_intraday_session_active():
            run_intraday_signals()

        # ── 12. Hourly 1H wick sweep ──────────────────────────────────────
        if near_1h_close():
            run_signals()
            check_open_trades()

        # ── 13. EOD alert: 21:00 BST ──────────────────────────────────────
        if near_eod_alert() and today_bst != _eod_posted_day:
            post_eod_alert()
            _eod_posted_day = today_bst

        # ── 14. Friday weekly review: 20:00 BST ───────────────────────────
        if near_friday_review():
            week = _current_week()
            if week != _review_posted_week:
                post_weekly_review()
                _review_posted_week = week

        time.sleep(300)


if __name__ == "__main__":
    main()
