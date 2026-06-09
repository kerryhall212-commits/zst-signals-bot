from datetime import datetime, timezone, timedelta
import pandas as pd


def _parse_dt(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["dt"] = pd.to_datetime(df["time"])
    return df


def prev_week_levels(weekly_df: pd.DataFrame) -> dict:
    if len(weekly_df) < 2:
        return {}
    row = weekly_df.iloc[-2]
    return {
        "prev_week_high": {"value": float(row["high"]), "type": "high", "label": "Previous Week High"},
        "prev_week_low":  {"value": float(row["low"]),  "type": "low",  "label": "Previous Week Low"},
    }


def prev_day_levels(daily_df: pd.DataFrame) -> dict:
    if len(daily_df) < 2:
        return {}
    row = daily_df.iloc[-2]
    return {
        "prev_day_high": {"value": float(row["high"]), "type": "high", "label": "Previous Day High"},
        "prev_day_low":  {"value": float(row["low"]),  "type": "low",  "label": "Previous Day Low"},
    }


def asian_session_levels(h1_df: pd.DataFrame) -> dict:
    df = _parse_dt(h1_df)
    now_utc = datetime.now(timezone.utc)

    for delta_days in (0, 1):
        target = (now_utc - timedelta(days=delta_days)).date()
        session = df[(df["dt"].dt.date == target) & (df["dt"].dt.hour < 7)]
        if not session.empty:
            return {
                "asian_high": {"value": float(session["high"].max()), "type": "high", "label": "Asian Session High"},
                "asian_low":  {"value": float(session["low"].min()),  "type": "low",  "label": "Asian Session Low"},
            }
    return {}


def get_all_levels(weekly_df: pd.DataFrame, daily_df: pd.DataFrame, h1_df: pd.DataFrame) -> dict:
    levels = {}
    levels.update(prev_week_levels(weekly_df))
    levels.update(prev_day_levels(daily_df))
    levels.update(asian_session_levels(h1_df))
    return levels
