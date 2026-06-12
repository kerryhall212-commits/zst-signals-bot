"""
SLOT 3 — London Open Sweep
Time:  08:00–10:00 BST | Gold + US30 | Max 1 signal per day

Asian High/Low or PDH/PDL swept on 15M candle.
Displacement candle confirms direction (body >60%, ≥10 pips).
OB identified (last opposing candle before displacement).
Entry when price retests the OB zone.

SL:   12 pips beyond swept level
TP3:  100 pips
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
    build_signal, is_displacement, price_in_ob, find_ob,
)
from config import M15_BARS, H1_BARS, DAY_BARS, WEEK_BARS

logger = logging.getLogger(__name__)

_SL_PIPS  = 12
_MIN_WICK = 3   # level breach must be >= 3 pips


def _sweep_and_displace(bars: list, level: float, direction: str, pip: float):
    """
    Scan bars for: sweep_bar wicks through level + closes back inside,
    then displacement_bar is a strong body candle in 'direction',
    then bars[-1] is at/near OB zone.

    Returns (ob, entry) or None.
    direction = "BUY"  (level is a LOW — swept downward then reversed up)
                "SELL" (level is a HIGH — swept upward then reversed down)
    """
    for i in range(len(bars) - 2):
        sw   = bars[i]
        sh   = float(sw["high"]); sl_v = float(sw["low"])
        so   = float(sw["open"]); sc   = float(sw["close"])

        # Sweep condition
        if direction == "BUY":
            swept = sl_v < level - _MIN_WICK * pip and sc > level
        else:
            swept = sh > level + _MIN_WICK * pip and sc < level

        if not swept:
            continue

        # Look for displacement in the next 1–3 bars
        for j in range(i + 1, min(i + 4, len(bars))):
            if not is_displacement(bars[j], direction, pip):
                continue

            # OB = last opposing candle before displacement
            ob = find_ob(bars, j, direction)

            # The most recent bar must be retesting the OB
            retest_close = float(bars[-1]["close"])
            if not price_in_ob(retest_close, ob, pip):
                continue

            return ob, retest_close

    return None


def generate_slot3_signal(symbol_config: dict) -> dict | None:
    from textbook_tuesday import is_tuesday_bst
    if is_tuesday_bst():
        logger.info("[S3] Tuesday — Textbook Tuesday replaces Slot 3.")
        return None

    sym       = symbol_config["symbol"]
    pip       = symbol_config.get("pip_size", 1.0)
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
    pdh = lv.get("prev_day_high",  {}).get("value")
    pdl = lv.get("prev_day_low",   {}).get("value")
    ash = lv.get("asian_high",     {}).get("value")
    asl = lv.get("asian_low",      {}).get("value")

    # DXY confirmation
    from slot_helpers import fetch_dxy, dxy_confirms

    # Check each level in priority order: Asian H/L first (more reactive), then PDH/PDL
    for direction, levels in [
        ("SELL", [(n, v) for n, v in [("Asian High", ash), ("PDH", pdh)] if v]),
        ("BUY",  [(n, v) for n, v in [("Asian Low", asl),  ("PDL", pdl)] if v]),
    ]:
        for level_name, level_val in levels:
            result = _sweep_and_displace(session_bars, level_val, direction, pip)
            if result is None:
                continue

            ob, entry = result

            # DXY check
            dxy = fetch_dxy()
            if not dxy_confirms(direction, dxy):
                logger.info("[S3][%s] DXY rejects %s.", sym, direction)
                continue

            sign = 1 if direction == "BUY" else -1
            sl   = (level_val - _SL_PIPS * pip) if direction == "BUY" \
                   else (level_val + _SL_PIPS * pip)
            risk = abs(entry - sl)
            # TP3 candidate at 1:4 R:R; build_signal caps at 1:6 / max_tp_pips
            tp3_cand = entry + sign * risk * 4

            reason = f"London sweep {level_name} — MSS + OB retest"
            logger.info("[S3][%s] %s at %s. Entry=%.2f SL=%.2f", sym, direction, level_name, entry, sl)

            sig = build_signal(direction, entry, sl, tp3_cand, reason, 3,
                               max_tp_pips=symbol_config.get("max_tp_pips"))
            if sig:
                return sig

    logger.info("[S3][%s] No London open sweep pattern.", sym)
    return None
