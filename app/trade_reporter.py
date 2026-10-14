"""
FIXED49: Individual trade reporter - گزارش تکی هر معامله به تلگرام
"""
from __future__ import annotations
import logging
log = logging.getLogger("trade_reporter")

def format_single_trade(action: dict, version: str = "v018") -> str:
    """Format single trade for telegram"""
    try:
        asset = action.get("asset","?")
        inst = action.get("instrument","?")
        direction = action.get("direction","?")
        amount = action.get("filled_amount") or action.get("amount",0)
        price = action.get("price") or action.get("limit_price",0)
        notional = action.get("notional_usd",0)
        order_id = action.get("order_id","?")
        status = action.get("status","?")
        intent = action.get("intent","?")
        reason = action.get("reason","")[:200]
        
        # Calculate profit if close
        pnl_text = ""
        if intent == "close":
            pnl_text = f" | بستن پوزیشن"
        
        # Side emoji
        if direction == "buy":
            emoji = "🟢 خرید" if intent in ("open","increase") else "🟢 بستن شورت"
        else:
            emoji = "🔴 فروش" if intent in ("open","increase") else "🔴 بستن لانگ"
        
        lines = [
            f"💰 <b>معامله تکی — {version}</b>",
            f"🕐 {action.get('ts','')}",
            f"🔖 ورژن: <b>{version}</b>",
            f"",
            f"{emoji} <b>{asset}</b> ({inst})",
            f"مقدار: {amount} @ {price}",
            f"ارزش: ${notional:.2f}",
            f"نوع: {intent} | وضعیت: {status}",
            f"Order ID: {order_id}",
            f"",
            f"دلیل: {reason}",
            f"{pnl_text}",
            f"",
            f"📊 برای بستن: دکمه 🟢/🔴 بستن {asset} در پنل",
        ]
        return "\n".join(lines)
    except Exception as e:
        return f"Trade report error: {e} | {action}"

