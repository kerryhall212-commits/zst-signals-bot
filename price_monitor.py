"""
Checks open US30 trades against recent 4H candles to detect
TP1/TP2/TP3, breakeven and SL events. Called hourly by main.py.

Stop progression once a trade is open:
  No TP yet   -> stop is the original SL.
  After TP1   -> stop moves to entry (breakeven).
  After TP2   -> only TP3 is tracked (full target).
"""

import logging
from datetime import datetime

from trade_log import get_open_trades, update_trade
from data_fetcher import fetch_candles, td_to_utc_close
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
            h4 = fetch_candles(cfg, "4h", H4_BARS)
        except Exception as e:
            logger.warning("[%s] Price monitor fetch failed: %s", sym_key, e)
            continue

        signal_time = datetime.fromisoformat(trade["signal_time_utc"])
        tz_offset   = cfg.get("td_tz_offset", 0)
        direction   = trade["direction"]
        entry       = trade["entry"]
        sl          = trade["sl"]
        tp1, tp2, tp3 = trade["tp1"], trade["tp2"], trade["tp3"]

        was_tp1, was_tp2, was_tp3 = trade["tp1_hit"], trade["tp2_hit"], trade["tp3_hit"]
        was_sl, was_be            = trade["sl_hit"], trade["be_hit"]

        tp1_hit, tp2_hit, tp3_hit = was_tp1, was_tp2, was_tp3
        sl_hit, be_hit            = was_sl, was_be
        changed = False

        for _, row in h4.iterrows():
            candle_close = td_to_utc_close(str(row["time"]), tz_offset)
            if candle_close <= signal_time:
                continue

            high = float(row["high"])
            low  = float(row["low"])

            if direction == "SELL":
                if not tp1_hit:
                    if high >= sl:
                        sl_hit = True; changed = True; break
                    if low <= tp1:
                        tp1_hit = True; changed = True
                elif not tp2_hit:
                    if high >= entry:
                        be_hit = True; changed = True; break
                    if low <= tp2:
                        tp2_hit = True; changed = True
                elif not tp3_hit:
                    if low <= tp3:
                        tp3_hit = True; changed = True; break
            else:  # BUY
                if not tp1_hit:
                    if low <= sl:
                        sl_hit = True; changed = True; break
                    if high >= tp1:
                        tp1_hit = True; changed = True
                elif not tp2_hit:
                    if low <= entry:
                        be_hit = True; changed = True; break
                    if high >= tp2:
                        tp2_hit = True; changed = True
                elif not tp3_hit:
                    if high >= tp3:
                        tp3_hit = True; changed = True; break

        if not changed:
            continue

        update_trade(
            trade["id"],
            tp1_hit=tp1_hit, tp2_hit=tp2_hit, tp3_hit=tp3_hit,
            sl_hit=sl_hit, be_hit=be_hit,
        )

        if sl_hit and not was_sl:
            logger.info("[%s] SL hit ❌", sym_key)
            send_message(format_tp_notification(trade, "sl"))
        elif be_hit and not was_be:
            logger.info("[%s] Breakeven hit ⚖️", sym_key)
            send_message(format_tp_notification(trade, "be"))
        elif tp3_hit and not was_tp3:
            logger.info("[%s] TP3 hit 🏆", sym_key)
            send_message(format_tp_notification(trade, "tp3"))
        elif tp2_hit and not was_tp2:
            logger.info("[%s] TP2 hit 🔥", sym_key)
            send_message(format_tp_notification(trade, "tp2"))
        elif tp1_hit and not was_tp1:
            logger.info("[%s] TP1 hit 🎯", sym_key)
            send_message(format_tp_notification(trade, "tp1"))
