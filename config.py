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
        "pip_label": "pips",
        "min_sweep_pips": 5,
        "sl_pips": 12,            # legacy — OB entry uses intraday_sl_pips
        "sl_min_from_entry": 10,
        "sl_max_from_entry": 15,
        "td_tz_offset": 3,
        # OB CE entry — fixed SL + TP structure
        "intraday_sl_pips":  15,  # fixed SL: always 15 pips from entry
        "entry_range_pips":   5,  # display ±5 pips entry zone
        "intraday_tp1_pips": 15,  # 1:1
        "intraday_tp2_pips": 30,  # 1:2
        "intraday_tp3_pips": 45,  # 1:3
        "daily_near_pips":   50,
    },
    "US30": {
        "data_source": "yfinance",
        "yf_symbol":   "YM=F",
        "symbol":      "YM=F",
        "ticker":      "US30",
        "display":     "US30",
        "signal_title": "ZST US30 SIGNAL",
        "decimals":    0,
        "pip_size":    1.0,
        "pip_label":   "points",
        "min_sweep_pips": 20,
        "sl_pips":     100,
        "sl_min_from_entry": 80,
        "sl_max_from_entry": 120,
        "td_tz_offset": -4,
        # OB CE entry — fixed SL + TP structure
        "intraday_sl_pips":  100,  # fixed SL: 100 points from entry
        "entry_range_pips":   20,  # display ±20 pts entry zone
        "intraday_tp1_pips": 100,  # 1:1
        "intraday_tp2_pips": 200,  # 1:2
        "intraday_tp3_pips": 300,  # 1:3
        "daily_near_pips":   200,
    },
}

# Run interval and data windows
SCAN_INTERVAL_HOURS = 1
M5_BARS   = 200  # 5M candles for Slot 3 London ORB
M15_BARS  = 200  # 15M candles for London/NY sweep slots
M30_BARS  = 100  # 30M candles for intraday engine
H1_BARS   = 200  # sweep detection & Asian session
H4_BARS   = 100  # MSS + OB
DAY_BARS  = 30   # previous day levels
WEEK_BARS = 10   # previous week levels

# Only post when a valid SMC signal is found
SKIP_DUPLICATE_SIGNALS = True
