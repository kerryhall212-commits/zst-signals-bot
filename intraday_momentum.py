"""
ZST Intraday Momentum Signal — 30M candle engine.

Sessions: London 08:00–11:00 BST, NY 13:30–16:00 BST.

Entry conditions (all must be met):
  1. Price at/near key level within 10 pips (PDH/PDL/Asian H/L/PWH/PWL)
  2. 30M momentum candle: body > 70% of range, >= 15 pips, closes away from level
  3. Previous 30M candle opposing direction
  4. Trade in session open direction
  5. DXY confirms (bearish DXY for BUY, bullish DXY for SELL)
  6. Daily bias aligns
  7. No news within 30 mins (enforced in main.py)
"""

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from signal_engine import _fetch, _td_to_utc_close
from key_levels import get_all_levels
from config import M30_BARS, H1_BARS, DAY_BARS, WEEK_BARS

logger = logging.getLogger(__name__)

_BODY_RATIO_MIN  = 0.70  # momentum candle body must be >= 70% of candle range
_NEAR_LEVEL_PIPS = 10    # candle must approach level within this many pips
_MIN_BODY_PIPS   = 15    # minimum body size in pips


def _effective_tz_offset(symbol_config: dict) -> int:
    if symbol_config.get("data_source") == "yfinance":
        return 0
    return symbol_config.get("td_tz_offset", 0)


def _bar_bst(time_str: str, tz_offset: int) -> datetime:
    utc = _td_to_utc_close(time_str, tz_offset).replace(tzinfo=timezone.utc)
    return utc.astimezone(ZoneInfo("Europe/London"))


def _intraday_session(now_bst: datetime) -> str | None:
    mins = now_bst.hour * 60 + now_bst.minute
    if 8 * 60 <= mins < 11 * 60:
        return "London"
    if 13 * 60 + 30 <= mins < 16 * 60:
        return "NY"
    return None


def _session_bounds(session: str) -> tuple[int, int]:
    return (8 * 60, 11 * 60) if session == "London" else (13 * 60 + 30, 16 * 60)


def _filter_session_bars(m30, tz_offset: int, session: str, bst_date) -> list:
    """Return 30M rows whose bar (open→close) falls within the session on bst_date."""
    start_mins, end_mins = _session_bounds(session)
    rows = []
    for _, row in m30.iterrows():
        bst = _bar_bst(row["time"], tz_offset)
        if bst.date() != bst_date:
            continue
        close_mins    = bst.hour * 60 + bst.minute
        bar_open_mins = close_mins - 30
        if bar_open_mins >= start_mins and close_mins <= end_mins:
            rows.append(row)
    return rows


def _is_momentum_candle(candle, direction: str, pip: float, level: float) -> bool:
    """
    Valid 30M momentum candle:
      - Body >= 70% of total range
      - Body >= MIN_BODY_PIPS
      - Direction matches trade direction
      - Closes away from the key level
    """
    h, l, o, c = (float(candle[k]) for k in ("high", "low", "open", "close"))
    rng = h - l
    if rng < pip:
        return False
    body = abs(c - o)
    if body / rng < _BODY_RATIO_MIN:
        return False
    if body < _MIN_BODY_PIPS * pip:
        return False
    if direction == "BUY" and c <= o:
        return False
    if direction == "SELL" and c >= o:
        return False
    # Close must be away from level
    if direction == "BUY" and c <= level:
        return False
    if direction == "SELL" and c >= level:
        return False
    return True


def _near_level(candle, direction: str, level: float, pip: float) -> bool:
    """For BUY: candle low within 10 pips of support. For SELL: high within 10 pips of resistance."""
    h, l = float(candle["high"]), float(candle["low"])
    dist = abs(l - level) if direction == "BUY" else abs(h - level)
    return dist <= _NEAR_LEVEL_PIPS * pip


def _fetch_dxy_m30():
    try:
        from yfinance_fetcher import fetch_ohlcv_yf
        return fetch_ohlcv_yf("DX-Y.NYB", "30m", 10)
    except Exception as e:
        logger.warning("[DXY] Fetch failed: %s", e)
        return None


def _dxy_confirms(direction: str, dxy_df) -> bool:
    """
    DXY confirmation:
      BUY  → DXY bearish (USD weakening lifts USD-denominated assets)
      SELL → DXY bullish (USD strengthening weighs on them)
    """
    if dxy_df is None or len(dxy_df) < 2:
        logger.warning("[DXY] Insufficient data — fail-open.")
        return True
    last        = dxy_df.iloc[-2]  # last confirmed 30M bar
    dxy_bullish = float(last["close"]) > float(last["open"])
    return (not dxy_bullish) if direction == "BUY" else dxy_bullish


def _build_intraday_signal(direction: str, entry: float, pip: float,
                            sl_pips: int, tp1_pips: int, tp2_pips: int,
                            tp3_pips: int, level_name: str,
                            session: str) -> dict | None:
    """
    Build signal with fixed-pip TPs and global R:R enforcement:
      Below 1:3  → reject (return None)
      1:3 to 1:6 → send
      Above 1:6  → cap TP3 at 1:6 and send
    """
    sign = 1 if direction == "BUY" else -1
    risk = sl_pips * pip
    sl   = entry - sign * risk
    tp1  = entry + sign * tp1_pips * pip
    tp2  = entry + sign * tp2_pips * pip

    rr_raw = tp3_pips / sl_pips
    if rr_raw < 3:
        logger.info("[IM] R:R %.2f below 1:3 — rejected.", rr_raw)
        return None
    if rr_raw > 6:
        tp3      = entry + sign * 6 * risk
        rr_label = "1:6"
    else:
        tp3      = entry + sign * tp3_pips * pip
        rr_label = f"1:{rr_raw:.0f}"

    logger.info("[IM] %s signal accepted R:R %s", direction, rr_label)

    return {
        "direction":          direction,
        "entry":              entry,
        "sl":                 sl,
        "tp1":                tp1,
        "tp2":                tp2,
        "tp3":                tp3,
        "rr":                 rr_label,
        "reason":             level_name,
        "session":            session,
        "invalidation_price": sl,
        "invalidation_side":  "above" if direction == "SELL" else "below",
        "signal_type":        "intraday_momentum",
    }


def generate_intraday_signal(symbol_config: dict) -> dict | None:
    sym       = symbol_config["symbol"]
    pip       = symbol_config.get("pip_size", 1.0)
    tz_offset = _effective_tz_offset(symbol_config)
    sl_pips   = symbol_config.get("intraday_sl_pips",  15)
    tp1_pips  = symbol_config.get("intraday_tp1_pips", 45)
    tp2_pips  = symbol_config.get("intraday_tp2_pips", 60)
    tp3_pips  = symbol_config.get("intraday_tp3_pips", 100)

    now_bst = datetime.now(ZoneInfo("Europe/London"))
    session = _intraday_session(now_bst)
    if session is None:
        return None

    try:
        m30    = _fetch(symbol_config, "30min", M30_BARS)
        weekly = _fetch(symbol_config, "1week", WEEK_BARS)
        daily  = _fetch(symbol_config, "1day",  DAY_BARS)
        h1     = _fetch(symbol_config, "1h",    H1_BARS)
    except Exception as e:
        logger.error("[IM][%s] Fetch failed: %s", sym, e)
        return None

    if len(m30) < 3:
        logger.info("[IM][%s] Not enough 30M bars.", sym)
        return None

    bst_date  = now_bst.date()
    sess_bars = _filter_session_bars(m30, tz_offset, session, bst_date)
    if len(sess_bars) < 2:
        logger.info("[IM][%s] Not enough %s session bars (%d).", sym, session, len(sess_bars))
        return None

    mom_candle  = sess_bars[-1]   # most recent completed 30M bar
    prev_candle = sess_bars[-2]   # bar before it

    # Session direction: is price above or below where the session opened?
    session_open = float(sess_bars[0]["open"])
    mom_close    = float(mom_candle["close"])
    session_dir  = "BUY" if mom_close > session_open else "SELL"

    # Daily bias from last completed daily candle
    daily_bullish: bool | None = None
    if len(daily) >= 2:
        last_day      = daily.iloc[-2]
        daily_bullish = float(last_day["close"]) > float(last_day["open"])

    # Key levels (h1 df feeds the Asian session detector in get_all_levels)
    lv = get_all_levels(weekly, daily, h1)
    support_levels = [
        ("PDL",       lv.get("prev_day_low",   {}).get("value")),
        ("PWL",       lv.get("prev_week_low",  {}).get("value")),
        ("Asian Low", lv.get("asian_low",      {}).get("value")),
    ]
    resist_levels = [
        ("PDH",        lv.get("prev_day_high",  {}).get("value")),
        ("PWH",        lv.get("prev_week_high", {}).get("value")),
        ("Asian High", lv.get("asian_high",     {}).get("value")),
    ]

    # Fetch DXY once for both direction checks
    dxy_df = _fetch_dxy_m30()

    for direction, levels in [("BUY", support_levels), ("SELL", resist_levels)]:
        if direction != session_dir:
            continue

        if daily_bullish is not None:
            if direction == "BUY" and not daily_bullish:
                logger.info("[IM][%s] Daily bias bearish — skip BUY.", sym)
                continue
            if direction == "SELL" and daily_bullish:
                logger.info("[IM][%s] Daily bias bullish — skip SELL.", sym)
                continue

        # Previous candle must be opposing direction
        prev_bullish = float(prev_candle["close"]) > float(prev_candle["open"])
        if direction == "BUY" and prev_bullish:
            logger.info("[IM][%s] Prev candle not bearish for BUY — skip.", sym)
            continue
        if direction == "SELL" and not prev_bullish:
            logger.info("[IM][%s] Prev candle not bullish for SELL — skip.", sym)
            continue

        if not _dxy_confirms(direction, dxy_df):
            logger.info("[IM][%s] DXY rejects %s.", sym, direction)
            continue

        for level_name, level_val in levels:
            if level_val is None:
                continue
            if not _near_level(mom_candle, direction, level_val, pip):
                continue
            if not _is_momentum_candle(mom_candle, direction, pip, level_val):
                continue

            logger.info("[IM][%s] %s candidate at %s.", sym, direction, level_name)
            sig = _build_intraday_signal(
                direction, mom_close, pip,
                sl_pips, tp1_pips, tp2_pips, tp3_pips,
                level_name, session,
            )
            if sig is not None:
                logger.info("[IM][%s] Signal ready: %s from %s", sym, direction, level_name)
                return sig

    logger.info("[IM][%s] No intraday momentum setup in %s session.", sym, session)
    return None
