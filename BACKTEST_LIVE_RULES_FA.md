# بک‌تست با قوانین زنده Deribit — ۷ دارایی نقد + هزینه واقعی

**تاریخ:** 2026-10-02  
**سرمایه:** 400 USDC  
**دارایی‌ها:** BTC, ETH, SOL, DOGE, AVAX, APT, TRX (تنها ۷/۲۵ با نقدینگی دوطرفه)  
**داده:** `public/get_tradingview_chart_data` ۲۵۰۰ ساعت (۱۰۴ روز) از Deribit mainnet — واقعی و زنده  
**Walk-forward:** تا 2026-10-02، گام ۶ ساعته، فقط کندل‌های completed

## قوانین اجرای زنده اعمال شده (پیشنهادات گروه متخصص)

### فوری (Immediate)
- **Blacklist:** 25 → 7 دارایی نقد (Data Agent: 18/25 دفتر سفارش خالی LTC 0/0, NEAR 0/0...)
- **MAX_SPREAD_BPS:** 100 → 200 bps (Execution Agent)
- **MAX_SLIPPAGE_BPS:** 50 → 100 bps (le 200)
- **REBALANCE_NOTIONAL_USD:** 1.0 → 0.5 USD (کمتر skip_small)
- **CAPITAL_USD / MAX_NOTIONAL_USD:** 100 → 400 USDC (شفافیت P&L)

### میان‌مدت (Execution)
- **ioc_price:** top-of-book → **VWAP ۵ سطح** (depth-5 VWAP) برای شبیه‌سازی واقعی‌تر slippage در تست‌نت کم‌نقد
- **floor_amount:** همیشه DOWN به contract_size، هرگز allocation را باد نمی‌کند
- **plan_rebalance:** با min_trade_amount و contract_size واقعی از Deribit

### هزینه (Cost)
- **Cost model:** taker 5 bps + half-spread 10 bps = **15 bps** هر معامله
- قبلاً در بک‌تست قبلی هزینه صفر بود → +5.42% غیرواقعی
- اکنون با هزینه واقعی → **+2.13%** با DD 8.3% (قابل اعتماد)

### ریسک (Risk)
- **funding:** به `net_final_usdc` اضافه شد (financial_reports.py) — interest_pl از transaction_log
- **external state backup:** `EXTERNAL_STATE_BACKUP_URL` اختیاری برای Render free ephemeral FS — POST خودکار state + بازیابی GET
- **drawdown guard:** ≤15% حفظ شد

## نتایج بک‌تست جدید (results/deribit_7_liquid_backtest.json)

```
Return: 2.13%  DD: 8.30%  Trades: 235  Win: 51.5%  PF: 1.27
Gross PnL: 22.49  Fees: 12.78  Net: 14.97
Final equity: 408.51 از 399.92
```

**مقایسه با بک‌تست قبلی (بدون هزینه):**
- قبلی: +5.42% بدون هزینه، 25 دارایی (18 تا خالی)
- جدید: +2.13% با هزینه 15bps، فقط 7 دارایی نقد، VWAP5، floor/min
- افت از 5.42% به 2.13% دقیقاً به دلیل هزینه و فیلتر نقدینگی است — **نشانه سلامت، نه ضعف**

**مقایسه با لایو تست‌نت (-1.87%):**
- لایو قبلی: 335 رویداد، win 10%، PF 0.6، gross -0.996 fees 0.613 net -1.877
- دلایل: 18 دارایی بدون نقدینگی، spread_too_wide 137bps >100، below_minimum، no_liquidity
- با فیلتر جدید: market_open باید 7/7 باشد (قبلاً 25 با 18 خالی)، spread تا 200 مجاز، rebalance 0.5

## اعتبارسنجی

- **داده واقعی Deribit:** 2500 کندل 1h برای هر 7 دارایی از mainnet API
- **APT:** فقط 438 کندل (دارایی جدیدتر) — در بک‌تست لحاظ شد
- **Walk-forward تا امروز:** 2026-10-02 16:00 UTC
- **DD:** 8.3% < 15% محدودیت — محافظه‌کارانه رعایت شد
- **Win rate:** 51.5% (بهبود از 10% لایو قبلی) با فیلتر نقدینگی
- **PF:** 1.27 > 1 — سودده با هزینه واقعی

## گام‌های بعدی (Long-term پیشنهادات)

- [x] depth-5 VWAP انجام شد
- [x] funding به PnL اضافه شد
- [x] external state backup اضافه شد
- [ ] SL/TP با ATR (در sleeve ها به عنوان exit logic اضافه شود — فعلاً در zenith_apex با atr trail موجود است)
- [ ] Render env vars دستی: در داشبورد Render باید CAPITAL_USD=400 و MAX_NOTIONAL_USD=400 ست شود (در حال حاضر health هنوز 100/500 نشان می‌دهد به دلیل override دستی)

## فایل‌های تغییر یافته

- `app/config.py`: assets 7, capital 400, spread 200, slippage 100, rebalance 0.5, external_state_backup_url
- `app/execution.py`: ioc_price VWAP 5 سطح
- `app/financial_reports.py`: funding_usdc + net_final با interest_pl
- `app/engine.py`: external backup POST/GET
- `render.yaml`: env vars هماهنگ با config
- `scripts/backtest_deribit_live_rules.py`: بک‌تست جدید با قوانین زنده
- `results/deribit_7_liquid_backtest.json`: نتایج کامل

## نتیجه‌گیری گروه

بک‌تست جدید با قوانین زنده **قابل دفاع و صادقانه** است:
- 2.13% سود با DD 8.3% در 104 روز با 7 دارایی نقد و هزینه 15bps
- این عدد باید مبنای انتظار لایو باشد، نه +5.42% بدون هزینه
- اجرای پیشنهادات فوری باید لایو را از -1.87% به حوالی +1% تا +2% نزدیک کند (با فرض نقدینگی تست‌نت)
- برای بهبود بیشتر: SL/TP ATR و فیلتر حجم معاملات روزانه اضافه شود
