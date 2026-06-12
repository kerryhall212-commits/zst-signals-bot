"""
SLOT 1 — Tokyo PDH/PDL Sweep
Time:  00:00–03:00 BST | Gold only | Max 1 signal per day

30M candle wicks through PDH or PDL and closes back inside (the sweep).
Entry at CE of the last bullish OB before the sweep (for SELL) or last
bearish OB (for BUY). Pullback must reach CE within 2 x 30M bars.
OB broken before CE reached → cancellation message.

SL:    fixed 15 pips from entry (intraday_sl_pips)
TPs:   risk × 3 / × 5 / capped at 1:6
Label: ZST SWING SIGNAL
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from signal_engine import _fetch
from key_levels import get_all_levels
from slot_helpers import (
    effective_tz_offset, filter_bst_bars,
    build_signal, sweep_ob_entry, OB_INVALIDATED,
)
from config import M30_BARS, H1_BARS, DAY_BARS, WEEK_BARS

logger = logging.getLogger(__name__)

_MIN_WICK_PIPS = 3


def generate_slot1_signal(symbol_config: dict) -> dict | None:
    if symbol_config.get("display") != "GOLD":
        return None

    sym       = symbol_config["symbol"]
    pip       = symbol_config.get("pip_size", 1.0)
    sl_pips   = symbol_config.get("intraday_sl_pips", 15)   # fixed SL from entry
    tz_offset = effective_tz_offset(symbol_config)

    now_bst  = datetime.now(ZoneInfo("Europe/London"))
    bst_date = now_bst.date()
    mins     = now_bst.hour * 60 + now_bst.minute

    if not (0 <= mins < 3 * 60):
        return None

    try:
        m30    = _fetch(symbol_config, "30min", M30_BARS)
        weekly = _fetch(symbol_config, "1week", WEEK_BARS)
        daily  = _fetch(symbol_config, "1day",  DAY_BARS)
        h1     = _fetch(symbol_config, "1h",    H1_BARS)
    except Exception as e:
        logger.error("[S1][%s] Fetch failed: %s", sym, e)
        return None

    lv  = get_all_levels(weekly, daily, h1)
    pdh = lv.get("prev_day_high", {}).get("value")
    pdl = lv.get("prev_day_low",  {}).get("value")

    if pdh is None or pdl is None:
        logger.info("[S1][%s] PDH/PDL unavailable.", sym)
        return None

    session_bars = filter_bst_bars(m30, tz_offset, bst_date, 0, 3 * 60)
    if len(session_bars) < 2:
        logger.info("[S1][%s] Not enough Tokyo session bars (%d).", sym, len(session_bars))
        return None

    max_tp = symbol_config.get("max_tp_pips")

    for direction, level, level_name, runner in [
        ("SELL", pdh, "PDH", pdl),   # runner target = PDL (far low)
        ("BUY",  pdl, "PDL", pdh),   # runner target = PDH (far high)
    ]:
        result = sweep_ob_entry(session_bars, level, direction, pip, _MIN_WICK_PIPS)

        if result == OB_INVALIDATED:
            logger.info("[S1][%s] OB invalidated at %s.", sym, level_name)
            return {"signal_type": "ob_invalidated", "slot": 1}

        if not isinstance(result, dict):
            continue

        entry = result["entry"]
        sign  = 1 if direction == "BUY" else -1
        sl    = entry + sign * sl_pips * pip  # fixed SL from entry

        sig = build_signal(direction, entry, sl, runner,
                           f"Tokyo {level_name} sweep — OB CE entry", 1,
                           max_tp_pips=max_tp)
        if sig:
            logger.info("[S1][%s] %s — %s swept. Entry=%.2f SL=%.2f",
                        sym, direction, level_name, entry, sl)
            return sig

    logger.info("[S1][%s] No Tokyo PDH/PDL sweep pattern.", sym)
    return None
