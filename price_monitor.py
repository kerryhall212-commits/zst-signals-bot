"""
Checks open trades against recent 4H candles to detect TP/SL hits.
Called at every 4H close by main.py.
"""

import logging
from datetime import datetime

from trade_log import get_open_trades, update_trade
from signal_engine import _fetch, _td_to_utc_close
from formatter import format_tp_notification
from telegram_sender import send_message
from config import SYMBOLS, H4_BARS

logger = logging.getLogger(__name__)


def check_open_trades() -> None:
    trades = get_open_trades()
    if not trades:
        return

    logger.info(f"Price monitor: checking {len(trades)} open trade(s).")

    for trade in trades:
        sym_key = trade["sym_key"]
        cfg = SYMBOLS.get(sym_key)
        if cfg is None:
            continue

        try:
            h4 = _fetch(cfg, "4h", H4_BARS)
        except Exception as e:
            logger.warning(f"[{sym_key}] Price monitor fetch failed: {e}")
            continue

        signal_time = datetime.fromisoformat(trade["signal_time_utc"])
        tz_offset   = cfg.get("td_tz_offset", 0)
        direction   = trade["direction"]
        sl          = trade["sl"]
        tp1, tp2, tp3 = trade["tp1"], trade["tp2"], trade["tp3"]

        # Snapshot state before scan so we know what changed
        was_tp1 = trade["tp1_hit"]
        was_tp2 = trade["tp2_hit"]
        was_tp3 = trade["tp3_hit"]
        was_sl  = trade["sl_hit"]

        tp1_hit, tp2_hit, tp3_hit, sl_hit = was_tp1, was_tp2, was_tp3, was_sl
        changed = False

        for _, row in h4.iterrows():
            candle_close = _td_to_utc_close(str(row["time"]), tz_offset)
            if candle_close <= signal_time:
                continue

            high = float(row["high"])
            low  = float(row["low"])

            if direction == "SELL":
                if high >= sl:
                    sl_hit = True; changed = True; break
                if not tp1_hit and low <= tp1:
                    tp1_hit = True; changed = True
                if tp1_hit and not tp2_hit and low <= tp2:
                    tp2_hit = True; changed = True
                if tp2_hit and not tp3_hit and low <= tp3:
                    tp3_hit = True; changed = True; break
            else:  # BUY
                if low <= sl:
                    sl_hit = True; changed = True; break
                if not tp1_hit and high >= tp1:
                    tp1_hit = True; changed = True
                if tp1_hit and not tp2_hit and high >= tp2:
                    tp2_hit = True; changed = True
                if tp2_hit and not tp3_hit and high >= tp3:
                    tp3_hit = True; changed = True; break

        if changed:
            update_trade(
                trade["id"],
                tp1_hit=tp1_hit, tp2_hit=tp2_hit,
                tp3_hit=tp3_hit, sl_hit=sl_hit,
            )

            # Send the notification for the highest new level hit
            if sl_hit and not was_sl:
                logger.info(f"[{sym_key}] {trade['id']}: SL hit ❌")
                send_message(format_tp_notification(trade, "sl"))
            elif tp3_hit and not was_tp3:
                logger.info(f"[{sym_key}] {trade['id']}: TP3 hit ✅")
                send_message(format_tp_notification(trade, "tp3"))
            elif tp2_hit and not was_tp2:
                logger.info(f"[{sym_key}] {trade['id']}: TP2 hit ✅")
                send_message(format_tp_notification(trade, "tp2"))
            elif tp1_hit and not was_tp1:
                logger.info(f"[{sym_key}] {trade['id']}: TP1 hit ✅")
                send_message(format_tp_notification(trade, "tp1"))
