"""
Shared utilities for slot-based signal engines (Slots 1–5).
"""

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from signal_engine import _td_to_utc_close

logger = logging.getLogger(__name__)


def effective_tz_offset(symbol_config: dict) -> int:
    if symbol_config.get("data_source") == "yfinance":
        return 0
    return symbol_config.get("td_tz_offset", 0)


def bar_bst(time_str: str, tz_offset: int) -> datetime:
    utc = _td_to_utc_close(time_str, tz_offset).replace(tzinfo=timezone.utc)
    return utc.astimezone(ZoneInfo("Europe/London"))


def filter_bst_bars(df, tz_offset: int, bst_date, start_mins: int, end_mins: int) -> list:
    """Return rows whose BST close time (in minutes) falls in [start_mins, end_mins)."""
    rows = []
    for _, row in df.iterrows():
        bst = bar_bst(row["time"], tz_offset)
        if bst.date() != bst_date:
            continue
        m = bst.hour * 60 + bst.minute
        if start_mins <= m < end_mins:
            rows.append(row)
    return rows


def build_signal(direction: str, entry: float, sl: float,
                 tp1: float, tp2: float, tp3_candidate: float,
                 reason: str, slot_num: int) -> dict | None:
    """R:R enforced: 1:3 minimum, 1:6 cap. Returns None if below 1:3."""
    risk = abs(entry - sl)
    if risk == 0:
        return None

    sign   = 1 if direction == "BUY" else -1
    rr_raw = abs(tp3_candidate - entry) / risk

    if rr_raw < 3:
        logger.info("[S%d] R:R %.2f below 1:3 — rejected.", slot_num, rr_raw)
        return None

    if rr_raw > 6:
        tp3      = entry + sign * risk * 6
        rr_label = "1:6"
    else:
        tp3      = tp3_candidate
        rr_label = f"1:{rr_raw:.0f}"

    logger.info("[S%d] %s accepted R:R %s", slot_num, direction, rr_label)

    return {
        "direction":          direction,
        "entry":              entry,
        "sl":                 sl,
        "tp1":                tp1,
        "tp2":                tp2,
        "tp3":                tp3,
        "rr":                 rr_label,
        "reason":             reason,
        "invalidation_price": sl,
        "invalidation_side":  "above" if direction == "SELL" else "below",
        "slot":               slot_num,
    }


def fetch_dxy():
    try:
        from yfinance_fetcher import fetch_ohlcv_yf
        return fetch_ohlcv_yf("DX-Y.NYB", "30m", 10)
    except Exception as e:
        logger.warning("[DXY] Fetch failed: %s", e)
        return None


def dxy_confirms(direction: str, dxy_df) -> bool:
    """BUY needs bearish DXY (USD weak), SELL needs bullish DXY (USD strong)."""
    if dxy_df is None or len(dxy_df) < 2:
        logger.warning("[DXY] Insufficient data — fail-open.")
        return True
    last    = dxy_df.iloc[-2]
    bullish = float(last["close"]) > float(last["open"])
    return (not bullish) if direction == "BUY" else bullish


def find_ob(bars: list, before_idx: int, new_direction: str):
    """
    Find the last opposing-color candle before before_idx (exclusive).
    Returns bars[before_idx] as fallback if nothing found.
    """
    for i in range(before_idx - 1, -1, -1):
        b = bars[i]
        if new_direction == "BUY" and float(b["close"]) < float(b["open"]):
            return b
        if new_direction == "SELL" and float(b["close"]) > float(b["open"]):
            return b
    return bars[min(before_idx, len(bars) - 1)]


def is_displacement(candle, direction: str, pip: float,
                    min_body_ratio: float = 0.60,
                    min_body_pips: int = 10) -> bool:
    """Strong body candle moving in 'direction'. Body >= 60% of range & >= 10 pips."""
    h, l, o, c = (float(candle[k]) for k in ("high", "low", "open", "close"))
    rng  = h - l
    body = abs(c - o)
    if rng < pip or body / rng < min_body_ratio or body < min_body_pips * pip:
        return False
    if direction == "BUY"  and c <= o:
        return False
    if direction == "SELL" and c >= o:
        return False
    return True


def price_in_ob(current_close: float, ob, pip: float,
                tolerance_pips: int = 5) -> bool:
    """True if current_close is within the OB's open-close body (±tolerance)."""
    ob_lo = min(float(ob["open"]), float(ob["close"])) - tolerance_pips * pip
    ob_hi = max(float(ob["open"]), float(ob["close"])) + tolerance_pips * pip
    return ob_lo <= current_close <= ob_hi
