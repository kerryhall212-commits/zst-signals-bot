"""
SLOT 5 — NY Open Sweep
Time:  13:30–15:00 BST | Gold + US30 | Max 1 signal per day

London High/Low (08:00–13:30 BST) or PDH/PDL swept at NY open.
Displacement + MSS confirmed on 15M.
Entry on first pullback OB.

SL:  12 pips Gold / 100 pts US30
TP3: 100 pips Gold / 300 pts US30
Label: ZST SWING SIGNAL
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from signal_engine import _fetch
from key_levels import get_all_levels
from slot_helpers import (
    effective_tz_offset, filter_bst_bars,
    build_signal, is_displacement, price_in_ob, find_ob,
    fetch_dxy, dxy_confirms,
)
from config import M15_BARS, H1_BARS, DAY_BARS, WEEK_BARS

logger = logging.getLogger(__name__)

_MIN_WICK = 3   # level breach must be >= 3 pips


def _session_high_low(bars: list) -> tuple[float, float]:
    highs = [float(b["high"]) for b in bars]
    lows  = [float(b["low"])  for b in bars]
    return max(highs), min(lows)


def _sweep_and_displace(bars, level, direction, pip):
    """Same sweep+displacement+OB logic as Slot 3."""
    for i in range(len(bars) - 2):
        sw = bars[i]
        sh = float(sw["high"]); sl_v = float(sw["low"])
        sc = float(sw["close"])

        if direction == "BUY":
            swept = sl_v < level - _MIN_WICK * pip and sc > level
        else:
            swept = sh > level + _MIN_WICK * pip and sc < level

        if not swept:
            continue

        for j in range(i + 1, min(i + 4, len(bars))):
            if not is_displacement(bars[j], direction, pip):
                continue

            ob = find_ob(bars, j, direction)
            retest_close = float(bars[-1]["close"])
            if not price_in_ob(retest_close, ob, pip):
                continue

            return ob, retest_close

    return None


def generate_slot5_signal(symbol_config: dict) -> dict | None:
    sym       = symbol_config["symbol"]
    pip       = symbol_config.get("pip_size", 1.0)
    tz_offset = effective_tz_offset(symbol_config)
    sl_pips   = symbol_config.get("sl_pips", 12)     # Gold=12, US30=100
    tp3_pips  = symbol_config.get("intraday_tp3_pips", 100)  # Gold=100, US30=300

    now_bst  = datetime.now(ZoneInfo("Europe/London"))
    bst_date = now_bst.date()
    mins     = now_bst.hour * 60 + now_bst.minute

    if not (13 * 60 + 30 <= mins < 15 * 60):
        return None

    try:
        m15    = _fetch(symbol_config, "15min", M15_BARS)
        weekly = _fetch(symbol_config, "1week", WEEK_BARS)
        daily  = _fetch(symbol_config, "1day",  DAY_BARS)
        h1     = _fetch(symbol_config, "1h",    H1_BARS)
    except Exception as e:
        logger.error("[S5][%s] Fetch failed: %s", sym, e)
        return None

    # ── London session H/L: 08:00–13:30 BST ──────────────────────────────
    london_bars = filter_bst_bars(m15, tz_offset, bst_date, 8 * 60, 13 * 60 + 30)
    if not london_bars:
        logger.info("[S5][%s] No London session bars available.", sym)
        return None
    london_high, london_low = _session_high_low(london_bars)

    # ── Key levels for PDH/PDL fallback ───────────────────────────────────
    lv  = get_all_levels(weekly, daily, h1)
    pdh = lv.get("prev_day_high", {}).get("value")
    pdl = lv.get("prev_day_low",  {}).get("value")

    # ── NY session bars: 13:30–15:00 BST ─────────────────────────────────
    ny_bars = filter_bst_bars(m15, tz_offset, bst_date, 13 * 60 + 30, 15 * 60)
    if len(ny_bars) < 3:
        logger.info("[S5][%s] Not enough NY open bars (%d).", sym, len(ny_bars))
        return None

    dxy = fetch_dxy()

    # Priority: London H/L first, then PDH/PDL
    for direction, levels in [
        ("SELL", [(n, v) for n, v in [("London High", london_high), ("PDH", pdh)] if v]),
        ("BUY",  [(n, v) for n, v in [("London Low",  london_low),  ("PDL", pdl)] if v]),
    ]:
        for level_name, level_val in levels:
            result = _sweep_and_displace(ny_bars, level_val, direction, pip)
            if result is None:
                continue

            if not dxy_confirms(direction, dxy):
                logger.info("[S5][%s] DXY rejects %s.", sym, direction)
                continue

            _, entry = result
            sign     = 1 if direction == "BUY" else -1
            sl       = (level_val - sl_pips * pip) if direction == "BUY" \
                       else (level_val + sl_pips * pip)
            tp1      = entry + sign * 36 * pip
            tp2      = entry + sign * 60 * pip
            tp3_cand = entry + sign * tp3_pips * pip

            reason = f"NY sweep {level_name} — displacement + OB retest"
            logger.info("[S5][%s] %s at %s. Entry=%.2f SL=%.2f", sym, direction, level_name, entry, sl)

            sig = build_signal(direction, entry, sl, tp1, tp2, tp3_cand, reason, 5)
            if sig:
                return sig

    logger.info("[S5][%s] No NY open sweep pattern.", sym)
    return None
