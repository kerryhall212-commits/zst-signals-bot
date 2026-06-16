"""
Records US30 trades received via the TradingView webhook and tracks
TP1/TP2/TP3/breakeven/SL outcomes. Used by main.py (writes) and
price_monitor.py (reads/updates).
"""

import json
import os
from datetime import datetime, timezone

_DATA_DIR = os.getenv("DATA_DIR", os.path.dirname(__file__))
_LOG_FILE = os.path.join(_DATA_DIR, "trade_log.json")


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

    trade_id = (
        f"{sym_key}_{signal['direction']}_"
        f"{signal['entry']:.0f}_{now.strftime('%Y%m%d%H%M%S')}"
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
        "reason":          signal.get("reason", ""),
        "signal_time_utc": now.isoformat(),
        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False,
        "sl_hit":  False,
        "be_hit":  False,
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
    """Trades not yet closed (no SL hit, no breakeven exit, no TP3)."""
    data = _load()
    return [
        t for t in data["trades"]
        if not t["sl_hit"] and not t["be_hit"] and not t["tp3_hit"]
    ]
