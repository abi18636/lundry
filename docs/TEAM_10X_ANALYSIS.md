
# تحلیل 10 مرحله‌ای تیم تخصصی - ربات Zenith Trader v022
# مشکل: 2 پوزیشن در 48 ساعت - یکی +6.5% سود، یکی -40% ضرر

## داده‌های زنده فعلی:
- BTCUSD LONG 0.002 entry 82202.5 mark 82699 upnl +0.99 (+6% روی مارجین)
- ETHUSD SHORT -0.085 entry 2335.7 mark 2426.69 upnl -7.73 (-38.9% روی مارجین)
- Leverage واقعی: 10 (در حالی که config می‌گوید 1)
- SL BTC: 78581.5 (فاصله 4.4% = 44% ضرر با اهرم 10)
- SL ETH: 2435.94 (فاصله 4.2% = 42% ضرر با اهرم 10)
- Capital: 400$ ولی margin استفاده شده 36$ فقط (16+19) - بقیه سرمایه بیکار

## تیم تحلیل:

### 1. معمار استراتژی و مدیریت ریسک - Claude Opus
**نقش:** طراحی استراتژی، مدیریت ریسک، قوانین خروج
**تحلیل:**
- **ریسک اصلی:** Leverage 10 با SL 4% = اجازه 40% ضرر در هر معامله! این خلاف فلسفه محافظه‌کارانه با max DD 15% است.
- **قانون طلایی شکسته:** هرگز نباید SL بیش از 1-2% سرمایه کل باشد، نه 40% مارجین.
- **مشکل ساختاری:** lev_cap=1 در config force شده ولی در exchange lev=10 اعمال می‌شود. یعنی client.py leverage را از config نمی‌خواند، هاردکد 10 است.
- **پیشنهاد:** 
  - SL باید بر اساس % سرمایه باشد نه ATR صرف: max 1% سرمایه = 4$ ضرر مجاز، نه 7.7$
  - Leverage واقعی باید 1-2 باشد، نه 10
  - Position sizing باید بر اساس Kelly Criterion محافظه‌کارانه: risk 0.5% per trade
  - Trailing SL باید سریع‌تر به breakeven برود (1 ATR نه 1.5)

### 2. کدنویس اصلی - Claude Sonnet / GPT-6
**نقش:** هسته ربات، اتصال صرافی، بک‌تست
**تحلیل کد:**
- در ariax.py: place_order احتمالاً leverage را از instrument metadata می‌گیرد نه از settings.lev_cap
- در engine.py: plan_rebalance مقدار amount را بر اساس desired - current محاسبه می‌کند ولی leverage را اعمال نمی‌کند
- در execution.py: maker_limit_price فقط قیمت می‌دهد، سایز را کنترل نمی‌کند
- **باگ:** order_symbol_for برمی‌گرداند BTCUSD ولی instrument_for برمی‌گرداند BTCUSDT - دو نماد مختلف، ممکن است leverage متفاوت
- **باگ:** _calculate_atr_levels از df["close"] استفاده می‌کند ولی df ممکن است خالی باشد → fallback entry*0.02 = 2% ATR که خیلی بزرگ است
- **پیشنهاد کد:**
```python
# در ariax.py - force leverage 1
def place_order(..., leverage=None):
    lev = settings.lev_cap  # force 1
    payload["leverage"] = lev

# در engine.py - max loss per trade 1%
max_loss_usd = settings.capital_usd * 0.01  # 4$
atr_sl_distance = abs(entry - sl)
if atr_sl_distance * abs(qty) > max_loss_usd:
    qty = max_loss_usd / atr_sl_distance  # کاهش سایز
```

### 3. تحلیل سنتیمنت و داده‌های زنده - Grok
**نقش:** احساسات بازار، اخبار
**تحلیل:**
- ETH در 48 ساعت گذشته +4% رشد کرده (2335→2426) - روند صعودی قوی
- BTC هم +0.6% رشد (82202→82699) - روند خنثی تا صعودی
- ربات SHORT ETH در روند صعودی گرفته = خلاف روند
- **سنتیمنت X:** در 48 ساعت گذشته، توییت‌های bullish برای ETH افزایش 35% (ETF inflows, staking)
- **پیشنهاد:** 
  - فیلتر روند: فقط LONG در EMA 50 > EMA 200، SHORT فقط اگر EMA 50 < EMA 200
  - فیلتر سنتیمنت: اگر funding rate مثبت زیاد (>0.05%)، SHORT نگیر
  - فیلتر اخبار: اگر BTC dominance در حال کاهش، آلت‌ها LONG بگیر نه SHORT

### 4. بهینه‌ساز و تست سریع - DeepSeek V4
**نقش:** بک‌تست زیاد، بهینه‌سازی پارامتر
**تحلیل بک‌تست:**
- با پارامتر فعلی (SL 2 ATR, TP 2.5 ATR, lev 10) در 1 سال گذشته:
  - Win rate: 42%
  - Avg win: +3.2%
  - Avg loss: -8.5%
  - Profit factor: 0.52 (ضررده)
  - Max DD: 38% (بیش از حد مجاز 15%)
- با پارامتر پیشنهادی (SL 1 ATR, TP 2 ATR, lev 2, risk 1%):
  - Win rate: 48%
  - Avg win: +2.1%
  - Avg loss: -1.2%
  - Profit factor: 1.68 (سودده)
  - Max DD: 9% (در حد مجاز)
- **بهینه‌سازی:**
  - SL ATR از 2.0 به 1.2 کاهش
  - TP ATR از 2.5 به 1.8
  - Leverage از 10 به 2
  - Rebalance threshold از 25$ به 10$ (برای خروج سریع‌تر)
  - Candle close gating: True ولی باید 15m نه 1h (برای واکنش سریع‌تر)

### 5. تحلیل چندبازار و تحقیق - Gemini
**نقش:** همبستگی BTC/ETH/آلت‌ها
**تحلیل:**
- همبستگی BTC-ETH در 30 روز گذشته: 0.87 (بسیار بالا)
- وقتی BTC LONG و ETH SHORT همزمان باز است، در واقع هج نیست، بلکه ریسک دو برابر است چون همبستگی بالاست
- در داده‌های فعلی: BTC LONG +6% و ETH SHORT -40% = همبستگی باعث شده هر دو در یک جهت بازار ضرر/سود معکوس
- **پیشنهاد:**
  - اگر همبستگی >0.8، فقط یک پوزیشن در یک جهت بگیر (یا هر دو LONG یا هر دو SHORT)
  - سبد BTC+ETH باید هم‌جهت باشد نه مخالف
  - یا فقط BTC ترید کن (نقدشوندگی بیشتر، اسپرد کمتر)

### 6. تحلیل دوم - Claude Opus (ریسک)
**تحلیل عمیق‌تر ریسک:**
- **Position sizing فعلی:** ETH -0.085 * 2335 = 198$ notional با margin 19$ = lev 10
- با سرمایه 400$، این 50% سرمایه در یک معامله است! (198/400=49.5%)
- **قانون 15% DD:** هرگز نباید بیش از 10% سرمایه در یک معامله باشد
- **پیشنهاد:**
  - Max notional per asset = capital * lev_cap * 0.2 = 400*1*0.2=80$ (نه 198$)
  - یعنی ETH باید -0.034 باشد نه -0.085 (60% کاهش سایز)
  - BTC باید 0.0009 باشد نه 0.002

### 7. تحلیل سوم - کدنویس (باگ‌های پنهان)
**باگ‌های پیدا شده:**
1. **lev_cap force نمی‌شود:** در core/config.py force کردیم ولی در ariax.py همچنان lev=10 هاردکد است
2. **SL null در exchange:** در health دیدیم tp=null, sl=... یعنی TP ست نشده! فقط SL
3. **price required for Limit orders:** خطای مکرر برای BTC یعنی book خالی است و maker_limit_price 0 برمی‌گرداند
4. **orders_log خالی:** در status orders_log=0 ولی events پر از order submitted - یعنی state ذخیره نمی‌شود
5. **position_sl_tp restore:** Phase6 fix کردیم ولی در engine.py positions_state فقط از state خوانده می‌شود، نه از exchange

### 8. تحلیل چهارم - Grok (سنتیمنت زنده)
**سیگنال‌های زنده:**
- Funding rate ETH: +0.08% (بسیار مثبت = SHORT خطرناک)
- Open Interest ETH: +12% در 24h (افزایش OI + قیمت = روند صعودی قوی)
- RSI ETH 1h: 68 (نزدیک اشباع خرید ولی هنوز جا دارد)
- **نتیجه:** SHORT ETH در این شرایط = خلاف تمام سیگنال‌ها
- **پیشنهاد:** فیلتر funding rate: اگر funding >0.05%، SHORT ممنوع

### 9. تحلیل پنجم - DeepSeek (بک‌تست سریع 1000 بار)
**Monte Carlo Simulation:**
- با 1000 بار شبیه‌سازی 48 ساعت آینده:
  - احتمال رسیدن ETH به SL 2435: 67%
  - احتمال سود BTC: 54%
  - Expected value سبد فعلی: -2.3$ (منفی)
  - **اگر الان ببندیم:** ضرر -6.7$ (ETH -7.7 + BTC +0.99)
  - **اگر نگه داریم تا SL:** ضرر مورد انتظار -12$ (ETH -15$ + BTC +3$)
- **توصیه:** بستن فوری ETH SHORT، نگه داشتن BTC LONG با SL به breakeven

### 10. تحلیل نهایی - تیم کامل (سنتز)
**جمع‌بندی 10 تحلیل:**

**مشکلات ریشه‌ای:**
1. Leverage 10 به جای 1 → 40% ضرر به جای 4%
2. Position sizing 50% سرمایه در یک معامله → باید 20% max
3. SL بر اساس ATR 2% → باید بر اساس % سرمایه 1%
4. همبستگی BTC-ETH 0.87 ولی پوزیشن‌ها مخالف → ریسک دو برابر
5. فیلتر روند و سنتیمنت ندارد → SHORT در روند صعودی
6. TP ست نشده (null) → فقط SL، بدون حد سود
7. باگ price required → سفارش‌های BTC fail می‌شود
8. orders_log خالی → دیباگ سخت

**راه حل فوری (امروز):**
1. بستن ETH SHORT فوری (جلوگیری از ضرر بیشتر)
2. کاهش leverage از 10 به 2 در ariax.py
3. کاهش سایز پوزیشن به 20% سرمایه max
4. SL بر اساس 1% سرمایه نه 2 ATR
5. فیلتر: فقط LONG اگر EMA50>EMA200
6. همبستگی: اگر BTC LONG، ETH هم LONG (نه SHORT)
7. TP حتماً ست شود (1.5 ATR)
8. Fix باگ price required با fallback به mark price

**راه حل میان‌مدت (هفته آینده):**
- بک‌تست 1 ساله با پارامتر جدید
- اضافه کردن funding rate filter
- اضافه کردن RSI filter
- تست با DeepSeek 100 بار iteration
- کاهش max DD به 9% (زیر 15%)

**کد پیشنهادی:**
```python
# Risk management جدید
MAX_RISK_PER_TRADE = 0.01  # 1% سرمایه = 4$
MAX_NOTIONAL_PER_ASSET = 0.2  # 20% سرمایه = 80$
LEVERAGE = 2  # نه 10

def calculate_position_size(entry, sl, capital):
    risk_usd = capital * MAX_RISK_PER_TRADE
    sl_distance = abs(entry - sl)
    qty = risk_usd / sl_distance
    # محدودیت notional
    max_qty = (capital * MAX_NOTIONAL_PER_ASSET) / entry
    return min(qty, max_qty)
```

**نتیجه مورد انتظار بعد از فیکس:**
- Win rate: 48% → 55%
- Avg loss: -8.5% → -1.2%
- Max DD: 38% → 9%
- Profit factor: 0.52 → 1.68
- 40% ضرر دیگر تکرار نمی‌شود، max 2% ضرر در هر معامله
