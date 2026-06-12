"""
SLOT 3 — London Opening Range Breakout (ORB)
Time:  08:00–12:30 BST | Gold only | Max 1 signal per day

Range  = high/low of 08:00–09:00 BST 1H candle.
         If < 15 pips, extend to 09:00–10:00 BST candle.
Break  = first 5M candle close BEYOND the range.
Entry  = wick rejection candle after the breakout:
           • wick dips back toward (or into) the range boundary
           • candle CLOSES on the breakout side of the range
           • wick ≥ 3 pips AND ≥ 30% of the bar range (volume proxy)
           • volume ≥ average of the last 10 post-range bars (if available)
SL     = range boundary ± 5 pips (skip if risk > intraday_sl_pips).
TPs    = 1:1 / 1:2 / 1:3 + runners.
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
from config import M5_BARS, H1_BARS, DAY_BARS, WEEK_BARS

logger = logging.getLogger(__name__)

_MIN_RANGE_PIPS    = 15
_SL_BUFFER_PIPS    = 5
_SLOT_END_MINS     = 12 * 60 + 30   # 12:30 BST
_MIN_WICK_PIPS     = 3              # minimum wick size to count as rejection
_MIN_WICK_RATIO    = 0.30           # wick must be ≥ 30% of total candle range
_VOL_LOOKBACK      = 10             # bars used to compute average volume


def _wick_rejection(bar, direction: str, range_high: float, range_low: float,
                    breakout_close: float, pip: float) -> bool:
    """
    True when the bar shows a wick rejection of the range after a breakout.

    BUY:  wick dips back toward/into range (low < breakout_close),
          close holds above range_high.
    SELL: wick spikes back toward/into range (high > breakout_close),
          close holds below range_low.
    Wick must be ≥ _MIN_WICK_PIPS AND ≥ _MIN_WICK_RATIO of total bar range.
    """
    lo, hi, cl = float(bar["low"]), float(bar["high"]), float(bar["close"])
    rng = hi - lo
    if rng < pip:
        return False

    if direction == "BUY":
        wick = cl - lo                          # lower wick (body bottom to low)
        if lo >= breakout_close:                # no dip back
            return False
        if cl <= range_high:                    # close fell back inside range
            return False
    else:
        wick = hi - cl                          # upper wick (body top to high)
        if hi <= breakout_close:                # no spike back
            return False
        if cl >= range_low:                     # close rose back inside range
            return False

    return wick >= _MIN_WICK_PIPS * pip and wick / rng >= _MIN_WICK_RATIO


def _vol_confirms(bar, post_bars: list) -> bool:
    """True if bar's volume ≥ average of last _VOL_LOOKBACK post-range bars."""
    try:
        bar_vol = float(bar.get("volume", 0))
        if bar_vol == 0:
            return True  # no volume data → fail-open
        vols = [float(b.get("volume", 0)) for b in post_bars[-_VOL_LOOKBACK:]
                if float(b.get("volume", 0)) > 0]
        if not vols:
            return True
        return bar_vol >= sum(vols) / len(vols)
    except Exception:
        return True


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

    if not (8 * 60 <= mins < _SLOT_END_MINS):
        return None

    try:
        m5     = _fetch(symbol_config, "5min",  M5_BARS)
        h1     = _fetch(symbol_config, "1h",    H1_BARS)
        weekly = _fetch(symbol_config, "1week", WEEK_BARS)
        daily  = _fetch(symbol_config, "1day",  DAY_BARS)
    except Exception as e:
        logger.error("[S3][%s] Fetch failed: %s", sym, e)
        return None

    # ── 1. Opening range ────────────────────────────────────────────────────────
    # 08:00–09:00 BST 1H candle closes at 09:00 BST → filter_bst_bars [9*60, 10*60).
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

    # ── 2. Breakout detection on 5M bars ───────────────────────────────────────
    post_bars = filter_bst_bars(m5, tz_offset, bst_date, breakout_start, _SLOT_END_MINS)
    if not post_bars:
        logger.info("[S3][%s] No post-range 5M bars yet.", sym)
        return None

    # Find the FIRST 5M close that breaks outside the range
    direction    = None
    breakout_bar = None
    breakout_idx = None
    for i, bar in enumerate(post_bars):
        c = float(bar["close"])
        if c > range_high and direction != "BUY":
            direction    = "BUY"
            breakout_bar = bar
            breakout_idx = i
        elif c < range_low and direction != "SELL":
            direction    = "SELL"
            breakout_bar = bar
            breakout_idx = i

    if not direction:
        logger.info("[S3][%s] No 5M breakout close. Last=%.2f Range=[%.2f, %.2f]",
                    sym, float(post_bars[-1]["close"]), range_low, range_high)
        return None

    # ── 3. Wick rejection + volume confirmation ────────────────────────────────
    # After the breakout bar, find the first 5M bar that:
    #   • wick dips back toward/into the range (the "pull back")
    #   • close holds on the breakout side (the "rejection")
    #   • wick ≥ 3 pips AND ≥ 30% of bar range (volume proxy)
    #   • volume ≥ average of post-range bars (if volume data available)
    breakout_close  = float(breakout_bar["close"])
    rejection_bar   = None

    for bar in post_bars[breakout_idx + 1:]:
        if not _wick_rejection(bar, direction, range_high, range_low,
                               breakout_close, pip):
            continue
        if not _vol_confirms(bar, post_bars):
            logger.info("[S3][%s] Wick rejection found but volume below avg — skip bar.",
                        sym)
            continue
        rejection_bar = bar
        break

    if rejection_bar is None:
        logger.info("[S3][%s] %s breakout %.2f — waiting for wick rejection pullback.",
                    sym, direction, breakout_close)
        return None

    entry = float(rejection_bar["close"])
    logger.info("[S3][%s] %s wick rejection confirmed. Entry=%.2f (breakout=%.2f)",
                sym, direction, entry, breakout_close)

    # ── 4. SL + risk check ──────────────────────────────────────────────────────
    sl = (range_low  - _SL_BUFFER_PIPS * pip) if direction == "BUY" \
          else (range_high + _SL_BUFFER_PIPS * pip)
    risk_pips = abs(entry - sl) / pip

    if risk_pips > max_sl:
        logger.info("[S3][%s] %s risk %.1f pips > %d max — skip.",
                    sym, direction, risk_pips, max_sl)
        return None

    # ── 5. Filters ──────────────────────────────────────────────────────────────
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

    # ── 6. Build signal ─────────────────────────────────────────────────────────
    runner    = pdh if direction == "BUY" else pdl
    range_str = f"{range_low:.{dec}f}–{range_high:.{dec}f}"
    reason    = f"London ORB — range {range_str}"

    logger.info("[S3][%s] %s entry=%.2f (breakout=%.2f) SL=%.2f risk=%.1f pips",
                sym, direction, entry, breakout_close, sl, risk_pips)

    sig = build_signal(direction, entry, sl, runner, reason, 3)
    if sig:
        sig["range_high"]         = range_high
        sig["range_low"]          = range_low
        sig["signal_type"]        = "london_orb"
        sig["invalidation_price"] = range_high if direction == "BUY" else range_low
        sig["invalidation_side"]  = "below"    if direction == "BUY" else "above"
    return sig
