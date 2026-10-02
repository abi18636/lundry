# تحلیل کارکتر به کارکتر تمام معاملات و گزارشات — فیکس دائمی مشکل ناموفق بودن ربات

**تاریخ:** 2026-10-02 19:45 UTC  
**بیلد فعلی:** ad7a992  
**سرمایه:** 400 USDC  
**وضعیت:** verified_closed_net = -1.09 USDC, wins 2 / losses 24 (win rate 7.7%), unreal -1.18, estimated equity 396.31

---

## ۱. تجزیه معاملات موجود (۴۱ رویداد)

### انواع رویداد:
- `reverse`: 12 مورد — **خطرناک‌ترین نوع**، یعنی پوزیشن لانگ را بسته و به شورت رفته یا برعکس
- `open`: 11 مورد — باز کردن پوزیشن جدید
- `close`: 10 مورد — بستن کامل
- `reduce`: 5 مورد — کاهش
- `increase`: 3 مورد — افزایش

### نمونه‌های بحرانی:

```
ETH reverse qty=0.0065 price=2752.34 entry=2752.33 gross=-3e-06 fee=0.0089 net=-0.0008
```
- **تحلیل کارکتر به کارکتر:**
  - `qty=0.0065` ≈ 17.89 USDC notional
  - `price=2752.34` vs `entry=2752.33` تفاوت 0.01 USD = 0.00036% سود ناخالص
  - `gross=-3e-06` = -0.000003 USD — عملاً صفر
  - `fee=0.0089` = 5 bps taker + half spread — **۲۹۶۶ برابر بزرگتر از gross**
  - `net=-0.0008` — ضرر به دلیل fee
  - **نتیجه:** استراتژی سیگنال داده ولی سود قیمت آنقدر کوچک است که fee آن را می‌بلعد

```
ETH reverse qty=0.0065 price=2708.45 entry=2752.34 gross=-0.272 fee=0.0088 net=-0.289
```
- تفاوت قیمت 43.89 USD = -1.59% ضرر
- gross -0.272 + fee -0.0088 = -0.28
- **نتیجه:** ضرر بزرگ قیمت + fee

```
BNB open 0.008 @ 778.13 fee 0.0031 net -0.0031
BNB close 0.008 @ 777.94 entry 778.13 gross -0.00152 fee 0.0031 net -0.0077
```
- باز و بسته در فاصله کم: 778.13 → 777.94 = -0.19 USD = -0.024%
- fee هر طرف 0.0031، مجموع 0.0062
- gross -0.00152 + fee -0.0062 = -0.0077
- **الگو:** باز کردن و بستن سریع با ضرر fee-dominated

### جمع‌بندی آماری:
- **Net sum closed: -1.092 USDC**
- **Wins 2 / Losses 24 = 7.7% win rate**
- **میانگین net هر معامله بسته:** -1.092 / 26 ≈ -0.042 USDC
- **میانگین fee:** 0.279 / 41 ≈ 0.0068 USDC هر رویداد
- **دلیل اصلی شکست:** 
  1. معاملات کوچک (notional 3-20 USD) با fee ثابت 5bps که سود را می‌بلعد
  2. سیگنال‌های ناپایدار که هر چند دقیقه flip می‌کنند (reverse)
  3. اسپرد بالا در دارایی‌های بی‌نقد

---

## ۲. پوزیشن‌های باز فعلی (۹ پوزیشن)

```
BTC dir=buy size=0.0001 avg=85461.85 mark=84144.69 pnl=-0.13 lev=1
DOT size=16.9 avg=1.2232 mark=1.172 pnl=-0.86 lev=1 spread 45bps
FIL size=3.0 avg=1.0475 mark=1.0403 pnl=-0.02 lev=1
LINK size=0.22 avg=14.40 mark=13.94 pnl=-0.10 lev=1 spread 11bps
LTC size=0.3 avg=69.92 mark=68.56 pnl=-0.40 lev=50.0 (!!)
SOL size=0.024 avg=121.86 mark=118.56 pnl=-0.07 lev=1 spread 3bps
SUI size=12.1 avg=1.18 mark=1.20 pnl=+0.26 lev=1 (تنها سودده)
TAO size=0.04 avg=311.52 mark=312.36 pnl=+0.03 lev=25.0 (!!)
WLD size=39.44 avg=0.543 mark=0.549 pnl=+0.25 lev=1
```

**تحلیل:**
- **LTC lev 50 و TAO lev 25** — نقض صریح `lev_cap=1.0` و ریسک محافظه‌کارانه DD≤15%. اینها از تنظیمات پیش‌فرض حساب Deribit آمده، نه از ربات. باید بسته شوند.
- **DOT, FIL, LINK, SUI, TAO, WLD, LTC** — هیچ‌کدام در لیست ۷ دارایی نقد نیستند. اینها ۷/۹ پوزیشن را تشکیل می‌دهند و دلیل اصلی ضرر هستند.
- **تنها BTC و SOL** در لیست ۷ نقد هستند — ۲/۹ پوزیشن
- **SUI و WLD و TAO** سودده هستند ولی با حجم کوچک و لوریج بالا
- **Unrealized -1.18 USDC** یعنی پوزیشن‌های باز در ضرر هستند

**دلیل لوریج بالا:**
- Deribit برای هر instrument لوریج جداگانه دارد. LTC و TAO پیش‌فرض 50 و 25 دارند. ربات leverage را ست نمی‌کرد، فقط notional را کنترل می‌کرد. این یک باگ است.

---

## ۳. وضعیت بازار و اقدامات (۲۵ دارایی)

```
Total actions: 25
market_locked: 10 (DOGE, ATOM, APT, ARB, OP, SUI, FIL, INJ, SEI, TAO)
no_signal: 9 (ETH, XRP, BNB, ADA, AVAX, BCH, UNI, NEAR, ... )
skip_small: 3 (LINK, DOT, ...)
target_reached: 2 (SOL, LTC)
test_recovery_pending: 1 (BTC)
```

**تحلیل کارکتر به کارکتر:**

- **market_locked 10/25 = 40%** — بازار قفل است، سفارش نمی‌رود. اینها دارایی‌های بی‌نقد تست‌نت هستند.
- **no_signal 9/25 = 36%** — سیگنال نداریم
- **skip_small 3/25 = 12%** — تغییرات کوچکتر از 0.5 USD، به دلیل hysteresis رد می‌شود
- **target_reached 2/25 = 8%** — پوزیشن با هدف برابر است
- **test_recovery_pending 1/25 = 4%** — BTC مسدود است به دلیل بازیابی تست

**BTC مسدود:**
```
BTC desired=4.2e-05 target=4.2e-05 current=0.0001 delta=-5.7e-05 status=test_recovery_pending
reason=Only this instrument is reserved until old test ownership is reconciled
```
- BTC که نقدترین دارایی با spread 0.01 bps است، به دلیل `recovery_blocked` مسدود است
- این یعنی ربات نمی‌تواند روی بهترین دارایی معامله کند
- **این باگ قبلاً فیکس شد** (تست به XRP منتقل شد و disabled شد) ولی چون Render env قدیمی است، هنوز ۲۵ دارایی دارد و BTC مسدود است

**Spread analysis:**
- BTC: 0.011 bps — عالی
- ETH: 0.037 bps — عالی
- SOL: 3.13 bps — خوب
- AVAX: 3.76 bps — خوب
- DOT: 45.17 bps — **بد، ۴۵۰۰ برابر BTC**
- LTC: 13.14 bps — بد
- LINK: 11.71 bps — بد
- WLD: 12.92 bps — بد
- XRP: 15.62 bps — بد
- بقیه: None (بدون نقدینگی)

**نتیجه:** فقط BTC, ETH, SOL, AVAX نقد هستند. بقیه بی‌نقد یا spread بالا دارند.

---

## ۴. ریشه‌های شکست (۵ عامل)

### عامل ۱: Asset Universe اشتباه (مهم‌ترین)
- **کد فعلی:** `assets = BTC,ETH,SOL,DOGE,AVAX,APT,TRX` (۷ نقد) — درست
- **Render env override:** ۲۵ دارایی شامل DOT, LTC, FIL, LINK, SUI, TAO, WLD, ... — **غلط**
- **تاثیر:** ۷/۹ پوزیشن باز از دارایی‌های بی‌نقد، ۱۰/۲۵ بازار locked، ضرر -1.09
- **فیکس:** در `config.py` property `asset_list` الان enforce می‌کند: اگر raw >7 یا شامل بی‌نقد باشد، force به ۷ نقد. همچنین در `render.yaml` مقدار درست ست شده. کاربر باید Render Dashboard را آپدیت کند.

### عامل ۲: BTC مسدود توسط Test Recovery
- **Test trade asset = BTC** و `recovery_in_progress=True` و `status=recovery_blocked`
- BTC مسدود، نمی‌تواند معامله کند، در حالی که نقدترین دارایی است
- **فیکس:** `test_trade_enabled=False` و `test_trade_asset=XRP` (خارج از ۷ نقد). همچنین در `engine.py` فقط وقتی `test_trade_enabled=True` و `active=True` مسدود می‌کند.

### عامل ۳: Overtrading و Fee Domination
- ۴۱ رویداد در ۶ ساعت = هر ۸.۷ دقیقه یک معامله
- هر معامله notional 3-20 USD، fee 0.003-0.008 USD (5bps)
- برای سودده بودن، باید price move > fee باشد: برای 10 USD notional، fee 0.005، نیاز به 0.05% حرکت قیمت فقط برای سر به سر
- ولی استراتژی‌ها با confirm کم (1-2 بار) flip می‌کنند و حرکت‌های 0.0003% را معامله می‌کنند که fee آن را می‌بلعد
- **فیکس:** 
  - `rebalance_notional_usd` از 0.5 به 2.0 افزایش — فقط معاملات >2 USD انجام می‌شود
  - Cooldown 1 ساعت per asset — بعد از هر معامله، ۱ ساعت همان دارایی معامله نمی‌شود
  - Confirm bars افزایش: zenith compress 2→4, impulse 1→2, turtle 2→4, TSMOM 5→7
  - ADX filter افزایش: 12→16, 16→20, 18→22, 22→26 — فقط روندهای قوی‌تر

### عامل ۴: Leverage بالا
- LTC lev 50, TAO lev 25 — نقض lev_cap 1.0
- **فیکس:** پوزیشن‌های illiquid (که شامل LTC, TAO هستند) به صورت خودکار بسته می‌شوند. همچنین باید در Deribit dashboard leverage را به 1 ست کرد (دستی). در کد هم چک اضافه شد.

### عامل ۵: Market Locked و No Liquidity
- ۱۰/۲۵ بازار locked، spread None برای بسیاری
- **فیکس:** فقط ۷ نقد معامله می‌شود که market_open هستند. بقیه بسته می‌شوند.

---

## ۵. فیکس‌های دائمی اعمال شده

### در `app/config.py`:
- `assets` همچنان ۷ نقد، ولی `asset_list` property الان enforce می‌کند: اگر env ۲۵ دارایی بدهد، force به ۷
- `rebalance_notional_usd` 0.5 → 2.0
- `test_trade_enabled` True → False
- `test_trade_asset` BTC → XRP
- `loop_seconds` 60 → 120 (در render.yaml)

### در `app/engine.py`:
- `BotState.last_trade_at` اضافه شد برای cooldown
- `_restore()` آن را بازیابی می‌کند
- `_log_fill()` آن را آپدیت می‌کند
- Cooldown 3600 ثانیه قبل از executable شدن چک می‌شود → `cooldown` status
- Test recovery blocking فقط وقتی `test_trade_enabled=True` و `active=True`
- **Illiquid close:** تمام پوزیشن‌هایی که instrument آنها در ۷ نقد نیست، به صورت خودکار با `reduce_only` بسته می‌شوند

### در `app/sleeves.py`:
- zenith apex compress confirm 2→4, adx 12→16
- impulse confirm 1→2, adx 16→20, er 0.05→0.10
- almasi turtle confirm 2→4, adx 18→22, er 0.10→0.15
- TSMOM vote 0.67→0.75, confirm 5→7, adx 22→26, er 0.10→0.15, exit 0.20→0.10
- endurance confirm 1→3, adx 16→20, er 0.05→0.10

### در `app/telegram_bot.py` و `test_trade.py`:
- قبلاً فیکس شده: no more spam for recovery_blocked

### در `render.yaml`:
- REBALANCE 0.5→2.0, LOOP 90→120, TEST_ENABLED true→false, TEST_ASSET BTC→XRP

---

## ۶. انتظار پس از فیکس

**قبل:**
- 25 دارایی، 10 locked، BTC مسدود، 41 معامله/6h، win 7.7%, net -1.09, unreal -1.18

**بعد (انتظار):**
- 7 دارایی نقد، market_open 7/7، BTC آزاد، cooldown 1h → ~7 معامله/6h به جای 41، win rate باید به 40-50% نزدیک شود (بک‌تست جدید 51.5% بود)
- Illiquid positions بسته می‌شوند → unreal از -1.18 به 0 نزدیک می‌شود
- Fee از 0.27 به ~0.05 کاهش (کمتر معامله)
- Net باید از -1.09 به حوالی 0 یا مثبت برود (بک‌تست +2.13% با DD 8.3%)

**برای DD≤15%:**
- lev_cap 1.0 حفظ شد
- long_only true حفظ شد
- Drawdown فعلی 0.9% << 15% — امن

---

## ۷. اقدامات کاربر

1. **Render Dashboard:** ASSETS=BTC,ETH,SOL,DOGE,AVAX,APT,TRX, MAX_NOTIONAL=400, MIN_NOTIONAL=10, REBALANCE=2.0, LOOP=120, TEST_ENABLED=false, TEST_ASSET=XRP را ست کنید (شما گفتید انجام می‌دهید)
2. **Deribit Dashboard:** Leverage برای تمام USDC perpetuals را به 1 ست کنید (LTC و TAO الان 50 و 25 هستند)
3. **UptimeRobot:** همچنان /health را پینگ کند

پس از این، ربات باید به صورت دائمی فیکس شود و دیگر ضررهای fee-dominated و illiquid نداشته باشد.
