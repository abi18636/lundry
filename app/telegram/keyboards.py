"""
Telegram Module - Keyboards
Independent: Only keyboard layouts
"""
from typing import List

def main_keyboard(positions: List[dict] = None, dashboard_url: str = None) -> dict:
    keyboard = [
        [{"text": "📊 وضعیت", "callback_data": "panel:status"}, {"text": "💼 پوزیشن", "callback_data": "panel:positions"}, {"text": "💰 موجودی", "callback_data": "panel:balance"}],
        [{"text": "🧩 آستین‌ها", "callback_data": "panel:sleeves"}, {"text": "📈 آمار", "callback_data": "panel:stats"}, {"text": "🧾 معاملات", "callback_data": "panel:trades"}],
        [{"text": "⚡ Tick", "callback_data": "panel:tick"}, {"text": "📄 گزارش فایل", "callback_data": "panel:file"}, {"text": "❤️ Health", "callback_data": "panel:health"}],
        [{"text": "🧪 تست ۶۰ثانیه", "callback_data": "panel:testtrade"}, {"text": "⏱ وضعیت تست", "callback_data": "panel:teststatus"}, {"text": "🛑 بستن تست", "callback_data": "panel:testclose"}],
    ]
    if positions:
        active_pos = []
        for p in positions:
            try:
                sz = float(p.get("size_currency") or p.get("size") or 0)
                if abs(sz) > 1e-12:
                    active_pos.append(p)
            except:
                active_pos.append(p)
        if active_pos:
            close_row = []
            for p in active_pos[:6]:
                inst = p.get("instrument_name", "")
                asset = inst.replace("USDT", "").replace("USD", "") if inst else "UNKNOWN"
                direction = p.get("direction", "")
                icon = "🟢" if direction in ("buy", "long") else "🔴"
                close_row.append({"text": f"{icon} بستن {asset}", "callback_data": f"close:{asset}"})
                if len(close_row) == 2:
                    keyboard.append(close_row)
                    close_row = []
            if close_row:
                keyboard.append(close_row)
            keyboard.append([{"text": "🛑 بستن همه پوزیشن‌ها", "callback_data": "panel:closeall"}])
    keyboard.append([{"text": "▶️ Start", "callback_data": "panel:start"}, {"text": "⏹ Stop", "callback_data": "panel:stop"}, {"text": "🔄 پنل", "callback_data": "panel:home"}])
    if dashboard_url:
        keyboard.append([{"text": "🌐 داشبورد وب", "url": dashboard_url}])
    return {"inline_keyboard": keyboard}
