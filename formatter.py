"""Signal and TP/SL notification formatters."""


def fmt(price: float, decimals: int = 0) -> str:
    if decimals == 0:
        return f"{round(price):,}"
    return f"{price:,.{decimals}f}"


def format_signal(symbol_config: dict, signal: dict) -> str:
    tick = symbol_config["ticker"]
    dir_ = signal["direction"]
    side = signal["invalidation_side"]
    inv  = signal["invalidation_price"]

    lines = [
        "🎯 <b>ZST SIGNAL</b>",
        "",
        f"<b>{dir_}</b> | <b>{tick}</b>",
        f"Entry: <code>{fmt(signal['entry'])}</code>",
        f"SL: <code>{fmt(signal['sl'])}</code>",
        f"TP1: <code>{fmt(signal['tp1'])}</code>",
        f"TP2: <code>{fmt(signal['tp2'])}</code>",
        f"TP3: <code>{fmt(signal['tp3'])}</code>",
    ]
    if "tp4" in signal:
        lines.append(f"TP4: <code>{fmt(signal['tp4'])}</code>")
    if "tp5" in signal:
        lines.append(f"TP5: <code>{fmt(signal['tp5'])}</code>")

    lines += [
        "",
        f"Reason: {signal['reason']}",
        f"Invalidation: 1H close {side} <code>{fmt(inv)}</code>",
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

    if level in ("tp4", "tp5"):
        tp_val = trade.get(level)
        return "\n".join([
            f"💰 <b>RUNNER HIT — {header}</b>",
            "",
            f"Entry: <code>{entry}</code>",
            f"{level.upper()}: <code>{fmt(tp_val) if tp_val else '—'}</code> ✅",
            "",
            "God is good 🙏",
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
