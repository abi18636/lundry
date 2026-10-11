"""
v017 LAYER 2: Volatility Targeting
Position size based on volatility, not fixed confidence
Risk constant, size variable (opposite of current)
"""
from __future__ import annotations

import logging
import numpy as np
import pandas as pd

log = logging.getLogger("volatility")

def volatility_targeted_size(df, per_cap, lev_cap, conf, target_vol_annual=0.40):
    """
    Adjust position size so dollar risk (volatility) stays constant,
    not size constant and risk variable
    """
    try:
        close = df["close"].astype(float)
        rets = close.pct_change().dropna()
        if len(rets) < 48:
            realized_vol = 0.5  # Conservative default
        else:
            realized_vol = float(rets.tail(168).std() * np.sqrt(24 * 365))
            realized_vol = max(0.15, min(realized_vol, 2.0))  # Clamp logical
        
        # Higher market vol -> smaller position (inverse)
        vol_scalar = target_vol_annual / realized_vol
        vol_scalar = min(vol_scalar, 1.5)  # Cap to prevent excessive leverage in calm market
        
        notional = per_cap * lev_cap * conf * vol_scalar
        return notional, realized_vol, vol_scalar
    except Exception as exc:
        log.warning(f"vol targeting failed: {exc}")
        notional = per_cap * lev_cap * conf
        return notional, 0.5, 1.0


def calculate_atr(df, n=14):
    """Calculate ATR"""
    try:
        high = df["high"].astype(float) if "high" in df.columns else df["close"].astype(float)
        low = df["low"].astype(float) if "low" in df.columns else df["close"].astype(float)
        close = df["close"].astype(float)
        tr = pd.concat([(high-low), (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
        atr = float(tr.rolling(n).mean().iloc[-1])
        return atr if np.isfinite(atr) else 0.0
    except:
        return 0.0
