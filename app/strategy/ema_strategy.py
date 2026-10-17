"""
Strategy Module - EMA Strategy
Independent: Only EMA logic, no execution or reporting dependency
Fix2: Candle Close Gating, Fix3: Sizing Guard
"""
from __future__ import annotations
import pandas as pd

def ema_signal(df: pd.DataFrame) -> int:
    """EMA 20/50 signal: 1=buy, -1=sell, 0=hold"""
    if df.empty or len(df) < 50:
        return 0
    try:
        close = df["close"]
        ema20 = close.ewm(span=20).mean().iloc[-1]
        ema50 = close.ewm(span=50).mean().iloc[-1]
        if ema20 > ema50:
            return 1
        elif ema20 < ema50:
            return -1
        return 0
    except:
        return 0

def calculate_desired_qty(signal: int, mark_price: float, capital_usd: float, n_assets: int, lev_cap: float) -> float:
    """Fix3: Sizing Guard"""
    if signal == 0 or mark_price <= 0:
        return 0.0
    allocated_usd = (capital_usd / n_assets) * lev_cap
    qty = (allocated_usd / mark_price) * signal
    return qty
