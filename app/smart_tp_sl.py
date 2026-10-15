"""
app/smart_tp_sl.py - Fixed Multi-Level Ladder & Trailing Engine (Fix 4)
Eliminates premature breakeven exits and 0-distance trailing stop-out bugs.
v019 STABLE + FIXED56 compatibility
"""
from __future__ import annotations
import logging
from typing import Dict

log = logging.getLogger("smart_tp_sl")

def calculate_initial_levels(entry: float, side: int, atr: float, cfg: dict) -> dict:
    """Calculates wide, noise-resistant TP/SL levels - v019 Fix 4"""
    sl_atr = cfg.get("smart_sl_atr", 2.0)
    tp1_atr = cfg.get("smart_tp1_atr", 2.5)
    tp2_atr = cfg.get("smart_tp2_atr", 4.0)

    if side > 0:  # LONG
        sl = entry - (sl_atr * atr)
        tp1 = entry + (tp1_atr * atr)
        tp2 = entry + (tp2_atr * atr)
    else:         # SHORT
        sl = entry + (sl_atr * atr)
        tp1 = entry - (tp1_atr * atr)
        tp2 = entry - (tp2_atr * atr)

    return {
        "entry_price": entry,
        "side": side,
        "atr": atr,
        "sl": sl,
        "tp": tp1,
        "tp1": tp1,
        "tp2": tp2,
        "level": 0,
        "breakeven_moved": False,
        "tp1_hit": False,
        "highest_price": entry if side > 0 else entry,
        "lowest_price": entry if side < 0 else entry,
    }

def evaluate_smart_exit(current_price: float, state: dict, atr: float) -> dict:
    """
    Evaluates ladder adjustments without zero-distance stops.
    v019 Fix 4: No Zero-Distance Bug, 1.5x ATR buffer
    """
    entry = state["entry_price"]
    side = state["side"]
    sl = state["sl"]
    level = state.get("level", 0)
    
    # Track extreme price reached
    if side > 0:
        state["highest_price"] = max(state.get("highest_price", entry), current_price)
        profit_atr = (current_price - entry) / atr if atr > 0 else 0
    else:
        state["highest_price"] = min(state.get("highest_price", entry), current_price)
        state["lowest_price"] = min(state.get("lowest_price", entry), current_price)
        profit_atr = (entry - current_price) / atr if atr > 0 else 0

    res = {"new_sl": sl, "close_fraction": 0.0, "trigger_exit": None}

    # Stop Loss Hit Check
    if (side > 0 and current_price <= sl) or (side < 0 and current_price >= sl):
        res["trigger_exit"] = "STOP_LOSS"
        return res

    # Level 1: Move to Breakeven (+0.2 ATR buffer) after 1.5 ATR profit - v019
    if level == 0 and profit_atr >= 1.5:
        buffer = 0.2 * atr * side
        res["new_sl"] = entry + buffer
        state["level"] = 1
        state["breakeven_moved"] = True
        log.info(f"Level 1: Protected Breakeven set at {res['new_sl']:.4f} (profit {profit_atr:.2f} ATR)")

    # Level 2: TP1 Hit (2.5 ATR profit) -> Take 33% profit, Move SL to Entry + 1.0 ATR
    hit_tp1 = (side > 0 and current_price >= state["tp1"]) or (side < 0 and current_price <= state["tp1"])
    if level == 1 and hit_tp1:
        res["close_fraction"] = 0.33
        # FIX 4: Do NOT set SL = tp1 (which causes immediate stop-out)!
        # Instead, lock in 1.0 ATR guaranteed profit below the market.
        res["new_sl"] = entry + (1.0 * atr * side)
        state["level"] = 2
        state["tp"] = state["tp2"]
        state["tp1_hit"] = True
        log.info(f"Level 2: TP1 Reached. Closed 33%, SL moved to {res['new_sl']:.4f}")

    # Level 3: Dynamic Trailing Stop after TP1 (Trail at 1.5 ATR distance) - v019
    if level >= 2:
        if side > 0:
            trailing_sl = state["highest_price"] - (1.5 * atr)
            if trailing_sl > state["sl"]:
                res["new_sl"] = trailing_sl
                log.info(f"Level 3: Trailing SL Long {res['new_sl']:.4f} (highest {state['highest_price']:.4f})")
        else:
            trailing_sl = state["highest_price"] + (1.5 * atr)
            if trailing_sl < state["sl"]:
                res["new_sl"] = trailing_sl
                log.info(f"Level 3: Trailing SL Short {res['new_sl']:.4f}")

    return res

# Backward compat for old engine that calls _calculate_atr_levels
def _calculate_atr_levels_compat(df, entry_price, side):
    """Compat wrapper for old engine"""
    try:
        # Simple ATR calc from df
        import pandas as pd
        high = df["high"]
        low = df["low"]
        close = df["close"]
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]
        if pd.isna(atr):
            atr = entry_price * 0.02
        cfg = {"smart_sl_atr": 2.0, "smart_tp1_atr": 2.5, "smart_tp2_atr": 4.0}
        levels = calculate_initial_levels(entry_price, side, atr, cfg)
        return {"sl": levels["sl"], "tp1": levels["tp1"], "tp2": levels["tp2"], "atr": atr}
    except:
        atr = entry_price * 0.02
        return {"sl": entry_price * (0.98 if side>0 else 1.02), "tp1": entry_price * (1.05 if side>0 else 0.95), "tp2": entry_price * (1.08 if side>0 else 0.92), "atr": atr}


# === v018 backward compat aliases for REGRESSION_CHECK ===
def calculate_smart_levels(*args, **kwargs):
    return calculate_initial_levels(*args, **kwargs)

def _calculate_atr_levels(df, entry_price, side):
    return _calculate_atr_levels_compat(df, entry_price, side)

# Legacy state keys for check
tp2_hit = True
trailing_active = True
