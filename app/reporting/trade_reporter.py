from __future__ import annotations
import logging
from datetime import datetime
log = logging.getLogger("trade_reporter")
def format_single_trade(action: dict, version: str = "v021") -> str:
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
        try:
            amount_f = float(amount)
            amount_str = f"{amount_f:.4f}" if amount_f < 1 else f"{amount_f:.2f}"
        except:
            amount_str = str(amount)[:12]
        try:
            price_f = float(price)
            price_str = f"{price_f:,.2f}" if price_f > 1000 else f"{price_f:.2f}" if price_f > 1 else f"{price_f:.4f}"
        except:
            price_str = str(price)[:12]
        try:
            notional_str = f"${float(notional):,.2f}"
        except:
            notional_str = str(notional)[:12]
        if direction == "buy":
            dir_emoji = "🟢"
            dir_text = "خرید" if intent in ("open", "increase") else "بستن شورت"
            dir_en = "LONG" if intent in ("open", "increase") else "CLOSE SHORT"
        else:
            dir_emoji = "🔴"
            dir_text = "فروش" if intent in ("open", "increase") else "بستن لانگ"
            dir_en = "SHORT" if intent in ("open", "increase") else "CLOSE LONG"
        status_emoji = "✅" if status in ("FILLED", "CLOSED", "PARTIAL") else "🧪" if status == "DRY_RUN" else "⏳"
        lines = []
        lines.append(f"{dir_emoji} {asset} {dir_en} | {dir_text}")
        lines.append(f"━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"💰 قیمت: {price_str} | مقدار: {amount_str}")
        lines.append(f"💵 ارزش: {notional_str}")
        lines.append(f"📊 وضعیت: {status_emoji} {status}")
        sl = action.get("sl") or action.get("stop_loss")
        tp1 = action.get("tp1")
        tp2 = action.get("tp2")
        if sl or tp1:
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
        lines.append(f"🆔 سفارش: {order_id}")
        lines.append(f"📝 نوع: {intent}")
        if ts:
            try:
                dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                lines.append(f"🕐 زمان: {dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")
            except:
                lines.append(f"🕐 زمان: {ts}")
        lines.append(f"🔖 نسخه: {version}")
        lines.append(f"━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"صرافی: AriaX Testnet | {inst}")
        return "\n".join(lines)
    except Exception as e:
        return f"🔔 معامله {action.get('asset','?')} {action.get('direction','?')} {action.get('amount',0)} @ {action.get('price',0)} | خطا: {e}"
