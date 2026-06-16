"""
ZST Signals Bot — TradingView webhook relay + morning briefing.

Webhook:
  POST /webhook  ->  receives a TradingView alert (JSON), formats it, and
                     posts it to Telegram. US30 signals are additionally
                     logged and tracked for TP1/TP2/TP3/breakeven/SL hits.

Scheduled (BST, weekdays):
  21:00 -> compute PDH/PDL/PWH/PWL for GOLD + US30 -> save levels.json
  05:45 -> post morning briefing (once, file-locked)
  07:00 -> add Asian H/L to levels.json
  Hourly -> check open US30 trades for TP/SL/breakeven hits
"""

import glob as _glob
import logging
import os
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, request

from config import (
    SYMBOLS,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHANNEL_ID,
    TWELVEDATA_API_KEY,
    WEBHOOK_SECRET,
)
from formatter import format_webhook_signal
from telegram_sender import send_message
import trade_log
from price_monitor import check_open_trades
from morning_briefing import post_morning_briefing
import levels_store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

_REQUIRED_FIELDS = ("symbol", "direction", "entry", "sl", "tp1", "tp2", "tp3")


# ── Webhook ────────────────────────────────────────────────────────────────────

def _match_sym_key(symbol: str) -> str | None:
    """Map a TradingView ticker string to a tracked symbol key, if any."""
    s = symbol.upper()
    if "US30" in s or "DJI" in s or "DOW" in s:
        return "US30"
    return None


def _parse_signal(payload: dict) -> dict:
    missing = [f for f in _REQUIRED_FIELDS if payload.get(f) in (None, "")]
    if missing:
        raise ValueError(f"missing field(s): {', '.join(missing)}")

    direction = str(payload["direction"]).strip().upper()
    if direction not in ("BUY", "SELL"):
        raise ValueError(f"invalid direction: {payload['direction']!r}")

    try:
        entry = float(payload["entry"])
        sl    = float(payload["sl"])
        tp1   = float(payload["tp1"])
        tp2   = float(payload["tp2"])
        tp3   = float(payload["tp3"])
    except (TypeError, ValueError):
        raise ValueError("entry/sl/tp1/tp2/tp3 must be numeric")

    return {
        "symbol":    str(payload["symbol"]),
        "direction": direction,
        "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "reason": payload.get("reason", "—"),
    }


@app.route("/webhook", methods=["POST"])
def webhook():
    payload = request.get_json(silent=True) or {}

    if WEBHOOK_SECRET:
        token = request.args.get("token") or payload.get("token")
        if token != WEBHOOK_SECRET:
            logger.warning("Webhook rejected — invalid/missing token.")
            return jsonify({"status": "error", "message": "unauthorized"}), 401

    if not payload:
        return jsonify({"status": "error", "message": "invalid or missing JSON body"}), 400

    try:
        signal = _parse_signal(payload)
    except ValueError as e:
        logger.error("Webhook payload rejected: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 400

    if not send_message(format_webhook_signal(signal)):
        return jsonify({"status": "error", "message": "telegram send failed"}), 502

    sym_key = _match_sym_key(signal["symbol"])
    if sym_key == "US30":
        trade_log.record_signal(sym_key, SYMBOLS.get(sym_key, {}), signal)
        logger.info("[%s] Trade logged for TP/SL/breakeven tracking.", sym_key)

    logger.info("Webhook signal posted: %s %s @ %s",
                signal["direction"], signal["symbol"], signal["entry"])
    return jsonify({"status": "ok"}), 200


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


# ── Time helpers ───────────────────────────────────────────────────────────────

def _bst_now() -> datetime:
    return datetime.now(ZoneInfo("Europe/London"))


# ── Briefing file lock (survives restarts) ─────────────────────────────────────

def _briefing_lock_path(today: str) -> str:
    return f"briefing_sent_{today}.txt"


def _briefing_lock_exists(today: str) -> bool:
    return os.path.exists(_briefing_lock_path(today))


def _write_briefing_lock(today: str) -> None:
    with open(_briefing_lock_path(today), "w") as f:
        f.write(today)


def _clean_old_briefing_files(today: str) -> None:
    keep = _briefing_lock_path(today)
    for path in _glob.glob("briefing_sent_*.txt"):
        if not path.endswith(keep):
            try:
                os.remove(path)
                logger.info("Removed old briefing lock: %s", path)
            except Exception as e:
                logger.warning("Could not remove old briefing lock %s: %s", path, e)


# ── Scheduler thread: levels, briefing, US30 trade monitor ────────────────────

_levels_computed_day:    str = ""
_asian_levels_added_day: str = ""
_briefing_posted_day:    str = ""
_briefing_cleanup_day:   str = ""


def _scheduler_loop() -> None:
    global _levels_computed_day, _asian_levels_added_day, _briefing_posted_day, _briefing_cleanup_day

    logger.info("Scheduler thread started (levels / briefing / US30 monitor, polls every 5 min).")
    _heartbeat_hour = -1

    while True:
        now_bst   = _bst_now()
        today_bst = str(now_bst.date())
        m         = now_bst.hour * 60 + now_bst.minute

        if now_bst.hour != _heartbeat_hour:
            _heartbeat_hour = now_bst.hour
            logger.info("Heartbeat %s BST", now_bst.strftime("%H:%M"))

        # 0. Midnight cleanup of old briefing lock files
        if now_bst.hour == 0 and now_bst.minute < 10:
            if today_bst != _briefing_cleanup_day:
                _clean_old_briefing_files(today_bst)
                _briefing_cleanup_day = today_bst

        # 1. Compute levels at 21:00 BST (weekdays)
        if now_bst.weekday() < 5 and now_bst.hour == 21 and now_bst.minute < 10:
            if today_bst != _levels_computed_day:
                logger.info("Computing daily levels...")
                levels_store.compute_and_save()
                _levels_computed_day = today_bst

        # 2. Morning briefing at 05:45 BST (once, file-locked)
        if now_bst.weekday() < 5 and m >= 5 * 60 + 45 and today_bst != _briefing_posted_day:
            if _briefing_lock_exists(today_bst):
                _briefing_posted_day = today_bst
                logger.info("Briefing lock found — already posted today.")
            elif post_morning_briefing():
                _write_briefing_lock(today_bst)
                _briefing_posted_day = today_bst

        # 3. Add Asian H/L at 07:00 BST
        if now_bst.weekday() < 5 and now_bst.hour == 7 and now_bst.minute < 10:
            if today_bst != _asian_levels_added_day:
                logger.info("Adding Asian H/L to levels...")
                levels_store.add_asian_levels()
                _asian_levels_added_day = today_bst

        # 4. Hourly US30 TP/SL/breakeven monitor
        if now_bst.minute < 10:
            check_open_trades()

        time.sleep(300)


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    logger.info("ZST Signals Bot starting (webhook relay + morning briefing).")
    logger.info(
        "Env — TWELVEDATA_API_KEY: %s | TELEGRAM_BOT_TOKEN: %s | TELEGRAM_CHANNEL_ID: %s | WEBHOOK_SECRET: %s",
        f"set ({TWELVEDATA_API_KEY[:4]}...)" if TWELVEDATA_API_KEY else "NOT SET",
        "set" if TELEGRAM_BOT_TOKEN else "NOT SET",
        TELEGRAM_CHANNEL_ID or "NOT SET",
        "set" if WEBHOOK_SECRET else "NOT SET (webhook is open to anyone with the URL)",
    )

    threading.Thread(target=_scheduler_loop, daemon=True).start()

    port = int(os.getenv("PORT", 8080))
    logger.info("Starting webhook server on 0.0.0.0:%d", port)
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
