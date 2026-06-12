"""
Tracks per-session signal sends.
1 signal per session (asian/london/ny) per symbol, 3 total per day.
Persists across restarts via JSON file.
"""

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

_DATA_DIR = os.getenv("DATA_DIR", os.path.dirname(__file__) or ".")
_FILE     = os.path.join(_DATA_DIR, "session_counter.json")
MAX_DAILY = 3


def _today_bst() -> str:
    return datetime.now(ZoneInfo("Europe/London")).strftime("%Y-%m-%d")


def _load() -> dict:
    try:
        with open(_FILE) as f:
            data = json.load(f)
            if data.get("date") != _today_bst():
                return {"date": _today_bst(), "fired": {}, "count": 0}
            return data
    except FileNotFoundError:
        return {"date": _today_bst(), "fired": {}, "count": 0}


def _save(data: dict) -> None:
    with open(_FILE, "w") as f:
        json.dump(data, f)


def get_count() -> int:
    return _load().get("count", 0)


def is_limit_reached() -> bool:
    return get_count() >= MAX_DAILY


def is_session_fired(sym_key: str, session: str) -> bool:
    d = _load()
    return d["fired"].get(sym_key, {}).get(session, False)


def mark_session_fired(sym_key: str, session: str) -> int:
    """Record session fired and increment total. Returns new total count."""
    d = _load()
    if sym_key not in d["fired"]:
        d["fired"][sym_key] = {}
    d["fired"][sym_key][session] = True
    d["count"] = d.get("count", 0) + 1
    _save(d)
    return d["count"]
