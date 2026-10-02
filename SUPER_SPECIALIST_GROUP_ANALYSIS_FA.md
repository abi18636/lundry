# گروه فوق‌تخصصی — کالبدشکافی سوددهی ربات (مو را از ماست کشیدن)

**تاریخ:** 2026-10-02  
**وضعیت فعلی:** build `c75146d0` → `488dec7` (سرمایه 100→400)  
**ریپو:** abi18636/lundry  
**اعضا گروه:** Data Agent, Regime Agent, Signal Agent, Execution Agent, Risk Agent, Cost Agent, Validation Agent, Report Agent

---

## خلاصه اجرایی — چرا لایو ضررده است ولی بک‌تست سودده؟

| معیار | بک‌تست almasi 177-v001 (6M) | بک‌تست zenith REBIRTH (6M) | لایو SUPER (335 رویداد) |
|---|---|---|---|
| بازده | **+5.42%** | **+11.9%** | **-1.87%** (net) |
| Sharpe | 1.60 | 2.22 | منفی (تخمین -0.8) |
| MDD | -3.77% | -9.0% | -? (drawdown لچ نشده ولی equity 100→99.9) |
| تعداد ترید | 28 | ~150 | 335 (بسته 48) |
| Win rate | 28.6% | ~35% | **10% (34W/300L)** |
| PF | 3.0 | ~2.5 | **0.6** (gross -0.99) |
| هزینه | 0.83 USDT | ~1.2 | 0.61 USDT (فقط fee، بدون funding کامل) |
| نگهداری avg | 79h | ~50h | چند دقیقه تا چند ساعت (بسته به سیگنال) |

**نتیجه:** بک‌تست با PF 3.0 و Sharpe >1.5 سودده، لایو با PF 0.6 و win 10% ضررده. اختلاف **نه از شانس، بلکه از تفاوت ساختاری محیط**.

---

## 1. Data Agent — کیفیت داده

### بک‌تست
- داده: `BTC_USDT_SWAP_1H.csv` و 9 جفت دیگر از Binance/OKX، از 2021 تا 2026-09-15، حجم واقعی، funding واقعی
- کندل‌ها: 4368 کندل 1h در 6M، حجم >0 در 99% کندل‌ها، `last_nonzero_volume_bar` چند دقیقه قبل

### لایو
- صرافی: Deribit testnet `*_USDC-PERPETUAL`
- بررسی order book الان:
  - نقدشونده دوطرفه: **7/25** → BTC, ETH, SOL, DOGE, AVAX, APT, TRX (bids>0 asks>0)
  - بدون نقدینگی: 18/25 → LTC `bids=0 asks=0`, NEAR `0/0`, XRP `1/0`, BNB `0/0`, LINK `0/0`, DOT `0/0`, BCH `0/0`, UNI `0/0`, ATOM `0/0`, ARB `0/1`, OP `0/0`, SUI `0/0`, FIL `0/0`, INJ `0/0`, SEI `0/0`, WLD `0/0`, TAO `0/0`, ADA `0/1`
- کندل‌ها: Deribit `get_tradingview_chart_data` با `resolution=60`، ولی در تست‌نت بسیاری از کندل‌ها حجم 0 (filler) هستند. `signal_no_trade_hours` گاهی >6h → `stale_signal_data` بلاک
- نتیجه: **70% universe در عمل غیرقابل معامله**، در حالی که بک‌تست فرض می‌کرد همه 25 نماد نقدشونده‌اند

**شواهد:**
- `audit/venue_all_25.json`: 25/25 `halted` در 2026-09-30
- `watchdog.log`: `venue markets mostly halted — expected on testnet maintenance`
- بعد از بازگشایی، `market_open_count=25` ولی `no_liquidity=2-4` در هر چرخه

---

## 2. Regime Agent — تفاوت رژیم بازار

- **IS 2021-2024:** روند صعودی قوی BTC (30k→70k)، trend-follow عالی کار می‌کند
- **OOS1 2025-03:** رنج با نوسان کم، بازده +6.8% ولی Sharpe پایین‌تر
- **6M اخیر (2026-03-17 تا 2026-09-15):** ماه آگوست +10.24% (روند قوی) بقیه ماه‌ها منفی -1.7%, -0.39%, -0.27%, -0.43%, -0.71% → سود کل از یک ماه می‌آید
- **لایو 2026-10-01:** بازار رنج با اسپرد بالا، بدون روند واضح، `active_signal_assets=12` ولی بسیاری `target_reached` یا `skip_small`

**نتیجه:** استراتژی trend-follow با win-rate 28% در رنج ضرر می‌دهد، چون منتظر روند بزرگ است که در تست‌نت نمی‌آید.

---

## 3. Signal Agent — تولید سیگنال

### بک‌تست
- `prepare_frames` از 2500h کندل، `run_all_sleeves` با 5 آستین مستقل
- هر آستین `per_asset` side: -1,0,+1 با `notional_usd` و `target_coin`
- مثال BTC: `target_coin 2.09e-05`, `notional 1.76`, `price 84272`

### لایو
- همان کد `sleeves.py` استفاده می‌شود، ولی `price` از `net_book` یا `mark_price` می‌آید
- اگر `price<=0` یا `asset not in net_book` → `insufficient_candles` بلاک
- اگر `candles` API خطا → `candle_api_error`
- در `/api/status` اخیر: `no_signal 13`, `target_reached 5` → یعنی 13 نماد اصلا سیگنال ندارد (side=0)، 5 تا به هدف رسیده

**تفاوت کلیدی:** بک‌تست `long_only=true` ولی در لایو هم `long_only=true`، پس shortها حذف می‌شوند. در بک‌تست almasi فقط long بود (28 long)، پس همخوانی دارد.

**مشکل:** `signal_no_trade_hours` اگر >6h باشد، ورود بلاک می‌شود (`stale_signal_data`). در تست‌نت چون حجم 0 است، این فیلتر بسیاری از سیگنال‌های معتبر را رد می‌کند، در حالی که در بک‌تست حجم واقعی بود و رد نمی‌شد.

---

## 4. Execution Agent — اجرای سفارش

### بک‌تست
- فرض اجرای ایده‌آل در `close` کندل بعدی، بدون `min_trade_amount`, `contract_size`, `tick_size`, `spread`, `slippage`
- هزینه 5+2 bp ثابت

### لایو
- `plan_rebalance(current, desired, price, step, minimum, threshold)`:
  - `floor_amount(desired, step)` → گرد به پایین، هرگز بالا نمی‌برد
  - اگر `abs(target) < minimum` → `target=0` → `below_exchange_minimum` (مثلا OP min=10، allocation 6.14 → معامله نمی‌شود، در بک‌تست می‌شد)
  - اگر `amount*price < 1` → `skip_small`
- `ioc_price(book, direction, meta, slippage_bps=50)`:
  - نیاز به `bids` و `asks` هر دو >0 برای ورود، فقط یک طرف برای خروج کافی نیست در کد قدیمی → `no executable liquidity` → قبلا `execution_validation_error` کل موتور را بلاک می‌کرد (فیکس شد)
  - `spread_bps = (ask-bid)/mid*10000`، اگر >100 → `spread_too_wide` (SEI اسپرد 137 bps)
  - `tick_size` گرد کردن داخل باند اسلیپیج

**شواهد لایو:**
- `Counter: no_signal 13, target_reached 5, no_liquidity 2, skip_small 2, below_min 1, stale_data 1, spread_too_wide 1`
- فقط 2-3 نماد در هر چرخه `can_execute=true`
- `confirmed_fill_count` از 0 به 5 در 1 ساعت → یعنی ترید می‌کند ولی کم

**هزینه واقعی لایو:**
- مثال: `ETH close entry 2751.94 exit 2752.33 qty 0.0065 gross 0.0025 fee_alloc 0.00894 exit_fee 0.00894 net -0.0153`
- gross کوچک مثبت ولی fee بزرگتر → net منفی
- در بک‌تست avg_win 0.106 (10.6%) و avg_loss -0.017 (1.7%) → PF 3.0، ولی در لایو gross منفی -0.99 → PF <1

---

## 5. Risk Agent — مدیریت ریسک و سرمایه

- **سرمایه:** قبلا 100، الان 400 (برای شفافیت بیشتر) — ولی لایو هنوز 100 نشون میده چون Render env var دستی 100 بود (باید به 400 تغییر کند)
- **سقف:** `notional_cap = min(capital, estimated_equity) * lev_cap`، `lev_cap=1.0` → max 100 یا 400
- **ذخیره:** `reserved_open_order_notional` برای سفارش‌های باز دیگر اپراتورها + `current_gross_notional` شامل تمام پوزیشن‌های USDC حتی خارج از 25 نماد (مثلا اگر دستی BTC-PERPETUAL باز کرده باشی)
- **نتیجه:** حتی اگر سیگنال 400$ بخواد، اگر `available_funds` کم باشه یا `gross + reserved > cap` → `risk_cap_blocked` یا `insufficient_funds`
- **Drawdown guard:** اگر `drawdown>=15%` لچ می‌شه و فقط `reduce_only` مجاز → در لایو drawdown ~0%، پس لچ نشده

**شفافیت با سرمایه 400:** با سرمایه 400، `Qty` و `~$notional` 4 برابر بزرگتر می‌شه، پس `PnL` و `fees` واضح‌تر دیده می‌شه. مثلا قبلا `SOL Qty 0.005 (~$0.58)`، با 400 میشه `~$2.34`.

---

## 6. Cost Agent — کارمزد و فاندینگ

- **بک‌تست:** `fee_slip 0.685 + funding 0.147 = total 0.832 USDT` برای 28 ترید در 6M
- **لایو:** `period_fees 0.18` برای 48 بسته، `total fees 0.61` برای 335 رویداد
- **تفاوت:** در بک‌تست funding محاسبه می‌شد، در لایو `net_final_usdc = net_price_fees` (funding فقط وقتی evidence کامل باشه اضافه می‌شه، فعلا صفر)
- **نتیجه:** حتی بدون funding، fee به تنهایی سودهای کوچک را می‌خوره

---

## 7. Validation Agent — اعتبارسنجی و overfit

- **پروتکل ضد overfit:** IS-fit / OOS1-select / OOS2-report، plateau score، MC
- **MC almasi:** `p_mdd_gt_15=0%`, `p_lose=10.5%`, `terminal_p05=96.4` → ریسک MDD>15% صفر
- **ولی:** داده بک‌تست Binance و لایو Deribit testnet از نظر نقدینگی و اسپرد کاملا متفاوت → **domain shift**، نه overfit کلاسیک
- **شواهد overfit نیست:** OOS1 Sharpe 1.37 و OOS2 1.53 نزدیک IS 1.45 → پایدار، ولی لایو Sharpe منفی → مشکل از محیط، نه از بهینه‌سازی

---

## 8. سنتز — چرا رد شد؟

| لایه | بک‌تست فرض | لایو واقعیت | تاثیر |
|---|---|---|---|
| داده | Binance پرحجم | Deribit testnet کم‌حجم، 18/25 دفتر خالی | 70% سیگنال‌ها اجرا نمی‌شن |
| اجرا | ایده‌آل close | min lot, tick, spread 100bps, slippage 50bps, floor down | بسیاری `below_min`, `skip_small`, `spread_too_wide` |
| هزینه | 5+2 bp | 5 bp + اسپرد واقعی + اسلیپیج | سود کوچک → زیان |
| ریسک | $100 مستقل | $100 + پوزیشن‌های خارج universe + reserved orders | بودجه کم |
| رژیم | روند صعودی | رنج + حجم صفر | trend-follow ضرر |
| فنی | - | halted 1 روز، validation error، کیبورد تلگرام، reporting suppression، zero-size | از دست رفتن سیگنال و سردرگمی |

**محاسبه ساده:** اگر 28 ترید بک‌تست با avg_win 10.6% و avg_loss -1.7% و PF 3.0 سودده بود، در لایو با gross -0.99 و fee 0.61 و win 10%، PF 0.6 می‌شه و ضررده.

---

## 9. پیشنهادات فوق‌تخصصی

### فوری (1-2 روز)
- [x] فیلتر zero-size (c75146d)
- [x] تفکیک no_liquidity (1f6cd1e)
- [x] فیکس کیبورد تلگرام (f6739ce)
- [ ] **Blacklist:** فقط 7 نماد نقدشونده را فعال کن: `ASSETS=BTC,ETH,SOL,DOGE,AVAX,APT,TRX` (در Render env)
- [ ] افزایش `MAX_SPREAD_BPS` به 200 و `MAX_SLIPPAGE_BPS` به 100 برای تست‌نت
- [ ] کاهش `REBALANCE_NOTIONAL_USD` از 1 به 0.5 برای `skip_small` کمتر
- [ ] سرمایه را در Render env به 400 تغییر بده (الان 100 مونده)

### میان‌مدت (1 هفته)
- بک‌تست را **با داده Deribit واقعی** (از `public/get_tradingview_chart_data` با 2500h) و با قوانین لایو (floor, min, spread, slippage) دوباره اجرا کن
- فقط 7 نماد نقدشونده را بک‌تست کن
- هزینه را `taker 5bp + half spread` شبیه‌سازی کن
- Walk-forward با داده تا 2026-10-02

### بلندمدت (2-4 هفته)
- اجرای جداگانه برای هر صرافی با پارامترهای متفاوت
- استفاده از depth 5 برای تخمین اسلیپیج واقعی
- اضافه کردن funding به PnL لایو (از `transaction_log`)
- ذخیره state در خارج از Render (Redis/Postgres) چون filesystem ephemeral و هر دیپلوی state ریست می‌شه
- اضافه کردن SL/TP واقعی به استراتژی‌ها (الان فقط سیگنال خروج داره)

---

## 10. فایل‌های مرجع برای تحلیل

- `FULL_HISTORY_FORENSIC_FA.md` — تاریخچه کامل
- `REPORT_FIX_FA.md` — فیکس گزارشات
- `SUPER_SPECIALIST_GROUP_ANALYSIS_FA.md` — همین فایل (گروه فوق‌تخصصی)
- `results/ALMASI_177_V001_SUMMARY_FA.md` + `almasi_177_v001_results.json` + `trades.csv`
- `results/ZENITH_V001_SUMMARY_FA.md`
- `results/BOT_FORENSIC_AUDIT_FA.md`
- `audit/financial_after.json` — نمونه FIFO
- `audit/venue_all_25.json` — halted
- `/api/status` و `/health` — وضعیت زنده

**این تحلیل توسط گروه فوق‌تخصصی تهیه شد و در گیت‌هاب برای مراجعه ثبت می‌شود.**
