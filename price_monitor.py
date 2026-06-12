"""
Checks open trades against recent 4H candles to detect TP/SL hits.
Called hourly by main.py.
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

    logger.info("Price monitor: checking %d open trade(s).", len(trades))

    for trade in trades:
        sym_key = trade["sym_key"]
        cfg = SYMBOLS.get(sym_key)
        if cfg is None:
            continue

        try:
            h4 = _fetch(cfg, "4h", H4_BARS)
        except Exception as e:
            logger.warning("[%s] Price monitor fetch failed: %s", sym_key, e)
            continue

        signal_time = datetime.fromisoformat(trade["signal_time_utc"])
        tz_offset   = cfg.get("td_tz_offset", 0)
        direction   = trade["direction"]
        sl          = trade["sl"]
        tp1, tp2, tp3 = trade["tp1"], trade["tp2"], trade["tp3"]
        tp4 = trade.get("tp4")
        tp5 = trade.get("tp5")

        was_tp1 = trade["tp1_hit"]
        was_tp2 = trade["tp2_hit"]
        was_tp3 = trade["tp3_hit"]
        was_tp4 = trade.get("tp4_hit", False)
        was_tp5 = trade.get("tp5_hit", False)
        was_sl  = trade["sl_hit"]

        tp1_hit = was_tp1
        tp2_hit = was_tp2
        tp3_hit = was_tp3
        tp4_hit = was_tp4
        tp5_hit = was_tp5
        sl_hit  = was_sl
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
                    tp3_hit = True; changed = True
                if tp3_hit and tp4 and not tp4_hit and low <= tp4:
                    tp4_hit = True; changed = True
                if tp4_hit and tp5 and not tp5_hit and low <= tp5:
                    tp5_hit = True; changed = True; break
            else:  # BUY
                if low <= sl:
                    sl_hit = True; changed = True; break
                if not tp1_hit and high >= tp1:
                    tp1_hit = True; changed = True
                if tp1_hit and not tp2_hit and high >= tp2:
                    tp2_hit = True; changed = True
                if tp2_hit and not tp3_hit and high >= tp3:
                    tp3_hit = True; changed = True
                if tp3_hit and tp4 and not tp4_hit and high >= tp4:
                    tp4_hit = True; changed = True
                if tp4_hit and tp5 and not tp5_hit and high >= tp5:
                    tp5_hit = True; changed = True; break

        if changed:
            update_trade(
                trade["id"],
                tp1_hit=tp1_hit, tp2_hit=tp2_hit,
                tp3_hit=tp3_hit, tp4_hit=tp4_hit,
                tp5_hit=tp5_hit, sl_hit=sl_hit,
            )

            if sl_hit and not was_sl:
                logger.info("[%s] SL hit ❌", sym_key)
                send_message(format_tp_notification(trade, "sl"))
            elif tp5_hit and not was_tp5:
                logger.info("[%s] TP5 hit 💰", sym_key)
                send_message(format_tp_notification(trade, "tp5"))
            elif tp4_hit and not was_tp4:
                logger.info("[%s] TP4 hit 💰", sym_key)
                send_message(format_tp_notification(trade, "tp4"))
            elif tp3_hit and not was_tp3:
                logger.info("[%s] TP3 hit 🏆", sym_key)
                send_message(format_tp_notification(trade, "tp3"))
            elif tp2_hit and not was_tp2:
                logger.info("[%s] TP2 hit 🔥", sym_key)
                send_message(format_tp_notification(trade, "tp2"))
            elif tp1_hit and not was_tp1:
                logger.info("[%s] TP1 hit 🎯", sym_key)
                send_message(format_tp_notification(trade, "tp1"))
