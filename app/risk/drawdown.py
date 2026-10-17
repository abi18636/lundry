"""
Risk Module - Drawdown Guard
Independent: Max DD 15%, capital preservation
"""
def check_drawdown(equity: float, peak: float, max_dd: float = 0.15) -> bool:
    """Return True if drawdown exceeded"""
    if peak <= 0:
        return False
    dd = (peak - equity) / peak
    return dd > max_dd
