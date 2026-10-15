"""
v019 + FIXED56: Individual trade reporter - ultra simple single line for reliability
"""
from __future__ import annotations
import logging
log = logging.getLogger("trade_reporter")

def format_single_trade(action: dict, version: str = "v019") -> str:
    """Format single trade - v019 ultra simple single line to avoid Telegram 400"""
    try:
        asset = str(action.get("asset","?"))[:10]
        direction = str(action.get("direction","?"))[:10]
        amount = action.get("filled_amount") or action.get("amount",0)
        price = action.get("price") or action.get("limit_price",0)
        notional = action.get("notional_usd",0)
        
        try:
            amount_str = f"{float(amount):.4f}"
        except:
            amount_str = str(amount)[:10]
        try:
            price_str = f"{float(price):.2f}"
        except:
            price_str = str(price)[:10]
        try:
            notional_str = f"{float(notional):.1f}"
        except:
            notional_str = str(notional)[:10]
        
        # Ultra simple single line - no newlines, no special chars that could break
        return f"TRADE {version} {asset} {direction} {amount_str} @ {price_str} ${notional_str}"
    except Exception as e:
        return f"TRADE {action.get('asset','?')} {action.get('direction','?')} {action.get('amount',0)}"
