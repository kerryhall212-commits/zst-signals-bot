"""
Textbook Tuesday — sweep of Monday's High/Low on 1H candles.

Sessions (BST):
  Asian   00:00–07:00  TIER 1  ⭐⭐⭐ PREMIUM SETUP  (quiet grab, London drives)
  London  07:00–11:00  TIER 2  ⭐⭐ STRONG SETUP     (classic textbook)
  Dead Zone 11:00–13:30            SKIP
  NY      13:30–16:00  TIER 1  ⭐⭐⭐ PREMIUM SETUP  (high volume, fast mover)

Two-candle pattern (same as 4H engine, applied to 1H):
  sweep_candle   = wick clears Monday's H/L, closes back inside
  confirm_candle = closes in direction of the trade (displacement)

Minimum R:R 1:3 enforced via _build_signal (rejects if TP3 too close).
"""

import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import pandas as pd

from signal_engine import (
    _fetch,
    _td_to_utc_close,
    is_bearish_wick_sweep,
    is_bullish_wick_sweep,
    _build_signal,
)
from key_levels import prev_day_levels
from config import H1_BARS, DAY_BARS

logger = logging.getLogger(__name__)

_SESSIONS = {
    "Asian":  {"start": (0,  0),  "end": (7,  0),  "label": "⭐⭐⭐ PREMIUM SETUP"},
    "London": {"start": (7,  0),  "end": (11, 0),  "label": "⭐⭐ STRONG SETUP"},
    "NY":     {"start": (13, 30), "end": (16, 0),  "label": "⭐⭐⭐ PREMIUM SETUP"},
}

# Only scan the most recent N session bars to avoid stale entries
_RECENCY_BARS = 4


def is_tuesday_bst() -> bool:
    return datetime.now(ZoneInfo("Europe/London")).weekday() == 1


def current_session() -> str | None:
    """Current BST session, or None for Dead Zone / outside hours."""
    now  = datetime.now(ZoneInfo("Europe/London"))
    mins = now.hour * 60 + now.minute
    if mins < 7 * 60:        return "Asian"
    if mins < 11 * 60:       return "London"
    if mins < 13 * 60 + 30: return None   # Dead Zone
    if mins < 16 * 60:       return "NY"
    return None


def _bar_bst(time_str: str, tz_offset: int) -> datetime:
    """Return BST-aware close datetime for a candle bar."""
    utc = _td_to_utc_close(time_str, tz_offset).replace(tzinfo=timezone.utc)
    return utc.astimezone(ZoneInfo("Europe/London"))


def _effective_tz_offset(symbol_config: dict) -> int:
    """yfinance timestamps are already UTC — don't apply td_tz_offset."""
    if symbol_config.get("data_source") == "yfinance":
        return 0
    return symbol_config.get("td_tz_offset", 0)


def _filter_session_bars(h1: pd.DataFrame, tz_offset: int, session: str,
                         target_bst_date) -> pd.DataFrame:
    cfg        = _SESSIONS[session]
    start_mins = cfg["start"][0] * 60 + cfg["start"][1]
    end_mins   = cfg["end"][0]   * 60 + cfg["end"][1]
    rows = []
    for _, row in h1.iterrows():
        bst      = _bar_bst(row["time"], tz_offset)
        if bst.date() != target_bst_date:
            continue
        bar_mins = bst.hour * 60 + bst.minute
        if start_mins <= bar_mins < end_mins:
            rows.append(row)
    return pd.DataFrame(rows).reset_index(drop=True) if rows else pd.DataFrame()


def generate_tt_signal(symbol_config: dict, session: str | None = None) -> dict | None:
    """
    Scan the most recent session bars for a Monday's H/L wick sweep + confirm.
    Returns a signal dict (from _build_signal) with added TT metadata, or None.
    """
    if session is None:
        session = current_session()
    if session is None:
        return None

    sym        = symbol_config["symbol"]
    pip        = symbol_config.get("pip_size", 1.0)
    sl_pips    = symbol_config.get("sl_pips", 12)
    sl_min     = symbol_config.get("sl_min_from_entry", 10)
    sl_max     = symbol_config.get("sl_max_from_entry", 15)
    min_sweep  = symbol_config.get("min_sweep_pips", 5)
    dec        = symbol_config.get("decimals", 2)
    tz_offset  = _effective_tz_offset(symbol_config)

    try:
        h1    = _fetch(symbol_config, "1h",   H1_BARS)
        daily = _fetch(symbol_config, "1day", DAY_BARS)
    except Exception as e:
        logger.warning("[TT][%s] Data fetch failed: %s", sym, e)
        return None

    pd_lvls     = prev_day_levels(daily)
    monday_high = pd_lvls.get("prev_day_high", {}).get("value")
    monday_low  = pd_lvls.get("prev_day_low",  {}).get("value")
    if monday_high is None or monday_low is None:
        logger.info("[TT][%s] Monday's H/L unavailable.", sym)
        return None

    today_bst  = datetime.now(ZoneInfo("Europe/London")).date()
    sess_bars  = _filter_session_bars(h1, tz_offset, session, today_bst)

    if len(sess_bars) < 2:
        logger.info("[TT][%s] Not enough %s session bars (%d).", sym, session, len(sess_bars))
        return None

    # Scan the most recent bars only — avoid stale entries
    recent = sess_bars.tail(_RECENCY_BARS)
    sess_label = _SESSIONS[session]["label"]

    for i in range(len(recent) - 1, 0, -1):
        sweep_c   = recent.iloc[i - 1]
        confirm_c = recent.iloc[i]

        # ── BEARISH: wick above Monday's High, confirm closes bearish ──────
        if (is_bearish_wick_sweep(sweep_c, monday_high, pip, min_sweep)
                and float(confirm_c["close"]) < float(confirm_c["open"])):
            entry    = float(confirm_c["close"])
            sl       = float(sweep_c["high"]) + sl_pips * pip
            sl_dist  = sl - entry

            if sl_dist > sl_max * pip:
                logger.info("[TT][%s] SELL — SL dist %.2f > max %.2f, skip.",
                            sym, sl_dist, sl_max * pip)
                continue
            if sl_dist < sl_min * pip:
                sl = entry + sl_min * pip

            logger.info("[TT][%s] SELL %s | Mon High %.2f swept | Entry=%.2f SL=%.2f",
                        sym, session, monday_high, entry, sl)

            sig = _build_signal("SELL", "STRONG", entry, sl, monday_low,
                                "Monday's High", dec, pip)
            if sig:
                sig.update({
                    "session":    session,
                    "tier_label": sess_label,
                    "sweep_label": "Monday's High",
                    "priority":   0,
                })
            return sig  # return even if None (rejected) — don't try other pairs

        # ── BULLISH: wick below Monday's Low, confirm closes bullish ───────
        if (is_bullish_wick_sweep(sweep_c, monday_low, pip, min_sweep)
                and float(confirm_c["close"]) > float(confirm_c["open"])):
            entry    = float(confirm_c["close"])
            sl       = float(sweep_c["low"]) - sl_pips * pip
            sl_dist  = entry - sl

            if sl_dist > sl_max * pip:
                logger.info("[TT][%s] BUY — SL dist %.2f > max %.2f, skip.",
                            sym, sl_dist, sl_max * pip)
                continue
            if sl_dist < sl_min * pip:
                sl = entry - sl_min * pip

            logger.info("[TT][%s] BUY %s | Mon Low %.2f swept | Entry=%.2f SL=%.2f",
                        sym, session, monday_low, entry, sl)

            sig = _build_signal("BUY", "STRONG", entry, sl, monday_high,
                                "Monday's Low", dec, pip)
            if sig:
                sig.update({
                    "session":    session,
                    "tier_label": sess_label,
                    "sweep_label": "Monday's Low",
                    "priority":   0,
                })
            return sig

    logger.info("[TT][%s] No valid sweep+confirm in %s session.", sym, session)
    return None


def format_tt_message(symbol_config: dict, signal: dict) -> str:
    from formatter import fmt
    d     = 0
    title = symbol_config["signal_title"]
    tick  = symbol_config["ticker"]
    dir_  = signal["direction"]

    return "\n".join([
        f"🚨 <b>{title}</b>",
        f"📅 <b>TEXTBOOK TUESDAY</b>",
        "",
        f"<b>{dir_}</b> | <b>{tick}</b>",
        f"Entry: <code>{fmt(signal['entry'], d)}</code>",
        f"SL: <code>{fmt(signal['sl'], d)}</code>",
        f"TP1: <code>{fmt(signal['tp1'], d)}</code>",
        f"TP2: <code>{fmt(signal['tp2'], d)}</code>",
        f"TP3: <code>{fmt(signal['tp3'], d)}</code> ← Draw on liquidity",
        "",
        f"Session: {signal['session']}",
        f"Probability: {signal['tier_label']}",
        f"Reason: {signal['sweep_label']} swept",
        f"Invalidation: 1H close {signal['invalidation_side']} "
        f"<code>{fmt(signal['invalidation_price'], d)}</code>",
        "",
        "🎯 Swing runner — let it breathe",
        "ZST Insider 🔐",
    ])
