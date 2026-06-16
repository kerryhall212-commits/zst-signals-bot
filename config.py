import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
TWELVEDATA_API_KEY  = os.getenv("TWELVEDATA_API_KEY")
WEBHOOK_SECRET      = os.getenv("WEBHOOK_SECRET")

# Used for morning briefing levels (PDH/PDL/PWH/PWL/ASH/ASL) and
# US30 TP/SL/breakeven trade monitoring.
SYMBOLS = {
    "GOLD": {
        "symbol":  "XAU/USD",
        "display": "GOLD",
    },
    "US30": {
        "data_source": "yfinance",
        "yf_symbol":   "^DJI",
        "display":     "US30",
    },
}

H1_BARS   = 200  # Asian session levels
H4_BARS   = 100  # US30 TP/SL/breakeven monitoring
DAY_BARS  = 30   # previous day levels
WEEK_BARS = 10   # previous week levels
