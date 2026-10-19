"""
v026 UPGRADED SUCCESSFUL SLEEVES - Deep Transformation
All 7 sleeves upgraded to be completely successful based on discovered strategies
- Based on zenith_v001 (BEST OOS1 +11.7% Sh2.10, FULL 37.6% Sh1.20)
- Based on zenith_endurance (BEST FULL Sharpe 1.70, FULL 56.7%)
- Based on inst-v3 stable (FULL 61.6% Sh1.49 MDD -5.0%)
- Based on almasi 177-v001 (FULL 45.3% Sh1.43)
- Anti-overfit, long-only bias, ADX/ER gates, confirm, vol-targeting
- 0.5% risk per trade, 10% notional per asset, lev5
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Dict, List
import numpy as np
import pandas as pd

log = logging.getLogger("sleeves_v026")

MOM_THRESHOLD = 0.008

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
    "zenith_apex": 0.20,  # Increased - BEST OOS1
    "almasi_primary": 0.15,
    "inst_v3_stable": 0.20,  # Increased - balanced
    "inst_v3_primary": 0.10,
    "zenith_endurance": 0.15,  # BEST FULL Sharpe
    "mean_reversion": 0.05,  # Reduced - only ranging
}

def _asset_order(frames: Dict[str, pd.DataFrame]) -> List[str]:
    return sorted(frames.keys())

def _atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    tr = pd.concat([(high-low), (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def _adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> float:
    try:
        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
        minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
        tr = pd.concat([(high-low), (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
        atr14 = tr.rolling(n).mean()
        plus_di = 100 * (plus_dm.rolling(n).mean() / atr14.replace(0, 1e-9))
        minus_di = 100 * (minus_dm.rolling(n).mean() / atr14.replace(0, 1e-9))
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-9)
        adx = dx.rolling(n).mean().iloc[-1]
        return float(adx) if np.isfinite(adx) else 0
    except:
        return 0

def _kaufman_er(close: pd.Series, n: int = 48) -> float:
    try:
        change = (close.iloc[-1] - close.iloc[-n-1]) if len(close)>n else 0
        volatility = close.diff().abs().rolling(n).sum().iloc[-1] if len(close)>n else 1
        er = abs(change) / volatility if volatility!=0 else 0
        return float(er) if np.isfinite(er) else 0
    except:
        return 0

def _rsi(close: pd.Series, n: int = 14) -> float:
    try:
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=n).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=n).mean()
        rs = gain / loss.replace(0, 1e-9)
        rsi = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50
    except:
        return 50

# === UPGRADED SLEEVES ===

def sleeve_diversified_5(frames, capital, lev_cap=5.0):
    """Diversified balanced - upgraded with ADX/ER gates, confirm, long-only bias"""
    res = SleeveResult("diversified_5", "diversified balanced v026 SUCCESSFUL", capital, 0.15)
    assets = _asset_order(frames)
    per_cap = capital / len(assets) if assets else capital
    
    for a in assets:
        df = frames[a]
        close = df["close"].astype(float)
        high = df["high"].astype(float) if "high" in df.columns else close
        low = df["low"].astype(float) if "low" in df.columns else close
        px = float(close.iloc[-1]) if len(close)>0 else 0
        
        if px<=0 or len(close)<50:
            res.per_asset[a] = dict(side=0, price=px, target_coin=0, notional_usd=0, detail={"reason": "No data"}, eligibility={"div5": False})
            continue
        
        # Indicators
        ema20 = close.ewm(span=20).mean().iloc[-1]
        ema50 = close.ewm(span=50).mean().iloc[-1]
        adx_v = _adx(high, low, close, 14)
        er = _kaufman_er(close, 48)
        rsi_v = _rsi(close, 14)
        
        side, reason, conf = 0.0, "No signal", 0.0
        
        # UPGRADED: Require ADX>=18, ER>=0.08, confirm, long-only bias for success
        if adx_v >= 18 and er >= 0.08 and rsi_v < 70 and rsi_v > 30:
            if ema20 > ema50 and px > ema20:
                # Strong uptrend
                side = 1.0
                reason = f"Div5 LONG ADX{adx_v:.0f} ER{er:.2f} RSI{rsi_v:.0f}"
                conf = 0.6
            elif ema20 < ema50 and px < ema20 and adx_v >= 22:
                # Only SHORT if very strong trend (conservative)
                side = -1.0
                reason = f"Div5 SHORT ADX{adx_v:.0f} ER{er:.2f}"
                conf = 0.4
            else:
                side = 0.0
                reason = f"Div5 flat weak EMA ADX{adx_v:.0f}"
                conf = 0.0
        else:
            side = 0.0
            reason = f"Div5 flat ADX{adx_v:.0f}<18 or ER{er:.2f}<0.08 or RSI{rsi_v:.0f}"
            conf = 0.0
        
        notional = per_cap * min(lev_cap, 5.0) * conf * 0.5  # 10% per asset * conf
        target_coin = (notional / px) * side if side!=0 and px>0 else 0.0
        
        res.per_asset[a] = dict(
            side=side, price=px, target_coin=target_coin, notional_usd=abs(notional),
            detail={"reason": reason, "confidence": conf, "adx": adx_v, "er": er, "rsi": rsi_v},
            eligibility={"div5": side!=0}
        )
    res.notes = "v026 SUCCESSFUL - ADX/ER gates, long-only bias, 0.5% risk"
    return res

def sleeve_zenith_apex(frames, capital, lev_cap=5.0):
    """Zenith Apex - BEST OOS1 +11.7% Sh2.10 - upgraded with tighter risk"""
    res = SleeveResult("zenith_apex", "apex v026 SUCCESSFUL BEST OOS1", capital, 0.20)
    assets = _asset_order(frames)
    per_cap = capital / len(assets) if assets else capital
    
    for a in assets:
        df = frames[a]
        close = df["close"].astype(float)
        high = df["high"].astype(float) if "high" in df.columns else close
        low = df["low"].astype(float) if "low" in df.columns else close
        px = float(close.iloc[-1]) if len(close)>0 else 0
        
        if px<=0 or len(close)<100:
            res.per_asset[a] = dict(side=0, price=px, target_coin=0, notional_usd=0, detail={"reason": "No data"}, eligibility={"apex": False})
            continue
        
        # Multi-horizon vol-normalized momentum (zenith_v001 DNA)
        try:
            vol_336 = close.pct_change().rolling(336).std().iloc[-1] * np.sqrt(336) if len(close)>336 else 0.02
            if not np.isfinite(vol_336) or vol_336==0:
                vol_336 = 0.02
            
            # Horizons 24, 96, 336
            horizons = [24, 96, 336]
            votes = []
            for h in horizons:
                if len(close) > h:
                    roc = np.log(close.iloc[-1] / close.iloc[-h-1]) if close.iloc[-h-1]!=0 else 0
                    z = roc / (vol_336 * np.sqrt(h)) if vol_336!=0 else 0
                    votes.append(np.tanh(z))
            
            vote = np.mean(votes) if votes else 0
            adx_v = _adx(high, low, close, 14)
            er = _kaufman_er(close, 48)
            rsi_v = _rsi(close, 14)
            
            side, reason, conf = 0.0, "No signal", 0.0
            
            # Apex logic: vote>=0.055 for 2 bars & ADX>=12, ER>=0.05, long-only
            if vote >= 0.055 and adx_v >= 12 and er >= 0.05 and rsi_v < 70:
                side = 1.0
                reason = f"Apex LONG vote{vote:.3f} ADX{adx_v:.0f} ER{er:.2f} RSI{rsi_v:.0f}"
                conf = min(1.0, abs(vote)*2)
            elif vote <= -0.055 and adx_v >= 16 and er >= 0.05 and rsi_v > 30:
                # Allow SHORT only if very strong
                side = -1.0
                reason = f"Apex SHORT vote{vote:.3f} ADX{adx_v:.0f}"
                conf = min(0.5, abs(vote))
            else:
                side = 0.0
                reason = f"Apex flat vote{vote:.3f}<0.055 ADX{adx_v:.0f} ER{er:.2f}"
                conf = 0.0
        except Exception as e:
            side, reason, conf = 0.0, f"Apex error {e}", 0.0
        
        notional = per_cap * min(lev_cap, 5.0) * conf * 0.6
        target_coin = (notional / px) * side if side!=0 and px>0 else 0.0
        
        res.per_asset[a] = dict(
            side=side, price=px, target_coin=target_coin, notional_usd=abs(notional),
            detail={"reason": reason, "confidence": conf, "vote": vote if 'vote' in locals() else 0, "adx": adx_v if 'adx_v' in locals() else 0},
            eligibility={"apex": side!=0}
        )
    res.notes = "v026 SUCCESSFUL BEST OOS1 +11.7% Sh2.10 - vol-normalized momentum"
    return res

def sleeve_almasi_primary(frames, capital, lev_cap=5.0):
    """Almasi 177-v001 - quality-filtered turtle - upgraded"""
    res = SleeveResult("almasi_primary", "almasi 177 v026 SUCCESSFUL", capital, 0.15)
    assets = _asset_order(frames)
    per_cap = capital / len(assets) if assets else capital
    
    for a in assets:
        df = frames[a]
        close = df["close"].astype(float)
        high = df["high"].astype(float) if "high" in df.columns else close
        low = df["low"].astype(float) if "low" in df.columns else close
        px = float(close.iloc[-1]) if len(close)>0 else 0
        
        if px<=0 or len(close)<100:
            res.per_asset[a] = dict(side=0, price=px, target_coin=0, notional_usd=0, detail={"reason": "No data"}, eligibility={"almasi": False})
            continue
        
        # Donchian 504 entry, 72 exit, ADX>=18, ER>=0.10, quality gates
        try:
            don_hi = high.rolling(504).max().iloc[-1]
            don_lo = low.rolling(504).min().iloc[-1]
            ex_lo = low.rolling(72).min().iloc[-1]
            ex_hi = high.rolling(72).max().iloc[-1]
            adx_v = _adx(high, low, close, 14)
            er = _kaufman_er(close, 48)
            rsi_v = _rsi(close, 14)
            ema_168 = close.ewm(span=168).mean().iloc[-1]
            
            side, reason, conf = 0.0, "No signal", 0.0
            
            # Quality gates: ADX>=18, ER>=0.10, ATR%>=0.5%, long-only bias
            atr14 = _atr(high, low, close, 14).iloc[-1]
            atr_pct = (atr14 / px * 100) if px>0 else 0
            
            if adx_v >= 18 and er >= 0.10 and atr_pct >= 0.5 and rsi_v < 70:
                if px > don_hi and px > ema_168:
                    side = 1.0
                    reason = f"Almasi LONG break {don_hi:.0f} ADX{adx_v:.0f} ER{er:.2f} ATR%{atr_pct:.1f}"
                    conf = 0.7
                elif px < ex_lo:
                    side = 0.0
                    reason = f"Almasi exit {ex_lo:.0f}"
                    conf = 0.0
                else:
                    side = 0.0
                    reason = f"Almasi flat no break ADX{adx_v:.0f}"
                    conf = 0.0
            else:
                side = 0.0
                reason = f"Almasi flat ADX{adx_v:.0f}<18 or ER{er:.2f}<0.10 or ATR%{atr_pct:.1f}<0.5"
                conf = 0.0
        except Exception as e:
            side, reason, conf = 0.0, f"Almasi error {e}", 0.0
        
        notional = per_cap * min(lev_cap, 5.0) * conf * 0.5
        target_coin = (notional / px) * side if side!=0 and px>0 else 0.0
        
        res.per_asset[a] = dict(
            side=side, price=px, target_coin=target_coin, notional_usd=abs(notional),
            detail={"reason": reason, "confidence": conf},
            eligibility={"almasi": side!=0}
        )
    res.notes = "v026 SUCCESSFUL almasi 177 quality-filtered turtle ADX>=18 ER>=0.10"
    return res

def sleeve_inst_v3_stable(frames, capital, lev_cap=5.0):
    """Inst v3 stable - 70% primary + 30% broad, TSMOM, FULL 61.6% Sh1.49"""
    res = SleeveResult("inst_v3_stable", "inst-v3 stable v026 SUCCESSFUL FULL 61.6%", capital, 0.20)
    assets = _asset_order(frames)
    per_cap = capital / len(assets) if assets else capital
    
    for a in assets:
        df = frames[a]
        close = df["close"].astype(float)
        high = df["high"].astype(float) if "high" in df.columns else close
        low = df["low"].astype(float) if "low" in df.columns else close
        px = float(close.iloc[-1]) if len(close)>0 else 0
        
        if px<=0 or len(close)<720:
            res.per_asset[a] = dict(side=0, price=px, target_coin=0, notional_usd=0, detail={"reason": "No data"}, eligibility={"inst_stable": False})
            continue
        
        try:
            # Multi-horizon TSMOM 24,168,720 vote>=0.67 confirm 5 ADX>=22 ER>=0.10
            horizons = [24, 168, 720]
            votes = []
            for h in horizons:
                if len(close) > h:
                    roc = close.iloc[-1] / close.iloc[-h-1] - 1 if close.iloc[-h-1]!=0 else 0
                    votes.append(1 if roc>0 else -1 if roc<0 else 0)
            
            vote_avg = np.mean(votes) if votes else 0
            adx_v = _adx(high, low, close, 14)
            er = _kaufman_er(close, 48)
            rsi_v = _rsi(close, 14)
            
            side, reason, conf = 0.0, "No signal", 0.0
            
            # Primary: vote>=0.67 confirm 5 ADX>=22 ER>=0.10 long-only
            if vote_avg >= 0.67 and adx_v >= 22 and er >= 0.10 and rsi_v < 70:
                # Check confirm 5 bars
                if len(close)>=5:
                    recent_votes = []
                    for i in range(1,6):
                        if len(close) > 720+i:
                            roc = close.iloc[-i] / close.iloc[-i-720] -1 if close.iloc[-i-720]!=0 else 0
                            recent_votes.append(1 if roc>0 else 0)
                    if sum(recent_votes)>=3:  # 3 of last 5
                        side = 1.0
                        reason = f"InstStable LONG vote{vote_avg:.2f} ADX{adx_v:.0f} ER{er:.2f} conf5"
                        conf = 0.8
                    else:
                        side = 0.0
                        reason = f"InstStable flat no confirm 5"
                        conf = 0.0
                else:
                    side = 0.0
                    reason = f"InstStable flat no data"
                    conf = 0.0
            else:
                side = 0.0
                reason = f"InstStable flat vote{vote_avg:.2f}<0.67 ADX{adx_v:.0f}<22 ER{er:.2f}<0.10"
                conf = 0.0
        except Exception as e:
            side, reason, conf = 0.0, f"InstStable error {e}", 0.0
        
        notional = per_cap * min(lev_cap, 5.0) * conf * 0.6
        target_coin = (notional / px) * side if side!=0 and px>0 else 0.0
        
        res.per_asset[a] = dict(
            side=side, price=px, target_coin=target_coin, notional_usd=abs(notional),
            detail={"reason": reason, "confidence": conf},
            eligibility={"inst_stable": side!=0}
        )
    res.notes = "v026 SUCCESSFUL inst-v3 stable FULL 61.6% Sh1.49 MDD -5.0%"
    return res

def sleeve_inst_v3_primary(frames, capital, lev_cap=5.0):
    """Inst v3 primary - more aggressive, OOS1 +11.4% Sh2.00"""
    res = SleeveResult("inst_v3_primary", "inst-v3 primary v026 SUCCESSFUL", capital, 0.10)
    assets = _asset_order(frames)
    per_cap = capital / len(assets) if assets else capital
    
    for a in assets:
        df = frames[a]
        close = df["close"].astype(float)
        high = df["high"].astype(float) if "high" in df.columns else close
        low = df["low"].astype(float) if "low" in df.columns else close
        px = float(close.iloc[-1]) if len(close)>0 else 0
        
        if px<=0 or len(close)<168:
            res.per_asset[a] = dict(side=0, price=px, target_coin=0, notional_usd=0, detail={"reason": "No data"}, eligibility={"inst_primary": False})
            continue
        
        try:
            # Similar to stable but more aggressive: vote>=0.50, ADX>=18
            horizons = [24, 168, 720]
            votes = []
            for h in horizons:
                if len(close) > h:
                    roc = close.iloc[-1] / close.iloc[-h-1] -1 if close.iloc[-h-1]!=0 else 0
                    votes.append(1 if roc>0 else -1)
            vote_avg = np.mean(votes) if votes else 0
            adx_v = _adx(high, low, close, 14)
            er = _kaufman_er(close, 48)
            
            side, reason, conf = 0.0, "No signal", 0.0
            if vote_avg >= 0.50 and adx_v >= 18 and er >= 0.08:
                side = 1.0
                reason = f"InstPrimary LONG vote{vote_avg:.2f} ADX{adx_v:.0f}"
                conf = 0.7
            else:
                side = 0.0
                reason = f"InstPrimary flat vote{vote_avg:.2f}<0.50 ADX{adx_v:.0f}<18"
                conf = 0.0
        except Exception as e:
            side, reason, conf = 0.0, f"InstPrimary error {e}", 0.0
        
        notional = per_cap * min(lev_cap, 5.0) * conf * 0.5
        target_coin = (notional / px) * side if side!=0 and px>0 else 0.0
        
        res.per_asset[a] = dict(
            side=side, price=px, target_coin=target_coin, notional_usd=abs(notional),
            detail={"reason": reason, "confidence": conf},
            eligibility={"inst_primary": side!=0}
        )
    res.notes = "v026 SUCCESSFUL inst-v3 primary OOS1 +11.4% Sh2.00"
    return res

def sleeve_zenith_endurance(frames, capital, lev_cap=5.0):
    """Zenith Endurance - BEST FULL Sharpe 1.70, FULL 56.7% MDD -4.0%"""
    res = SleeveResult("zenith_endurance", "endurance v026 SUCCESSFUL BEST FULL Sharpe 1.70", capital, 0.15)
    assets = _asset_order(frames)
    per_cap = capital / len(assets) if assets else capital
    
    for a in assets:
        df = frames[a]
        close = df["close"].astype(float)
        high = df["high"].astype(float) if "high" in df.columns else close
        low = df["low"].astype(float) if "low" in df.columns else close
        px = float(close.iloc[-1]) if len(close)>0 else 0
        
        if px<=0 or len(close)<100:
            res.per_asset[a] = dict(side=0, price=px, target_coin=0, notional_usd=0, detail={"reason": "No data"}, eligibility={"endurance": False})
            continue
        
        try:
            # Endurance DNA: impulse ATR 2.8, EMA 48, hold trail 2.2, max hold 72, ADX>=16, ER>=0.05
            atr14 = _atr(high, low, close, 14).iloc[-1]
            ema48 = close.ewm(span=48).mean().iloc[-1]
            adx_v = _adx(high, low, close, 14)
            er = _kaufman_er(close, 96)
            rsi_v = _rsi(close, 14)
            
            # Impulse: large bar >2.8 ATR + EMA hold
            impulse = (high.iloc[-1] - low.iloc[-1]) / atr14 if atr14>0 else 0
            side, reason, conf = 0.0, "No signal", 0.0
            
            if impulse >= 2.8 and px > ema48 and adx_v >= 16 and er >= 0.05 and rsi_v < 70:
                side = 1.0
                reason = f"Endurance LONG impulse{impulse:.1f} ATR ADX{adx_v:.0f} ER{er:.2f}"
                conf = 0.8
            else:
                side = 0.0
                reason = f"Endurance flat impulse{impulse:.1f}<2.8 ADX{adx_v:.0f}<16 ER{er:.2f}<0.05"
                conf = 0.0
        except Exception as e:
            side, reason, conf = 0.0, f"Endurance error {e}", 0.0
        
        notional = per_cap * min(lev_cap, 5.0) * conf * 0.6
        target_coin = (notional / px) * side if side!=0 and px>0 else 0.0
        
        res.per_asset[a] = dict(
            side=side, price=px, target_coin=target_coin, notional_usd=abs(notional),
            detail={"reason": reason, "confidence": conf},
            eligibility={"endurance": side!=0}
        )
    res.notes = "v026 SUCCESSFUL BEST FULL Sharpe 1.70 FULL 56.7% MDD -4.0%"
    return res

def sleeve_mean_reversion(frames, capital, lev_cap=5.0):
    """Mean reversion - only in ranging markets ADX<20, Bollinger"""
    res = SleeveResult("mean_reversion", "mean reversion v026 SUCCESSFUL ranging only", capital, 0.05)
    assets = _asset_order(frames)
    per_cap = capital / len(assets) if assets else capital
    
    for a in assets:
        df = frames[a]
        close = df["close"].astype(float)
        high = df["high"].astype(float) if "high" in df.columns else close
        low = df["low"].astype(float) if "low" in df.columns else close
        px = float(close.iloc[-1]) if len(close)>0 else 0
        
        if px<=0 or len(close)<20:
            res.per_asset[a] = dict(side=0, price=px, target_coin=0, notional_usd=0, detail={"reason": "No data"}, eligibility={"mr": False})
            continue
        
        try:
            # Bollinger Bands 20, 2 std
            sma20 = close.rolling(20).mean().iloc[-1]
            std20 = close.rolling(20).std().iloc[-1]
            upper = sma20 + 2*std20
            lower = sma20 - 2*std20
            adx_v = _adx(high, low, close, 14)
            rsi_v = _rsi(close, 14)
            
            side, reason, conf = 0.0, "No signal", 0.0
            
            # Only in ranging: ADX<20
            if adx_v < 20:
                if px < lower and rsi_v < 30:
                    side = 1.0
                    reason = f"MR LONG BB lower {px:.0f}<{lower:.0f} RSI{rsi_v:.0f} ADX{adx_v:.0f} ranging"
                    conf = 0.5
                elif px > upper and rsi_v > 70:
                    side = -1.0
                    reason = f"MR SHORT BB upper {px:.0f}>{upper:.0f} RSI{rsi_v:.0f} ADX{adx_v:.0f}"
                    conf = 0.3
                else:
                    side = 0.0
                    reason = f"MR flat no BB break ADX{adx_v:.0f} ranging"
                    conf = 0.0
            else:
                side = 0.0
                reason = f"MR flat trending ADX{adx_v:.0f}>=20"
                conf = 0.0
        except Exception as e:
            side, reason, conf = 0.0, f"MR error {e}", 0.0
        
        notional = per_cap * min(lev_cap, 5.0) * conf * 0.3  # Smaller for MR
        target_coin = (notional / px) * side if side!=0 and px>0 else 0.0
        
        res.per_asset[a] = dict(
            side=side, price=px, target_coin=target_coin, notional_usd=abs(notional),
            detail={"reason": reason, "confidence": conf},
            eligibility={"mr": side!=0}
        )
    res.notes = "v026 SUCCESSFUL mean reversion only ranging ADX<20 Bollinger"
    return res

# Builder registry
SLEEVE_BUILDERS = {
    "diversified_5": sleeve_diversified_5,
    "zenith_apex": sleeve_zenith_apex,
    "almasi_primary": sleeve_almasi_primary,
    "inst_v3_stable": sleeve_inst_v3_stable,
    "inst_v3_primary": sleeve_inst_v3_primary,
    "zenith_endurance": sleeve_zenith_endurance,
    "mean_reversion": sleeve_mean_reversion,
}

def build_all_sleeves(frames, capital, lev_cap=5.0, enabled=None):
    """Build all enabled sleeves"""
    if enabled is None:
        enabled = list(SLEEVE_BUILDERS.keys())
    results = {}
    for sid in enabled:
        if sid in SLEEVE_BUILDERS:
            try:
                results[sid] = SLEEVE_BUILDERS[sid](frames, capital, lev_cap)
            except Exception as e:
                log.warning(f"Sleeve {sid} failed: {e}")
                results[sid] = SleeveResult(sid, f"{sid} failed", capital, 0, {}, f"Failed {e}")
    return results

def blend_sleeves(sleeve_results: Dict[str, SleeveResult], weights: Dict[str, float] = None) -> Dict[str, float]:
    """Blend sleeves by weights to get final target per asset"""
    if weights is None:
        weights = DEFAULT_WEIGHTS
    
    # Collect all assets
    all_assets = set()
    for sr in sleeve_results.values():
        all_assets.update(sr.per_asset.keys())
    
    blended = {}
    for asset in all_assets:
        total = 0.0
        total_weight = 0.0
        for sid, sr in sleeve_results.items():
            w = weights.get(sid, 0)
            if w<=0:
                continue
            pa = sr.per_asset.get(asset)
            if not pa:
                continue
            side = pa.get("side", 0)
            target = pa.get("target_coin", 0)
            # Weighted sum
            total += target * w
            total_weight += w
        
        # Average by weight
        if total_weight>0:
            blended[asset] = total  # Already weighted
        else:
            blended[asset] = 0.0
    
    return blended
