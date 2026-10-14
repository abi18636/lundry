"""
FIXED53: Individual trade reporter - plain text for reliability
"""
from __future__ import annotations
import logging
log = logging.getLogger("trade_reporter")

def format_single_trade(action: dict, version: str = "v018") -> str:
    """Format single trade - FIXED53 plain text without HTML to avoid parse errors"""
    try:
        asset = str(action.get("asset","?"))[:20]
        inst = str(action.get("instrument","?"))[:20]
        direction = str(action.get("direction","?"))
        amount = action.get("filled_amount") or action.get("amount",0)
        price = action.get("price") or action.get("limit_price",0)
        notional = action.get("notional_usd",0)
        order_id = str(action.get("order_id","?"))[:30]
        status = str(action.get("status","?"))[:30]
        intent = str(action.get("intent","?"))[:20]
        
        # Safe number formatting
        try:
            amount_str = f"{float(amount):.6f}"
        except:
            amount_str = str(amount)[:20]
        try:
            price_str = f"{float(price):.4f}"
        except:
            price_str = str(price)[:20]
        try:
            notional_str = f"{float(notional):.2f}"
        except:
            notional_str = str(notional)[:20]
        
        # Simple emoji without HTML
        if direction == "buy":
            emoji = "BUY" if intent in ("open","increase") else "CLOSE SHORT"
        else:
            emoji = "SELL" if intent in ("open","increase") else "CLOSE LONG"
        
        lines = [
            f"TRADE {version} - {asset} {emoji}",
            f"Time: {action.get('ts','')}",
            f"Version: {version}",
            f"Instrument: {inst}",
            f"Amount: {amount_str} @ {price_str}",
            f"Notional: ${notional_str}",
            f"Type: {intent} Status: {status}",
            f"Order ID: {order_id}",
            f"Close btn: close:{asset}",
        ]
        return "\n".join(lines)
    except Exception as e:
        return f"Trade {action.get('asset','?')} {action.get('direction','?')} {action.get('amount',0)} error:{e}"

def format_single_trade_html(action: dict, version: str = "v018") -> str:
    """HTML version - kept for reference but not used in FIXED53"""
    try:
        import html
        def _esc(s):
            return html.escape(str(s))[:200]
        asset = _esc(action.get("asset","?"))
        # ... same as before but not used
        return f"Trade {asset}"
    except:
        return "Trade error"
