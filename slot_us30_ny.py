"""
US30 NY Session Signal Engine
Time: 13:30–21:00 BST (scanned until 16:00 BST by the global window)
Instrument: YM=F (Dow Jones E-mini Futures via yfinance)

Three setups — all require DXY confirmation:

  Setup 1 — PDH/PDL sweep and reclaim:
    Wick sweeps PDH or PDL, candle closes back inside → OB CE entry.

  Setup 2 — Asian range (00:00–07:00 BST) H/L sweep:
    Same as Setup 1 but the swept level is Asian High or Low.

  Setup 3 — Break and retest:
    Full body close (both open AND close) beyond a key level.
    Price pulls back to the level; level holds as S/R.
    Entry on the retest candle; SL 100 pts beyond the level.

SL: 100 points from entry (Setup 1/2) or 100 pts beyond level (Setup 3).
TPs: 1:1 / 1:2 / 1:3 + runners if key level beyond 1:4 R:R away.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from signal_engine import _fetch
from key_levels import get_all_levels
from slot_helpers import (
    effective_tz_offset, filter_bst_bars,
    build_signal, sweep_ob_entry, OB_INVALIDATED,
    fetch_dxy, dxy_confirms,
)
from config import M15_BARS, H1_BARS, DAY_BARS, WEEK_BARS

logger = logging.getLogger(__name__)

_SL_PTS      = 100  # fixed SL distance (Setup 1 & 2)
_RETEST_TOL  = 50   # pts: how close to level counts as "touched and retested"


def _asian_range(h1, tz_offset: int, bst_date):
    """High/low of 00:00–07:00 BST 1H bars, or (None, None)."""
    bars = filter_bst_bars(h1, tz_offset, bst_date, 0, 7 * 60)
    if not bars:
        return None, None
    return (
        max(float(b["high"]) for b in bars),
        min(float(b["low"])  for b in bars),
    )


def _full_body_break(bars: list, level: float, direction: str) -> int | None:
    """
    Return index of the most recent bar where BOTH open and close are
    beyond `level` in the break direction. None if no such bar found.
    direction "BUY"  = broke above (o > level, c > level)
    direction "SELL" = broke below (o < level, c < level)
    """
    idx = None
    for i, bar in enumerate(bars):
        o, c = float(bar["open"]), float(bar["close"])
        if direction == "BUY"  and o > level and c > level:
            idx = i
        elif direction == "SELL" and o < level and c < level:
            idx = i
    return idx


def _retest_holds(bars: list, level: float, direction: str,
                  break_idx: int, tol: float = _RETEST_TOL) -> bool:
    """
    After break_idx: did price pull back to within `tol` pts of level,
    then the final bar close back in the break direction (level holds)?
    """
    post = bars[break_idx + 1:]
    if len(post) < 2:
        return False

    retest = False
    for bar in post[:-1]:
        lo, hi = float(bar["low"]), float(bar["high"])
        if direction == "BUY"  and lo <= level + tol:
            retest = True
            break
        if direction == "SELL" and hi >= level - tol:
            retest = True
            break

    if not retest:
        return False

    last_close = float(post[-1]["close"])
    return (direction == "BUY" and last_close > level) or \
           (direction == "SELL" and last_close < level)


def generate_us30_ny_signal(symbol_config: dict) -> dict | None:
    sym       = symbol_config["symbol"]
    pip       = symbol_config.get("pip_size", 1.0)
    sl_pts    = symbol_config.get("intraday_sl_pips", _SL_PTS)
    tz_offset = effective_tz_offset(symbol_config)

    now_bst  = datetime.now(ZoneInfo("Europe/London"))
    bst_date = now_bst.date()
    mins     = now_bst.hour * 60 + now_bst.minute

    if not (13 * 60 + 30 <= mins < 21 * 60):
        logger.info("[US30] Outside NY session (13:30–21:00 BST) — skip.")
        return None

    try:
        m15    = _fetch(symbol_config, "15min", M15_BARS)
        h1     = _fetch(symbol_config, "1h",    H1_BARS)
        weekly = _fetch(symbol_config, "1week", WEEK_BARS)
        daily  = _fetch(symbol_config, "1day",  DAY_BARS)
    except Exception as e:
        logger.error("[US30] Fetch failed: %s", e)
        return None

    lv  = get_all_levels(weekly, daily, h1)
    pdh = lv.get("prev_day_high", {}).get("value")
    pdl = lv.get("prev_day_low",  {}).get("value")

    asian_high, asian_low = _asian_range(h1, tz_offset, bst_date)

    ny_bars = filter_bst_bars(m15, tz_offset, bst_date, 13 * 60 + 30, mins)
    if len(ny_bars) < 2:
        logger.info("[US30] Not enough NY bars (%d).", len(ny_bars))
        return None

    dxy = fetch_dxy()

    # ── Setups 1 + 2: Sweep → OB CE ────────────────────────────────────────────
    for level_name, level_val, direction in [
        ("PDH",        pdh,        "SELL"),
        ("PDL",        pdl,        "BUY"),
        ("Asian High", asian_high, "SELL"),
        ("Asian Low",  asian_low,  "BUY"),
    ]:
        if level_val is None:
            continue

        runner = pdl if direction == "SELL" else pdh
        result = sweep_ob_entry(ny_bars, level_val, direction, pip)

        if result == OB_INVALIDATED:
            logger.info("[US30] OB invalidated at %s.", level_name)
            return {"signal_type": "ob_invalidated", "slot": 5}

        if not isinstance(result, dict):
            continue

        if not dxy_confirms(direction, dxy):
            logger.info("[US30] DXY rejects %s sweep %s.", direction, level_name)
            continue

        entry = result["entry"]
        sign  = 1 if direction == "BUY" else -1
        sl    = entry - sign * sl_pts * pip
        reason = f"NY sweep {level_name} — OB CE entry"
        logger.info("[US30] Setup 1/2 %s at %s. Entry=%.0f SL=%.0f",
                    direction, level_name, entry, sl)

        sig = build_signal(direction, entry, sl, runner, reason, 5)
        if sig:
            sig["signal_type"] = "us30_ny"
            return sig

    # ── Setup 3: Break and retest ───────────────────────────────────────────────
    all_day = filter_bst_bars(m15, tz_offset, bst_date, 0, mins)

    for level_name, level_val, direction in [
        ("PDH",        pdh,        "BUY"),
        ("PDL",        pdl,        "SELL"),
        ("Asian High", asian_high, "BUY"),
        ("Asian Low",  asian_low,  "SELL"),
    ]:
        if level_val is None or len(all_day) < 3:
            continue

        brk_idx = _full_body_break(all_day, level_val, direction)
        if brk_idx is None:
            continue

        if not _retest_holds(all_day, level_val, direction, brk_idx):
            continue

        if not dxy_confirms(direction, dxy):
            logger.info("[US30] DXY rejects B&R %s %s.", direction, level_name)
            continue

        sign   = 1 if direction == "BUY" else -1
        entry  = float(all_day[-1]["close"])
        sl     = level_val - sign * sl_pts * pip
        runner = pdh if direction == "BUY" else pdl
        reason = f"Break and retest {level_name}"

        logger.info("[US30] Setup 3 B&R %s at %s. Entry=%.0f SL=%.0f",
                    direction, level_name, entry, sl)

        sig = build_signal(direction, entry, sl, runner, reason, 5)
        if sig:
            sig["signal_type"]        = "us30_ny"
            sig["invalidation_price"] = level_val
            sig["invalidation_side"]  = "below" if direction == "BUY" else "above"
            return sig

    logger.info("[US30] No NY setup. PDH=%s PDL=%s Asian=[%s, %s] NY bars=%d",
                f"{pdh:.0f}" if pdh else "—",
                f"{pdl:.0f}" if pdl else "—",
                f"{asian_high:.0f}" if asian_high else "—",
                f"{asian_low:.0f}"  if asian_low  else "—",
                len(ny_bars))
    return None
