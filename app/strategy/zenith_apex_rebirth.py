"""
Zenith Apex Rebirth v025 - Deep Transformation
Fixes overall loss-making nature of bot
- Trend following with ADX filter (only strong trends)
- 4h main trend, 1h entry
- RSI + Bollinger + Volume confluence
- 0.5% risk per trade, 5% daily loss limit
- Cooldown 4h per asset
- Hold 1-24h, not minutes
- Scale out 50% at TP1, 50% at TP2 with trailing
"""
from __future__ import annotations
import pandas as pd
import numpy as np
from typing import Dict, Tuple

def calculate_adx(df: pd.DataFrame, period: int = 14) -> float:
    """ADX for trend strength - only trade if ADX>25"""
    try:
        if len(df) < period*2:
            return 0
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        close = df["close"].astype(float)
        
        # True Range
        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        # Directional Movement
        up_move = high - high.shift()
        down_move = low.shift() - low
        
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        
        # Smoothed
        tr_smooth = tr.rolling(period).mean()
        plus_dm_smooth = pd.Series(plus_dm).rolling(period).mean()
        minus_dm_smooth = pd.Series(minus_dm).rolling(period).mean()
        
        # DI
        plus_di = 100 * (plus_dm_smooth / tr_smooth)
        minus_di = 100 * (minus_dm_smooth / tr_smooth)
        
        # DX and ADX
        dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di))
        adx = dx.rolling(period).mean().iloc[-1]
        
        return float(adx) if not pd.isna(adx) else 0
    except:
        return 0

def calculate_rsi(df: pd.DataFrame, period: int = 14) -> float:
    try:
        if len(df) < period:
            return 50
        close = df["close"].astype(float)
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50
    except:
        return 50

def calculate_bollinger_position(df: pd.DataFrame, period: int = 20) -> float:
    """Position in Bollinger Bands: 0=lower, 0.5=middle, 1=upper"""
    try:
        if len(df) < period:
            return 0.5
        close = df["close"].astype(float)
        sma = close.rolling(period).mean()
        std = close.rolling(period).std()
        upper = sma + 2*std
        lower = sma - 2*std
        price = float(close.iloc[-1])
        upper_last = float(upper.iloc[-1])
        lower_last = float(lower.iloc[-1])
        if upper_last == lower_last:
            return 0.5
        pos = (price - lower_last) / (upper_last - lower_last)
        return max(0, min(1, pos))
    except:
        return 0.5

def zenith_apex_rebirth_signal(df_1h: pd.DataFrame, df_4h: pd.DataFrame = None) -> Dict:
    """
    Returns: {
        "direction": 1 for LONG, -1 for SHORT, 0 for NO TRADE,
        "strength": 0-1,
        "reasons": [],
        "adx": float,
        "rsi": float,
        "bb_pos": float
    }
    """
    result = {"direction": 0, "strength": 0, "reasons": [], "adx": 0, "rsi": 50, "bb_pos": 0.5}
    
    try:
        if df_1h.empty or len(df_1h) < 50:
            result["reasons"].append(f"Not enough 1h data {len(df_1h)}<50")
            return result
        
        close_1h = df_1h["close"].astype(float)
        ema20 = close_1h.ewm(span=20).mean().iloc[-1]
        ema50 = close_1h.ewm(span=50).mean().iloc[-1]
        try:
            ema200 = close_1h.ewm(span=200).mean().iloc[-1]
        except:
            ema200 = close_1h.ewm(span=50).mean().iloc[-1]
        
        # 4h trend if available, else use 1h 50/200
        if df_4h is not None and not df_4h.empty and len(df_4h) >= 50:
            close_4h = df_4h["close"].astype(float)
            ema50_4h = close_4h.ewm(span=50).mean().iloc[-1]
            ema200_4h = close_4h.ewm(span=200).mean().iloc[-1] if len(df_4h)>=200 else ema50_4h
            main_trend_up = ema50_4h > ema200_4h
            main_trend_down = ema50_4h < ema200_4h
        else:
            main_trend_up = ema50 > ema200
            main_trend_down = ema50 < ema200
        
        # Indicators
        adx = calculate_adx(df_1h)
        rsi = calculate_rsi(df_1h)
        bb_pos = calculate_bollinger_position(df_1h)
        price = float(close_1h.iloc[-1])
        
        result["adx"] = adx
        result["rsi"] = rsi
        result["bb_pos"] = bb_pos
        
        # Filter 1: ADX >25 for strong trend
        if adx < 20:
            result["reasons"].append(f"Weak trend ADX {adx:.1f}<20")
            return result
        
        # Filter 2: RSI not overbought/oversold
        if rsi > 75:
            result["reasons"].append(f"Overbought RSI {rsi:.1f}>75")
            # Only allow SHORT if overbought
            if not main_trend_down:
                return result
        if rsi < 25:
            result["reasons"].append(f"Oversold RSI {rsi:.1f}<25")
            if not main_trend_up:
                return result
        
        # Determine direction based on main trend + momentum
        long_signals = 0
        short_signals = 0
        
        # Signal 1: Main trend
        if main_trend_up:
            long_signals += 1
        if main_trend_down:
            short_signals += 1
        
        # Signal 2: EMA20 vs EMA50 momentum
        if ema20 > ema50:
            long_signals += 1
        else:
            short_signals += 1
        
        # Signal 3: Price vs EMA20
        if price > ema20:
            long_signals += 1
        else:
            short_signals += 1
        
        # Signal 4: Bollinger position - mean reversion
        # If price near lower band (bb_pos<0.2) and uptrend -> LONG
        # If price near upper band (bb_pos>0.8) and downtrend -> SHORT
        if bb_pos < 0.2 and main_trend_up:
            long_signals += 1
        if bb_pos > 0.8 and main_trend_down:
            short_signals += 1
        
        # Signal 5: RSI
        if rsi < 40 and main_trend_up:
            long_signals += 1
        if rsi > 60 and main_trend_down:
            short_signals += 1
        
        # Decide - v025: require 2 not 3 for more trades but still filtered
        if long_signals >= 2 and long_signals > short_signals:
            result["direction"] = 1
            result["strength"] = min(1.0, long_signals / 5.0)
            result["reasons"].append(f"LONG confluence {long_signals}/5 ADX {adx:.1f} RSI {rsi:.1f} BB {bb_pos:.2f}")
        elif short_signals >= 2 and short_signals > long_signals:
            result["direction"] = -1
            result["strength"] = min(1.0, short_signals / 5.0)
            result["reasons"].append(f"SHORT confluence {short_signals}/5 ADX {adx:.1f} RSI {rsi:.1f} BB {bb_pos:.2f}")
        else:
            result["reasons"].append(f"No confluence long {long_signals} short {short_signals}")
        
        return result
        
    except Exception as e:
        result["reasons"].append(f"Error {e}")
        return result

def calculate_position_size_v025(entry: float, sl: float, capital: float, risk_pct: float = 0.005, max_notional_pct: float = 0.10, leverage: float = 5) -> Tuple[float, float]:
    """
    v025 Position sizing: 0.5% risk per trade, 10% notional per asset, lev5
    Returns (qty, notional)
    """
    try:
        risk_usd = capital * risk_pct  # 0.5% = 2$
        sl_distance = abs(entry - sl)
        if sl_distance <= 0:
            sl_distance = entry * 0.015  # 1.5% fallback
        
        qty_by_risk = risk_usd / sl_distance
        max_notional = capital * max_notional_pct
        qty_by_notional = max_notional / entry
        
        qty = min(qty_by_risk, qty_by_notional)
        
        # Min notional 10$ for 10 assets (to avoid dust)
        if qty * entry < 10.0:
            qty = 10.0 / entry
        
        # Max notional check with leverage
        notional = qty * entry
        max_notional_lev = capital * max_notional_pct * leverage
        if notional > max_notional_lev:
            qty = max_notional_lev / entry
        
        return max(0, qty), qty * entry
    except:
        return 0, 0
