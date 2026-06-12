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
                 runner_candidate: float | None, reason: str, slot_num: int,
                 max_tp_pips: float | None = None) -> dict | None:
    """
    Standard TPs: TP1=1:1, TP2=1:2, TP3=1:3 (always).
    Runner TPs: TP4=1:5 added if runner_candidate >= 1:4 R:R away.
                TP5=1:6 added if runner_candidate >= 1:5 R:R away.
    Returns None if risk == 0.
    """
    risk = abs(entry - sl)
    if risk == 0:
        return None

    sign = 1 if direction == "BUY" else -1

    tp1 = entry + sign * risk * 1
    tp2 = entry + sign * risk * 2
    tp3 = entry + sign * risk * 3

    tp4 = tp5 = None
    if runner_candidate is not None:
        runner_rr = abs(runner_candidate - entry) / risk
        if runner_rr >= 4:
            tp4 = entry + sign * risk * 5
        if runner_rr >= 5:
            tp5 = entry + sign * risk * 6

    if max_tp_pips is not None:
        def _cap(tp: float) -> float:
            return (entry + sign * max_tp_pips) if abs(tp - entry) > max_tp_pips else tp
        tp1 = _cap(tp1)
        tp2 = _cap(tp2)
        tp3 = _cap(tp3)
        if tp4 is not None:
            tp4 = _cap(tp4)
        if tp5 is not None:
            tp5 = _cap(tp5)

    if tp5 is not None:
        rr_label = "1:1 / 1:2 / 1:3 | runner: 1:5 / 1:6"
    elif tp4 is not None:
        rr_label = "1:1 / 1:2 / 1:3 | runner: 1:5"
    else:
        rr_label = "1:1 / 1:2 / 1:3"

    logger.info("[S%d] %s entry=%.2f sl=%.2f TP3=%.2f rr=%s",
                slot_num, direction, entry, sl, tp3, rr_label)

    sig = {
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
    if tp4 is not None:
        sig["tp4"] = tp4
    if tp5 is not None:
        sig["tp5"] = tp5
    return sig


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


# Sentinel returned by sweep_ob_entry when OB body is broken
OB_INVALIDATED = "OB_INVALIDATED"


def sweep_ob_entry(bars: list, level: float, direction: str, pip: float,
                   min_wick_pips: int = 3) -> dict | str | None:
    """
    SMC sweep → OB CE entry method (final entry rule).

    1. Find the most recent sweep candle within the last 3 bars:
       wick clears the level by min_wick_pips, candle closes back inside.
    2. OB = last bullish candle before sweep (SELL) / last bearish candle (BUY).
    3. Entry triggered when price pulls back to OB CE (50% of OB body).
    4. 2-candle rule: pullback must occur within 2 bars of the sweep.
    5. Invalidation: if any post-sweep bar closes through the OB body.

    Returns:
        {"entry": ce, "ob": ob_row}  — entry zone reached; fire signal
        OB_INVALIDATED               — OB broken; send cancellation message
        None                         — no qualifying pattern yet
    """
    if len(bars) < 2:
        return None

    scan_start = max(0, len(bars) - 3)

    for i in range(scan_start, len(bars)):
        sw   = bars[i]
        sh   = float(sw["high"]); sl_v = float(sw["low"])
        sc   = float(sw["close"]); so   = float(sw["open"])

        if direction == "SELL":
            swept = sh > level + min_wick_pips * pip and sc < level and sc < so
        else:
            swept = sl_v < level - min_wick_pips * pip and sc > level and sc > so

        if not swept:
            continue

        ob = find_ob(bars, i, direction)
        if ob is None:
            continue

        ob_o           = float(ob["open"])
        ob_c           = float(ob["close"])
        ob_ce          = (ob_o + ob_c) / 2
        ob_body_top    = max(ob_o, ob_c)
        ob_body_bottom = min(ob_o, ob_c)

        # Check 1–2 bars after the sweep for entry or invalidation
        post_bars = bars[i + 1: i + 3]
        if not post_bars:
            continue  # sweep is on the last bar — wait for next candle

        for bar_j in post_bars:
            bj_c = float(bar_j["close"])
            bj_h = float(bar_j["high"])
            bj_l = float(bar_j["low"])

            if direction == "SELL":
                if bj_c > ob_body_top:     # closed above OB body → invalidated
                    return OB_INVALIDATED
                if bj_h >= ob_ce:           # pulled back to entry zone
                    return {"entry": ob_ce, "ob": ob}
            else:
                if bj_c < ob_body_bottom:  # closed below OB body → invalidated
                    return OB_INVALIDATED
                if bj_l <= ob_ce:           # pulled back to entry zone
                    return {"entry": ob_ce, "ob": ob}

    return None
