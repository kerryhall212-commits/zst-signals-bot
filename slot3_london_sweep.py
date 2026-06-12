"""
SLOT 3 — London Open Sweep
Time:  08:00–10:00 BST | Gold + US30 | Max 1 signal per day

Asian High/Low or PDH/PDL swept on 15M candle.
OB identified (last bullish candle before sweep for SELL, bearish for BUY).
Entry at CE of OB when price pulls back into OB zone (within 2 bars).
OB broken before entry → cancellation message.

SL:    fixed intraday_sl_pips from entry (15 Gold / 50 US30)
TPs:   risk × 3 / × 5 / capped at 1:6
Label: ZST SWING SIGNAL

TUESDAY SPECIAL: Slot 3 yields to Textbook Tuesday.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from signal_engine import _fetch
from key_levels import get_all_levels
from slot_helpers import (
    effective_tz_offset, filter_bst_bars,
    build_signal, sweep_ob_entry, OB_INVALIDATED,
    fetch_dxy, dxy_confirms,
)
from config import M15_BARS, H1_BARS, DAY_BARS, WEEK_BARS

logger = logging.getLogger(__name__)


def generate_slot3_signal(symbol_config: dict) -> dict | None:
    from textbook_tuesday import is_tuesday_bst
    if is_tuesday_bst():
        logger.info("[S3] Tuesday — Textbook Tuesday replaces Slot 3.")
        return None

    sym       = symbol_config["symbol"]
    pip       = symbol_config.get("pip_size", 1.0)
    sl_pips   = symbol_config.get("intraday_sl_pips", 15)
    tz_offset = effective_tz_offset(symbol_config)

    now_bst  = datetime.now(ZoneInfo("Europe/London"))
    bst_date = now_bst.date()
    mins     = now_bst.hour * 60 + now_bst.minute

    if not (8 * 60 <= mins < 10 * 60):
        return None

    try:
        m15    = _fetch(symbol_config, "15min", M15_BARS)
        weekly = _fetch(symbol_config, "1week", WEEK_BARS)
        daily  = _fetch(symbol_config, "1day",  DAY_BARS)
        h1     = _fetch(symbol_config, "1h",    H1_BARS)
    except Exception as e:
        logger.error("[S3][%s] Fetch failed: %s", sym, e)
        return None

    session_bars = filter_bst_bars(m15, tz_offset, bst_date, 8 * 60, 10 * 60)
    if len(session_bars) < 3:
        logger.info("[S3][%s] Not enough London open bars (%d).", sym, len(session_bars))
        return None

    lv  = get_all_levels(weekly, daily, h1)
    pdh = lv.get("prev_day_high", {}).get("value")
    pdl = lv.get("prev_day_low",  {}).get("value")
    ash = lv.get("asian_high",    {}).get("value")
    asl = lv.get("asian_low",     {}).get("value")
    dxy = fetch_dxy()

    # SELL runner = PDL (far low); BUY runner = PDH (far high)
    for direction, levels, runner in [
        ("SELL", [(n, v) for n, v in [("Asian High", ash), ("PDH", pdh)] if v], pdl),
        ("BUY",  [(n, v) for n, v in [("Asian Low",  asl), ("PDL", pdl)] if v], pdh),
    ]:
        for level_name, level_val in levels:
            result = sweep_ob_entry(session_bars, level_val, direction, pip)

            if result == OB_INVALIDATED:
                logger.info("[S3][%s] OB invalidated at %s.", sym, level_name)
                return {"signal_type": "ob_invalidated", "slot": 3}

            if not isinstance(result, dict):
                continue

            if not dxy_confirms(direction, dxy):
                logger.info("[S3][%s] DXY rejects %s.", sym, direction)
                continue

            entry = result["entry"]
            sign  = 1 if direction == "BUY" else -1
            sl    = entry + sign * sl_pips * pip

            reason = f"London sweep {level_name} — OB CE entry"
            logger.info("[S3][%s] %s at %s. Entry=%.2f SL=%.2f", sym, direction, level_name, entry, sl)

            sig = build_signal(direction, entry, sl, runner, reason, 3)
            if sig:
                return sig

    logger.info("[S3][%s] No London open sweep pattern.", sym)
    return None
