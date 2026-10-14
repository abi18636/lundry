"""
FIXED52: Individual trade reporter - گزارش تکی هر معامله به تلگرام
"""
from __future__ import annotations
import logging
import html
log = logging.getLogger("trade_reporter")

def _esc(s):
    try:
        return html.escape(str(s))[:200]
    except:
        return str(s)[:200]

def format_single_trade(action: dict, version: str = "v018") -> str:
    """Format single trade for telegram - FIXED52 with HTML escaping"""
    try:
        asset = _esc(action.get("asset","?"))
        inst = _esc(action.get("instrument","?"))
        direction = _esc(action.get("direction","?"))
        amount = action.get("filled_amount") or action.get("amount",0)
        price = action.get("price") or action.get("limit_price",0)
        notional = action.get("notional_usd",0)
        order_id = _esc(action.get("order_id","?"))
        status = _esc(action.get("status","?"))
        intent = _esc(action.get("intent","?"))
        reason = _esc(action.get("reason","")[:200])
        
        # Side emoji
        if direction == "buy":
            emoji = "🟢 خرید" if intent in ("open","increase") else "🟢 بستن شورت"
        else:
            emoji = "🔴 فروش" if intent in ("open","increase") else "🔴 بستن لانگ"
        
        # Format numbers safely
        try:
            amount_str = f"{float(amount):.6f}"
        except:
            amount_str = str(amount)
        try:
            price_str = f"{float(price):.4f}"
        except:
            price_str = str(price)
        try:
            notional_str = f"{float(notional):.2f}"
        except:
            notional_str = str(notional)
        
        lines = [
            f"💰 <b>معامله تکی — {_esc(version)}</b>",
            f"🕐 {_esc(action.get('ts',''))}",
            f"🔖 ورژن: <b>{_esc(version)}</b>",
            f"",
            f"{emoji} <b>{asset}</b> ({inst})",
            f"مقدار: {amount_str} @ {price_str}",
            f"ارزش: ${notional_str}",
            f"نوع: {intent} | وضعیت: {status}",
            f"Order ID: {order_id}",
            f"",
            f"دلیل: {reason}",
            f"",
            f"📊 برای بستن: دکمه بستن {asset} در پنل",
        ]
        return "\n".join(lines)
    except Exception as e:
        import traceback
        return f"Trade report error: {e} | {str(action)[:500]}"
