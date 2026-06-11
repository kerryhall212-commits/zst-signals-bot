"""
ZST Guaranteed Daily Signal — fires at 13:00 BST if no other signal sent today.

Bias determined by majority vote across three factors:
  1. Price vs PDH / PDL / PWH / PWL (level position)
  2. Yesterday's daily candle direction
  3. DXY 30M last completed bar direction

Tiebreak: price above PDH-PDL midpoint → SELL (premium), below → BUY (discount).
Falls back to 14:30 BST if 13:00 is blocked by news.
"""

import logging
from zoneinfo import ZoneInfo
from datetime import datetime

from signal_engine import _fetch
from key_levels import get_all_levels
from config import H1_BARS, DAY_BARS, WEEK_BARS

logger = logging.getLogger(__name__)


def _fetch_dxy():
    try:
        from yfinance_fetcher import fetch_ohlcv_yf
        return fetch_ohlcv_yf("DX-Y.NYB", "30m", 10)
    except Exception as e:
        logger.warning("[DS] DXY fetch failed: %s", e)
        return None


def _build_signal(direction: str, entry: float, pip: float,
                  sl_pips: int, tp1_pips: int, tp2_pips: int,
                  tp3_pips: int, reason: str, inv_label: str) -> dict | None:
    """Fixed-pip signal with global 1:3 min / 1:6 max R:R rule."""
    sign = 1 if direction == "BUY" else -1
    risk = sl_pips * pip
    sl   = entry - sign * risk
    tp1  = entry + sign * tp1_pips * pip
    tp2  = entry + sign * tp2_pips * pip

    rr_raw = tp3_pips / sl_pips
    if rr_raw < 3:
        logger.info("[DS] R:R %.2f below 1:3 — rejected.", rr_raw)
        return None
    if rr_raw > 6:
        tp3      = entry + sign * 6 * risk
        rr_label = "1:6"
    else:
        tp3      = entry + sign * tp3_pips * pip
        rr_label = f"1:{rr_raw:.0f}"

    return {
        "direction":          direction,
        "entry":              entry,
        "sl":                 sl,
        "tp1":                tp1,
        "tp2":                tp2,
        "tp3":                tp3,
        "rr":                 rr_label,
        "reason":             reason,
        "inv_label":          inv_label,
        "invalidation_price": sl,
        "invalidation_side":  "above" if direction == "SELL" else "below",
        "signal_type":        "daily_guaranteed",
    }


def generate_daily_signal(symbol_config: dict) -> dict | None:
    sym       = symbol_config["symbol"]
    pip       = symbol_config.get("pip_size", 1.0)
    near_pips = symbol_config.get("daily_near_pips", 50)
    sl_pips   = symbol_config.get("intraday_sl_pips",  15)
    tp1_pips  = symbol_config.get("intraday_tp1_pips", 45)
    tp2_pips  = symbol_config.get("intraday_tp2_pips", 60)
    tp3_pips  = symbol_config.get("intraday_tp3_pips", 100)

    try:
        weekly = _fetch(symbol_config, "1week", WEEK_BARS)
        daily  = _fetch(symbol_config, "1day",  DAY_BARS)
        h1     = _fetch(symbol_config, "1h",    H1_BARS)
    except Exception as e:
        logger.error("[DS][%s] Fetch failed: %s", sym, e)
        return None

    if len(h1) < 2:
        logger.info("[DS][%s] Not enough H1 bars.", sym)
        return None

    lv  = get_all_levels(weekly, daily, h1)
    pdh = lv.get("prev_day_high",  {}).get("value")
    pdl = lv.get("prev_day_low",   {}).get("value")
    pwh = lv.get("prev_week_high", {}).get("value")
    pwl = lv.get("prev_week_low",  {}).get("value")

    price = float(h1.iloc[-1]["close"])

    # ── Bias scoring (3 independent factors) ─────────────────────────────────
    bear, bull = 0, 0
    bear_reasons, bull_reasons = [], []

    # 1. Level position
    if pdh and price > pdh:
        bear += 1; bear_reasons.append("price above PDH")
    elif pwh and abs(price - pwh) <= near_pips * pip:
        bear += 1; bear_reasons.append("price near PWH")

    if pdl and price < pdl:
        bull += 1; bull_reasons.append("price below PDL")
    elif pwl and abs(price - pwl) <= near_pips * pip:
        bull += 1; bull_reasons.append("price near PWL")

    # 2. Yesterday's daily candle
    if len(daily) >= 2:
        last_d = daily.iloc[-2]
        if float(last_d["close"]) < float(last_d["open"]):
            bear += 1; bear_reasons.append("daily candle bearish")
        elif float(last_d["close"]) > float(last_d["open"]):
            bull += 1; bull_reasons.append("daily candle bullish")

    # 3. DXY direction
    dxy = _fetch_dxy()
    if dxy is not None and len(dxy) >= 2:
        dxy_last = dxy.iloc[-2]
        if float(dxy_last["close"]) > float(dxy_last["open"]):
            bear += 1; bear_reasons.append("DXY rising")
        else:
            bull += 1; bull_reasons.append("DXY falling")

    # ── Direction decision ────────────────────────────────────────────────────
    if bear > bull:
        direction = "SELL"
        reasons   = bear_reasons
    elif bull > bear:
        direction = "BUY"
        reasons   = bull_reasons
    else:
        # Tiebreak: price vs PDH-PDL midpoint
        if pdh and pdl:
            direction = "SELL" if price > (pdh + pdl) / 2 else "BUY"
            reasons   = ["price in premium zone" if direction == "SELL"
                         else "price in discount zone"]
        else:
            logger.info("[DS][%s] Bias tied, no levels for tiebreak — skip.", sym)
            return None

    reason_str = " + ".join(reasons[:2])
    logger.info("[DS][%s] %s (bear=%d bull=%d): %s", sym, direction, bear, bull, reason_str)

    # Invalidation label — reference nearest key level
    if direction == "SELL":
        if pdh and price > pdh:
            inv_label = f"1H close above PDH"
        elif pwh:
            inv_label = f"1H close above PWH"
        else:
            inv_label = "1H close above SL"
    else:
        if pdl and price < pdl:
            inv_label = f"1H close below PDL"
        elif pwl:
            inv_label = f"1H close below PWL"
        else:
            inv_label = "1H close below SL"

    return _build_signal(direction, price, pip,
                         sl_pips, tp1_pips, tp2_pips, tp3_pips,
                         reason_str, inv_label)
