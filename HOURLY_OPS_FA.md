# نظارت ساعتی (Hourly Watchdog)

## هدف
هر ساعت وضعیت ربات زنده را بررسی و در صورت نیاز tick/start می‌زند؛ فقط خطاهای واقعی سفارش (نه halt بازار) را به تلگرام می‌فرستد.

## اجرا
```bash
export BOT_URL=https://zenith-trader-bot.onrender.com
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...
python3 scripts/hourly_watchdog.py
# یا حلقه مداوم:
bash scripts/run_watchdog_loop.sh
```

## رفتار
- `/health` — اگر unreachable → هشدار TG
- اگر `running=false` → `POST /api/start`
- اگر `last_loop_at` > 15 دقیقه → `POST /api/tick`
- اگر `status=error` و بازار باز → TG نمونه خطا
- اگر اکثر بازارها `market_halted` → فقط لاگ (بدون اسپم TG)

## لاگ
`state/watchdog.log`
