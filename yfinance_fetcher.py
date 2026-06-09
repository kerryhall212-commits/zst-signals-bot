"""
Yahoo Finance data fetcher.
Used for instruments not available on TwelveData free tier (e.g. ^DJI).
4H data is built by fetching 1H bars and resampling to 4H aligned to UTC midnight.
"""

import pandas as pd
import yfinance as yf

# (interval) -> (yfinance period, yfinance interval)
_FETCH_PARAMS = {
    "1h":    ("60d",  "1h"),
    "4h":    ("60d",  "1h"),   # fetched as 1H then resampled
    "1day":  ("6mo",  "1d"),
    "1week": ("2y",   "1wk"),
}


def fetch_ohlcv_yf(symbol: str, interval: str, outputsize: int = 100) -> pd.DataFrame:
    period, yf_interval = _FETCH_PARAMS.get(interval, ("60d", "1h"))

    raw = yf.download(symbol, period=period, interval=yf_interval,
                      progress=False, auto_adjust=True)
    if raw.empty:
        raise ValueError(f"yfinance returned no data for {symbol} [{interval}]")

    # Flatten MultiIndex columns (yfinance ≥ 0.2 returns these for single tickers too)
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    raw.columns = [c.lower() for c in raw.columns]

    # Convert index to UTC, drop timezone info so downstream code handles plain strings
    if hasattr(raw.index, "tz") and raw.index.tz is not None:
        raw.index = raw.index.tz_convert("UTC").tz_localize(None)
    elif raw.index.tz is None and hasattr(raw.index, "tz_localize"):
        pass  # already naive — assume UTC

    if interval == "4h":
        raw = raw.resample("4h", origin="start_day").agg({
            "open":   "first",
            "high":   "max",
            "low":    "min",
            "close":  "last",
            "volume": "sum",
        }).dropna(subset=["close"])

    df = raw.reset_index()
    df.columns = [str(c).lower() for c in df.columns]

    # Rename date/datetime index column to 'time'
    for col in df.columns:
        if col in ("date", "datetime"):
            df = df.rename(columns={col: "time"})
            break

    df["time"] = df["time"].astype(str)
    df = df[["time", "open", "high", "low", "close"]]
    df = df.sort_values("time").tail(outputsize).reset_index(drop=True)
    return df
