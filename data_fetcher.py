import os
import logging
import requests
import pandas as pd

BASE_URL = "https://api.twelvedata.com"
logger = logging.getLogger(__name__)

# Try slash format first (canonical), then no-slash as fallback
_SYMBOL_FORMATS = {
    "XAU/USD": ["XAU/USD", "XAUUSD"],
    "XAUUSD":  ["XAUUSD", "XAU/USD"],
}


def fetch_ohlcv(symbol: str, interval: str = "1h", outputsize: int = 100) -> pd.DataFrame:
    api_key = os.getenv("TWELVEDATA_API_KEY")
    if not api_key:
        raise RuntimeError(
            "TWELVEDATA_API_KEY environment variable is not set. "
            "Add it in the Railway dashboard under Variables."
        )

    attempts = _SYMBOL_FORMATS.get(symbol, [symbol])
    last_err = None

    for sym in attempts:
        params = {
            "symbol": sym,
            "interval": interval,
            "outputsize": outputsize,
            "apikey": api_key,
            "format": "JSON",
        }
        response = requests.get(f"{BASE_URL}/time_series", params=params, timeout=15)
        response.raise_for_status()
        data = response.json()

        if "values" in data:
            if sym != symbol:
                logger.info("Symbol %r failed; succeeded with %r", symbol, sym)
            df = pd.DataFrame(data["values"])
            df = df.rename(columns={"datetime": "time"})
            for col in ("open", "high", "low", "close"):
                df[col] = pd.to_numeric(df[col])
            df = df.sort_values("time").reset_index(drop=True)
            return df

        last_err = data.get("message", "Unknown error")
        logger.warning("Symbol %r [%s] API error: %s", sym, interval, last_err)

    raise ValueError(f"API error for {symbol} [{interval}] (tried {attempts}): {last_err}")
