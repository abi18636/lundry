# تاریخچه کامل فعالیت ربات ZENITH SUPER — تحلیل forensic و دلایل اختلاف بک‌تست vs لایو

**تاریخ تهیه:** 2026-10-02 (Asia/Tehran)  
**ریپو:** https://github.com/abi18636/lundry  
**آخرین کامیت‌های مرتبط:** 
- `849e6a2` feat exact capital & P&L
- `e32989a` fix compact sample-style reports + fix suppression
- `1f6cd1e` fix no_liquidity handling
- `f6739ce` fix telegram keyboard
- `c75146d` fix zero-size positions filter

**وضعیت زنده فعلی (build c75146d0):**
```
trading_ready: true
trading_state: ready
market_open: 25/25
active_signal: 12
confirmed_fill: 0 (بعد از ریست)
last_fill: null (ریست)
telegram_sent: 4+
```

---

## 1. خلاصه بک‌تست‌های اولیه (آزمایشگاه)

### almasi 177-v001 primary (استراتژی جایگزین کشف‌شده)
- **ساختار:** 70% TQ_core (Donchian 504/72 + ADX≥18 + ER≥0.10 + confirm 2 کندل + long-only + vol-target 12%) + 30% VQ_core (Vol-breakout ATR 2.5x)
- **هزینه:** 5+2 bp + funding واقعی، بدون اهرم، $100
- **نتایج:**
  - IS 2021-2024: +29.4% | Sharpe 1.45 | MDD -3.5%
  - OOS1 2025-03: +6.8% | Sharpe 1.37 | MDD -2.4%
  - 6 ماه: +5.42% | Sharpe 1.60 | Sortino 1.35 | MDD -3.77% | 28 ترید long-only | Win rate 28.6% / PF 3.00 | هزینه 0.83 USDT | نگهداری avg 79h
  - FULL ~5 سال: +45.3% | Sharpe 1.43 | MDD -6.0%
  - MC P(MDD>15%) ≈0%

> نکته: Win-rate پایین (28%) در trend-follow طبیعی است، PF=3 یعنی سود متوسط برنده 3x زیان بازنده.

### zenith apex REBIRTH (استراتژی اصلی)
- **DNA جدید:** 80% compress (ATR percentile) + 20% impulse (کندل ضربه‌ای ATR + EMA hold)
- **نتایج REBIRTH:**
  - OOS1: +9.6% / Sharpe 1.55
  - OOS2: +10.7% / Sharpe 2.00
  - 6M: +11.9% / Sharpe 2.22
  - FULL: +56.3% / Sharpe 1.32 / MDD -9.0% | 856 ترید | end 156.4
- **قدیمی multiz2:** 6M +1.7% / Sharpe 0.55 | FULL +37.6% / MDD -4.3% — REBIRTH عمدا متفاوت (path_div 0.46)

### سایر آستین‌ها
- `inst_v3_stable` (30% وزن): نسخه پایدار institutional، تمرکز بر Sharpe و MDD پایین
- `inst_v3_primary` (10%): نسخه primary
- `zenith_endurance` (10%): نسخه کم‌ریسک MDD -4.7%

**معماری SUPER:** 5 آستین مستقل، هر کدام بودجه جدا (25/25/30/10/10)، جمع هدف‌ها فقط در لایه سفارش (net at order layer)، بدون ترکیب DNA.

**پروتکل ضد overfit:** IS + OOS1 + OOS2 + 6M + FULL، plateau score، هزینه واقعی، long-only، lev≤1، MDD≤15%.

---

## 2. تاریخچه فعالیت لایو (بر اساس audit/ و health)

### فاز 1: Venue Halted (2026-09-30 تا 2026-10-01 09:00)
- `audit/venue_all_25.json` و `watchdog.log`:
  - 25/25 بازار `state=halted` — صرافی testnet در maintenance/error condition
  - `market_open_count=0`, `primary_blocker=venue_halted`
  - هیچ سفارشی ارسال نشد، `confirmed_fill_count=0`
  - تشخیص درست: `is_active=true` و `API reachable` به معنای قابل معامله بودن نیست، باید `state` چک شود.

### فاز 2: بازگشایی بازار (2026-10-01 12:17 UTC)
- `audit/financial_after.json`:
  - بازار باز شد، `market_open=25`
  - `execution_count=69`, `closed=32`, `wins=11 losses=20`, `verified_closed_net=-0.1789`, `fees=0.1354`, `unreal=-0.0053`
  - نمونه FIFO: BTC open 0.0001@83941.1 fee 0.00419, close gross 0.00192 net -0.00647
- Health بعد از بازگشایی: `trading_ready=true`, `active_signal=14`, `confirmed_fill 21-69`

### فاز 3: گزارشات دقیق سرمایه (849e6a2)
- اضافه شدن `FinancialJournal` v3 با Decimal USDC، FIFO، fee_known فقط وقتی USDC + trade_id
- گزارش جامع 6 ساعته فایل + گزارش خورد per-trade با سرمایه دقیق
- مشکل: بعد از این تغییر، `on_cycle` فقط خطاها را می‌فرستاد و fills را سرکوب می‌کرد → کاربر گفت «گزارش نمیاد / معامله نمی‌کنه»

### فاز 4: فیکس گزارش به سبک نمونه (e32989a)
- فرمت جدید compact مثل نمونه کاربر:
  - `🎯 OPEN SELL • Donchian_Trend [💵 LIVE] DOTUSD @ 1.1573 Qty 51.1 (~$59.1)`
  - `🟢 CLOSE BUY • Recovered • HBARUSD Entry 0.1193 → Exit 0.126 (+5.62%) PnL +1.44$`
- فیکس صف مالی که با `refresh()` شکست قفل می‌شد
- Push شد، Render دیپلوی کرد

### فاز 5: باگ نقدینگی و کیبورد تلگرام (1f6cd1e, f6739ce)
- Health جدید: `primary_blocker=execution_validation_error`, `trading_ready=false`
- بررسی order book:
  - `LTC bids=[] asks=[]`, `NEAR 0/0`, `SOL bids only`, `XRP 1/0`, `BNB 0/0` و 18 نماد دیگر بدون نقدینگی دوطرفه
  - فقط 7 نماد نقدشونده: BTC, ETH, SOL, DOGE, AVAX, APT, TRX
  - `ioc_price()` با `no executable liquidity` خطا می‌داد و کل موتور را blocked می‌کرد
- فیکس: `no_liquidity` جدا از `execution_validation_error` → `trading_ready=true` دوباره
- باگ کیبورد تلگرام: `([{"url"}])[0]` یک dict برمی‌گردوند نه list → API تلگرام خطا → دکمه‌ها کار نمی‌کرد
- فیکس شد، `telegram_messages_sent` از 1 به 4 افزایش یافت

### فاز 6: تناقض 7 vs 9 پوزیشن (c75146d)
- `get_positions` API 10 مورد برمی‌گردونه با 3 تا سایز صفر (`BTC zero 0.0`, `LTC zero`, `TRX zero` با `direction=zero`)
- بروکر UI فقط 7 غیرصفر نشون میده
- تلگرام قبلی `len(positions)` خام = 10/9 نشون می‌داد
- فیکس: فیلتر `abs(size_currency)>1e-12` + نمایش `فیلترشده بدون صفر (خام X)`

### وضعیت فعلی (c75146d0, 2026-10-02 14:40 UTC)
```
build c75146d0, loop 1-5, ready true, blocker null, market_open 25, active 12, fills 0-5, last_fill 14:33 UTC
positions: 10 خام، 7 فیلترشده (ETH, LINK, NEAR, SOL, SUI, TAO, WLD)
actions: no_signal 13, target_reached 5, no_liquidity 2, skip_small 2, below_min 1, stale_data 1, spread_too_wide 1
financial: execution 101-102, closed 48, wins 13 losses 34, verified_net -0.315, fees 0.183, unreal -0.16
```

---

## 3. چرا نتایج لایو غیرقابل قبول و بی‌شباهت به بک‌تست است؟ — تحلیل ریشه‌ای

### 3.1 اختلاف داده و صرافی
- **بک‌تست:** داده `BTC_USDT_SWAP_1H.csv` و 9 جفت دیگر از Binance/OKX (USDT perpetual) با نقدینگی بالا، اسپرد کم (<5 bps)، فاندینگ واقعی
- **لایو:** Deribit testnet USDC perpetual با نقدینگی بسیار کم، اسپرد بالا (تا 137 bps برای SEI)، بسیاری از جفت‌ها دفتر خالی
- **نتیجه:** سیگنال‌های بک‌تست که در Binance سودده بودند، در Deribit به خاطر `no_liquidity` یا `spread_too_wide` اجرا نمی‌شن یا با اسلیپیج بالا اجرا می‌شن

### 3.2 اختلاف اجرا
- **بک‌تست:** فرض اجرای ایده‌آل در قیمت بسته شدن کندل بعدی، بدون در نظر گرفتن `min_trade_amount`, `contract_size`, `tick_size`, `max_spread_bps=100`, `max_slippage_bps=50`
- **لایو:**
  - `plan_rebalance` با `floor_amount` به سمت پایین گرد می‌کنه، هرگز allocation را بالا نمی‌بره → اگر allocation < min_lot (مثلا OP با min 10)، `below_exchange_minimum` و معامله نمی‌شه (در بک‌تست معامله می‌شد)
  - `skip_small` با `rebalance_notional_usd=1` → تغییرات کوچک < $1 نادیده گرفته می‌شه
  - `stale_signal_data` اگر کندل‌های اخیر حجم صفر داشته باشن (zero-volume filler) → سیگنال رد می‌شه
  - `spread_too_wide` اگر اسپرد >100 bps → ورود بلاک

### 3.3 اختلاف ریسک و سرمایه
- **بک‌تست:** سرمایه $100، هر آستین مستقل، بدون در نظر گرفتن پوزیشن‌های خارج از universe
- **لایو:**
  - `risk_cap = min(capital, estimated_equity) * lev_cap` و `available_funds` از حساب USDC
  - `reserved_open_order_notional` برای سفارش‌های باز دیگر اپراتورها
  - `current_gross_notional` شامل تمام پوزیشن‌های USDC حتی خارج از 25 نماد → بودجه ریسک مصرف می‌شه
  - `drawdown_guard` اگر `drawdown>=15%` لچ می‌شه و افزایش ریسک متوقف

### 3.4 هزینه‌ها
- **بک‌تست:** 5+2 bp + funding
- **لایو Deribit:** taker 0.05% (5 bp) + maker 0%، ولی به خاطر IOC و اسپرد بالا، هزینه واقعی بیشتره
- **نمونه لایو:** BTC fee 0.00419 برای notional 8.36 → 5 bp، ولی به خاطر gross کوچک 0.00192، net منفی -0.00647
- **نتیجه:** در لایو `wins 13 losses 34` با `verified_net -0.315`، در حالی که بک‌تست PF 3.0 و Sharpe 1.6 داشت

### 3.5 Overfit و تفاوت رژیم بازار
- بک‌تست IS 2021-2024 شامل روند صعودی قوی BTC بود، ولی OOS1 2025-03 و 6 ماه اخیر ممکنه رنج یا نزولی باشه
- استراتژی‌های trend-follow مثل almasi با win-rate 28% در رنج ضرر می‌دن
- تست‌نت Deribit حجم واقعی نداره (20 کندل اخیر BTC حجم 0)، پس سیگنال‌ها بر اساس داده مصنوعی صفر-حجم تولید می‌شن

### 3.6 مشکلات فنی که عملکرد را بدتر کرد
- Venue halted 25/25 برای ~1 روز → از دست رفتن سیگنال‌ها
- Execution validation error به خاطر دفتر خالی → کل موتور blocked
- Telegram keyboard invalid → کاربر نمی‌تونست وضعیت را چک کنه
- Reporting suppression → کاربر فکر می‌کرد معامله نمی‌شه
- Zero-size positions → سردرگمی در شمارش پوزیشن

---

## 4. پیشنهادات برای بهبود

### کوتاه‌مدت (فیکس فوری)
- [x] فیلتر zero-size positions (انجام شد c75146d)
- [x] تفکیک no_liquidity از validation error (انجام شد 1f6cd1e)
- [x] فیکس کیبورد تلگرام (انجام شد f6739ce)
- [ ] اضافه کردن لیست سیاه برای نمادهای بدون نقدینگی (مثلا فقط 7 نماد نقدشونده را فعال نگه دار: BTC, ETH, SOL, DOGE, AVAX, APT, TRX)
- [ ] کاهش `MAX_SPREAD_BPS` از 100 به 200 برای تست‌نت یا افزایش `MAX_SLIPPAGE_BPS`
- [ ] لاگ دقیق‌تر برای هر بلاکر در داشبورد وب

### میان‌مدت (همگرایی با بک‌تست)
- بک‌تست را با داده Deribit واقعی (نه Binance) و با قوانین اجرای لایو (min lot, spread filter, slippage) دوباره اجرا کن
- هزینه‌ها را با `taker 5 bp + slippage واقعی` شبیه‌سازی کن
- فقط نمادهای نقدشونده تست‌نت را در بک‌تست نگه دار
- استراتژی‌ها را با `long_only=true` و `lev_cap=1.0` و `rebalance_threshold=1` دقیقا مثل لایو بک‌تست کن
- Walk-forward واقعی با داده تا 2026-10-01

### بلندمدت (معماری)
- اجرای جداگانه برای هر صرافی (Deribit vs Binance) با پارامترهای متفاوت
- استفاده از `get_order_book` depth 5 برای تخمین اسلیپیج واقعی به جای 50 bps ثابت
- اضافه کردن `funding` به PnL لایو (الان فقط fee)
- ذخیره‌سازی state در خارج از Render (چون filesystem ephemeral و هر دیپلوی state ریست می‌شه → `confirmed_fill_count` از 5 به 0)

---

## 5. فایل‌های مرجع

- `results/ALMASI_177_V001_SUMMARY_FA.md` — خلاصه almasi
- `results/ZENITH_V001_SUMMARY_FA.md` — خلاصه zenith
- `results/BOT_FORENSIC_AUDIT_FA.md` — تحلیل اولیه forensic
- `audit/financial_after.json` — نمونه FIFO با سرمایه دقیق
- `audit/venue_all_25.json` — اثبات halted بودن 25 بازار
- `audit/deployed_status.json` — وضعیت اولیه با halted
- `REPORT_FIX_FA.md` — فیکس گزارشات به سبک نمونه
- `TELEGRAM_PANEL_FA.md` — پنل تلگرام
- `DEPLOY_STATUS_FA.md` — وضعیت دیپلوی

---

## 6. نتیجه‌گیری

ربات از نظر فنی سالم است (loop, health, trading_ready) ولی **از نظر اقتصادی** به خاطر اختلاف داده/نقدینگی/اجرا بین بک‌تست (Binance پرنقدینگی) و لایو (Deribit testnet کم‌نقدینگی) نتایجش غیرقابل قبول است. این رد شدن به معنای بی‌ارزش بودن استراتژی‌ها نیست، بلکه به معنای **عدم تطابق محیط بک‌تست با محیط لایو** است. برای قضاوت نهایی باید بک‌تست را با داده و قوانین Deribit و فقط با 7 نماد نقدشونده دوباره اجرا کرد.

**این فایل به عنوان تاریخچه کامل برای تحلیل‌های بعدی در گیت‌هاب قرار می‌گیرد.**
