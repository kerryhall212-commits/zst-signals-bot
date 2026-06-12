"""
SLOT 5 — NY Open Sweep (Gold) + US30 NY Engine

Gold:  London H/L or PDH/PDL swept at NY open → OB CE entry.
US30:  3 setups via slot_us30_ny (PDH/PDL sweep, Asian range sweep, B&R).
       US30 only fires 13:30–21:00 BST (NY session).
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
from slot_us30_ny import generate_us30_ny_signal

logger = logging.getLogger(__name__)


def _session_high_low(bars: list) -> tuple[float, float]:
    highs = [float(b["high"]) for b in bars]
    lows  = [float(b["low"])  for b in bars]
    return max(highs), min(lows)


def generate_slot5_signal(symbol_config: dict) -> dict | None:
    if symbol_config.get("display") == "US30":
        return generate_us30_ny_signal(symbol_config)

    sym       = symbol_config["symbol"]
    pip       = symbol_config.get("pip_size", 1.0)
    sl_pips   = symbol_config.get("intraday_sl_pips", 15)
    tz_offset = effective_tz_offset(symbol_config)

    now_bst  = datetime.now(ZoneInfo("Europe/London"))
    bst_date = now_bst.date()
    mins     = now_bst.hour * 60 + now_bst.minute

    if not (13 * 60 <= mins < 16 * 60):
        return None

    try:
        m15    = _fetch(symbol_config, "15min", M15_BARS)
        weekly = _fetch(symbol_config, "1week", WEEK_BARS)
        daily  = _fetch(symbol_config, "1day",  DAY_BARS)
        h1     = _fetch(symbol_config, "1h",    H1_BARS)
    except Exception as e:
        logger.error("[S5][%s] Fetch failed: %s", sym, e)
        return None

    # London session H/L: 08:00–13:30 BST
    london_bars = filter_bst_bars(m15, tz_offset, bst_date, 8 * 60, 13 * 60 + 30)
    if not london_bars:
        logger.info("[S5][%s] No London session bars available.", sym)
        return None
    london_high, london_low = _session_high_low(london_bars)

    lv  = get_all_levels(weekly, daily, h1)
    pdh = lv.get("prev_day_high", {}).get("value")
    pdl = lv.get("prev_day_low",  {}).get("value")

    # NY session bars: 13:30–15:00 BST
    ny_bars = filter_bst_bars(m15, tz_offset, bst_date, 13 * 60 + 30, 15 * 60)
    if len(ny_bars) < 2:
        logger.info("[S5][%s] Not enough NY open bars (%d).", sym, len(ny_bars))
        return None

    dxy = fetch_dxy()

    # SELL runner = PDL; BUY runner = PDH
    for direction, levels, runner in [
        ("SELL", [(n, v) for n, v in [("London High", london_high), ("PDH", pdh)] if v], pdl),
        ("BUY",  [(n, v) for n, v in [("London Low",  london_low),  ("PDL", pdl)] if v], pdh),
    ]:
        for level_name, level_val in levels:
            result = sweep_ob_entry(ny_bars, level_val, direction, pip)

            if result == OB_INVALIDATED:
                logger.info("[S5][%s] OB invalidated at %s.", sym, level_name)
                return {"signal_type": "ob_invalidated", "slot": 5}

            if not isinstance(result, dict):
                continue

            if not dxy_confirms(direction, dxy):
                logger.info("[S5][%s] DXY rejects %s.", sym, direction)
                continue

            entry = result["entry"]
            sign  = 1 if direction == "BUY" else -1
            sl    = entry + sign * sl_pips * pip

            reason = f"NY sweep {level_name} — OB CE entry"
            logger.info("[S5][%s] %s at %s. Entry=%.2f SL=%.2f", sym, direction, level_name, entry, sl)

            sig = build_signal(direction, entry, sl, runner, reason, 5)
            if sig:
                return sig

    logger.info("[S5][%s] No NY open sweep pattern.", sym)
    return None
