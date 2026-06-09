import requests
import pandas as pd
from config import TWELVEDATA_API_KEY

BASE_URL = "https://api.twelvedata.com"


def fetch_ohlcv(symbol: str, interval: str = "1h", outputsize: int = 100) -> pd.DataFrame:
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": outputsize,
        "apikey": TWELVEDATA_API_KEY,
        "format": "JSON",
    }
    response = requests.get(f"{BASE_URL}/time_series", params=params, timeout=15)
    response.raise_for_status()
    data = response.json()

    if "values" not in data:
        raise ValueError(f"API error for {symbol} [{interval}]: {data.get('message', 'Unknown')}")

    df = pd.DataFrame(data["values"])
    df = df.rename(columns={"datetime": "time"})
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col])
    df = df.sort_values("time").reset_index(drop=True)
    return df
