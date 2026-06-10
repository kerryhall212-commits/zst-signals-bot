"""
5-7AM Continuation Pullback — daily morning priority signal (RANK 0).

Rules:
  1. Overnight move (00:00–05:00 BST) >= 30 pips in one direction
  2. Pullback (05:00–07:00 BST): retraces AGAINST overnight direction,
     stays inside Tokyo range (00:00–07:00 BST H/L), <= 50% retracement
  3. 7AM BST 1H candle closes IN the direction of the overnight move
  4. Entry = close of 7AM candle (open of 8AM)
     SL    = Tokyo range boundary + buffer
     TP1   = 1:1
     TP2   = overnight move extended from entry
     TP3   = PDH/PDL (draw on liquidity)

Fires Mon–Fri at 07:00–07:10 BST. RANK 0 — overrides all other setups.
"""

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from signal_engine import _fetch, _td_to_utc_close, _build_signal
from key_levels import prev_day_levels
from config import H1_BARS, DAY_BARS

logger = logging.getLogger(__name__)

_MIN_OVERNIGHT_PIPS = 30
_MAX_PULLBACK_PCT   = 0.50


def _effective_tz_offset(symbol_config: dict) -> int:
    if symbol_config.get("data_source") == "yfinance":
        return 0
    return symbol_config.get("td_tz_offset", 0)


def _bar_bst(time_str: str, tz_offset: int) -> datetime:
    utc = _td_to_utc_close(time_str, tz_offset).replace(tzinfo=timezone.utc)
    return utc.astimezone(ZoneInfo("Europe/London"))


def _bst_bars(h1, tz_offset: int, bst_date, h_start: int, h_end: int) -> list:
    """Return rows whose BST close hour is in [h_start, h_end)."""
    rows = []
    for _, row in h1.iterrows():
        bst = _bar_bst(row["time"], tz_offset)
        if bst.date() == bst_date and h_start <= bst.hour < h_end:
            rows.append(row)
    return rows


def generate_morning_signal(symbol_config: dict) -> dict | None:
    sym       = symbol_config["symbol"]
    pip       = symbol_config.get("pip_size", 1.0)
    sl_pips   = symbol_config.get("sl_pips", 12)
    sl_min    = symbol_config.get("sl_min_from_entry", 10)
    sl_max    = symbol_config.get("sl_max_from_entry", 15)
    dec       = symbol_config.get("decimals", 2)
    tz_offset = _effective_tz_offset(symbol_config)

    try:
        h1    = _fetch(symbol_config, "1h",   H1_BARS)
        daily = _fetch(symbol_config, "1day", DAY_BARS)
    except Exception as e:
        logger.warning("[MS][%s] Fetch failed: %s", sym, e)
        return None

    today_bst = datetime.now(ZoneInfo("Europe/London")).date()

    # ── 1. Overnight move: bars closing 01:00–05:00 BST ──────────────────
    overnight = _bst_bars(h1, tz_offset, today_bst, 1, 6)
    if len(overnight) < 3:
        logger.info("[MS][%s] Not enough overnight bars (%d).", sym, len(overnight))
        return None

    ov_open  = float(overnight[0]["open"])
    ov_close = float(overnight[-1]["close"])
    move     = ov_close - ov_open
    move_pips = abs(move) / pip

    if move_pips < _MIN_OVERNIGHT_PIPS:
        logger.info("[MS][%s] Overnight move %.1f pips < %d min.", sym, move_pips, _MIN_OVERNIGHT_PIPS)
        return None

    direction = "BUY" if move > 0 else "SELL"
    logger.info("[MS][%s] Overnight %s %.1f pips.", sym, direction, move_pips)

    # ── 2. Tokyo range: bars closing 01:00–07:00 BST ─────────────────────
    tokyo = _bst_bars(h1, tz_offset, today_bst, 1, 8)
    if not tokyo:
        return None
    tokyo_high = max(float(r["high"]) for r in tokyo)
    tokyo_low  = min(float(r["low"])  for r in tokyo)

    # ── 3. Pullback: bars closing 06:00–07:00 BST ─────────────────────────
    pullback = _bst_bars(h1, tz_offset, today_bst, 6, 8)
    if not pullback:
        logger.info("[MS][%s] No pullback bars.", sym)
        return None

    pb_high = max(float(r["high"]) for r in pullback)
    pb_low  = min(float(r["low"])  for r in pullback)

    # Must stay inside Tokyo range
    if pb_high > tokyo_high or pb_low < tokyo_low:
        logger.info("[MS][%s] Pullback broke Tokyo range.", sym)
        return None

    # Must not retrace more than 50% of overnight move
    if direction == "BUY":
        retrace_limit = ov_open + abs(move) * (1 - _MAX_PULLBACK_PCT)
        if pb_low < retrace_limit:
            logger.info("[MS][%s] BUY pullback > 50%%.", sym)
            return None
    else:
        retrace_limit = ov_open - abs(move) * (1 - _MAX_PULLBACK_PCT)
        if pb_high > retrace_limit:
            logger.info("[MS][%s] SELL pullback > 50%%.", sym)
            return None

    # ── 4. 7AM confirmation: bar closing at 07:00 BST ─────────────────────
    confirm = _bst_bars(h1, tz_offset, today_bst, 7, 8)
    if not confirm:
        logger.info("[MS][%s] No 7AM candle.", sym)
        return None

    candle_7am = confirm[-1]
    c_open  = float(candle_7am["open"])
    c_close = float(candle_7am["close"])

    if direction == "BUY" and c_close <= c_open:
        logger.info("[MS][%s] 7AM candle not bullish.", sym)
        return None
    if direction == "SELL" and c_close >= c_open:
        logger.info("[MS][%s] 7AM candle not bearish.", sym)
        return None

    # ── 5. Entry, SL, TP ──────────────────────────────────────────────────
    entry = c_close

    if direction == "BUY":
        sl      = tokyo_low - sl_pips * pip
        sl_dist = entry - sl
        if sl_dist > sl_max * pip: sl = entry - sl_max * pip
        if sl_dist < sl_min * pip: sl = entry - sl_min * pip
        tp2 = entry + abs(move)
    else:
        sl      = tokyo_high + sl_pips * pip
        sl_dist = sl - entry
        if sl_dist > sl_max * pip: sl = entry + sl_max * pip
        if sl_dist < sl_min * pip: sl = entry + sl_min * pip
        tp2 = entry - abs(move)

    pd_lvls = prev_day_levels(daily)
    pdh     = pd_lvls.get("prev_day_high", {}).get("value")
    pdl     = pd_lvls.get("prev_day_low",  {}).get("value")
    cand_tp3 = pdh if direction == "BUY" and pdh else (pdl if direction == "SELL" and pdl else tp2)

    logger.info("[MS][%s] %s Entry=%.2f SL=%.2f TP2=%.2f TP3=%.2f",
                sym, direction, entry, sl, tp2, cand_tp3)

    sig = _build_signal(direction, "STRONG", entry, sl, cand_tp3,
                        "5-7AM continuation pullback", dec, pip)
    if sig:
        sig["tp2"]      = tp2
        sig["priority"] = 0
    return sig


def format_morning_signal(symbol_config: dict, signal: dict) -> str:
    from formatter import fmt
    d     = 0
    title = symbol_config["signal_title"]
    tick  = symbol_config["ticker"]
    dir_  = signal["direction"]
    side  = signal["invalidation_side"]

    return "\n".join([
        f"🚨 <b>{title}</b>",
        f"⭐ <b>PRIORITY SETUP</b>",
        "",
        f"<b>{dir_}</b> | <b>{tick}</b>",
        f"Entry: <code>{fmt(signal['entry'], d)}</code>",
        f"SL: <code>{fmt(signal['sl'], d)}</code>",
        f"TP1: <code>{fmt(signal['tp1'], d)}</code>",
        f"TP2: <code>{fmt(signal['tp2'], d)}</code>",
        f"TP3: <code>{fmt(signal['tp3'], d)}</code>",
        "",
        "Reason: 5-7AM continuation pullback",
        "        — 7AM close confirmed",
        f"Invalidation: 1H close {side} <code>{fmt(signal['invalidation_price'], d)}</code>",
        "",
        "ZST Insider 🔐",
    ])
