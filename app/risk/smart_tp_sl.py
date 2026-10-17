"""
Risk Module - Smart TP/SL
Independent: Only SL/TP calculation, no execution
Fix4: No zero-distance, 1.5xATR buffer, BE only after 1.5xATR
"""
from __future__ import annotations

def calculate_initial_levels(entry: float, side: int, atr: float, cfg: dict) -> dict:
    """Fix4: Smart TP/SL with ATR buffers"""
    try:
        sl_atr = cfg.get("smart_sl_atr", 2.0)
        tp1_atr = cfg.get("smart_tp1_atr", 2.5)
        tp2_atr = cfg.get("smart_tp2_atr", 4.0)
        be_trigger = cfg.get("smart_be_trigger_atr", 1.5)
        
        if side > 0:  # Long
            sl = entry - atr * sl_atr
            tp1 = entry + atr * tp1_atr
            tp2 = entry + atr * tp2_atr
            # Ensure no zero-distance: SL < entry < TP
            if sl >= entry:
                sl = entry - atr * 1.5
            if tp1 <= entry:
                tp1 = entry + atr * 2.5
        else:  # Short
            sl = entry + atr * sl_atr
            tp1 = entry - atr * tp1_atr
            tp2 = entry - atr * tp2_atr
            if sl <= entry:
                sl = entry + atr * 1.5
            if tp1 >= entry:
                tp1 = entry - atr * 2.5
        
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
            "highest_price": entry,
            "lowest_price": entry,
            "be_trigger_atr": be_trigger
        }
    except Exception as e:
        return {"entry_price": entry, "side": side, "atr": atr, "sl": entry*0.98, "tp": entry*1.02, "tp1": entry*1.02, "tp2": entry*1.04, "level": 0, "breakeven_moved": False, "tp1_hit": False, "highest_price": entry, "lowest_price": entry}

def evaluate_smart_exit(current_price: float, position: dict, cfg: dict) -> dict:
    """Evaluate if should close based on SL/TP/BE"""
    try:
        entry = position.get("entry_price", 0)
        side = position.get("side", 0)
        sl = position.get("sl", 0)
        tp1 = position.get("tp1", 0)
        tp2 = position.get("tp2", 0)
        atr = position.get("atr", 0)
        be_trigger = position.get("be_trigger_atr", 1.5)
        
        if side > 0:  # Long
            if current_price <= sl:
                return {"should_close": True, "reason": "SL", "close_fraction": 1.0}
            if current_price >= tp2:
                return {"should_close": True, "reason": "TP2", "close_fraction": 1.0}
            if current_price >= tp1 and not position.get("tp1_hit"):
                return {"should_close": True, "reason": "TP1", "close_fraction": 0.5, "new_sl": entry}
            # BE only after 1.5xATR profit
            if not position.get("breakeven_moved") and current_price >= entry + atr * be_trigger:
                return {"should_close": False, "reason": "BE", "new_sl": entry}
        else:  # Short
            if current_price >= sl:
                return {"should_close": True, "reason": "SL", "close_fraction": 1.0}
            if current_price <= tp2:
                return {"should_close": True, "reason": "TP2", "close_fraction": 1.0}
            if current_price <= tp1 and not position.get("tp1_hit"):
                return {"should_close": True, "reason": "TP1", "close_fraction": 0.5, "new_sl": entry}
            if not position.get("breakeven_moved") and current_price <= entry - atr * be_trigger:
                return {"should_close": False, "reason": "BE", "new_sl": entry}
        
        return {"should_close": False, "reason": "hold"}
    except:
        return {"should_close": False, "reason": "error"}
