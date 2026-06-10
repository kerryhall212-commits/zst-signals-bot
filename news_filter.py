"""
News blackout filter — blocks signals 30 mins before/after high-impact USD events.
Fetches from ForexFactory once per day and caches in memory.
"""

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import requests

logger = logging.getLogger(__name__)

BLACKOUT_MINS = 30

_cache_date   = None
_cache_events: list = []


def _fetch_todays_events() -> list:
    global _cache_date, _cache_events
    today = datetime.now(ZoneInfo("Europe/London")).date()
    if _cache_date == today:
        return _cache_events

    try:
        resp = requests.get(
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json", timeout=10
        )
        resp.raise_for_status()
        all_events = resp.json()
        _cache_events = [
            e for e in all_events
            if e.get("impact") == "High"
            and e.get("country") == "USD"
            and _event_london_date(e) == today
        ]
        logger.info("[NewsFilter] %d high-impact USD event(s) today.", len(_cache_events))
    except Exception as e:
        logger.warning("[NewsFilter] Fetch failed: %s", e)
        _cache_events = []

    _cache_date = today
    return _cache_events


def _event_london_date(event: dict):
    try:
        return (
            datetime.fromisoformat(event["date"])
            .astimezone(ZoneInfo("Europe/London"))
            .date()
        )
    except Exception:
        return None


def _event_utc(event: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(event["date"]).astimezone(timezone.utc)
    except Exception:
        return None


def is_news_blackout() -> bool:
    """Return True if now is within BLACKOUT_MINS of a high-impact USD event."""
    events = _fetch_todays_events()
    if not events:
        return False

    now = datetime.now(timezone.utc)
    for e in events:
        event_time = _event_utc(e)
        if event_time is None:
            continue
        diff_mins = (now - event_time).total_seconds() / 60
        if -BLACKOUT_MINS <= diff_mins <= BLACKOUT_MINS:
            title = e.get("title", "event")
            logger.info(
                "[NewsFilter] BLACKOUT — '%s' %.0f min %s.",
                title,
                abs(diff_mins),
                "away" if diff_mins < 0 else "ago",
            )
            return True

    return False
