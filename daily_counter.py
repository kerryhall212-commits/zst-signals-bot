"""
Tracks how many signals have been sent today (BST).
Resets automatically at 00:00 BST each day.
Tracks per-slot firings so each slot fires at most once per day.
"""

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

_DATA_DIR     = os.getenv("DATA_DIR", os.path.dirname(__file__))
_COUNTER_FILE = os.path.join(_DATA_DIR, "daily_counter.json")
MAX_DAILY = 6


def _today_bst() -> str:
    return datetime.now(ZoneInfo("Europe/London")).strftime("%Y-%m-%d")


def _load() -> dict:
    if os.path.exists(_COUNTER_FILE):
        try:
            with open(_COUNTER_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"date": "", "count": 0, "limit_notified": False, "slots_fired": []}


def _save(data: dict) -> None:
    with open(_COUNTER_FILE, "w") as f:
        json.dump(data, f)


def _fresh(data: dict) -> dict:
    """Reset counter if BST date has rolled over."""
    today = _today_bst()
    if data["date"] != today:
        return {"date": today, "count": 0, "limit_notified": False, "slots_fired": []}
    if "slots_fired" not in data:
        data["slots_fired"] = []
    return data


def get_count() -> int:
    return _fresh(_load())["count"]


def is_limit_reached() -> bool:
    return get_count() >= MAX_DAILY


def is_limit_notified() -> bool:
    return _fresh(_load())["limit_notified"]


def increment() -> tuple[int, bool]:
    """Increment counter. Returns (new_count, just_hit_limit)."""
    d = _fresh(_load())
    d["count"] += 1
    just_hit = d["count"] == MAX_DAILY
    _save(d)
    return d["count"], just_hit


def mark_limit_notified() -> None:
    d = _fresh(_load())
    d["limit_notified"] = True
    _save(d)


def is_slot_fired(slot: int) -> bool:
    """Return True if this slot already fired today."""
    return slot in _fresh(_load())["slots_fired"]


def mark_slot_fired(slot: int) -> None:
    """Record that slot fired today."""
    d = _fresh(_load())
    if slot not in d["slots_fired"]:
        d["slots_fired"].append(slot)
    _save(d)
