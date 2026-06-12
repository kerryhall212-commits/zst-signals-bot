"""
SLOT 4 — London Continuation
Time:  10:00–11:30 BST | Gold + US30 | Max 1 signal per day

London trend established in 08:00–10:00 BST opening range.
Price pulls back to 50% of opening range (OR_mid).
30M OB identified in pullback — entry on OB retest.
Previous high/low must remain intact.

SL:   12 pips | TP3: 70 pips
Label: ZST INTRADAY SIGNAL
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from signal_engine import _fetch
from key_levels import get_all_levels
from slot_helpers import (
    effective_tz_offset, filter_bst_bars,
    build_signal, find_ob, price_in_ob, dxy_confirms, fetch_dxy,
)
from config import M30_BARS, H1_BARS, DAY_BARS, WEEK_BARS

logger = logging.getLogger(__name__)

_SL_PIPS           = 12
_OR_TOLERANCE_PIPS = 10  # price within this many pips of OR_mid to qualify


def generate_slot4_signal(symbol_config: dict) -> dict | None:
    sym       = symbol_config["symbol"]
    pip       = symbol_config.get("pip_size", 1.0)
    tz_offset = effective_tz_offset(symbol_config)

    now_bst  = datetime.now(ZoneInfo("Europe/London"))
    bst_date = now_bst.date()
    mins     = now_bst.hour * 60 + now_bst.minute

    if not (10 * 60 <= mins < 11 * 60 + 30):
        return None

    try:
        m30    = _fetch(symbol_config, "30min", M30_BARS)
        weekly = _fetch(symbol_config, "1week", WEEK_BARS)
        daily  = _fetch(symbol_config, "1day",  DAY_BARS)
        h1     = _fetch(symbol_config, "1h",    H1_BARS)
    except Exception as e:
        logger.error("[S4][%s] Fetch failed: %s", sym, e)
        return None

    # ── London Opening Range: 08:00–10:00 BST ────────────────────────────
    or_bars = filter_bst_bars(m30, tz_offset, bst_date, 8 * 60, 10 * 60)
    if len(or_bars) < 2:
        logger.info("[S4][%s] Not enough OR bars (%d).", sym, len(or_bars))
        return None

    or_high = max(float(b["high"]) for b in or_bars)
    or_low  = min(float(b["low"])  for b in or_bars)
    or_mid  = (or_high + or_low) / 2

    # London direction: last OR bar close vs OR boundaries
    last_or_close = float(or_bars[-1]["close"])
    if last_or_close > or_high - 5 * pip:
        direction = "BUY"    # broke out upward
    elif last_or_close < or_low + 5 * pip:
        direction = "SELL"   # broke out downward
    else:
        logger.info("[S4][%s] No London trend breakout — last close inside OR.", sym)
        return None

    # ── Pullback bars: 10:00–11:30 BST ───────────────────────────────────
    pb_bars = filter_bst_bars(m30, tz_offset, bst_date, 10 * 60, 11 * 60 + 30)
    if len(pb_bars) < 1:
        logger.info("[S4][%s] No 10–11:30 BST bars yet.", sym)
        return None

    current_close = float(pb_bars[-1]["close"])

    # Price must be near OR_mid (pulled back to 50% zone)
    if abs(current_close - or_mid) > _OR_TOLERANCE_PIPS * pip:
        logger.info("[S4][%s] Price %.2f not near OR_mid %.2f.", sym, current_close, or_mid)
        return None

    # ── Previous high/low intact check ───────────────────────────────────
    current_low  = float(pb_bars[-1]["low"])
    current_high = float(pb_bars[-1]["high"])
    if direction == "BUY" and current_low < or_low:
        logger.info("[S4][%s] BUY invalidated — OR_low broken.", sym)
        return None
    if direction == "SELL" and current_high > or_high:
        logger.info("[S4][%s] SELL invalidated — OR_high broken.", sym)
        return None

    # ── OB in pullback ────────────────────────────────────────────────────
    all_london = or_bars + pb_bars
    ob = find_ob(all_london, len(all_london), direction)
    if not price_in_ob(current_close, ob, pip):
        logger.info("[S4][%s] Price not at OB zone.", sym)
        return None

    # ── DXY ───────────────────────────────────────────────────────────────
    dxy = fetch_dxy()
    if not dxy_confirms(direction, dxy):
        logger.info("[S4][%s] DXY rejects %s.", sym, direction)
        return None

    sign = 1 if direction == "BUY" else -1
    sl   = or_low - _SL_PIPS * pip if direction == "BUY" else or_high + _SL_PIPS * pip
    risk = abs(current_close - sl)
    # TP3 candidate at 1:4 R:R; build_signal caps at 1:6 / max_tp_pips
    tp3c = current_close + sign * risk * 4

    reason = "London continuation — 50% OR pullback OB retest"
    logger.info("[S4][%s] %s OR_mid=%.2f Entry=%.2f SL=%.2f", sym, direction, or_mid, current_close, sl)
    return build_signal(direction, current_close, sl, tp3c, reason, 4,
                        max_tp_pips=symbol_config.get("max_tp_pips"))
