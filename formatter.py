def fmt(price: float, decimals: int = 0) -> str:
    return f"{round(price):,}"


def format_tp_notification(trade: dict, level: str) -> str:
    """
    level: "tp1", "tp2", "tp3", or "sl"
    """
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
            "Move SL to entry. Trade is risk-free 🔐",
            "ZST Insider",
        ])

    if level == "tp2":
        return "\n".join([
            f"💰 <b>TP2 HIT — {header}</b>",
            "",
            f"Entry: <code>{entry}</code>",
            f"TP2: <code>{fmt(trade['tp2'])}</code> ✅",
            "",
            "Let TP3 run 🚀",
            "ZST Insider",
        ])

    if level == "tp3":
        return "\n".join([
            f"🏆 <b>TP3 HIT — {header}</b>",
            "",
            f"Entry: <code>{entry}</code>",
            f"TP3: <code>{fmt(trade['tp3'])}</code> ✅",
            f"R:R: {trade.get('rr', '—')}",
            "",
            "Full target. Textbook execution 🙌",
            "ZST Insider 🔐",
        ])

    # sl
    return "\n".join([
        f"🛡 <b>STOPPED OUT — {header}</b>",
        "",
        f"Entry: <code>{entry}</code>",
        f"SL: <code>{fmt(trade['sl'])}</code> ❌",
        "",
        "Risk managed. Next setup loading...",
        "ZST Insider",
    ])


def format_smc_message(symbol_config: dict, signal: dict) -> str:
    d     = 0  # all prices shown as whole numbers
    title = symbol_config["signal_title"]
    tick  = symbol_config["ticker"]
    dir_  = signal["direction"]
    qual  = signal.get("quality", "VALID")
    side  = signal["invalidation_side"]   # "above" or "below"

    lines = [
        f"🚨 <b>{title}</b>",
    ]

    if qual == "STRONG":
        lines.append("⭐️ <b>STRONG SIGNAL</b>")

    lines += [
        "",
        f"<b>{dir_}</b> | <b>{tick}</b>",
        f"Entry: <code>{fmt(signal['entry'], d)}</code>",
        f"SL: <code>{fmt(signal['sl'], d)}</code>",
        f"TP1: <code>{fmt(signal['tp1'], d)}</code>",
        f"TP2: <code>{fmt(signal['tp2'], d)}</code>",
        f"TP3: <code>{fmt(signal['tp3'], d)}</code>",
        "",
        f"R:R: {signal.get('rr', '—')}",
        f"Reason: {signal['reason']}",
        f"Invalidation: 1H close {side} <code>{fmt(signal['invalidation_price'], d)}</code>",
        "",
        "ZST Insider 🔐",
    ]

    return "\n".join(lines)
