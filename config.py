import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY")

SYMBOLS = {
    "GOLD": {
        "symbol": "XAU/USD",
        "ticker": "XAU/USD",
        "display": "GOLD",
        "signal_title": "ZST GOLD SIGNAL",
        "decimals": 2,
        "pip_size": 1.0,          # 1 pip = $1 on Gold
        "min_sweep_pips": 5,
        "sl_pips": 12,            # SL = swept level + 12 pips (default)
        "sl_min_from_entry": 10,  # extend SL if closer than 10 pips to entry
        "sl_max_from_entry": 15,  # reject setup if SL is more than 15 pips from entry
        "td_tz_offset": 3,
        # Intraday momentum fixed pip levels
        "intraday_sl_pips":  15,
        "intraday_tp1_pips": 45,
        "intraday_tp2_pips": 60,
        "intraday_tp3_pips": 100,
    },
    "US30": {
        "data_source": "yfinance",
        "yf_symbol":   "^DJI",
        "symbol":      "^DJI",
        "ticker":      "US30",
        "display":     "US30",
        "signal_title": "ZST US30 SIGNAL",
        "decimals":    0,
        "pip_size":    1.0,
        "min_sweep_pips": 20,
        "sl_pips":     100,           # SL = swept level + 100 points (default)
        "sl_min_from_entry": 80,      # extend SL if closer than 80 pts to entry
        "sl_max_from_entry": 120,     # reject setup if SL is more than 120 pts from entry
        "td_tz_offset": -4,
        # Intraday momentum fixed point levels
        "intraday_sl_pips":  50,
        "intraday_tp1_pips": 150,
        "intraday_tp2_pips": 200,
        "intraday_tp3_pips": 300,
    },
}

# Run interval and data windows
SCAN_INTERVAL_HOURS = 1
M30_BARS  = 100  # 30M candles for intraday engine
H1_BARS   = 200  # sweep detection & Asian session
H4_BARS   = 100  # MSS + OB
DAY_BARS  = 30   # previous day levels
WEEK_BARS = 10   # previous week levels

# Only post when a valid SMC signal is found
SKIP_DUPLICATE_SIGNALS = True
