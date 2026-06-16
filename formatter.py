"""Webhook signal and trade outcome formatters."""


def fmt(price: float, decimals: int = 0) -> str:
    if decimals == 0:
        return f"{round(price):,}"
    return f"{price:,.{decimals}f}"


def _fmt_price(value) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if v == int(v):
        return f"{int(v):,}"
    return f"{v:,.2f}"


def format_webhook_signal(signal: dict) -> str:
    direction = signal["direction"]
    symbol    = signal["symbol"]
    side      = "above" if direction == "SELL" else "below"

    lines = [
        "🎯 <b>ZST SIGNAL</b>",
        "",
        f"<b>{direction}</b> | <b>{symbol}</b>",
        f"Entry: <code>{_fmt_price(signal['entry'])}</code>",
        f"SL: <code>{_fmt_price(signal['sl'])}</code>",
        f"TP1: <code>{_fmt_price(signal['tp1'])}</code>",
        f"TP2: <code>{_fmt_price(signal['tp2'])}</code>",
        f"TP3: <code>{_fmt_price(signal['tp3'])}</code>",
        "",
        f"Reason: {signal['reason']}",
        f"Invalidation: 1H close {side} <code>{_fmt_price(signal['sl'])}</code>",
        "",
        "ZST Insider 🔐",
    ]
    return "\n".join(lines)


def format_tp_notification(trade: dict, level: str) -> str:
    display = trade.get("display", trade.get("sym_key", ""))
    dir_    = trade["direction"]
    entry   = fmt(trade["entry"])
    header  = f"{display} {dir_}"

    if level == "tp1":
        return "\n".join([
            f"🎯 <b>TP1 HIT — {header}</b>",
            "",
            f"Entry: <code>{entry}</code>",
            f"TP1: <code>{fmt(trade['tp1'])}</code> ✅",
            "",
            "Move SL to entry 🔒",
            "ZST Insider 🔐",
        ])

    if level == "tp2":
        return "\n".join([
            f"🔥 <b>TP2 HIT — {header}</b>",
            "",
            f"Entry: <code>{entry}</code>",
            f"TP2: <code>{fmt(trade['tp2'])}</code> ✅",
            "",
            "Move SL to TP1 🔒",
            "ZST Insider 🔐",
        ])

    if level == "tp3":
        return "\n".join([
            f"🏆 <b>TP3 HIT — {header}</b>",
            "",
            f"Entry: <code>{entry}</code>",
            f"TP3: <code>{fmt(trade['tp3'])}</code> ✅",
            "",
            "Full target! 🙏",
            "ZST Insider 🔐",
        ])

    if level == "be":
        return "\n".join([
            f"⚖️ <b>BREAKEVEN — {header}</b>",
            "",
            f"Entry: <code>{entry}</code>",
            "Stopped at breakeven after TP1 — no loss 🔒",
            "",
            "Next setup loading 💪",
            "ZST Insider 🔐",
        ])

    # sl
    return "\n".join([
        f"❌ <b>STOPPED OUT — {header}</b>",
        "",
        f"Entry: <code>{entry}</code>",
        f"SL: <code>{fmt(trade['sl'])}</code>",
        "",
        "Next setup loading 💪",
        "ZST Insider 🔐",
    ])
