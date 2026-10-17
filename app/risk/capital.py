"""
Risk Module - Capital Management
Independent: Only capital allocation, no strategy
Fix3: Sizing Guard <1000$ -> BTC/ETH only
"""
from __future__ import annotations
from typing import List

ALLOWED_ASSETS_FULL = ["BTC", "ETH", "SOL", "AVAX", "LINK", "XRP", "DOGE", "ADA", "DOT", "LTC"]
ALLOWED_ASSETS_SMALL_CAP = ["BTC", "ETH"]

def effective_assets(capital_usd: float, assets_str: str) -> List[str]:
    """Fix3: If <1000$ only BTC/ETH"""
    if capital_usd < 1000.0:
        return ALLOWED_ASSETS_SMALL_CAP.copy()
    return [a.strip() for a in assets_str.split(",") if a.strip() in ALLOWED_ASSETS_FULL]

def calculate_position_size(capital_usd: float, n_assets: int, lev_cap: float, mark_price: float, signal: int) -> float:
    """Conservative sizing within 15% DD"""
    if mark_price <= 0 or signal == 0:
        return 0.0
    allocated = (capital_usd / n_assets) * lev_cap
    qty = (allocated / mark_price) * signal
    return qty
