"""
Builds and posts the Friday evening weekly review to Telegram.
Posted at 20:00 BST (19:00 UTC in summer / 20:00 UTC in winter).
"""

import logging
import re
import requests
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from trade_log import get_week_trades, _week_label
from signal_engine import _fetch
from telegram_sender import send_message
from config import SYMBOLS, WEEK_BARS, DAY_BARS

logger = logging.getLogger(__name__)


# ── Utilities ─────────────────────────────────────────────────────────────────

def _week_range_str() -> str:
    now    = datetime.now(timezone.utc).replace(tzinfo=None)
    monday = now - timedelta(days=now.weekday())
    friday = monday + timedelta(days=4)
    if monday.month == friday.month:
        return f"{monday.strftime('%b')} {monday.day} - {friday.day}"
    return f"{monday.strftime('%b')} {monday.day} - {friday.strftime('%b')} {friday.day}"


def _et_to_london(date_str: str, time_str: str) -> str:
    if not time_str or time_str.lower() in ("all day", "tentative"):
        return "TBD"
    try:
        m = re.match(r"(\d+):(\d+)(am|pm)", time_str.lower())
        if not m:
            return time_str
        h, mn, ampm = int(m.group(1)), int(m.group(2)), m.group(3).upper()
        dt_et  = datetime.strptime(f"{date_str} {h:02d}:{mn:02d} {ampm}", "%b %d, %Y %I:%M %p")
        dt_et  = dt_et.replace(tzinfo=ZoneInfo("America/New_York"))
        dt_ldn = dt_et.astimezone(ZoneInfo("Europe/London"))
        return dt_ldn.strftime(f"%H:%M {dt_ldn.strftime('%Z')}")
    except Exception:
        return time_str


# ── Stats ─────────────────────────────────────────────────────────────────────

def _achieved_rr(trade: dict) -> float:
    if trade["sl_hit"]:
        return -1.0
    if trade["tp3_hit"]:
        try:
            return float(trade["rr"].split(":")[1])
        except Exception:
            return 3.0
    if trade["tp2_hit"]:
        return 2.0
    if trade["tp1_hit"]:
        return 1.0
    return 0.0


def _compute_stats(trades: list) -> dict:
    empty = {
        "gold_count": 0, "us30_count": 0, "total": 0,
        "tp1": 0, "tp2": 0, "tp3": 0, "sl": 0, "running": 0,
        "win_rate": None, "avg_rr": None,
        "best_trade": None, "best_rr": -999, "best_session": None,
    }
    if not trades:
        return empty

    gold_count = sum(1 for t in trades if t["sym_key"] == "GOLD")
    us30_count = sum(1 for t in trades if t["sym_key"] == "US30")
    tp1     = sum(1 for t in trades if t["tp1_hit"])
    tp2     = sum(1 for t in trades if t["tp2_hit"])
    tp3     = sum(1 for t in trades if t["tp3_hit"])
    sl      = sum(1 for t in trades if t["sl_hit"])
    running = sum(1 for t in trades if not t["sl_hit"] and not t["tp3_hit"])

    closed = [t for t in trades if t["sl_hit"] or t["tp3_hit"]]
    wins   = sum(1 for t in closed if t["tp1_hit"])
    losses = sum(1 for t in closed if t["sl_hit"])
    win_rate = round(wins / (wins + losses) * 100) if (wins + losses) > 0 else None

    rr_vals = [_achieved_rr(t) for t in closed]
    avg_rr  = round(sum(rr_vals) / len(rr_vals), 1) if rr_vals else None

    best_trade, best_rr = None, -999
    for t in closed:
        rr = _achieved_rr(t)
        if rr > best_rr:
            best_rr, best_trade = rr, t

    session_wins: dict[str, int] = {}
    for t in trades:
        if t["tp1_hit"]:
            s = t.get("session", "Unknown")
            session_wins[s] = session_wins.get(s, 0) + 1
    best_session = max(session_wins, key=session_wins.get) if session_wins else None

    return {
        "gold_count": gold_count, "us30_count": us30_count, "total": len(trades),
        "tp1": tp1, "tp2": tp2, "tp3": tp3, "sl": sl, "running": running,
        "win_rate": win_rate, "avg_rr": avg_rr,
        "best_trade": best_trade, "best_rr": best_rr, "best_session": best_session,
    }


# ── Key levels ────────────────────────────────────────────────────────────────

def _fetch_key_levels() -> dict:
    result = {}
    for sym_key, cfg in SYMBOLS.items():
        try:
            weekly  = _fetch(cfg, "1week", WEEK_BARS)
            daily   = _fetch(cfg, "1day",  DAY_BARS)
            if len(weekly) < 2 or daily.empty:
                result[sym_key] = None
                continue
            pwh     = float(weekly.iloc[-2]["high"])
            pwl     = float(weekly.iloc[-2]["low"])
            current = float(daily.iloc[-1]["close"])
            result[sym_key] = {
                "display": cfg.get("display", sym_key),
                "pwh": pwh, "pwl": pwl, "current": current,
            }
        except Exception as e:
            logger.warning(f"[{sym_key}] Level fetch failed: {e}")
            result[sym_key] = None
    return result


def _get_bias(current: float, pwh: float, pwl: float) -> str:
    if current > pwh:
        return "Bullish"
    if current < pwl:
        return "Bearish"
    mid = (pwh + pwl) / 2
    return "Neutral (Bullish)" if current > mid else "Neutral (Bearish)"


# ── News ──────────────────────────────────────────────────────────────────────

def _fetch_news() -> list:
    for url in [
        "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
        "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    ]:
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 404:
                continue
            resp.raise_for_status()
            events = resp.json()
            usd = [e for e in events
                   if e.get("impact") == "High" and e.get("country") == "USD"]
            if usd:
                return usd[:5]
        except Exception as e:
            logger.warning(f"News fetch failed ({url}): {e}")
    return []


def _format_news_line(event: dict) -> str:
    title    = event.get("title", "—")
    date_str = event.get("date", "")
    try:
        dt_event  = datetime.fromisoformat(date_str)
        dt_london = dt_event.astimezone(ZoneInfo("Europe/London"))
        day       = dt_london.strftime("%a")
        time_bst  = dt_london.strftime(f"%H:%M {dt_london.strftime('%Z')}")
        return f"{day} {time_bst} — {title}"
    except Exception:
        return f"— {title}"


# ── Message builder ───────────────────────────────────────────────────────────

def build_review_message() -> str:
    now    = datetime.now(timezone.utc).replace(tzinfo=None)
    week   = _week_label(now)
    trades = get_week_trades(week)
    stats  = _compute_stats(trades)
    levels = _fetch_key_levels()
    news   = _fetch_news()

    bt      = stats.get("best_trade")
    best_rr = stats.get("best_rr", -999)

    L = [
        f"📊 <b>ZST WEEKLY REVIEW</b>",
        f"Week of {_week_range_str()}",
        "",
        f"📈 <b>Signals this week</b>",
        f"Gold: {stats['gold_count']}  |  US30: {stats['us30_count']}  |  Total: {stats['total']}",
        "",
        f"🎯 <b>Results</b>",
        f"✅ TP1: {stats['tp1']}   ✅ TP2: {stats['tp2']}   ✅ TP3: {stats['tp3']}",
        f"❌ SL: {stats['sl']}   ⏳ Running: {stats['running']}",
        "",
        f"📐 <b>Performance</b>",
        f"Win rate: {stats['win_rate']}%" if stats['win_rate'] is not None else "Win rate: —",
        f"Avg R:R: 1:{stats['avg_rr']}" if stats['avg_rr'] is not None else "Avg R:R: —",
        f"Best trade: {bt['display']} {bt['direction']} +{best_rr:.0f}R" if (bt and best_rr > 0) else "Best trade: —",
        f"Best session: {stats['best_session'] or '—'}",
    ]

    # Trade of the week
    if bt and best_rr > 0:
        if bt["sl_hit"]:
            result_label = "SL hit ❌"
        elif bt["tp3_hit"]:
            result_label = "TP3 hit ✅"
        elif bt["tp2_hit"]:
            result_label = "TP2 hit ✅"
        else:
            result_label = "TP1 hit ✅"

        L += [
            "",
            f"🏆 <b>Trade of the week</b>",
            f"{bt['display']} {bt['direction']} — {result_label}",
            f"Entry: {round(bt['entry']):,}   R:R: 1:{best_rr:.0f}",
            f"Setup: {bt['reason']}",
        ]

    # Key levels
    L += ["", "📅 <b>Next week prep</b>"]

    for sym_key in ("GOLD", "US30"):
        lv = levels.get(sym_key)
        display = SYMBOLS.get(sym_key, {}).get("display", sym_key)
        if lv and lv.get("pwh") and lv.get("pwl"):
            bias = _get_bias(lv["current"], lv["pwh"], lv["pwl"])
            L += [
                f"{display}  PWH: {round(lv['pwh']):,}  PWL: {round(lv['pwl']):,}  Bias: {bias}",
            ]
        else:
            L.append(f"{display}: levels unavailable")

    # News
    L += ["", "🗓 <b>Big news next week</b>"]
    if news:
        L += [_format_news_line(e) for e in news]
    else:
        L.append("Check ForexFactory for next week's events.")

    L += [
        "",
        "Rest this weekend. Come back focused Monday. 🙏",
        "Zero Stress. Always. 🤎",
        "ZST Insider 🔐",
    ]

    return "\n".join(L)


def post_weekly_review() -> None:
    logger.info("Posting weekly review...")
    try:
        msg = build_review_message()
        if send_message(msg):
            logger.info("Weekly review posted successfully.")
        else:
            logger.error("Weekly review Telegram send failed.")
    except Exception as e:
        logger.exception(f"Weekly review error: {e}")
