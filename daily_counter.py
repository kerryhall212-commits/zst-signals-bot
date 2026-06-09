"""
Tracks how many signals have been sent today (UTC).
Resets automatically at 00:00 UTC each day.
"""

import json
import os
from datetime import datetime, timezone

_COUNTER_FILE = os.path.join(os.path.dirname(__file__), "daily_counter.json")
MAX_DAILY = 2


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load() -> dict:
    if os.path.exists(_COUNTER_FILE):
        try:
            with open(_COUNTER_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"date": "", "count": 0, "limit_notified": False}


def _save(data: dict) -> None:
    with open(_COUNTER_FILE, "w") as f:
        json.dump(data, f)


def _fresh(data: dict) -> dict:
    """Reset counter if date has rolled over."""
    if data["date"] != _today_utc():
        return {"date": _today_utc(), "count": 0, "limit_notified": False}
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
