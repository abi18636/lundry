"""Optional, advisory-only Kronos forecast. Never submits orders.

The model lives outside the trading service to avoid changing the free-tier
runtime. Callers provide an async forecast function; failures stay visible
and do not alter the trading decision or the entry safety lock.
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable
import pandas as pd


@dataclass(frozen=True)
class Forecast:
    asset: str
    candle_start: str
    last_close: float
    forecast_close: float
    predicted_return: float


def closed_hourly_context(df: pd.DataFrame, now: pd.Timestamp, lookback: int = 400) -> pd.DataFrame:
    if lookback < 2 or not isinstance(df, pd.DataFrame):
        raise ValueError('invalid data or lookback')
    required = ['dt', 'open', 'high', 'low', 'close', 'volume']
    if not set(required).issubset(df.columns):
        raise ValueError('missing OHLCV fields')
    if now.tzinfo is None:
        raise ValueError('now must be timezone-aware')
    data = df[required].copy()
    data['dt'] = pd.to_datetime(data['dt'], utc=True, errors='raise')
    data = data.sort_values('dt').reset_index(drop=True)
    if data['dt'].duplicated().any():
        raise ValueError('duplicate timestamps')
    data = data[data['dt'] + pd.Timedelta(hours=1) <= now.tz_convert('UTC')].tail(lookback).reset_index(drop=True)
    if len(data) != lookback:
        raise ValueError('insufficient closed candles')
    if not (data['dt'].diff().dropna() == pd.Timedelta(hours=1)).all():
        raise ValueError('missing or irregular candles')
    if now.tz_convert('UTC') - (data['dt'].iloc[-1] + pd.Timedelta(hours=1)) > pd.Timedelta(hours=2):
        raise ValueError('stale candles')
    for col in required[1:]:
        data[col] = pd.to_numeric(data[col], errors='raise')
    if not data[required[1:]].apply(lambda s: s.map(lambda x: pd.notna(x) and abs(x) != float('inf'))).all().all():
        raise ValueError('non-finite data')
    if (data[['open','high','low','close']] <= 0).any().any() or (data['volume'] < 0).any():
        raise ValueError('invalid prices or volume')
    if ((data['high'] < data[['open','close','low']].max(axis=1)) | (data['low'] > data[['open','close','high']].min(axis=1))).any():
        raise ValueError('invalid OHLC envelope')
    return data


async def shadow_forecast(asset: str, candles: pd.DataFrame, now: pd.Timestamp,
                          forecast_fn: Callable[[pd.DataFrame, pd.Series], Awaitable[pd.DataFrame]],
                          horizon: int = 4, timeout: float = 20.0) -> Forecast:
    """Calculate a forecast only; must not be used as an execution instruction."""
    if horizon < 1 or horizon > 24 or timeout <= 0:
        raise ValueError('invalid horizon or timeout')
    data = closed_hourly_context(candles, now)
    future = pd.Series(pd.date_range(data['dt'].iloc[-1] + pd.Timedelta(hours=1), periods=horizon, freq='h'))
    predicted = await asyncio.wait_for(forecast_fn(data.copy(), future), timeout)
    if not isinstance(predicted, pd.DataFrame) or len(predicted) != horizon or 'close' not in predicted:
        raise ValueError('invalid prediction')
    close = float(predicted['close'].iloc[-1])
    if not (0 < close < float('inf')):
        raise ValueError('invalid predicted close')
    last = float(data['close'].iloc[-1])
    return Forecast(asset=asset, candle_start=data['dt'].iloc[-1].isoformat(), last_close=last,
                    forecast_close=close, predicted_return=close / last - 1)
