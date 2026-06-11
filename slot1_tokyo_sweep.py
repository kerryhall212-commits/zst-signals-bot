"""
SLOT 1 — Tokyo PDH/PDL Sweep
Time:  00:00–03:00 BST | Gold only | Max 1 signal per day

30M candle wicks through PDH or PDL and closes back inside,
then the following 30M candle confirms direction.

Entry: close of confirmation candle
SL:    12 pips beyond the swept level
TP1:   36 pips | TP2: 60 pips | TP3: opposite level (PDL/PDH)
Label: ZST SWING SIGNAL
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from signal_engine import _fetch
from key_levels import get_all_levels
from slot_helpers import (
    effective_tz_offset, filter_bst_bars,
    build_signal,
)
from config import M30_BARS, H1_BARS, DAY_BARS, WEEK_BARS

logger = logging.getLogger(__name__)

_SL_PIPS  = 12
_TP1_PIPS = 36
_TP2_PIPS = 60
_MIN_WICK_PIPS = 3  # wick must clear the level by at least 3 pips


def generate_slot1_signal(symbol_config: dict) -> dict | None:
    if symbol_config.get("display") != "GOLD":
        return None

    sym       = symbol_config["symbol"]
    pip       = symbol_config.get("pip_size", 1.0)
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

    # Scan consecutive pairs: (sweep_bar, confirm_bar)
    for i in range(len(session_bars) - 1):
        sweep   = session_bars[i]
        confirm = session_bars[i + 1]

        sh, sl_c, so, sc = (float(sweep[k])   for k in ("high", "low", "open", "close"))
        _ch, _cl, co, cc = (float(confirm[k]) for k in ("high", "low", "open", "close"))

        # ── BEARISH: wick above PDH, close back below ─────────────────────
        if sh > pdh + _MIN_WICK_PIPS * pip and sc < pdh and sc < so:
            if cc < co:  # confirm candle is bearish
                entry    = cc
                sl       = pdh + _SL_PIPS * pip
                sign     = -1
                tp1      = entry + sign * _TP1_PIPS * pip
                tp2      = entry + sign * _TP2_PIPS * pip
                tp3_cand = pdl     # opposite level

                sig = build_signal("SELL", entry, sl, tp1, tp2, tp3_cand,
                                   "Tokyo PDH sweep — 30M wick rejection", 1)
                if sig:
                    logger.info("[S1][%s] SELL — PDH %.2f swept. Entry=%.2f SL=%.2f",
                                sym, pdh, entry, sl)
                    return sig

        # ── BULLISH: wick below PDL, close back above ─────────────────────
        if sl_c < pdl - _MIN_WICK_PIPS * pip and sc > pdl and sc > so:
            if cc > co:  # confirm candle is bullish
                entry    = cc
                sl       = pdl - _SL_PIPS * pip
                sign     = 1
                tp1      = entry + sign * _TP1_PIPS * pip
                tp2      = entry + sign * _TP2_PIPS * pip
                tp3_cand = pdh     # opposite level

                sig = build_signal("BUY", entry, sl, tp1, tp2, tp3_cand,
                                   "Tokyo PDL sweep — 30M wick rejection", 1)
                if sig:
                    logger.info("[S1][%s] BUY — PDL %.2f swept. Entry=%.2f SL=%.2f",
                                sym, pdl, entry, sl)
                    return sig

    logger.info("[S1][%s] No Tokyo PDH/PDL sweep pattern.", sym)
    return None
