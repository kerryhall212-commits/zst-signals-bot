"""
SLOT 3 — London Opening Range Breakout (ORB)
Time:  08:00–11:00 BST | Gold + US30 | Max 1 signal per day

Range = high/low of 08:00–09:00 BST 1H candle.
If range < 15 pips, extend to cover 09:00–10:00 BST candle too.
Breakout = 15M candle close BEYOND the range (not just a wick).
SL = range boundary + 5 pips (skip if risk > intraday_sl_pips from entry).
TPs: 1:1 / 1:2 / 1:3 + runners if key level beyond 1:3.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from signal_engine import _fetch
from key_levels import get_all_levels
from slot_helpers import (
    effective_tz_offset, filter_bst_bars,
    build_signal, fetch_dxy, dxy_confirms,
)
from config import M15_BARS, H1_BARS, DAY_BARS, WEEK_BARS

logger = logging.getLogger(__name__)

_MIN_RANGE_PIPS  = 15
_SL_BUFFER_PIPS  = 5


def generate_slot3_signal(symbol_config: dict) -> dict | None:
    from textbook_tuesday import is_tuesday_bst
    if is_tuesday_bst():
        logger.info("[S3] Tuesday — Textbook Tuesday replaces Slot 3.")
        return None

    sym       = symbol_config["symbol"]
    pip       = symbol_config.get("pip_size", 1.0)
    max_sl    = symbol_config.get("intraday_sl_pips", 15)
    tz_offset = effective_tz_offset(symbol_config)
    dec       = symbol_config.get("decimals", 2)

    now_bst  = datetime.now(ZoneInfo("Europe/London"))
    bst_date = now_bst.date()
    mins     = now_bst.hour * 60 + now_bst.minute

    if not (8 * 60 <= mins < 11 * 60):
        return None

    try:
        m15    = _fetch(symbol_config, "15min", M15_BARS)
        h1     = _fetch(symbol_config, "1h",    H1_BARS)
        weekly = _fetch(symbol_config, "1week", WEEK_BARS)
        daily  = _fetch(symbol_config, "1day",  DAY_BARS)
    except Exception as e:
        logger.error("[S3][%s] Fetch failed: %s", sym, e)
        return None

    # ── 1. Opening range ────────────────────────────────────────────────────────
    # The 08:00–09:00 BST 1H candle closes at 09:00 BST (close_time = 9*60).
    # filter_bst_bars uses close time, so [9*60, 10*60) captures it.
    h1_08 = filter_bst_bars(h1, tz_offset, bst_date, 9 * 60, 10 * 60)

    if not h1_08:
        logger.info("[S3][%s] 08:00 range candle not closed yet.", sym)
        return None

    range_high = max(float(b["high"]) for b in h1_08)
    range_low  = min(float(b["low"])  for b in h1_08)
    range_pips = (range_high - range_low) / pip
    breakout_start = 9 * 60

    if range_pips < _MIN_RANGE_PIPS:
        h1_09 = filter_bst_bars(h1, tz_offset, bst_date, 10 * 60, 11 * 60)
        if not h1_09:
            logger.info("[S3][%s] Range %.1f pips < %d — waiting for 09:00 candle.",
                        sym, range_pips, _MIN_RANGE_PIPS)
            return None
        all_range  = h1_08 + h1_09
        range_high = max(float(b["high"]) for b in all_range)
        range_low  = min(float(b["low"])  for b in all_range)
        range_pips = (range_high - range_low) / pip
        breakout_start = 10 * 60
        if range_pips < _MIN_RANGE_PIPS:
            logger.info("[S3][%s] Extended range %.1f pips < %d — skip.",
                        sym, range_pips, _MIN_RANGE_PIPS)
            return None

    logger.info("[S3][%s] Range %.2f–%.2f (%.1f pips)", sym, range_low, range_high, range_pips)

    # ── 2. Breakout detection ───────────────────────────────────────────────────
    post_bars = filter_bst_bars(m15, tz_offset, bst_date, breakout_start, 11 * 60)
    if not post_bars:
        logger.info("[S3][%s] No post-range bars yet.", sym)
        return None

    direction    = None
    breakout_bar = None
    for bar in post_bars:
        c = float(bar["close"])
        if c > range_high:
            direction    = "BUY"
            breakout_bar = bar
        elif c < range_low:
            direction    = "SELL"
            breakout_bar = bar

    if not direction:
        logger.info("[S3][%s] No breakout close. Last=%.2f Range=[%.2f, %.2f]",
                    sym, float(post_bars[-1]["close"]), range_low, range_high)
        return None

    # ── 3. SL + risk check ──────────────────────────────────────────────────────
    entry = float(breakout_bar["close"])
    sign  = 1 if direction == "BUY" else -1

    sl        = range_low  - _SL_BUFFER_PIPS * pip if direction == "BUY" \
                else range_high + _SL_BUFFER_PIPS * pip
    risk_pips = abs(entry - sl) / pip

    if risk_pips > max_sl:
        logger.info("[S3][%s] %s risk %.1f pips > %d max — range too wide. Skip.",
                    sym, direction, risk_pips, max_sl)
        return None

    # ── 4. Filters ──────────────────────────────────────────────────────────────
    lv  = get_all_levels(weekly, daily, h1)
    pdh = lv.get("prev_day_high", {}).get("value")
    pdl = lv.get("prev_day_low",  {}).get("value")

    if len(daily) >= 2:
        last_d     = daily.iloc[-2]
        daily_bull = float(last_d["close"]) > float(last_d["open"])
        if direction == "BUY"  and not daily_bull:
            logger.info("[S3][%s] Daily bearish — skip BUY.", sym)
            return None
        if direction == "SELL" and daily_bull:
            logger.info("[S3][%s] Daily bullish — skip SELL.", sym)
            return None

    dxy = fetch_dxy()
    if not dxy_confirms(direction, dxy):
        logger.info("[S3][%s] DXY rejects %s.", sym, direction)
        return None

    # ── 5. Build signal ─────────────────────────────────────────────────────────
    runner     = pdh if direction == "BUY" else pdl
    range_str  = f"{range_low:.{dec}f}–{range_high:.{dec}f}"
    reason     = f"London ORB — range {range_str}"

    logger.info("[S3][%s] %s breakout. Entry=%.2f SL=%.2f Risk=%.1f pips",
                sym, direction, entry, sl, risk_pips)

    sig = build_signal(direction, entry, sl, runner, reason, 3)
    if sig:
        sig["range_high"]         = range_high
        sig["range_low"]          = range_low
        sig["signal_type"]        = "london_orb"
        sig["invalidation_price"] = range_high if direction == "BUY" else range_low
        sig["invalidation_side"]  = "below"    if direction == "BUY" else "above"
    return sig
