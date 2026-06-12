"""
Saves and loads daily watched levels (PDH, PDL, PWH, PWL, ASH, ASL).
Written at 21:00 BST. Asian H/L appended at 07:00 BST.
"""

import json
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from signal_engine import _fetch
from key_levels import prev_day_levels, prev_week_levels, asian_session_levels
from config import SYMBOLS, H1_BARS, DAY_BARS, WEEK_BARS

logger = logging.getLogger(__name__)

_DATA_DIR   = os.getenv("DATA_DIR", os.path.dirname(__file__) or ".")
LEVELS_FILE = os.path.join(_DATA_DIR, "levels.json")


def compute_and_save() -> dict:
    """Fetch PDH/PDL/PWH/PWL for all symbols and write to levels.json."""
    levels = {}
    for sym_key, cfg in SYMBOLS.items():
        try:
            daily  = _fetch(cfg, "1day",  DAY_BARS)
            weekly = _fetch(cfg, "1week", WEEK_BARS)
            pd_lv  = prev_day_levels(daily)
            pw_lv  = prev_week_levels(weekly)
            levels[sym_key] = {
                "pdh": pd_lv.get("prev_day_high",  {}).get("value"),
                "pdl": pd_lv.get("prev_day_low",   {}).get("value"),
                "pwh": pw_lv.get("prev_week_high", {}).get("value"),
                "pwl": pw_lv.get("prev_week_low",  {}).get("value"),
                "ash": None,
                "asl": None,
            }
            logger.info("[Levels][%s] PDH=%.0f  PDL=%.0f  PWH=%.0f  PWL=%.0f",
                        sym_key,
                        levels[sym_key]["pdh"] or 0,
                        levels[sym_key]["pdl"] or 0,
                        levels[sym_key]["pwh"] or 0,
                        levels[sym_key]["pwl"] or 0)
        except Exception as e:
            logger.error("[Levels][%s] Failed: %s", sym_key, e)
            levels[sym_key] = {
                "pdh": None, "pdl": None,
                "pwh": None, "pwl": None,
                "ash": None, "asl": None,
            }

    with open(LEVELS_FILE, "w") as f:
        json.dump(levels, f)
    return levels


def add_asian_levels() -> None:
    """Compute Asian H/L and add to existing levels.json (called at 07:00 BST)."""
    try:
        with open(LEVELS_FILE) as f:
            levels = json.load(f)
    except FileNotFoundError:
        levels = {}

    for sym_key, cfg in SYMBOLS.items():
        try:
            h1  = _fetch(cfg, "1h", H1_BARS)
            asl = asian_session_levels(h1)
            if sym_key not in levels:
                levels[sym_key] = {}
            levels[sym_key]["ash"] = asl.get("asian_high", {}).get("value")
            levels[sym_key]["asl"] = asl.get("asian_low",  {}).get("value")
            logger.info("[Levels][%s] ASH=%.0f  ASL=%.0f",
                        sym_key,
                        levels[sym_key]["ash"] or 0,
                        levels[sym_key]["asl"] or 0)
        except Exception as e:
            logger.error("[Levels][%s] Asian H/L failed: %s", sym_key, e)

    with open(LEVELS_FILE, "w") as f:
        json.dump(levels, f)


def load() -> dict:
    try:
        with open(LEVELS_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning("levels.json not found — levels not yet computed.")
        return {}
