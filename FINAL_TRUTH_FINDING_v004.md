# 🔍 گزارش نهایی گروه حقیقت‌یاب v004 - بررسی کاراکتر به کاراکتر

**تاریخ: 2026-10-09 21:00 | Build: 1ae942b | Equity: 200$ | 15+ باگ عمیق**

---

## 👥 تیم 6 نفره - یافته‌ها

### Agent A1 - Engine (1154 خط)
**باگ 1: Cooldown 900s (15 دقیقه) - CRITICAL**
- خط 870: `cooldown_seconds = 900`
- تأثیر: در کرش -25% یک‌روزه، 15 دقیقه یعنی از دست دادن کل حرکت
- فیکس v003: 900→60 ثانیه

**باگ 2: Backoff 600s Global - CRITICAL**
- خط 1021: `retry_after = time.time()+600` برای هر خطا
- تأثیر: خطای کوچک 5$ notional → کل موتور 10 دقیقه بلوکه → `order_error_backoff`
- فیکس v003/v004: 600→60 ثانیه، فقط برای خطاهای جدی، per-asset، skip برای small/qty exceeds

**باگ 3: State Ephemeral - HIGH**
- `state/bot_state.json` در Render free پاک می‌شود
- تأثیر: baseline equity اشتباه → drawdown false 50% → halted
- فیکس موجود: چک peak > capital*1.5 → reset

### Agent A2 - Execution (198 خط)
**باگ 1: minNotional 5$ Logic - MEDIUM**
- خط 90-110: دو status `target_reached` و `skip_small` هر دو معامله نمی‌کنند ولی دلیل متفاوت
- تأثیر: LINK با 1.5$ notional هیچوقت معامله نمی‌شود
- فیکس: rebalance 1$→5$

**باگ 2: IOC VWAP 5 levels - LOW**
- اگر order book خالی → `no executable liquidity` → کل چرخه fail
- تأثیر: AVAX, LINK گاهی book خالی → market_halted
- فیکس پیشنهادی: fallback به mark price

### Agent A3 - Exchange (1018 خط)
**باگ 1: Candles Pagination - MEDIUM**
- AriaX فقط 700-1000h کندل دارد، `inst_v3` نیاز 726h → مرز → گاهی 0 سیگنال
- تأثیر: zenith_apex 0/5 active
- فیکس v003: break_n 96→24, pct_lo 0.25→0.05

**باگ 2: Positions Direction - CRITICAL - NEW!**
- خط 583-939: `qty exceeds position size` برای close exact size
- تأثیر: ETH short -0.068 close با buy 0.068 → fail → order_error_backoff 3x
- **این باگ اصلی ضرر است!** ربات نمی‌تواند پوزیشن ضررده را ببندد → ضرر بیشتر
- فیکس v004: amount *0.995 (1% کمتر) ولی هنوز fail می‌دهد
- **نیاز به فیکس عمیق‌تر:** close_position با market order یا reduce 2%+

**باگ 3: Instrument Cache 300s - LOW**
- Cache 5 دقیقه‌ای → اگر minOrderQty تغییر کند، با مقدار قدیمی معامله → خطا

### Agent A4 - Financial (560 خط)
**باگ 1: FIFO avg_before None - HIGH**
- اگر avg_before None → gross None → `verified_closed_net_usdc=0`
- تأثیر: کاربر فکر می‌کند هیچ سودی نداشته، در حالی که equity 201$ است
- مشاهده: `verified_closed_net_usdc=0.0` ولی equity 200$

**باگ 2: Fee Handling - MEDIUM**
- fee_known فقط اگر trade_ids و fee_currency درست باشد
- تأثیر: PnL incomplete → گزارش ناقص

**باگ 3: Equity Baseline - HIGH**
- `estimated = capital + equity - baseline`
- اگر baseline 0 (state reset) → estimated 20199$ اشتباه
- فیکس: چک peak > capital*1.5 → reset

**باگ 4: Funding Ignored - MEDIUM**
- Funding perpetual هر 8 ساعت در PnL نیست
- تأثیر: short‌ها در نزول funding می‌گیرند ولی گزارش نیست

### Agent A5 - Strategy (637 خط)
**باگ 1: Diversified_5 FLAT 4/5 - MEDIUM**
- الان 4/5 FLAT (BTC,ETH,AVAX,LINK) و فقط SOL short
- تأثیر: 80% سرمایه بلااستفاده → اگر 1 ماه رنج، هیچ سودی
- درست برای حفظ سرمایه ولی کاربر انتظار سود دارد

**باگ 2: Zenith Apex 0/5 - HIGH - FIXED v003**
- pct_lo=0.25 (25% از low) → در رنج breakout رخ نمی‌دهد → 0 سیگنال
- فیکس v003: pct_lo 0.25→0.05, break_n 96→24, exit 48→24

**باگ 3: Almasi 504h Entry - CRITICAL - FIXED v003**
- entry_n=504h = 21 روز! برای کرش -25% یک‌روزه خیلی کند
- تأثیر: almasi 1/5 active → 20% سرمایه بلااستفاده
- فیکس v003: 504→72h (21 روز→3 روز) → الان 4/5 active ✅

**باگ 4: Inst V3 Vote 0.10 - MEDIUM**
- vote 0.10 خیلی پایین → سیگنال ضعیف → WR پایین
- Trade-off: 0.33→2/5 active WR بالا، 0.10→5/5 active WR پایین
- فیکس: 0.10 برای تضمین 5/5

**باگ 5: Weights Static - MEDIUM**
- وزن‌ها ثابت 30/20/20/15/10/5، بر اساس سود اخیر تطبیق نمی‌یابند
- فیکس پیشنهادی: dynamic weights بر اساس Sharpe 30 روزه

### Agent A6 - Config (176 خط)
**باگ 1: Lev 5x vs 2x - HIGH - FIXED**
- بک‌تست 1x DD 8.3%، لایو 5x DD 41% → مشاهده 29%
- فیکس v002: 5x→2x, render.yaml 5.0→2.0

**باگ 2: Assets TOP5 - MEDIUM**
- 5 جفت خیلی کم برای تنوع، بک‌تست 7 جفت سودده‌تر
- تأثیر: تنوع کم → ریسک بیشتر

---

## 🧬 مشکلات ساختاری عمیق (Beyond Bugs)

### 1. Single Point of Failure - AriaX
- کل ربات به یک صرافی وابسته
- Render free tier sleeps → AriaX down → کل ربات halted
- بک‌تست Deribit نقدینگی بالا، لایو AriaX نقدینگی کم

### 2. No Stop-Loss / Take-Profit - CRITICAL
- هیچ SL/TP در کد نیست!
- فقط trend-following → در کرش -25% ضرر بزرگ
- باید ATR-based SL/TP: SL 2*ATR, TP 4*ATR

### 3. No Funding Rate - MEDIUM
- Perpetual funding هر 8 ساعت نادیده
- short در نزول funding می‌گیرد ولی حساب نمی‌شود

### 4. Overfitting 504h - HIGH - FIXED
- 21 روز entry روی 2021-2024 بهینه، در 2026 کرش کار نمی‌کند
- باید walk-forward ماهانه

### 5. No Regime Detection - HIGH
- تشخیص نمی‌دهد بازار رنج/روند/کرش
- همه trend-following → در رنج ضرر
- باید: ADX>20 trend, ADX<20 range, ATR>5% crash

### 6. Qty Exceeds Bug - CRITICAL - NEW!
- AriaX bug: close exact size → qty exceeds
- حتی با *0.995 هم fail می‌دهد
- **این دلیل اصلی ضرر است:** نمی‌تواند پوزیشن ضررده را ببندد
- Workaround موجود فقط برای long، برای short هم باید باشد

---

## ✅ فیکس‌های v003 + v004

### v003 (4668cde):
- Cooldown 900s→60s
- Backoff 600s→120s per-asset, skip small/qty
- Zenith Apex pct_lo 0.25→0.05 break 96→24
- Almasi entry 504→72h (21→3 روز) → 1/5→4/5 active ✅
- Config ATR SL/TP 2x/4x

### v004 (1ae942b):
- AriaX limit_ioc amount*0.995 برای close
- close_position size*0.995
- Engine skip backoff برای qty exceeds
- Backoff 120→60s

### نتیجه v004:
- Build 1ae942b LIVE
- Sleeves: diversified 1/5, almasi 4/5 (was 1/5), inst 5/5,5/5
- Total active: 15 signals (was 12)
- Equity: 200$ (از 166$ بازیابی)
- ولی هنوز blocker: `error` qty exceeds 3x

---

## 🔴 مشکل باقی‌مانده - Qty Exceeds

**وضعیت فعلی:**
```
BTC pending_check
ETH error qty exceeds - کاهش long ممکن نیست (ولی short است!)
SOL error qty exceeds
AVAX error qty exceeds
LINK skip_small 1.53$<5$
```

**ریشه:**
- AriaX/Bybit reduceOnly با exact size fail می‌دهد
- Workaround فعلی فقط برای long (current>0) و فقط 0.995 reduction
- برای short هم باید باشد و reduction بیشتر (2% یا 5%)

**فیکس پیشنهادی v005:**
```python
# engine.py workaround گسترش
if "ariax" in url:
    if current !=0 and abs(desired) < abs(current):
        # Any reduction (long or short) - keep current to avoid bug
        # OR close with 95% size
        if desired == 0:
            # Full close - use 95% size
            desired = current * 0.05  # Leave 5% to avoid qty exceeds?
            # Or use close_position endpoint
        else:
            desired = current  # No reduction
```

**یا:**
```python
# ariax.py close with 90% size + second close for remainder
close_size = abs(size) * 0.90
# Then close remaining 10% in next cycle
```

---

## 📊 خلاصه نهایی

**قبل حقیقت‌یاب:**
- فکر می‌کردیم 5 دلیل: اهرم، short bug، رژیم، اجرا، داده

**بعد حقیقت‌یاب 6 نفره کاراکتر به کاراکتر:**
- **15+ باگ عمیق** پیدا شد
- **6 مشکل ساختاری** (single point, no SL/TP, funding, overfitting, regime, qty exceeds)
- **مهمترین:** Qty exceeds bug که نمی‌گذارد پوزیشن ضررده بسته شود → ضرر بیشتر

**Equity:**
- قبل v002: 166$ (-16.8%)
- بعد v002: 201$ (+0.98%) بازیابی +35$
- بعد v003/v004: 200$ (stable) ولی blocker qty exceeds

**فیکس‌های انجام شده:**
- v002: short bug, flat-preserve, 2x
- v003: cooldown, backoff, apex, almasi entry
- v004: qty exceeds *0.995

**فیکس‌های باقی‌مانده برای v005:**
- Qty exceeds با 90% close + remainder
- Workaround برای short هم (نه فقط long)
- SL/TP ATR-based
- Regime detection
- Dynamic weights

---

**گزارش نهایی گروه حقیقت‌یاب 6 نفره - کاراکتر به کاراکتر - 15+ باگ**
**Build: 1ae942b | Equity: 200$ | Blocker: qty exceeds 3x | نیاز به v005**
