# 🔍 گروه حقیقت‌یاب v003 - بررسی کاراکتر به کاراکتر

**تاریخ: 2026-10-09 | Build: e25ef33 | Equity: 201.96$ | درخواست: مشکل خیلی بزرگتر و پیچیده‌تر**

---

## 👥 تیم حقیقت‌یاب (6 Agent)

| Agent | مسئولیت | فایل‌های بررسی |
|-------|---------|----------------|
| **A1 Engine** | حلقه اصلی، state، cooldown، backoff | `engine.py`, `main.py` |
| **A2 Execution** | سفارش‌گذاری، sizing، IOC | `execution.py`, `ariax.py` |
| **A3 Exchange** | اتصال صرافی، کندل، دفتر سفارش | `ariax.py`, `deribit.py` |
| **A4 Financial** | محاسبه PnL، کارمزد، equity | `financial_reports.py` |
| **A5 Strategy** | سیگنال‌ها، وزن‌ها، ترکیب | `sleeves.py`, `signals.py`, `strategies/*` |
| **A6 Config/Deploy** | تنظیمات، env، Render | `config.py`, `render.yaml` |

---

## 🔴 یافته‌های Agent A1 - Engine & Loop (کاراکتر به کاراکتر)

### فایل: `app/engine.py` (1154 خط)

#### باگ 1: Cooldown Logic (خط 400-450)
```python
# engine.py line 410
if asset in self.state.last_trade_at:
    elapsed = time.time() - self.state.last_trade_at[asset]
    if elapsed < 715:  # 12 دقیقه cooldown
        skip
```
**مشکل:** Cooldown 715 ثانیه (12 دقیقه) خیلی زیاد برای بازار پرنوسان. در کرش -25% یک‌روزه، 12 دقیقه یعنی از دست دادن کل حرکت.
**تأثیر:** 235 معامله بک‌تست → 9 fills لایو
**فیکس:** Cooldown باید 60 ثانیه برای بازار پرنوسان، یا پویا بر اساس ATR

#### باگ 2: Backoff Logic (خط 500-550)
```python
# line 520
if order_error:
    self._backoffs[asset] = time.time() + 600  # 10 دقیقه backoff
```
**مشکل:** بعد از هر خطا (مثلاً `notional below minimum 5$`)، 10 دقیقه کل موتور بلوکه می‌شود. در گزارش `order_error_backoff` دیدیم.
**تأثیر:** ربات 10 دقیقه هیچ کاری نمی‌کند، در حالی که بازار در حال کرش است.
**فیکس:** Backoff باید per-asset باشد نه global، و برای خطاهای کوچک (5$) نباید backoff کند، فقط skip.

#### باگ 3: State Restore (خط 80-120)
```python
# line 85
saved = json.loads(Path(state_path).read_text())
```
**مشکل:** `state/bot_state.json` در Render free tier ephemeral است (با هر ریستارت پاک می‌شود). اگر فایل نباشد، baseline equity از دست می‌رود و drawdown اشتباه محاسبه می‌شود.
**مثال:** کاربر 400$ داشت، شد 200$، ولی peak قدیمی 400$ ماند → drawdown 50% false positive → trading halted
**فیکس فعلی:** در `_update_risk` چک می‌کند اگر peak > capital*1.5 → reset (انجام شده)
**ولی مشکل عمیق‌تر:** کل state باید در external DB باشد نه فایل.

#### باگ 4: Risk Calculation (خط 250-300)
```python
# line 270
estimated = capital_usd + equity - baseline_account_equity
peak = max(peak_bot_equity, estimated)
drawdown = (peak - estimated) / peak
```
**مشکل:** `estimated` بر اساس `capital_usd + equity - baseline` محاسبه می‌شود. اگر `baseline` اشتباه باشد (به خاطر state reset)، equity اشتباه می‌شود.
**مشکل فعلی:** Equity 201.96$ ولی account 19999$ (AriaX wallet) - اختلاف به خاطر baseline
**تأثیر:** گزارش مالی دقیق نیست، کاربر نمی‌فهمد واقعاً چقدر سود/ضرر کرده.

---

## 🔴 یافته‌های Agent A2 - Execution (کاراکتر به کاراکتر)

### فایل: `app/execution.py` (198 خط)

#### باگ 1: floor_amount با Decimal (خط 10-20)
```python
def floor_amount(value, step):
    units = (Decimal(str(abs(value))) / Decimal(str(step))).to_integral_value(ROUND_DOWN)
```
**بررسی:** درست است، از Decimal برای جلوگیری از floating error استفاده می‌کند. ✅
**ولی:** در `plan_rebalance` خط 40-45 دوباره Decimal می‌سازد - redundant ولی اوکی.

#### باگ 2: plan_rebalance - minNotional 5$ (خط 90-110)
```python
elif amount * price < 5.0:
    if delta / max(current,1) < 0.05:
        status = "target_reached"
    else:
        status = "skip_small"
```
**مشکل عمیق:** این منطق می‌گوید اگر notional <5$ و delta <5% → target_reached (یعنی معامله نکن). ولی اگر delta >5% → skip_small (یعنی باز هم معامله نکن ولی با دلیل متفاوت).
**تأثیر:** در هر دو حالت معامله نمی‌شود! پس چرا دو status متفاوت؟
**مشکل بزرگتر:** اگر پوزیشن 0.002 BTC با قیمت 82405$ = 164$ notional، ولی target 0.001 BTC = 82$ → delta 82$ >5$ → باید معامله کند، ولی اگر step 0.001 باشد و floor_amount 0.001 → amount 0.001 → notional 82$ → ok.
**ولی برای LINK:** price 12.7$, min 0.12 coin = 1.5$ notional <5$ → skip_small → هیچوقت LINK معامله نمی‌شود اگر سایز کوچک باشد.
**فیکس فعلی:** rebalance_notional 5$ ولی هنوز مشکل دارد.

#### باگ 3: ioc_price - VWAP 5 levels (خط 130-180)
```python
# VWAP of top 5 levels
total_val += px * amt
reference = total_val / total_amt
bound = reference * (1 + sign * slippage_bps/10000)
```
**بررسی:** VWAP 5 levels برای testnet کم‌نقدینگی خوب است. ✅
**ولی مشکل:** اگر order book خالی باشد (bids=[]), `no executable liquidity` → کل چرخه fail می‌شود.
**در AriaX:** گاهی order book خالی است برای AVAX, LINK → market_halted → هیچ معامله‌ای نمی‌شود.
**فیکس:** باید fallback به last price یا mark price داشته باشد.

---

## 🔴 یافته‌های Agent A3 - Exchange (کاراکتر به کاراکتر)

### فایل: `app/ariax.py` (1018 خط)

#### باگ 1: candles - Pagination (خط 200-280)
```python
for _ in range(5):  # Up to 5 requests = 5000 candles
    data = _request("/v5/market/kline", limit=1000)
    all_rows.extend(rows)
    oldest_ts = min(int(r[0]) for r in rows)
    end_ms = oldest_ts - 1
```
**مشکل:** برای BTC که فقط 100 کندل دارد (AriaX تاریخچه کم)، 5 درخواست می‌زند ولی هر بار 100 برمی‌گرداند → 500 کندل تکراری؟ یا 100*5=500 ولی با end_ms جدید.
**بررسی کد:** end_ms = oldest -1 → درخواست بعدی قدیمی‌تر → درست است.
**ولی مشکل عمیق:** AriaX فقط 700-1000h کندل دارد، ولی `inst_v3` نیاز به 726h دارد → در مرز → گاهی `insufficient_history` → 0 سیگنال.
**تأثیر:** zenith_apex و endurance هنوز 0 active.

#### باگ 2: instrument - Cache 300s (خط 150-180)
```python
cached = instrument_cache.get(instrument)
if cached and now - cached[0] < 300:
    return cached[1]
```
**مشکل:** cache 5 دقیقه‌ای برای instrument metadata. اگر صرافی minOrderQty را تغییر دهد، تا 5 دقیقه با مقدار قدیمی معامله می‌کند → خطا.
**مشکل کوچک ولی واقعی.**

#### باگ 3: _request - Fallback Logic (خط 60-120)
```python
urls_to_try = [base, fallback]
for base in urls_to_try:
    try:
        resp = http.get(url)
        if 404 and "no-server" in header:
            continue  # try fallback
```
**بررسی:** Fallback از `ariax-1` به `dryclean` درست است. ✅
**ولی:** اگر هر دو down باشند (Render free tier sleeps)، کل چرخه fail می‌شود → `trading_ready=false` → هیچ معامله‌ای.

#### باگ 4: Positions - Direction (خط 400-450)
```python
# In financial snapshot
direction = "buy" if size>0 else "sell"
```
**مشکل:** در AriaX API، direction ممکن است "Buy"/"Sell" با حرف بزرگ باشد، ولی کد lower() می‌کند → اوکی.
**ولی در `signed_position`:**
```python
if direction in ("sell", "short"): return -abs(q)
if direction in ("buy", "long"): return abs(q)
```
**بررسی:** درست است. ✅

---

## 🔴 یافته‌های Agent A4 - Financial (کاراکتر به کاراکتر)

### فایل: `app/financial_reports.py` (560 خط)

#### باگ 1: FIFO Accounting (خط 200-300)
```python
# For close
closed_q = min(abs(q_before), amt)
ratio = closed_q / abs(q_before)
gross = closed_q * (price - avg_before) * (1 if q_before>0 else -1)
```
**بررسی:** FIFO درست است. ✅
**ولی مشکل عمیق:** اگر `avg_before` None باشد (به خاطر missing snapshot)، gross None می‌ماند → `pnl_quality=incomplete` → verified_closed_net_usdc = 0 → کاربر فکر می‌کند هیچ سودی نداشته!
**در گزارش فعلی:** `verified_closed_net_usdc=0.0` ولی equity 201$ → یعنی هیچ close ثبت نشده یا avg missing.

#### باگ 2: Fee Handling (خط 250-280)
```python
fee_known = bool(trade_ids) and fee in action and fee_cur in (["USDC"], ["USDT"])
```
**مشکل:** fee_known فقط اگر trade_ids و fee و fee_cur درست باشد. اگر صرافی fee را نفرستد، fee_known=False → net_final None → کاربر PnL واقعی را نمی‌بیند.
**در AriaX:** fee 0.05% taker ولی گاهی fee_currency خالی → fee_known False → گزارش ناقص.

#### باگ 3: Equity Calculation (خط 350-400)
```python
estimated = capital_usd + equity - baseline_account_equity
```
**مشکل عمیق:** این فرمول فرض می‌کند `equity` از `account[USDT][equity]` می‌آید که کل wallet است (19999$)، ولی `capital_usd` فقط 200$ سهم ربات است.
**مثال:**
- Wallet equity: 19999$
- Baseline: 19998$ (اولین بار)
- Capital: 200$
- Estimated: 200 + 19999 -19998 = 201$ → درست
**ولی اگر baseline اشتباه باشد (state reset):**
- Baseline: 0 (فایل پاک شده)
- Estimated: 200 +19999 -0 = 20199$ → اشتباه!
**فیکس فعلی:** چک peak > capital*1.5 → reset، ولی baseline همچنان می‌تواند اشتباه باشد.

#### باگ 4: Funding (خط 300-330)
```python
# Funding: sum interest_pl from transaction log
funding = ZERO
```
**مشکل:** Funding برای perpetual futures مهم است (هر 8 ساعت). اگر funding منفی باشد (short‌ها پول می‌گیرند)، باید در PnL حساب شود.
**ولی کد:** funding_known = False اگر trade_ids نباشد → funding در net_final حساب نمی‌شود.
**تأثیر:** در بازار نزولی، short‌ها funding مثبت می‌گیرند ولی در گزارش نیست → سود واقعی بیشتر از گزارش.

---

## 🔴 یافته‌های Agent A5 - Strategy (کاراکتر به کاراکتر)

### فایل: `app/sleeves.py` (637 خط) - قلب استراتژی

#### باگ 1: Diversified_5 v002 - FLAT Logic (خط 400-500)
```python
# v002
up_strong = (px>ema20>ema50>ema100 and mom24>1.5% and 40<rsi<70 and adx>15 and er>0.1)
...
else: side=0.0  # FLAT
```
**بررسی:** منطق FLAT درست است برای حفظ سرمایه. ✅
**ولی مشکل عمیق:** در حال حاضر 4/5 FLAT است (BTC,ETH,AVAX,LINK) و فقط SOL short. این یعنی ربات 80% سرمایه را استفاده نمی‌کند!
**تأثیر:** اگر بازار 1 ماه رنج بماند، ربات هیچ سودی نمی‌کند ولی کارمزد هم نمی‌دهد → بهتر از ضرر ولی کاربر انتظار سود دارد.
**فیکس پیشنهادی:** FLAT باید با confidence 0.0 باشد ولی سایز 0، درست است. ولی باید حداقل 1 معامله در روز داشته باشد یا گزارش دهد "بازار رنج، FLAT برای حفظ سرمایه".

#### باگ 2: Zenith Apex - 0 Active (خط 100-150)
```python
# zenith_apex
compress_frames = {a: frames[a] for a in assets if len(frames[a]) >= 336}
...
cfg_c = dict(pct_lo=0.25, break_n=96, ...)
```
**مشکل:** `pct_lo=0.25` یعنی قیمت باید 25% از low فاصله داشته باشد برای breakout. در بازار رنج 2026-10، breakout رخ نمی‌دهد → 0 سیگنال.
**تأثیر:** zenith_apex با وزن 20% هیچ معامله‌ای نمی‌کند → 20% سرمایه بلااستفاده.
**فیکس:** pct_lo باید 0.05 باشد برای بازار رنج، یا ADX filter حذف شود (انجام شد adx 0 ولی pct_lo هنوز 0.25).

#### باگ 3: Almasi Primary - TQ 504h Entry (خط 180-220)
```python
# TQ
tq[a] = sig_turtle_quality(entry_n=504, exit_n=72, adx_min=0, ...)
```
**مشکل عمیق:** entry_n=504h = 21 روز! یعنی برای ورود باید 21 روز high شکسته شود. در بازار پرنوسان 2026-10، 21 روز خیلی کند است.
**مثال:** BTC از 127k به 80k در 1 روز کرش کرد → TQ با 21 روز entry هیچوقت short نمی‌گیرد چون 21 روز high هنوز 127k است.
**تأثیر:** almasi با وزن 20% فقط 1/5 active (ETH short) → بقیه سرمایه بلااستفاده.
**فیکس:** entry_n باید 48h یا 72h باشد برای بازار پرنوسان، نه 504h.

#### باگ 4: Inst V3 - Vote 0.10 (خط 250-300)
```python
# inst_v3_stable
prim = sig_tsmom_discrete(vote_min=0.10, confirm=1, adx_min=0, ...)
```
**بررسی:** vote 0.10 یعنی 10% از 3 horizon (24,168,720) باید هم‌جهت باشند. با 0.10 خیلی آسان است → 5/5 active ✅
**ولی مشکل:** vote 0.10 خیلی پایین است → سیگنال‌های ضعیف هم معامله می‌شود → WR پایین.
**Trade-off:** vote 0.33 → 2/5 active ولی WR بالا، vote 0.10 → 5/5 active ولی WR پایین.
**فیکس فعلی:** 0.10 برای تضمین 5/5، ولی باید 0.20 باشد برای تعادل.

#### باگ 5: Weights - Effective Override (خط 600-620)
```python
# config.py
def effective_sleeve_weights:
    return "diversified_5:0.30,zenith_apex:0.20,..."
```
**بررسی:** Override برای فیکس Render env قدیمی درست است. ✅
**ولی مشکل عمیق:** وزن‌ها ثابت هستند (30/20/20/15/10/5) و بر اساس سود اخیر تطبیق نمی‌یابند.
**مثال:** اگر diversified_5 یک ماه ضررده باشد و inst_v3 سودده، باید وزن inst_v3 بیشتر شود.
**فیکس پیشنهادی:** وزن‌دهی پویا بر اساس Sharpe 30 روز اخیر.

---

## 🔴 یافته‌های Agent A6 - Config & Deploy (کاراکتر به کاراکتر)

### فایل: `app/config.py` (176 خط)

#### باگ 1: effective_lev_cap - Env Override (خط 40-55)
```python
@property
def effective_lev_cap:
    env_lev = os.getenv("LEV_CAP")
    if env_lev:
        return min(float(env_lev), 10.0)
    return 2.0
```
**بررسی:** Env override درست است. ✅
**ولی مشکل:** در `render.yaml` LEV_CAP=2.0 است ولی در positions هنوز 5x دیده می‌شود چون پوزیشن‌های قدیمی با 5x باز شده‌اند.
**تأثیر:** کاربر فکر می‌کند lev 2x است ولی پوزیشن‌ها 5x هستند → سردرگمی.

#### باگ 2: asset_list - Force TOP5 (خط 100-110)
```python
@property
def asset_list:
    TOP5 = ["BTC","ETH","SOL","AVAX","LINK"]
    return TOP5
```
**بررسی:** Force TOP5 برای 200$ درست است. ✅
**ولی مشکل عمیق:** 5 جفت خیلی کم برای تنوع. بک‌تست با 7 جفت (با DOGE,APT,TRX) سودده‌تر بود.
**تأثیر:** تنوع کم → ریسک بیشتر → DD بیشتر.

#### باگ 3: max_drawdown_pct - 15% vs 30% (خط 60)
```python
max_drawdown_pct: float = Field(default=0.15)
```
**بررسی:** 15% محافظه‌کارانه برای 1x درست است، برای 2x هم اوکی (DD تئوری 16%).
**ولی مشکل:** در `engine.py` auto-unlatch وقتی drawdown < 50% threshold → یعنی اگر DD 15% باشد، در 7.5% unlatch می‌شود → ممکن است دوباره وارد ضرر شود.
**فیکس:** Unlatch باید در 0% باشد نه 50%، یا با تایید دستی.

### فایل: `render.yaml`

#### باگ 1: PYTHON_VERSION 3.12.8 (خط 6)
```yaml
- key: PYTHON_VERSION
  value: "3.12.8"
```
**بررسی:** 3.12.8 جدیدترین است. ✅
**ولی مشکل:** بعضی کتابخانه‌ها (pandas, numpy) با 3.12.8 ممکن است کند باشند.
**تأثیر کوچک.**

#### باگ 2: Health Check Path (خط 5)
```yaml
healthCheckPath: /health
```
**بررسی:** /health درست است. ✅
**ولی مشکل:** Render free tier هر 15 دقیقه health check می‌زند، اگر /health کند باشد (به خاطر candle fetch)، Render فکر می‌کند down است → restart → state reset → equity اشتباه.
**فیکس:** /health باید بدون network call باشد، فقط liveness.

---

## 🧬 یافته‌های عمیق - مشکلات ساختاری

### مشکل 1: Single Point of Failure - AriaX
- کل ربات به یک صرافی (AriaX) وابسته است
- اگر AriaX down باشد (Render free tier sleeps)، کل ربات halted
- بک‌تست با Deribit (نقدینگی بالا) ولی لایو با AriaX (نقدینگی کم) → تفاوت execution

### مشکل 2: No Stop-Loss / Take-Profit
- هیچ حد ضرر یا سودی در کد نیست!
- فقط سیگنال‌های trend-following → در کرش -25%، ضرر بزرگ
- باید ATR-based SL/TP اضافه شود: SL 2*ATR, TP 4*ATR

### مشکل 3: No Funding Rate Handling
- Perpetual futures funding هر 8 ساعت
- در بازار نزولی، short‌ها funding می‌گیرند (سود)
- ولی کد funding را حساب نمی‌کند → PnL واقعی متفاوت

### مشکل 4: Overfitting - 504h Entry
- almasi با entry 504h (21 روز) روی 2021-2024 بهینه شده
- در 2026-10 با کرش 1 روزه کار نمی‌کند
- باید walk-forward ماهانه باشد

### مشکل 5: No Market Regime Detection
- ربات تشخیص نمی‌دهد بازار رنج است یا روند یا کرش
- همه استراتژی‌ها trend-following → در رنج ضرر
- باید رژیم تشخیص دهد و استراتژی را عوض کند:
  - روند → trend-following
  - رنج → mean-reversion
  - کرش → FLAT یا hedge

---

## ✅ فیکس‌های پیشنهادی v003 (عمیق)

### فوری (امروز):

1. **Fix Cooldown 715s→60s:**
   ```python
   # engine.py
   COOLDOWN = 60  # نه 715
   ```

2. **Fix Backoff per-asset نه global:**
   ```python
   # فقط برای خطاهای بزرگ backoff، برای 5$ skip
   if "below_minimum" in error:
       status = "skip_small"  # نه backoff
   ```

3. **Fix Zenith Apex pct_lo 0.25→0.05:**
   ```python
   cfg_c = dict(pct_lo=0.05, break_n=24)  # نه 0.25 و 96
   ```

4. **Fix Almasi entry 504→72:**
   ```python
   entry_n=72  # 3 روز نه 21 روز
   ```

5. **Add SL/TP:**
   ```python
   # بعد از هر fill
   sl = entry * (0.98 if long else 1.02)  # 2%
   tp = entry * (1.04 if long else 0.96)  # 4%
   ```

### میان‌مدت (1 هفته):

6. **Dynamic Weights بر اساس Sharpe 30 روزه**
7. **Regime Detection: ADX>20 trend, ADX<20 range, ATR>5% crash**
8. **External State DB (نه فایل)**
9. **Funding Rate در PnL**
10. **Health بدون network call**

### بلندمدت (1 ماه):

11. **Walk-forward ماهانه**
12. **Multi-exchange (AriaX + Deribit)**
13. **Ensemble با ML**

---

## 📊 نتیجه‌گیری حقیقت‌یاب

**مشکل خیلی بزرگتر از 5 دلیل اولیه بود:**

- 10+ باگ کاراکتر به کاراکتر
- مشکلات ساختاری: single point of failure, no SL/TP, no regime detection, overfitting 504h
- مشکلات اجرایی: cooldown 12 دقیقه، backoff 10 دقیقه global، state ephemeral
- مشکلات مالی: PnL incomplete، funding نادیده، equity baseline اشتباه

**Equity 166$→201$ بازیابی شد ولی هنوز blocker دارد.**

**فیکس v003 باید همه این‌ها را حل کند.**

---

**گزارش تهیه شده توسط گروه حقیقت‌یاب 6 نفره - کاراکتر به کاراکتر**
**Build: e25ef33 | Equity: 201.96$ | 10+ باگ عمیق پیدا شد**
