"""
Records every signal sent by the bot and tracks TP/SL outcomes.
Used by price_monitor.py (updates) and weekly_review.py (reads).
"""

import json
import os
from datetime import datetime, timezone

_DATA_DIR = os.getenv("DATA_DIR", os.path.dirname(__file__))
_LOG_FILE = os.path.join(_DATA_DIR, "trade_log.json")


def _week_label(dt=None) -> str:
    if dt is None:
        dt = datetime.now(timezone.utc).replace(tzinfo=None)
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _load() -> dict:
    if os.path.exists(_LOG_FILE):
        try:
            with open(_LOG_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"trades": []}


def _save(data: dict) -> None:
    with open(_LOG_FILE, "w") as f:
        json.dump(data, f, indent=2)


def record_signal(sym_key: str, symbol_config: dict, signal: dict) -> str:
    """Append a new trade record. Returns the trade ID."""
    data = _load()
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    hour = now.hour
    if 7 <= hour < 12:
        session = "London"
    elif 12 <= hour < 20:
        session = "NY"
    else:
        session = "Off-hours"

    trade_id = (
        f"{sym_key}_{signal['direction']}_"
        f"{signal['entry']:.0f}_{now.strftime('%Y%m%d%H%M')}"
    )

    trade = {
        "id":              trade_id,
        "sym_key":         sym_key,
        "display":         symbol_config.get("display", sym_key),
        "direction":       signal["direction"],
        "entry":           signal["entry"],
        "sl":              signal["sl"],
        "tp1":             signal["tp1"],
        "tp2":             signal["tp2"],
        "tp3":             signal["tp3"],
        "tp4":             signal.get("tp4"),
        "tp5":             signal.get("tp5"),
        "rr":              signal.get("rr", "—"),
        "priority":        signal.get("priority", 3),
        "slot":            signal.get("slot", 0),
        "reason":          signal.get("reason", ""),
        "signal_time_utc": now.isoformat(),
        "week":            _week_label(now),
        "session":         session,
        "tp1_hit":         False,
        "tp2_hit":         False,
        "tp3_hit":         False,
        "tp4_hit":         False,
        "tp5_hit":         False,
        "sl_hit":          False,
    }

    data["trades"].append(trade)
    _save(data)
    return trade_id


def update_trade(trade_id: str, **kwargs) -> None:
    data = _load()
    for t in data["trades"]:
        if t["id"] == trade_id:
            t.update(kwargs)
            break
    _save(data)


def get_open_trades() -> list:
    """All trades not yet fully closed (no SL and no TP3)."""
    data = _load()
    return [t for t in data["trades"] if not t["sl_hit"] and not t["tp3_hit"]]


def get_week_trades(week_label: str = None) -> list:
    if week_label is None:
        week_label = _week_label()
    data = _load()
    return [t for t in data["trades"] if t.get("week") == week_label]
