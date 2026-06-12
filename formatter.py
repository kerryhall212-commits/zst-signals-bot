def fmt(price: float, decimals: int = 0) -> str:
    return f"{round(price):,}"


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


def format_swing_signal(symbol_config: dict, signal: dict) -> str:
    d           = 0
    tick        = symbol_config["ticker"]
    dir_        = signal["direction"]
    side        = signal["invalidation_side"]
    pip         = symbol_config.get("pip_size", 1.0)
    pip_label   = symbol_config.get("pip_label", "pips")
    entry_range = symbol_config.get("entry_range_pips", 5)
    risk_pips   = round(abs(signal["sl"] - signal["entry"]) / pip)

    lines = [
        "🎯 <b>ZST SWING SIGNAL</b>",
        "",
        f"<b>{dir_}</b> | <b>{tick}</b>",
        f"Entry: <code>{fmt(signal['entry'], d)}</code> (±{entry_range} {pip_label})",
        f"SL: <code>{fmt(signal['sl'], d)}</code> ({risk_pips} {pip_label})",
        f"TP1: <code>{fmt(signal['tp1'], d)}</code> (1:1)",
        f"TP2: <code>{fmt(signal['tp2'], d)}</code> (1:2)",
        f"TP3: <code>{fmt(signal['tp3'], d)}</code> (1:3)",
    ]
    if "tp4" in signal:
        lines.append(f"TP4: <code>{fmt(signal['tp4'], d)}</code> (1:5) — runner")
    if "tp5" in signal:
        lines.append(f"TP5: <code>{fmt(signal['tp5'], d)}</code> (1:6) — runner")

    lines += [
        "",
        f"Reason: {signal['reason']}",
        f"Invalidation: Close {side} <code>{fmt(signal['invalidation_price'], d)}</code>",
        "",
        "ZST Insider 🔐",
    ]
    return "\n".join(lines)


def format_intraday_signal(symbol_config: dict, signal: dict) -> str:
    d    = 0
    tick = symbol_config["ticker"]
    dir_ = signal["direction"]
    side = signal["invalidation_side"]

    inv_label = signal.get("inv_label")
    if inv_label:
        inv_line = f"Invalidation: {inv_label} <code>{fmt(signal['invalidation_price'], d)}</code>"
    else:
        inv_line = f"Invalidation: 30M close {side} <code>{fmt(signal['invalidation_price'], d)}</code>"

    return "\n".join([
        "⚡ <b>ZST INTRADAY SIGNAL</b>",
        "",
        f"<b>{dir_}</b> | <b>{tick}</b>",
        f"Entry: <code>{fmt(signal['entry'], d)}</code>",
        f"SL: <code>{fmt(signal['sl'], d)}</code>",
        f"TP1: <code>{fmt(signal['tp1'], d)}</code>",
        f"TP2: <code>{fmt(signal['tp2'], d)}</code>",
        f"TP3: <code>{fmt(signal['tp3'], d)}</code>",
        "",
        f"Reason: {signal['reason']}",
        inv_line,
        "",
        "ZST Insider 🔐",
    ])
