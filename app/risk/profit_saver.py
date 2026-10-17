"""
Risk Module - Profit Saver
Independent: Save profit, BE, trailing
"""
def should_move_to_breakeven(current_price: float, entry: float, atr: float, side: int, be_trigger_atr: float = 1.5) -> bool:
    """BE only after 1.5xATR profit - Fix4"""
    if side > 0:
        return current_price >= entry + atr * be_trigger_atr
    else:
        return current_price <= entry - atr * be_trigger_atr
