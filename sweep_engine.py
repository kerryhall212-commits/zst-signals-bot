"""
Core sweep detection engine.

Flow per 1H bar:
  1. Wick sweeps a key level (wick beyond + body close inside)
  2. Next candle confirms reclaim (closes further from level)
  3. OB = last opposing-color candle before the sweep bar
  4. Price wicks back to OB CE within 3 candles = ENTRY
  5. SL = OB HIGH + 5 pips (SELL) or OB LOW - 5 pips (BUY), max 15 pips
  6. Runway check: no key level between entry and TP3
"""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from signal_engine import _fetch
from slot_helpers import effective_tz_offset, filter_bst_bars, find_ob, build_signal
from config import H1_BARS

logger = logging.getLogger(__name__)

_MIN_WICK_PIPS     = 3
_SL_BUFFER_PIPS    = 5
_MAX_PULLBACK_BARS = 3


# ── Session bar retrieval ──────────────────────────────────────────────────────

def _get_session_bars(symbol_config: dict, session: str) -> list:
    tz_offset = effective_tz_offset(symbol_config)
    now_bst   = datetime.now(ZoneInfo("Europe/London"))
    bst_date  = now_bst.date()
    prev_date = (now_bst - timedelta(days=1)).date()
    mins      = now_bst.hour * 60 + now_bst.minute

    try:
        h1 = _fetch(symbol_config, "1h", H1_BARS)
    except Exception as e:
        logger.error("[Sweep][%s] H1 fetch failed: %s", symbol_config["symbol"], e)
        return []

    if session == "asian":
        if mins >= 21 * 60:
            # Session just started tonight
            return filter_bst_bars(h1, tz_offset, bst_date, 21 * 60, 24 * 60)
        else:
            # Early morning — session started yesterday at 21:00
            prev_night = filter_bst_bars(h1, tz_offset, prev_date, 21 * 60, 24 * 60)
            early      = filter_bst_bars(h1, tz_offset, bst_date,  0,       8 * 60)
            return prev_night + early

    elif session == "london":
        return filter_bst_bars(h1, tz_offset, bst_date, 8 * 60, 13 * 60)

    else:  # ny
        return filter_bst_bars(h1, tz_offset, bst_date, 13 * 60, 21 * 60)


# ── Level helpers ──────────────────────────────────────────────────────────────

def _watched_levels(levels_for_sym: dict, session: str) -> list[tuple]:
    """Returns [(name, value, direction), ...]"""
    base = [
        ("PDH", "pdh", "SELL"),
        ("PDL", "pdl", "BUY"),
        ("PWH", "pwh", "SELL"),
        ("PWL", "pwl", "BUY"),
    ]
    asian_extras = [
        ("Asian High", "ash", "SELL"),
        ("Asian Low",  "asl", "BUY"),
    ]
    entries = base + (asian_extras if session in ("london", "ny") else [])
    return [
        (name, float(val), direction)
        for name, key, direction in entries
        if (val := levels_for_sym.get(key)) is not None
    ]


def _runway_clear(entry: float, direction: str, tp3: float,
                  all_vals: list[float]) -> bool:
    """No key level should sit strictly between entry and TP3."""
    lo = min(entry, tp3)
    hi = max(entry, tp3)
    return not any(lo < v < hi for v in all_vals)


def _find_runner(entry: float, direction: str, risk: float,
                 all_vals: list[float]) -> float | None:
    """Nearest key level that is >= 4 R:R from entry."""
    sign = 1 if direction == "BUY" else -1
    candidates = [v for v in all_vals if (v - entry) * sign / risk >= 4]
    return min(candidates, key=lambda v: abs(v - entry)) if candidates else None


# ── Core sweep check ───────────────────────────────────────────────────────────

def _check_level(bars: list, symbol_config: dict,
                 level_name: str, level_val: float, direction: str,
                 all_levels: list[tuple]) -> dict | None:
    sym    = symbol_config["symbol"]
    pip    = symbol_config.get("pip_size", 1.0)
    max_sl = symbol_config.get("intraday_sl_pips", 15)

    # Only scan the most recent 8 bars for relevance
    search_start = max(0, len(bars) - 8)

    for i in range(search_start, len(bars)):
        bar = bars[i]
        h, l, c = float(bar["high"]), float(bar["low"]), float(bar["close"])

        # Step 1 — Sweep: wick beyond level, body closes inside
        if direction == "SELL":
            swept = h > level_val + _MIN_WICK_PIPS * pip and c < level_val
        else:
            swept = l < level_val - _MIN_WICK_PIPS * pip and c > level_val

        if not swept:
            continue

        logger.info("[Sweep][%s] %s sweep at %s=%.2f (bar %d/%d)",
                    sym, direction, level_name, level_val, i, len(bars) - 1)

        # Step 2 — Reclaim: next bar closes further away from swept level
        post = bars[i + 1:]
        if not post:
            continue  # sweep is the last bar — wait for next poll

        reclaim_bar = post[0]
        rc = float(reclaim_bar["close"])
        reclaimed = (rc < c) if direction == "SELL" else (rc > c)
        if not reclaimed:
            logger.info("[Sweep][%s] %s %s — reclaim not confirmed (close=%.2f, next=%.2f).",
                        sym, level_name, direction, c, rc)
            continue

        # Step 3 — OB: last opposing-color candle before sweep bar
        ob = find_ob(bars, i, direction)
        if ob is None:
            continue

        ob_h = float(ob["high"])
        ob_l = float(ob["low"])
        ce   = (float(ob["open"]) + float(ob["close"])) / 2

        # Step 4 — CE pullback: within _MAX_PULLBACK_BARS of reclaim bar
        pullback_window = bars[i + 2: i + 2 + _MAX_PULLBACK_BARS]
        if not pullback_window:
            logger.info("[Sweep][%s] %s %s — waiting for CE pullback (%.2f).",
                        sym, level_name, direction, ce)
            continue

        entry_bar = None
        for pb in pullback_window:
            ph, pl = float(pb["high"]), float(pb["low"])
            if direction == "SELL" and ph >= ce:
                entry_bar = pb
                break
            elif direction == "BUY" and pl <= ce:
                entry_bar = pb
                break

        if entry_bar is None:
            logger.info("[Sweep][%s] %s %s — CE %.2f not reached in %d bars.",
                        sym, level_name, direction, ce, len(pullback_window))
            continue

        # Step 5 — SL from OB H/L + buffer; skip if exceeds max
        entry = ce
        sl    = (ob_h + _SL_BUFFER_PIPS * pip) if direction == "SELL" \
                 else (ob_l - _SL_BUFFER_PIPS * pip)
        risk_pips = abs(entry - sl) / pip
        if risk_pips > max_sl:
            logger.info("[Sweep][%s] %s risk %.1f pips > %d max — skip.",
                        sym, direction, risk_pips, max_sl)
            continue

        # Step 6 — Runway: no key level between entry and TP3
        risk     = abs(entry - sl)
        sign     = 1 if direction == "BUY" else -1
        tp3      = entry + sign * risk * 3
        all_vals = [v for _, v, _ in all_levels]

        if not _runway_clear(entry, direction, tp3, all_vals):
            logger.info("[Sweep][%s] %s %s — runway to TP3 blocked.", sym, direction, level_name)
            continue

        # Runner
        runner = _find_runner(entry, direction, risk, all_vals)
        reason = f"{level_name} swept — OB retest"

        sig = build_signal(direction, entry, sl, runner, reason, 0)
        if sig:
            sig["signal_type"]        = "zst_signal"
            sig["invalidation_price"] = ob_h if direction == "SELL" else ob_l
            sig["invalidation_side"]  = "above" if direction == "SELL" else "below"
            logger.info("[Sweep][%s] %s %s — signal built. Entry=%.2f SL=%.2f risk=%.1f pips",
                        sym, direction, level_name, entry, sl, risk_pips)
            return sig

    return None


# ── Public entry point ─────────────────────────────────────────────────────────

def check_session(symbol_config: dict, session: str,
                  levels_for_sym: dict) -> dict | None:
    """
    Scan session bars for a qualifying sweep → OB → CE signal.
    Returns signal dict or None.
    """
    sym    = symbol_config["symbol"]
    bars   = _get_session_bars(symbol_config, session)
    levels = _watched_levels(levels_for_sym, session)

    if len(bars) < 2:
        logger.info("[Sweep][%s][%s] Not enough 1H bars (%d).", sym, session, len(bars))
        return None
    if not levels:
        logger.info("[Sweep][%s][%s] No levels loaded.", sym, session)
        return None

    logger.info("[Sweep][%s][%s] Scanning %d bars across %d levels.",
                sym, session, len(bars), len(levels))

    for level_name, level_val, direction in levels:
        sig = _check_level(bars, symbol_config, level_name, level_val,
                           direction, levels)
        if sig:
            return sig

    return None
