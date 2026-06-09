"""
4H Wick Sweep Signal Engine — Smart Money Concepts

Signal fires on every 4H candle close (00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC).
Two-candle pattern: sweep candle + confirmation candle (both must be complete).

TwelveData note: forex instruments (XAU/USD) have candle timestamps in UTC+3,
labeled with the candle CLOSE time. The 'td_tz_offset' in each symbol config
converts that timestamp to a true UTC close time so session and completeness
checks work correctly.
"""

import logging
from datetime import datetime, timezone, timedelta
import pandas as pd

from data_fetcher import fetch_ohlcv
from key_levels import get_all_levels
from config import H4_BARS, DAY_BARS, WEEK_BARS

logger = logging.getLogger(__name__)

WICK_RATIO_MIN = 0.40   # wick must be >= 40% of total candle range


def _fetch(symbol_config: dict, interval: str, outputsize: int) -> pd.DataFrame:
    """Route to yfinance or TwelveData depending on symbol config."""
    if symbol_config.get("data_source") == "yfinance":
        from yfinance_fetcher import fetch_ohlcv_yf
        return fetch_ohlcv_yf(symbol_config["yf_symbol"], interval, outputsize)
    return fetch_ohlcv(symbol_config["symbol"], interval, outputsize)


# ── Timestamp utilities ────────────────────────────────────────────────────────

def _td_to_utc_close(time_str: str, tz_offset: int) -> datetime:
    """Convert a TwelveData candle timestamp to its UTC close time."""
    return datetime.fromisoformat(time_str) - timedelta(hours=tz_offset)


def drop_incomplete_candle(h4_df: pd.DataFrame, tz_offset: int) -> pd.DataFrame:
    """
    TwelveData includes the current (not-yet-closed) 4H candle in time series.
    Drop it: if the UTC close time of the last candle is still in the future,
    that candle is incomplete.
    """
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    last_utc_close = _td_to_utc_close(h4_df.iloc[-1]["time"], tz_offset)
    if last_utc_close > now_utc:
        return h4_df.iloc[:-1].reset_index(drop=True)
    return h4_df


def is_london_ny_session(candle_time_str: str, tz_offset: int) -> bool:
    """
    Returns True if the confirmation candle CLOSES within London/NY hours
    (07:00–20:00 UTC), which covers the user-specified 07:00–16:00 window
    plus late-NY overlap.
    """
    utc_close = _td_to_utc_close(candle_time_str, tz_offset)
    return 7 <= utc_close.hour <= 20


# ── Candle validators ──────────────────────────────────────────────────────────

def is_bearish_wick_sweep(candle: pd.Series, level: float, pip: float,
                          min_sweep_pips: int = 5) -> bool:
    """
    Valid 4H bearish wick sweep of a HIGH level:
      - High exceeds level by >= min_sweep_pips
      - Close back below level
      - Bearish body (close < open)
      - Upper wick (high - open) >= 40% of total range
    """
    h, l, o, c = float(candle["high"]), float(candle["low"]), \
                  float(candle["open"]), float(candle["close"])
    rng = h - l
    if rng < pip:
        return False
    if h < level + min_sweep_pips * pip:     # wick must clear level
        return False
    if c >= level:                            # must close back below level
        return False
    if c >= o:                                # must be bearish body
        return False
    if (h - o) / rng < WICK_RATIO_MIN:       # upper wick >= 40%
        return False
    return True


def is_bullish_wick_sweep(candle: pd.Series, level: float, pip: float,
                          min_sweep_pips: int = 5) -> bool:
    """
    Valid 4H bullish wick sweep of a LOW level:
      - Low dips below level by >= min_sweep_pips
      - Close back above level
      - Bullish body (close > open)
      - Lower wick (open - low) >= 40% of total range
    """
    h, l, o, c = float(candle["high"]), float(candle["low"]), \
                  float(candle["open"]), float(candle["close"])
    rng = h - l
    if rng < pip:
        return False
    if l > level - min_sweep_pips * pip:     # wick must dip below level
        return False
    if c <= level:                            # must close back above level
        return False
    if c <= o:                                # must be bullish body
        return False
    if (o - l) / rng < WICK_RATIO_MIN:       # lower wick >= 40%
        return False
    return True


# ── R:R validator & signal builder ───────────────────────────────────────────

MIN_RR = 1 / 3   # reject if R:R is below 1:3
MAX_RR = 1 / 6   # cap TP3 at 1:6 even if structure allows more

def _build_signal(direction: str, quality: str, entry: float, sl: float,
                  candidate_tp3: float, swept_names: str,
                  dec: int, pip: float) -> dict | None:
    """
    Apply R:R rules, compute TP1/TP2/TP3, and return signal dict.
    Returns None if R:R is below the minimum 1:3.
    """
    risk = abs(entry - sl)
    if risk == 0:
        return None

    sign = -1 if direction == "SELL" else 1

    tp1 = entry + sign * risk          # 1:1
    tp2 = entry + sign * risk * 2      # 1:2

    # TP3: use nearest structure level if it gives 1:3–1:6, else cap / reject
    dist_to_struct = abs(candidate_tp3 - entry)
    rr_struct = dist_to_struct / risk

    if rr_struct < 3:
        # Structure is too close — R:R below 1:3, reject setup
        return None
    elif rr_struct > 6:
        # Structure is beyond 1:6 — cap TP3 at 1:6
        tp3 = entry + sign * risk * 6
        rr_label = "1:6"
    else:
        tp3 = candidate_tp3
        rr_label = f"1:{rr_struct:.0f}"

    inv_side  = "above" if direction == "SELL" else "below"
    logger.info(f"Signal accepted — R:R {rr_label}")

    return {
        "direction":          direction,
        "quality":            quality,
        "entry":              entry,
        "sl":                 sl,
        "tp1":                tp1,
        "tp2":                tp2,
        "tp3":                tp3,
        "rr":                 rr_label,
        "reason":             f"{swept_names} swept on 4H — wick rejection confirmed",
        "invalidation_price": sl,
        "invalidation_side":  inv_side,
    }


# ── TP3: next major support / resistance ──────────────────────────────────────

def _swing_lows(h4_df: pd.DataFrame, n: int = 3) -> list[float]:
    result = []
    for i in range(n, len(h4_df) - n):
        if float(h4_df.iloc[i]["low"]) == float(h4_df.iloc[i - n: i + n + 1]["low"].min()):
            result.append(float(h4_df.iloc[i]["low"]))
    return result


def _swing_highs(h4_df: pd.DataFrame, n: int = 3) -> list[float]:
    result = []
    for i in range(n, len(h4_df) - n):
        if float(h4_df.iloc[i]["high"]) == float(h4_df.iloc[i - n: i + n + 1]["high"].max()):
            result.append(float(h4_df.iloc[i]["high"]))
    return result


def next_major_support(h4_df: pd.DataFrame, below: float, pip: float) -> float:
    candidates = [s for s in _swing_lows(h4_df) if s < below - 10 * pip]
    return max(candidates) if candidates else below - abs(below) * 0.005


def next_major_resistance(h4_df: pd.DataFrame, above: float, pip: float) -> float:
    candidates = [s for s in _swing_highs(h4_df) if s > above + 10 * pip]
    return min(candidates) if candidates else above + abs(above) * 0.005


# ── Main signal pipeline ───────────────────────────────────────────────────────

def generate_smc_signal(symbol_config: dict) -> dict | None:
    sym                = symbol_config["symbol"]
    pip                = symbol_config.get("pip_size", 1.0)
    sl_pips            = symbol_config.get("sl_pips", 12)
    sl_min_from_entry  = symbol_config.get("sl_min_from_entry", 10)
    sl_max_from_entry  = symbol_config.get("sl_max_from_entry", 15)
    min_sweep_pips     = symbol_config.get("min_sweep_pips", 5)
    dec                = symbol_config.get("decimals", 2)
    tz_offset          = symbol_config.get("td_tz_offset", 0)

    try:
        weekly = _fetch(symbol_config, "1week", WEEK_BARS)
        daily  = _fetch(symbol_config, "1day",  DAY_BARS)
        h4     = _fetch(symbol_config, "4h",    H4_BARS)
        h1     = _fetch(symbol_config, "1h",    200)
    except Exception as e:
        logger.error(f"[{sym}] Fetch failed: {e}")
        return None

    # Drop the current incomplete candle if TwelveData included it
    h4 = drop_incomplete_candle(h4, tz_offset)

    if len(h4) < 4:
        logger.warning(f"[{sym}] Not enough complete 4H bars.")
        return None

    confirm = h4.iloc[-1]   # last COMPLETED 4H candle = displacement confirmation
    sweep   = h4.iloc[-2]   # candle before that = sweep candle being validated

    logger.info(f"[{sym}] Confirm: {confirm['time']}  Sweep: {sweep['time']}")

    # ── Session filter ─────────────────────────────────────────────────────
    if not is_london_ny_session(confirm["time"], tz_offset):
        logger.info(f"[{sym}] Confirm candle closes outside London/NY (UTC) — skip.")
        return None

    # ── Key levels ─────────────────────────────────────────────────────────
    lv  = get_all_levels(weekly, daily, h1)
    pdh = lv.get("prev_day_high",  {}).get("value")
    pdl = lv.get("prev_day_low",   {}).get("value")
    pwh = lv.get("prev_week_high", {}).get("value")
    pwl = lv.get("prev_week_low",  {}).get("value")
    ash = lv.get("asian_high",     {}).get("value")
    asl = lv.get("asian_low",      {}).get("value")

    entry = float(confirm["close"])

    # ── BEARISH SELL ───────────────────────────────────────────────────────
    if float(confirm["close"]) < float(confirm["open"]):
        swept = {}
        for name, val in [("PDH", pdh), ("PWH", pwh), ("Asian High", ash)]:
            if val is not None and is_bearish_wick_sweep(sweep, val, pip, min_sweep_pips):
                swept[name] = val

        if swept:
            has_pdh = "PDH" in swept
            has_ash = "Asian High" in swept
            quality = "STRONG" if (has_pdh and has_ash) else "VALID"
            priority = 1 if has_ash else (2 if has_pdh else 3)

            sl   = float(sweep["high"]) + sl_pips * pip
            names = " + ".join(swept.keys())

            # Clamp SL to [sl_min_from_entry, sl_max_from_entry] measured from entry
            sl_dist = sl - entry  # positive: SL is above entry (correct for SELL)
            if sl_dist > sl_max_from_entry * pip:
                logger.info(f"[{sym}] SELL rejected — SL {sl_dist:.2f} pts from entry "
                            f"exceeds max {sl_max_from_entry * pip:.2f}")
                return None
            if sl_dist < sl_min_from_entry * pip:
                sl = entry + sl_min_from_entry * pip

            # Candidate TP3: nearest support below entry
            cand_tp3 = next_major_support(h4, entry, pip)

            logger.info(f"[{sym}] SELL {quality}: {names} | "
                        f"Entry={entry:.{dec}f}  SL={sl:.{dec}f}  CandTP3={cand_tp3:.{dec}f}")

            sig = _build_signal("SELL", quality, entry, sl, cand_tp3, names, dec, pip)
            if sig is None:
                logger.info(f"[{sym}] Rejected — R:R below 1:3.")
            elif sig is not None:
                sig["priority"] = priority
            return sig

    # ── BULLISH BUY ────────────────────────────────────────────────────────
    if float(confirm["close"]) > float(confirm["open"]):
        swept = {}
        for name, val in [("PDL", pdl), ("PWL", pwl), ("Asian Low", asl)]:
            if val is not None and is_bullish_wick_sweep(sweep, val, pip, min_sweep_pips):
                swept[name] = val

        if swept:
            has_pdl = "PDL" in swept
            has_asl = "Asian Low" in swept
            quality = "STRONG" if (has_pdl and has_asl) else "VALID"
            priority = 1 if has_asl else (2 if has_pdl else 3)

            sl   = float(sweep["low"]) - sl_pips * pip
            names = " + ".join(swept.keys())

            # Clamp SL to [sl_min_from_entry, sl_max_from_entry] measured from entry
            sl_dist = entry - sl  # positive: SL is below entry (correct for BUY)
            if sl_dist > sl_max_from_entry * pip:
                logger.info(f"[{sym}] BUY rejected — SL {sl_dist:.2f} pts from entry "
                            f"exceeds max {sl_max_from_entry * pip:.2f}")
                return None
            if sl_dist < sl_min_from_entry * pip:
                sl = entry - sl_min_from_entry * pip

            # Candidate TP3: nearest resistance above entry
            cand_tp3 = next_major_resistance(h4, entry, pip)

            logger.info(f"[{sym}] BUY {quality}: {names} | "
                        f"Entry={entry:.{dec}f}  SL={sl:.{dec}f}  CandTP3={cand_tp3:.{dec}f}")

            sig = _build_signal("BUY", quality, entry, sl, cand_tp3, names, dec, pip)
            if sig is None:
                logger.info(f"[{sym}] Rejected — R:R below 1:3.")
            elif sig is not None:
                sig["priority"] = priority
            return sig

    logger.info(f"[{sym}] No valid 4H wick sweep setup.")
    return None
