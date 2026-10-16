"""
PROFESSIONAL Trade Reporter - Clear, Professional, Persian/English
"""
from __future__ import annotations
import logging
from datetime import datetime

log = logging.getLogger("trade_reporter")

def format_single_trade(action: dict, version: str = "v019") -> str:
    """Professional trade report - clear, structured, with SL/TP"""
    try:
        asset = str(action.get("asset", "?")).upper()[:10]
        inst = str(action.get("instrument", "?"))[:15]
        direction = str(action.get("direction", "?")).lower()
        amount = action.get("filled_amount") or action.get("amount", 0)
        price = action.get("price") or action.get("limit_price", 0)
        notional = action.get("notional_usd", 0)
        order_id = str(action.get("order_id", "?"))[:20]
        status = str(action.get("status", "?")).upper()[:15]
        intent = str(action.get("intent", "?")).lower()[:15]
        ts = str(action.get("ts", ""))[:19]
        
        # Format numbers
        try:
            amount_f = float(amount)
            amount_str = f"{amount_f:.4f}" if amount_f < 1 else f"{amount_f:.2f}"
        except:
            amount_str = str(amount)[:12]
        
        try:
            price_f = float(price)
            if price_f > 1000:
                price_str = f"{price_f:,.2f}"
            elif price_f > 1:
                price_str = f"{price_f:.2f}"
            else:
                price_str = f"{price_f:.4f}"
        except:
            price_str = str(price)[:12]
        
        try:
            notional_f = float(notional)
            notional_str = f"${notional_f:,.2f}"
        except:
            notional_str = str(notional)[:12]
        
        # Direction and emoji
        if direction == "buy":
            dir_emoji = "🟢"
            dir_text = "خرید" if intent in ("open", "increase") else "بستن شورت"
            dir_en = "LONG" if intent in ("open", "increase") else "CLOSE SHORT"
        else:
            dir_emoji = "🔴"
            dir_text = "فروش" if intent in ("open", "increase") else "بستن لانگ"
            dir_en = "SHORT" if intent in ("open", "increase") else "CLOSE LONG"
        
        # Status emoji
        if status in ("FILLED", "CLOSED", "PARTIAL"):
            status_emoji = "✅"
        elif status in ("DRY_RUN",):
            status_emoji = "🧪"
        else:
            status_emoji = "⏳"
        
        # Build professional report
        lines = []
        lines.append(f"{dir_emoji} {asset} {dir_en} | {dir_text}")
        lines.append(f"━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"💰 قیمت: {price_str} | مقدار: {amount_str}")
        lines.append(f"💵 ارزش: {notional_str}")
        lines.append(f"📊 وضعیت: {status_emoji} {status}")
        
        # Add SL/TP if available from action or intent
        sl = action.get("sl") or action.get("stop_loss")
        tp = action.get("tp") or action.get("take_profit")
        tp1 = action.get("tp1")
        tp2 = action.get("tp2")
        
        if sl or tp or tp1:
            lines.append(f"🛡️ حد ضرر/سود:")
            if sl:
                try:
                    lines.append(f"   SL: {float(sl):,.2f}")
                except:
                    lines.append(f"   SL: {sl}")
            if tp1 and tp2:
                try:
                    lines.append(f"   TP1: {float(tp1):,.2f} | TP2: {float(tp2):,.2f}")
                except:
                    lines.append(f"   TP1: {tp1} | TP2: {tp2}")
            elif tp:
                try:
                    lines.append(f"   TP: {float(tp):,.2f}")
                except:
                    lines.append(f"   TP: {tp}")
        
        lines.append(f"🆔 سفارش: {order_id}")
        lines.append(f"📝 نوع: {intent}")
        if ts:
            # Convert ISO to readable
            try:
                dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                ts_readable = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
                lines.append(f"🕐 زمان: {ts_readable}")
            except:
                lines.append(f"🕐 زمان: {ts}")
        
        lines.append(f"🔖 نسخه: {version}")
        lines.append(f"━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"صرافی: AriaX Testnet | {inst}")
        
        return "\n".join(lines)
    
    except Exception as e:
        # Fallback ultra simple but professional
        try:
            asset = action.get("asset", "?")
            direction = action.get("direction", "?")
            amount = action.get("amount", 0)
            price = action.get("price", 0)
            return f"🔔 معامله {asset} {direction} {amount} @ {price} | خطا فرمت: {e}"
        except:
            return f"Trade report error: {e}"

def format_position(pos: dict) -> str:
    """Professional position report"""
    try:
        symbol = pos.get("symbol") or pos.get("instrument_name") or "?"
        size = float(pos.get("size") or 0)
        entry = float(pos.get("entry") or pos.get("average_price") or 0)
        mark = float(pos.get("mark") or pos.get("mark_price") or 0)
        upnl = float(pos.get("upnl") or pos.get("floating_profit_loss") or 0)
        lev = pos.get("lev") or pos.get("leverage") or 1
        
        side = "LONG" if size > 0 else "SHORT"
        emoji = "🟢" if size > 0 else "🔴"
        
        pnl_emoji = "📈" if upnl >= 0 else "📉"
        
        lines = [
            f"{emoji} {symbol} {side} x{lev}",
            f"مقدار: {abs(size):.4f} | ورود: {entry:,.2f} | مارک: {mark:,.2f}",
            f"{pnl_emoji} سود: {upnl:+.2f} USDT",
        ]
        return "\n".join(lines)
    except Exception as e:
        return f"Position error: {e}"

def format_balance(wallet: dict) -> str:
    """Professional balance report"""
    try:
        balances = wallet.get("balances", {}) if isinstance(wallet, dict) else {}
        usdt = balances.get("USDT", 0)
        equity = wallet.get("equity", usdt)
        
        lines = [
            f"💰 موجودی",
            f"━━━━━━━━━━━━━━━━━━━━",
            f"USDT: {float(usdt):,.2f}",
            f"ارزش کل: {float(equity):,.2f} USDT",
        ]
        
        # Add other assets if non-zero
        for asset in ["BTC", "ETH", "SOL"]:
            bal = balances.get(asset, 0)
            if abs(float(bal or 0)) > 0.0001:
                lines.append(f"{asset}: {float(bal):.6f}")
        
        return "\n".join(lines)
    except Exception as e:
        return f"Balance error: {e}"
