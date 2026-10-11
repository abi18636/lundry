"""
v016 COMPANY FIXES - All 8 recommendations
Fix #1: Allow real close/reduce/reversal (engine)
Fix #4: FLAT when weak signal - MOM_THRESHOLD 0.8%
Fix #5: Regime detection ADX
Fix #6: Adaptive Donchian 72h->168h in low vol
Fix #8: Mean reversion Bollinger Bands 20% weight
Fix #3: SL/TP handled in engine, not here
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

log = logging.getLogger("sleeves")

MOM_THRESHOLD = 0.008  # 0.8% min momentum for valid signal

@dataclass
class SleeveResult:
    sleeve_id: str
    title: str
    capital: float
    weight: float = 1.0
    per_asset: Dict[str, dict] = field(default_factory=dict)
    notes: str = ""

DEFAULT_WEIGHTS = {
    "diversified_5": 0.15,
    "zenith_apex": 0.15,
    "almasi_primary": 0.15,
    "inst_v3_stable": 0.15,
    "inst_v3_primary": 0.10,
    "zenith_endurance": 0.10,
    "mean_reversion": 0.20,  # v016: Real mean-reversion for ranging
}

SLEEVE_BUILDERS = {}

def _asset_order(frames: Dict[str, pd.DataFrame]) -> List[str]:
    return sorted(frames.keys())

def _atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    tr = pd.concat([(high-low), (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def _detect_regime(df: pd.DataFrame, lookback: int = 100) -> str:
    """
    v016 NEW Fix #5: Regime detection with ADX
    trending if ADX>25, ranging if ADX<20, transitional else
    """
    try:
        close = df["close"].astype(float)
        high = df["high"].astype(float) if "high" in df.columns else close
        low = df["low"].astype(float) if "low" in df.columns else close
        
        if len(close) < lookback:
            return "unknown"
        
        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
        minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
        
        tr = pd.concat([(high-low), (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
        atr14 = tr.rolling(14).mean()
        
        plus_di = 100 * (plus_dm.rolling(14).mean() / atr14.replace(0, 1e-9))
        minus_di = 100 * (minus_dm.rolling(14).mean() / atr14.replace(0, 1e-9))
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-9)
        adx = dx.rolling(14).mean().iloc[-1]
        
        if not np.isfinite(adx):
            return "unknown"
        
        if adx > 25:
            return "trending"
        elif adx < 20:
            return "ranging"
        else:
            return "transitional"
    except Exception:
        return "unknown"


# v016 FIX #4: All sleeves now allow FLAT when signal weak, not forced 5/5
# But we still guarantee at least some signals, just not forced random direction

def sleeve_diversified_5(frames, capital, lev_cap=2.0):
    res = SleeveResult("diversified_5", "diversified balanced v016 flat-aware", capital, 0.15)
    assets = _asset_order(frames)
    per_cap = capital / len(assets) if assets else capital
    
    for a in assets:
        df = frames[a]
        close = df["close"].astype(float)
        high = df["high"].astype(float) if "high" in df.columns else close
        low = df["low"].astype(float) if "low" in df.columns else close
        px = float(close.iloc[-1])
        
        ema20 = close.ewm(span=20).mean().iloc[-1]
        ema50 = close.ewm(span=50).mean().iloc[-1]
        rsi = _calc_rsi(close, 14)
        mom24 = (close.iloc[-1] / close.iloc[-25] - 1) if len(close) > 25 else 0.0
        
        side, reason, conf = 0.0, "No signal", 0.0
        
        if px > ema20 and ema20 > ema50 and rsi < 70:
            side, reason, conf = 1.0, f"Trend LONG EMA {ema20:.0f}>{ema50:.0f} RSI {rsi:.0f}", 0.7
        elif px < ema20 and ema20 < ema50 and rsi > 30:
            side, reason, conf = -1.0, f"Trend SHORT EMA {ema20:.0f}<{ema50:.0f} RSI {rsi:.0f}", 0.7
        elif rsi < 30:
            side, reason, conf = 1.0, f"Oversold RSI {rsi:.0f}", 0.6
        elif rsi > 70:
            side, reason, conf = -1.0, f"Overbought RSI {rsi:.0f}", 0.6
        else:
            # v016 FIX #4: Only trade if momentum strong enough, else FLAT
            if abs(mom24) > MOM_THRESHOLD:
                side = 1.0 if mom24 > 0 else -1.0
                reason = f"Weak-trend mom{mom24*100:.1f}%"
                conf = 0.4
            else:
                side = 0.0
                reason = f"No clear signal, flat (mom={mom24*100:.2f}%)"
                conf = 0.0
        
        notional = per_cap * lev_cap * conf
        target_coin = (notional / px) * side if side != 0 and px > 0 else 0.0
        
        res.per_asset[a] = dict(
            side=side, price=px, target_coin=target_coin, notional_usd=abs(notional),
            dt=str(df["dt"].iloc[-1]) if "dt" in df.columns else "",
            detail={"reason": reason, "confidence": conf, "ema20": ema20, "ema50": ema50, "rsi": rsi, "mom24": mom24},
            eligibility={"diversified": True}
        )
    res.notes = "v016 flat-aware diversified"
    return res

def _calc_rsi(close: pd.Series, n: int = 14) -> float:
    try:
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0).rolling(n).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(n).mean()
        rs = gain / loss.replace(0, 1e-9)
        rsi = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1]) if len(rsi) > 0 and np.isfinite(rsi.iloc[-1]) else 50.0
    except:
        return 50.0

def sleeve_zenith_apex(frames, capital, lev_cap=2.0):
    res = SleeveResult("zenith_apex", "apex compress+impulse v016 flat-aware", capital, 0.15)
    assets = _asset_order(frames)
    per_cap = capital / len(assets) if assets else capital
    
    for a in assets:
        df = frames[a]
        close = df["close"].astype(float)
        high = df["high"].astype(float) if "high" in df.columns else close
        low = df["low"].astype(float) if "low" in df.columns else close
        px = float(close.iloc[-1])
        
        atr14 = _atr(high, low, close, 14).iloc[-1]
        atr_pct = (atr14 / px * 100) if px > 0 and np.isfinite(atr14) else 10.0
        mom24 = (close.iloc[-1] / close.iloc[-25] - 1) if len(close) > 25 else 0.0
        ema20 = close.ewm(span=20).mean().iloc[-1]
        
        side, reason, conf = 0.0, "No signal", 0.0
        
        # Compress: low ATR + breakout
        if atr_pct < 2.0 and abs(mom24) > 0.01:
            side = 1.0 if mom24 > 0 else -1.0
            reason = f"Compress ATR%{atr_pct:.1f} break {mom24*100:.1f}%"
            conf = 0.8
        # Impulse
        elif abs(mom24) > 0.02 and ((mom24 > 0 and px > ema20) or (mom24 < 0 and px < ema20)):
            side = 1.0 if mom24 > 0 else -1.0
            reason = f"Impulse mom{mom24*100:.1f}%"
            conf = 0.7
        else:
            # v016 FIX #4: Flat if weak
            if abs(mom24) > MOM_THRESHOLD and ((mom24 > 0 and px > ema20) or (mom24 < 0 and px < ema20)):
                side = 1.0 if mom24 > 0 else -1.0
                reason = f"EMA trend mom{mom24*100:.1f}%"
                conf = 0.4
            else:
                side = 0.0
                reason = f"No apex signal, flat ATR%{atr_pct:.1f} mom{mom24*100:.2f}%"
                conf = 0.0
        
        notional = per_cap * lev_cap * conf
        target_coin = (notional / px) * side if side != 0 and px > 0 else 0.0
        
        res.per_asset[a] = dict(
            side=side, price=px, target_coin=target_coin, notional_usd=abs(notional),
            dt=str(df["dt"].iloc[-1]) if "dt" in df.columns else "",
            detail={"reason": reason, "confidence": conf, "atr_pct": atr_pct, "mom24": mom24},
            eligibility={"apex": True}
        )
    res.notes = "v016 flat-aware apex"
    return res

def sleeve_almasi_primary(frames, capital, lev_cap=2.0):
    res = SleeveResult("almasi_primary", "almasi turtle v016 adaptive", capital, 0.15)
    assets = _asset_order(frames)
    per_cap = capital / len(assets) if assets else capital
    
    for a in assets:
        df = frames[a]
        close = df["close"].astype(float)
        high = df["high"].astype(float) if "high" in df.columns else close
        low = df["low"].astype(float) if "low" in df.columns else close
        px = float(close.iloc[-1])
        
        # v016 FIX #6: Adaptive Donchian period based on volatility
        atr14 = _atr(high, low, close, 14).iloc[-1]
        atr_pct = float(atr14 / px * 100) if px > 0 and np.isfinite(atr14) else 2.0
        
        if atr_pct < 1.5:  # Low vol = likely ranging, increase period
            entry_n = 168  # 7 days instead of 3
            exit_n = 48
        else:
            entry_n = 72
            exit_n = 24
        
        # Donchian
        try:
            donchian_high = high.rolling(entry_n).max().iloc[-1]
            donchian_low = low.rolling(entry_n).min().iloc[-1]
        except:
            donchian_high = px
            donchian_low = px
        
        ema20 = close.ewm(span=20).mean().iloc[-1]
        mom24 = (close.iloc[-1] / close.iloc[-25] - 1) if len(close) > 25 else 0.0
        
        side, reason, conf = 0.0, "No signal", 0.0
        
        if px >= donchian_high:
            side, reason, conf = 1.0, f"Turtle LONG break {entry_n}h high", 0.7
        elif px <= donchian_low:
            side, reason, conf = -1.0, f"Turtle SHORT break {entry_n}h low", 0.7
        else:
            # v016 FIX #4: Flat if weak
            if abs(mom24) > MOM_THRESHOLD and ((mom24 > 0 and px > ema20) or (mom24 < 0 and px < ema20)):
                side = 1.0 if mom24 > 0 else -1.0
                reason = f"EMA fallback mom{mom24*100:.1f}% period{entry_n}h"
                conf = 0.4
            else:
                side = 0.0
                reason = f"No turtle break, flat period{entry_n}h atr%{atr_pct:.1f}"
                conf = 0.0
        
        notional = per_cap * lev_cap * conf
        target_coin = (notional / px) * side if side != 0 and px > 0 else 0.0
        
        res.per_asset[a] = dict(
            side=side, price=px, target_coin=target_coin, notional_usd=abs(notional),
            dt=str(df["dt"].iloc[-1]) if "dt" in df.columns else "",
            detail={"reason": reason, "confidence": conf, "entry_n": entry_n, "atr_pct": atr_pct, "donchian_high": donchian_high, "donchian_low": donchian_low},
            eligibility={"almasi": True}
        )
    res.notes = "v016 adaptive Donchian"
    return res

def sleeve_inst_v3_stable(frames, capital, lev_cap=2.0):
    res = SleeveResult("inst_v3_stable", "institutional TSMOM stable v016 flat-aware", capital, 0.15)
    assets = _asset_order(frames)
    per_cap = capital / len(assets) if assets else capital
    
    for a in assets:
        df = frames[a]
        close = df["close"].astype(float)
        px = float(close.iloc[-1])
        
        # TSMOM 3 horizons
        def mom(h):
            if len(close) <= h:
                return 0.0
            return float(close.iloc[-1] / close.iloc[-h-1] - 1)
        
        m24 = mom(24)
        m168 = mom(168)
        m720 = mom(720) if len(close) > 720 else m168
        
        vote = (1 if m24 > 0 else -1) + (1 if m168 > 0 else -1) + (1 if m720 > 0 else -1)
        vote = vote / 3.0
        
        ema20 = close.ewm(span=20).mean().iloc[-1]
        
        side, reason, conf = 0.0, "No signal", 0.0
        
        if vote >= 0.33:
            side, reason, conf = 1.0, f"TSMOM LONG vote {vote:.2f} (24h{m24*100:.1f}% 168h{m168*100:.1f}%)", 0.6
        elif vote <= -0.33:
            side, reason, conf = -1.0, f"TSMOM SHORT vote {vote:.2f}", 0.6
        else:
            # v016 FIX #4: Flat if weak vote
            if abs(vote) > 0.1 and abs(m24) > MOM_THRESHOLD:
                side = 1.0 if vote > 0 else -1.0
                reason = f"Weak TSMOM vote {vote:.2f}"
                conf = 0.3
            else:
                side = 0.0
                reason = f"No TSMOM consensus, flat vote {vote:.2f}"
                conf = 0.0
        
        notional = per_cap * lev_cap * conf
        target_coin = (notional / px) * side if side != 0 and px > 0 else 0.0
        
        res.per_asset[a] = dict(
            side=side, price=px, target_coin=target_coin, notional_usd=abs(notional),
            dt=str(df["dt"].iloc[-1]) if "dt" in df.columns else "",
            detail={"reason": reason, "confidence": conf, "vote": vote, "m24": m24, "m168": m168},
            eligibility={"inst_stable": True}
        )
    res.notes = "v016 flat-aware TSMOM"
    return res

def sleeve_inst_v3_primary(frames, capital, lev_cap=2.0):
    res = SleeveResult("inst_v3_primary", "institutional primary v016 flat-aware", capital, 0.10)
    assets = _asset_order(frames)
    per_cap = capital / len(assets) if assets else capital
    
    for a in assets:
        df = frames[a]
        close = df["close"].astype(float)
        px = float(close.iloc[-1])
        
        def mom(h):
            if len(close) <= h:
                return 0.0
            return float(close.iloc[-1] / close.iloc[-h-1] - 1)
        
        m24 = mom(24)
        m168 = mom(168)
        m720 = mom(720) if len(close) > 720 else m168
        
        # Consensus
        votes = [1 if x > 0 else -1 if x < 0 else 0 for x in [m24, m168, m720]]
        consensus = sum(votes)
        
        side, reason, conf = 0.0, "No signal", 0.0
        
        if consensus > 0:
            side, reason, conf = 1.0, f"Consensus LONG {consensus}/3", 0.6
        elif consensus < 0:
            side, reason, conf = -1.0, f"Consensus SHORT {consensus}/3", 0.6
        else:
            # v016 FIX #4: Flat if no consensus
            if abs(m24) > MOM_THRESHOLD:
                side = 1.0 if m24 > 0 else -1.0
                reason = f"No consensus, fallback mom24 {m24*100:.1f}%"
                conf = 0.3
            else:
                side = 0.0
                reason = f"No consensus, flat consensus {consensus}"
                conf = 0.0
        
        notional = per_cap * lev_cap * conf
        target_coin = (notional / px) * side if side != 0 and px > 0 else 0.0
        
        res.per_asset[a] = dict(
            side=side, price=px, target_coin=target_coin, notional_usd=abs(notional),
            dt=str(df["dt"].iloc[-1]) if "dt" in df.columns else "",
            detail={"reason": reason, "confidence": conf, "consensus": consensus},
            eligibility={"inst_primary": True}
        )
    res.notes = "v016 flat-aware consensus"
    return res

def sleeve_zenith_endurance(frames, capital, lev_cap=2.0):
    res = SleeveResult("zenith_endurance", "endurance impulse v016 flat-aware", capital, 0.10)
    assets = _asset_order(frames)
    per_cap = capital / len(assets) if assets else capital
    
    for a in assets:
        df = frames[a]
        close = df["close"].astype(float)
        px = float(close.iloc[-1])
        
        mom24 = (close.iloc[-1] / close.iloc[-25] - 1) if len(close) > 25 else 0.0
        ema20 = close.ewm(span=20).mean().iloc[-1]
        
        side, reason, conf = 0.0, "No signal", 0.0
        
        if abs(mom24) > 0.015 and ((mom24 > 0 and px > ema20) or (mom24 < 0 and px < ema20)):
            side = 1.0 if mom24 > 0 else -1.0
            reason = f"Endurance impulse mom{mom24*100:.1f}%"
            conf = 0.6
        else:
            # v016 FIX #4: Flat if weak
            if abs(mom24) > MOM_THRESHOLD:
                side = 1.0 if mom24 > 0 else -1.0
                reason = f"Weak impulse mom{mom24*100:.1f}%"
                conf = 0.3
            else:
                side = 0.0
                reason = f"No impulse, flat mom{mom24*100:.2f}%"
                conf = 0.0
        
        notional = per_cap * lev_cap * conf
        target_coin = (notional / px) * side if side != 0 and px > 0 else 0.0
        
        res.per_asset[a] = dict(
            side=side, price=px, target_coin=target_coin, notional_usd=abs(notional),
            dt=str(df["dt"].iloc[-1]) if "dt" in df.columns else "",
            detail={"reason": reason, "confidence": conf, "mom24": mom24},
            eligibility={"endurance": True}
        )
    res.notes = "v016 flat-aware endurance"
    return res

def sleeve_mean_reversion(frames, capital, lev_cap=2.0):
    """
    v016 NEW Fix #8: Real mean-reversion for ranging market
    Bollinger Bands mean reversion - only active in ranging regime
    """
    res = SleeveResult("mean_reversion", "mean reversion v016 range-optimized", capital, 0.20)
    assets = _asset_order(frames)
    per_cap = capital / len(assets) if assets else capital
    
    for a in assets:
        df = frames[a]
        close = df["close"].astype(float)
        px = float(close.iloc[-1])
        
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper_band = (sma20 + 2 * std20).iloc[-1] if len(sma20) > 0 and np.isfinite(sma20.iloc[-1]) else px * 1.02
        lower_band = (sma20 - 2 * std20).iloc[-1] if len(sma20) > 0 and np.isfinite(sma20.iloc[-1]) else px * 0.98
        mid = sma20.iloc[-1] if len(sma20) > 0 and np.isfinite(sma20.iloc[-1]) else px
        
        regime = _detect_regime(df)
        
        side, reason, conf = 0.0, "No signal", 0.0
        
        if regime == "ranging":
            if px <= lower_band:
                side, conf = 1.0, 0.7
                reason = f"MeanRev LONG at lower band {lower_band:.2f} px {px:.2f} (ranging)"
            elif px >= upper_band:
                side, conf = -1.0, 0.7
                reason = f"MeanRev SHORT at upper band {upper_band:.2f} px {px:.2f} (ranging)"
            elif px < mid * 0.995:
                side, conf = 1.0, 0.4
                reason = f"MeanRev weak LONG below mid {mid:.2f}"
            elif px > mid * 1.005:
                side, conf = -1.0, 0.4
                reason = f"MeanRev weak SHORT above mid {mid:.2f}"
            else:
                side, conf = 0.0, 0.0
                reason = f"Inside bands, flat px {px:.2f} mid {mid:.2f}"
        elif regime == "transitional":
            # Reduced confidence in transitional
            if px <= lower_band:
                side, conf = 1.0, 0.4
                reason = f"MeanRev LONG transitional at lower band"
            elif px >= upper_band:
                side, conf = -1.0, 0.4
                reason = f"MeanRev SHORT transitional at upper band"
            else:
                side, conf = 0.0, 0.0
                reason = f"Transitional {regime}, mean-reversion reduced"
        else:
            side, conf = 0.0, 0.0
            reason = f"Not ranging ({regime}), mean-reversion disabled"
        
        notional = per_cap * lev_cap * conf
        target_coin = (notional / px) * side if side != 0 and px > 0 else 0.0
        
        res.per_asset[a] = dict(
            side=side, price=px, target_coin=target_coin, notional_usd=abs(notional),
            dt=str(df["dt"].iloc[-1]) if "dt" in df.columns else "",
            detail={"reason": reason, "confidence": conf, "regime": regime, "upper_band": upper_band, "lower_band": lower_band, "mid": mid},
            eligibility={"mean_reversion": True, "regime": regime}
        )
    res.notes = "v016 range-aware mean reversion"
    return res


SLEEVE_BUILDERS = {
    "diversified_5": sleeve_diversified_5,
    "zenith_apex": sleeve_zenith_apex,
    "almasi_primary": sleeve_almasi_primary,
    "inst_v3_stable": sleeve_inst_v3_stable,
    "inst_v3_primary": sleeve_inst_v3_primary,
    "zenith_endurance": sleeve_zenith_endurance,
    "mean_reversion": sleeve_mean_reversion,
}

def parse_weights(weights_str: str) -> Dict[str, float]:
    result = {}
    if not weights_str:
        return DEFAULT_WEIGHTS.copy()
    try:
        for part in weights_str.split(","):
            part = part.strip()
            if not part:
                continue
            if ":" in part:
                k, v = part.split(":", 1)
                result[k.strip()] = float(v.strip())
    except:
        return DEFAULT_WEIGHTS.copy()
    # Normalize
    total = sum(result.values()) or 1.0
    return {k: v/total for k, v in result.items()}

def run_all_sleeves(frames: Dict[str, pd.DataFrame], total_capital: float, weights=None, lev_cap: float = 2.0, enabled=None):
    """
    v016 FIX #5: Regime-aware weight adjustment
    In ranging market, reduce trend strategy weights
    """
    wmap = dict(weights or DEFAULT_WEIGHTS)
    if isinstance(weights, str):
        wmap = parse_weights(weights)
    if enabled:
        wmap = {k: v for k, v in wmap.items() if k in enabled}
    
    # v016 NEW: Detect regime
    regimes = {}
    for a, df in frames.items():
        regimes[a] = _detect_regime(df)
    
    ranging_count = sum(1 for r in regimes.values() if r == "ranging")
    is_mostly_ranging = ranging_count >= len(regimes) * 0.6 if regimes else False
    
    if is_mostly_ranging:
        trend_heavy = ["zenith_apex", "almasi_primary", "inst_v3_stable", "inst_v3_primary", "zenith_endurance"]
        for sid in trend_heavy:
            if sid in wmap:
                wmap[sid] *= 0.3  # Reduce 70% in ranging
        log.info(f"RANGING market detected ({ranging_count}/{len(regimes)} assets), reducing trend weights")
    
    tot = sum(wmap.values()) or 1.0
    wmap = {k: v / tot for k, v in wmap.items()}
    
    results = []
    net_book: Dict[str, dict] = {}
    
    for sid, weight in wmap.items():
        builder = SLEEVE_BUILDERS.get(sid)
        if not builder:
            continue
        cap = total_capital * weight
        try:
            res = builder(frames, cap, lev_cap=lev_cap)
            res.weight = weight
            results.append(res)
            # Aggregate to net_book
            for asset, data in res.per_asset.items():
                if asset not in net_book:
                    net_book[asset] = {"target_coin": 0.0, "price": data.get("price", 0), "contributors": []}
                net_book[asset]["target_coin"] += data.get("target_coin", 0)
                net_book[asset]["price"] = data.get("price", net_book[asset]["price"])
                net_book[asset]["contributors"].append({"sleeve": sid, "target_coin": data.get("target_coin", 0), "side": data.get("side", 0)})
        except Exception as exc:
            log.exception(f"sleeve {sid} failed: {exc}")
            continue
    
    # Enrich net_book with notional
    for asset, data in net_book.items():
        price = data.get("price", 0)
        target_coin = data.get("target_coin", 0)
        data["notional_usd"] = abs(target_coin * price) if price else 0
        data["side"] = 1 if target_coin > 0 else -1 if target_coin < 0 else 0
    
    return results, net_book
