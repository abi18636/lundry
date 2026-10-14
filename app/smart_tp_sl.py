"""
v018 FIXED53 SMART TP/SL LADDER
هوشمند پله‌ای:
- ثبت TP/SL در معاملات
- TP هوشمند با سطوح چندگانه
- وقتی سود ده: tp=tp1, sl=tp به صورت پله‌ای

Levels:
L0: Entry, SL0 = entry ± 2*ATR, TP1 = entry ± 1.5*ATR
L1: Profit 1*ATR -> SL = breakeven, close 25%
L2: Hit TP1 -> close 30%, SL = TP1 (lock profit), TP = TP2 (3*ATR)
L3: Hit TP2 -> close 30%, SL = TP2, TP = TP3 (5*ATR)
L4: Hit TP3 -> trailing SL 1.5*ATR, or close remaining 15%
"""
from __future__ import annotations
import logging
from typing import Dict, Optional, Tuple
log = logging.getLogger("smart_tp_sl")

def calculate_smart_levels(entry: float, side: int, atr: float, cfg: dict = None) -> dict:
    """Calculate multi-level TP/SL based on ATR"""
    cfg = cfg or {}
    sl_atr = cfg.get('sl_atr', 2.0)  # initial SL 2 ATR
    tp1_atr = cfg.get('tp1_atr', 1.5)
    tp2_atr = cfg.get('tp2_atr', 3.0)
    tp3_atr = cfg.get('tp3_atr', 5.0)
    tp4_atr = cfg.get('tp4_atr', 8.0)
    
    if entry <=0 or atr <=0 or side ==0:
        return {}
    
    if side > 0:  # LONG
        sl0 = entry - sl_atr * atr
        tp1 = entry + tp1_atr * atr
        tp2 = entry + tp2_atr * atr
        tp3 = entry + tp3_atr * atr
        tp4 = entry + tp4_atr * atr
    else:  # SHORT
        sl0 = entry + sl_atr * atr
        tp1 = entry - tp1_atr * atr
        tp2 = entry - tp2_atr * atr
        tp3 = entry - tp3_atr * atr
        tp4 = entry - tp4_atr * atr
    
    return {
        "entry_price": entry,
        "side": side,
        "atr": atr,
        "sl": sl0,
        "tp": tp1,  # current TP
        "sl_initial": sl0,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "tp4": tp4,
        "level": 0,  # 0=initial, 1=breakeven, 2=tp1 hit, 3=tp2 hit, 4=tp3 hit
        "breakeven_moved": False,
        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False,
        "partial_taken": 0.0,  # fraction closed
        "profit_locked": 0.0,
        "highest_profit_atr": 0.0,
        "trailing_active": False,
    }

def manage_smart_exit(asset: str, current_price: float, state: dict, atr: float, cfg: dict = None) -> dict:
    """
    Smart TP/SL ladder management
    Returns: {
        new_sl, new_tp, close_fraction, level, reason, 
        breakeven_moved, tp1_hit, etc
    }
    """
    cfg = cfg or {}
    entry = state.get("entry_price", 0)
    side = state.get("side", 0)
    sl = state.get("sl")
    tp = state.get("tp")
    level = state.get("level", 0)
    partial = state.get("partial_taken", 0.0)
    
    if entry <=0 or side ==0 or atr <=0:
        return {
            "new_sl": sl, "new_tp": tp, "close_fraction": 0.0,
            "level": level, "reason": "invalid state",
            "breakeven_moved": state.get("breakeven_moved", False),
            "tp1_hit": state.get("tp1_hit", False),
            "tp2_hit": state.get("tp2_hit", False),
            "tp3_hit": state.get("tp3_hit", False),
            "partial_taken": partial,
        }
    
    # Calculate profit in ATR units
    if side > 0:
        profit_atr = (current_price - entry) / atr if atr>0 else 0
    else:
        profit_atr = (entry - current_price) / atr if atr>0 else 0
    
    # Track highest profit
    highest = max(state.get("highest_profit_atr", 0), profit_atr)
    
    actions = {
        "new_sl": sl,
        "new_tp": tp,
        "close_fraction": 0.0,
        "level": level,
        "reason": "",
        "breakeven_moved": state.get("breakeven_moved", False),
        "tp1_hit": state.get("tp1_hit", False),
        "tp2_hit": state.get("tp2_hit", False),
        "tp3_hit": state.get("tp3_hit", False),
        "partial_taken": partial,
        "highest_profit_atr": highest,
        "trailing_active": state.get("trailing_active", False),
        "profit_locked": state.get("profit_locked", 0.0),
    }
    
    tp1 = state.get("tp1")
    tp2 = state.get("tp2")
    tp3 = state.get("tp3")
    tp4 = state.get("tp4")
    
    # === LEVEL 0 -> 1: Breakeven after 1 ATR profit === FIXED53
    if level == 0 and profit_atr >= 1.0 and not state.get("breakeven_moved"):
        actions["new_sl"] = entry
        actions["breakeven_moved"] = True
        actions["level"] = 1
        actions["close_fraction"] = 0.20
        actions["partial_taken"] = partial + 0.20
        actions["reason"] = f"سطح 1: سود {profit_atr:.1f}xATR -> SL به نقطه ورود {entry:.4f} | 20% بستن"
        actions["profit_locked"] = 0.0
        log.info(f"{asset} L1 breakeven: profit {profit_atr:.1f} ATR, SL->entry, close 20%")
        return actions
    
    # === LEVEL 1 -> 2: Hit TP1 (1.5 ATR) ===
    if level <=1:
        hit_tp1 = False
        if side > 0 and tp1 and current_price >= tp1:
            hit_tp1 = True
        if side < 0 and tp1 and current_price <= tp1:
            hit_tp1 = True
        
        if hit_tp1 and not state.get("tp1_hit"):
            actions["tp1_hit"] = True
            actions["level"] = 2
            actions["new_sl"] = tp1  # SL = TP1 (lock profit) as user requested: sl=tp
            actions["new_tp"] = tp2  # TP becomes TP2
            actions["close_fraction"] = 0.30  # close 30% as user: tp=tp1
            actions["partial_taken"] = partial + 0.30
            actions["profit_locked"] = abs(tp1 - entry) * 0.30 if side else 0
            actions["reason"] = f"سطح 2: TP1 hit {tp1:.4f} → بستن 30% | SL=TP1 {tp1:.4f} | TP جدید=TP2 {tp2:.4f}"
            log.info(f"{asset} L2 TP1 hit: close 30%, SL->TP1 {tp1}, TP->TP2 {tp2}")
            return actions
    
    # === LEVEL 2 -> 3: Hit TP2 (3 ATR) ===
    if level <=2 and state.get("tp1_hit"):
        hit_tp2 = False
        if side > 0 and tp2 and current_price >= tp2:
            hit_tp2 = True
        if side < 0 and tp2 and current_price <= tp2:
            hit_tp2 = True
        
        if hit_tp2 and not state.get("tp2_hit"):
            actions["tp2_hit"] = True
            actions["level"] = 3
            actions["new_sl"] = tp2  # SL = TP2 (user: tp=tp1, sl=tp -> now sl=tp2)
            actions["new_tp"] = tp3
            actions["close_fraction"] = 0.30
            actions["partial_taken"] = partial + 0.30
            actions["profit_locked"] = state.get("profit_locked",0) + abs(tp2 - entry)*0.30
            actions["reason"] = f"سطح 3: TP2 hit {tp2:.4f} → بستن 30% | SL=TP2 {tp2:.4f} | TP جدید=TP3 {tp3:.4f}"
            log.info(f"{asset} L3 TP2 hit: close 30%, SL->TP2 {tp2}, TP->TP3 {tp3}")
            return actions
    
    # === LEVEL 3 -> 4: Hit TP3 (5 ATR) ===
    if level <=3 and state.get("tp2_hit"):
        hit_tp3 = False
        if side > 0 and tp3 and current_price >= tp3:
            hit_tp3 = True
        if side < 0 and tp3 and current_price <= tp3:
            hit_tp3 = True
        
        if hit_tp3 and not state.get("tp3_hit"):
            actions["tp3_hit"] = True
            actions["level"] = 4
            actions["new_sl"] = tp3
            actions["new_tp"] = tp4
            actions["close_fraction"] = 0.15  # close 15% leaving 5% runner
            actions["partial_taken"] = partial + 0.15
            actions["trailing_active"] = True
            actions["reason"] = f"سطح 4: TP3 hit {tp3:.4f} → بستن 15% | SL=TP3 | تریلینگ فعال TP4={tp4:.4f}"
            log.info(f"{asset} L4 TP3 hit: close 15%, trailing active")
            return actions
    
    # === LEVEL 4+: Trailing stop ===
    if level >=4 or state.get("trailing_active"):
        # Trailing SL 1.5 ATR from current price
        trailing_atr_mult = cfg.get('trailing_atr', 1.5)
        if side > 0:
            trailing_sl = current_price - trailing_atr_mult * atr
            # Only move SL up (profitable direction)
            if actions["new_sl"] is None or trailing_sl > actions["new_sl"]:
                # Don't move SL below TP2/TP3 locked level
                min_sl = tp2 if state.get("tp2_hit") else tp1 if state.get("tp1_hit") else entry
                if trailing_sl > min_sl:
                    actions["new_sl"] = trailing_sl
                    actions["reason"] = f"تریلینگ: SL به {trailing_sl:.4f} (1.5 ATR از قیمت)"
                    log.info(f"{asset} trailing SL -> {trailing_sl}")
        else:
            trailing_sl = current_price + trailing_atr_mult * atr
            if actions["new_sl"] is None or trailing_sl < actions["new_sl"]:
                max_sl = tp2 if state.get("tp2_hit") else tp1 if state.get("tp1_hit") else entry
                if trailing_sl < max_sl:
                    actions["new_sl"] = trailing_sl
                    actions["reason"] = f"تریلینگ: SL به {trailing_sl:.4f}"
                    log.info(f"{asset} trailing SL -> {trailing_sl}")
        actions["trailing_active"] = True
        actions["level"] = max(level, 4)
    
    return actions

def check_smart_triggers(asset: str, current_price: float, state: dict) -> Optional[str]:
    """Check if SL/TP triggered"""
    if not state:
        return None
    side = state.get("side", 0)
    sl = state.get("sl")
    tp = state.get("tp")
    entry = state.get("entry_price", 0)
    
    if side ==0 or entry <=0:
        return None
    
    # Check SL
    if sl is not None:
        if side > 0 and current_price <= sl:
            return f"stop_loss SL={sl:.4f} Level={state.get('level',0)}"
        if side < 0 and current_price >= sl:
            return f"stop_loss SL={sl:.4f} Level={state.get('level',0)}"
    
    # Check TP (current TP level)
    if tp is not None:
        if side > 0 and current_price >= tp:
            return f"take_profit TP={tp:.4f} Level={state.get('level',0)}"
        if side < 0 and current_price <= tp:
            return f"take_profit TP={tp:.4f} Level={state.get('level',0)}"
    
    return None
