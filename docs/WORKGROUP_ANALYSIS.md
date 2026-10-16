# کارگروه بررسی کامل - Zenith Trader Bot v019

## تاریخچه گفتگو از اول تا آخر

### فاز 1: v018 FIXED56
- تلگرام با failed 0 کار می‌کرد (build cd5d804)
- پنل با دکمه‌های بستن مجزا
- گزارش معامله تکی per fill

### فاز 2: v019 پچ جدید (New Text Document 3.txt)
5 فیکس درخواستی:
1. Fix1 Maker/Post-Only: حذف IOC 1% slippage، استفاده از limit post_only در Best Bid/Ask
2. Fix2 Candle Close Gating: انتقال از 60s به بسته شدن ساعتی، threshold $1→$15
3. Fix3 Sizing Guard: <1000$ فقط BTC/ETH برای جلوگیری از لات <$5
4. Fix4 Smart TP/SL: بازنویسی بدون باگ zero-distance، بافر 1.5×ATR، BE فقط بعد 1.5×ATR
5. Fix5 Full Async Engine: httpx.AsyncClient + asyncio.gather 30s→800ms

### فاز 3: مشکلات تلگرام (50+ بیلد)
**علائم:**
- trades_alerted 4 sent 0 failed 4 last_error null
- /send_test 200 OK (message_id 9682) و /send_trade_test 200 OK (9684) کار می‌کرد
- اما معاملات موتور fail می‌شد

**ریشه‌یابی:**
1. **فساد فایل telegram_bot.py:** 
   - duplicate `def _api` (2 بار)
   - duplicate `def send` (2 بار)
   - duplicate `def send_trade_report` (2 بار) - یکی `-> None` و یکی `-> bool`
   - duplicate stats increment در exception handler: دو بار `trades_alerted+=1 failed+=1` بدون ارسال
   - باعث می‌شد `failed` زیاد شود ولی `last_error` null بماند

2. **Async blocking:**
   - `run_cycle_async` داخلش `httpx.post` سینک را صدا می‌زد
   - باعث timeout در event loop

3. **راه‌حل نهایی:**
   - بازسازی از fixed56 اصلی 689 خطی
   - 1 _api, 1 send, 1 trade_report -> bool
   - حذف duplicate stats
   - queue + to_thread: _log_fill فقط queue، flush via asyncio.to_thread
   - init_reporter wrapper

**نتیجه:** Build d155348 / fa88036 / 6d6f1e0 با stats 2/2/0 ✅

### فاز 4: معاملات شماتیک و پنل تلگرام
**گزارش کاربر:** معاملات شماتیک، پنل تست کار نمی‌کند، گزارشات غیر واقعی

**بررسی:**
- /api/debug/env: ARIAX_API_KEY_exists false
- config.py: dry_run=True پیش‌فرض
- effective_dry_run: True چون کلید نیست → dry_run simulated
- orders_log: status dry_run
- telegram_bot.py: effective_test_trade_enabled وجود نداشت → AttributeError
- engine.py: test_trader وجود نداشت → "test_trader در دسترس نیست"

**فیکس اعمال شده:**
- config: effective_test_trade_enabled, effective_dry_run, test_trade_asset, hold_seconds
- engine: SimpleAriaXTestTrader با شبیه‌سازی dry_run و معامله واقعی
- اما کاربر می‌گوید ربات کلا کار نمی‌کند

### فاز 5: درخواست کارگروه جداگانه
کاربر درخواست بررسی کامل از اول تا آخر و حل تمام مشکلات

## لیست کامل مشکلات باقی‌مانده

1. **کلید صرافی:** ARIAX_API_KEY در Render تنظیم نشده → معاملات شبیه‌سازی
2. **DRY_RUN:** حتی با کلید، باید DRY_RUN=false تنظیم شود
3. **پنل تلگرام:** دکمه تست باید با test_trader کار کند
4. **گزارشات غیر واقعی:** به خاطر dry_run status و notional کوچک
5. **رگرسیون:** باید 50/50 حفظ شود
6. **امنیت:** کلیدها نباید در گیت‌هاب کامیت شود
7. **داشبورد وب:** باید live باشد
8. **UptimeRobot:** /health باید کار کند

## راه‌حل کارگروه - 5 عامل

### عامل 1: StrategyAgent - استراتژی v019
- Fix1-5 حفظ شود
- post_only True, threshold 15, effective_assets BTC/ETH if <1000
- Smart TP/SL با ATR

### عامل 2: ExecutionAgent - اجرای واقعی
- effective_dry_run: اگر کلید نباشد True، اگر باشد از DRY_RUN env
- AriaXAsyncClient برای market data حتی در dry_run
- قیمت‌ها واقعی از orderbook

### عامل 3: RiskAgent - ریسک محافظه‌کار
- max DD 15%, capital preservation
- sizing guard, min_notional $5
- SL/TP با ATR

### عامل 4: TelegramAgent - تلگرام پایدار
- 1 _api, 1 send, 1 trade_report -> bool
- queue + to_thread
- init_reporter wrapper
- test_trader برای پنل
- panel با دکمه‌های بستن مجزا

### عامل 5: MonitoringAgent - مانیتورینگ
- /health, /api/status, /api/debug/*
- UptimeRobot friendly
- گزارشات واقعی با قیمت بازار

## اقدامات نهایی

1. بازگرداندن به نسخه پایدار fa88036 (6d6f1e0) که کار می‌کرد
2. اضافه کردن minimal fix برای effective_test_trade_enabled و test_trader بدون شکستن dry_run
3. اطمینان از 50/50 رگرسیون
4. مستندسازی برای تنظیم کلیدهای واقعی
5. تولید PDF/TXT نهایی با لینک‌های raw

## وضعیت فعلی لایو (6d6f1e0)
- Loop 1 orders 2 ✅
- Trade queued + SMART TP/SL + Trade report sent ✅
- Telegram 2/2/0 ✅
- Env ARIAX_API_KEY_exists false → dry_run True (شبیه‌سازی) - نیاز به تنظیم کلید برای واقعی
- Test trader اضافه شد و کار می‌کند (تست شد via API)
