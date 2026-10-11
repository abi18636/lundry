"""
v017 LAYER 4: Smart Exit Management - Trailing Stop + Partial Take Profit
Protect profit, reduce give-back
"""
from __future__ import annotations

import logging
from typing import Dict

log = logging.getLogger("exit_manager")

def manage_position_exit(asset, current_price, position_state, atr):
    """
    Dynamic exit management:
    1. When profit reaches 1xATR, move SL to breakeven
    2. When profit reaches 2xATR, close 50% (partial profit lock)
    3. For remainder, trailing stop 1.5xATR from current price
    """
    entry = position_state.get("entry_price", 0)
    side = position_state.get("side", 0)
    sl = position_state.get("sl")
    partial_taken = position_state.get("partial_taken", False)
    breakeven_moved = position_state.get("breakeven_moved", False)
    
    if entry <= 0 or side == 0 or atr <= 0:
        return {"new_sl": sl, "close_fraction": 0.0, "reason": "", "breakeven_moved": breakeven_moved, "partial_taken": partial_taken}
    
    pnl_in_atr = (current_price - entry) / atr * side if atr > 0 else 0
    
    actions = {"new_sl": sl, "close_fraction": 0.0, "reason": "", "breakeven_moved": breakeven_moved, "partial_taken": partial_taken}
    
    # Stage 1: Move SL to breakeven after 1xATR profit
    if pnl_in_atr >= 1.0 and not breakeven_moved:
        actions["new_sl"] = entry
        actions["breakeven_moved"] = True
        actions["reason"] = f"Moved SL to breakeven {entry:.2f} after 1xATR profit {pnl_in_atr:.1f}x"
        log.info(f"{asset} breakeven SL: {entry} after {pnl_in_atr:.1f}xATR")
    
    # Stage 2: Partial profit take at 2xATR
    if pnl_in_atr >= 2.0 and not partial_taken:
        actions["close_fraction"] = 0.5
        actions["partial_taken"] = True
        actions["reason"] += f" | Partial 50% profit-take at 2xATR {pnl_in_atr:.1f}x"
        log.info(f"{asset} partial 50% at 2xATR profit {pnl_in_atr:.1f}x")
    
    # Stage 3: Trailing stop for remainder after 2xATR
    if pnl_in_atr >= 2.0:
        trailing_sl = current_price - (1.5 * atr * side)
        # Only move SL in profitable direction
        if side > 0:  # LONG
            if actions["new_sl"] is None or trailing_sl > actions["new_sl"]:
                actions["new_sl"] = trailing_sl
                actions["reason"] += f" | Trailing SL {trailing_sl:.2f}"
        else:  # SHORT
            if actions["new_sl"] is None or trailing_sl < actions["new_sl"]:
                actions["new_sl"] = trailing_sl
                actions["reason"] += f" | Trailing SL {trailing_sl:.2f}"
    
    return actions


def check_exit_triggers(asset, current_price, position_state):
    """
    Check if SL/TP or trailing stop triggered
    Returns: None or "stop_loss" or "take_profit" or "trailing_stop" or "partial_take"
    """
    if not position_state:
        return None
    
    side = position_state.get("side", 0)
    sl = position_state.get("sl")
    tp = position_state.get("tp")
    entry = position_state.get("entry_price", 0)
    
    if side == 0 or entry <= 0:
        return None
    
    # Check SL
    if sl is not None:
        if side > 0 and current_price <= sl:
            return "stop_loss"
        if side < 0 and current_price >= sl:
            return "stop_loss"
    
    # Check TP
    if tp is not None:
        if side > 0 and current_price >= tp:
            return "take_profit"
        if side < 0 and current_price <= tp:
            return "take_profit"
    
    return None
