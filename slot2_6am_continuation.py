"""
SLOT 2 — 6AM Continuation
Time:  06:00–07:30 BST | Gold + US30 | Max 1 signal per day

Overnight move (midnight–06:00 BST) establishes direction.
The 6AM 1H candle pulls back into the last opposing-candle OB.
Entry at CE (50% of the OB candle body), near a key level (≤20 pips).
DXY must confirm direction.

SL:   12 pips beyond OB high/low
TP1:  36 pips | TP2: 60 pips | TP3: 100 pips
Label: ZST INTRADAY SIGNAL
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from signal_engine import _fetch
from key_levels import get_all_levels
from slot_helpers import (
    effective_tz_offset, filter_bst_bars,
    build_signal, fetch_dxy, dxy_confirms, find_ob,
)
from config import H1_BARS, DAY_BARS, WEEK_BARS

logger = logging.getLogger(__name__)

_SL_PIPS            = 12
_MIN_OVERNIGHT_PIPS = 20   # overnight move must be at least 20 pips
_CE_TOLERANCE_PIPS  = 15   # price must be within 15 pips of CE to fire
_NEAR_LEVEL_PIPS    = 20   # must be near a key level within 20 pips


def generate_slot2_signal(symbol_config: dict) -> dict | None:
    sym       = symbol_config["symbol"]
    pip       = symbol_config.get("pip_size", 1.0)
    tz_offset = effective_tz_offset(symbol_config)

    now_bst  = datetime.now(ZoneInfo("Europe/London"))
    bst_date = now_bst.date()
    mins     = now_bst.hour * 60 + now_bst.minute

    if not (6 * 60 <= mins < 7 * 60 + 30):
        return None

    try:
        h1     = _fetch(symbol_config, "1h",    H1_BARS)
        weekly = _fetch(symbol_config, "1week", WEEK_BARS)
        daily  = _fetch(symbol_config, "1day",  DAY_BARS)
    except Exception as e:
        logger.error("[S2][%s] Fetch failed: %s", sym, e)
        return None

    # ── Overnight bars: midnight to 06:00 BST ────────────────────────────
    overnight = filter_bst_bars(h1, tz_offset, bst_date, 0, 6 * 60)
    if len(overnight) < 2:
        logger.info("[S2][%s] Not enough overnight bars (%d).", sym, len(overnight))
        return None

    ov_open  = float(overnight[0]["open"])
    ov_close = float(overnight[-1]["close"])
    move     = ov_close - ov_open
    move_pips = abs(move) / pip

    if move_pips < _MIN_OVERNIGHT_PIPS:
        logger.info("[S2][%s] Overnight move %.1f pips < min %d.", sym, move_pips, _MIN_OVERNIGHT_PIPS)
        return None

    direction = "BUY" if move > 0 else "SELL"

    # ── OB: last opposing H1 candle in overnight sequence ────────────────
    ob = find_ob(overnight, len(overnight), direction)
    if ob is None:
        logger.info("[S2][%s] No OB found in overnight bars.", sym)
        return None

    ob_open  = float(ob["open"])
    ob_close = float(ob["close"])
    ob_high  = float(ob["high"])
    ob_low   = float(ob["low"])
    ce       = (ob_open + ob_close) / 2  # 50% of OB body

    # ── Current bar during 06:00–07:30 BST ───────────────────────────────
    pullback_bars = filter_bst_bars(h1, tz_offset, bst_date, 6 * 60, 7 * 60 + 30)
    if not pullback_bars:
        logger.info("[S2][%s] No 6AM bars yet.", sym)
        return None

    current_close = float(pullback_bars[-1]["close"])

    # CE proximity check
    if abs(current_close - ce) > _CE_TOLERANCE_PIPS * pip:
        logger.info("[S2][%s] Price %.2f not near CE %.2f (dist %.1f pips).",
                    sym, current_close, ce, abs(current_close - ce) / pip)
        return None

    # ── Key level proximity ───────────────────────────────────────────────
    lv  = get_all_levels(weekly, daily, h1)
    levels = [
        lv.get("prev_day_high",  {}).get("value"),
        lv.get("prev_day_low",   {}).get("value"),
        lv.get("prev_week_high", {}).get("value"),
        lv.get("prev_week_low",  {}).get("value"),
        lv.get("asian_high",     {}).get("value"),
        lv.get("asian_low",      {}).get("value"),
    ]
    near = any(
        v is not None and abs(current_close - v) <= _NEAR_LEVEL_PIPS * pip
        for v in levels
    )
    if not near:
        logger.info("[S2][%s] %s not near any key level within %d pips.",
                    sym, direction, _NEAR_LEVEL_PIPS)
        return None

    # ── Daily bias ────────────────────────────────────────────────────────
    if len(daily) >= 2:
        last_day = daily.iloc[-2]
        daily_bull = float(last_day["close"]) > float(last_day["open"])
        if direction == "BUY" and not daily_bull:
            logger.info("[S2][%s] Daily bearish — skip BUY.", sym)
            return None
        if direction == "SELL" and daily_bull:
            logger.info("[S2][%s] Daily bullish — skip SELL.", sym)
            return None

    # ── DXY ───────────────────────────────────────────────────────────────
    dxy = fetch_dxy()
    if not dxy_confirms(direction, dxy):
        logger.info("[S2][%s] DXY rejects %s.", sym, direction)
        return None

    # ── Build signal ──────────────────────────────────────────────────────
    entry = current_close
    sign  = 1 if direction == "BUY" else -1
    sl    = (ob_low  - _SL_PIPS * pip) if direction == "BUY" else (ob_high + _SL_PIPS * pip)
    risk  = abs(entry - sl)
    # Runner candidate at 1:4; build_signal adds TP4 if >=1:4 and TP5 if >=1:5
    runner = entry + sign * risk * 4

    logger.info("[S2][%s] %s OB CE=%.2f Entry=%.2f SL=%.2f", sym, direction, ce, entry, sl)
    return build_signal(direction, entry, sl, runner,
                        "6AM OB continuation — overnight move CE retest", 2)
