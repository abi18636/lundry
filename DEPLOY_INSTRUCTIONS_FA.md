# 🚀 راهنمای دیپلوی فوری v002

## مشکل فعلی
- Build لایو: `197f6d8` (قدیمی، باگ short، lev 5x)
- Equity: 166$ از 200$ = -16.8%
- Blocker: `order_error_backoff` (به خاطر notional below minimum)

## فیکس‌های آماده در workspace

### فایل‌های تغییر یافته:
```
/home/user/zenith_trader_bot/app/sleeves.py  (v002 profitable + short fix)
/home/user/zenith_trader_bot/app/config.py   (lev 2.0 + env override)
/home/user/zenith_trader_bot/render.yaml     (LEV_CAP 2.0, REBALANCE 5.0)
/home/user/zenith_trader_bot/FIX_BACKTEST_VS_LIVE_v002_FA.md
/home/user/zenith_trader_bot/ANALYSIS_BACKTEST_VS_LIVE_FA.md
```

### Zip آماده:
`/home/user/zenith_v002_profitable_fix.zip` (21KB)

---

## گزینه A: آپلود دستی به GitHub (سریع‌ترین، 2 دقیقه)

1. برو به: https://github.com/abi18636/lundry
2. کلیک `Add file` → `Upload files`
3. فایل‌های زیر را از workspace دانلود و آپلود کن:
   - `app/sleeves.py`
   - `app/config.py`
   - `render.yaml`
4. Commit message بنویس:
   ```
   fix v002 profitable: short bug + flat-preserve + 2x conservative for 15% DD
   ```
5. `Commit changes` → Render خودکار دیپلوی (2-3 دقیقه)
6. چک کن: https://zenith-trader-bot.onrender.com/health → build جدید باید hash جدید باشد نه 197f6d8
7. چک کن: https://zenith-trader-bot.onrender.com/api/status → sleeves باید diversified_5 v002 باشد

## گزینه B: از طریق Render Dashboard (بدون Git)

1. https://dashboard.render.com/web/srv-daukknojo6nc73dib9gg
2. `Environment` → `Add Environment Variable` یا Edit:
   ```
   LEV_CAP = 2.0
   REBALANCE_NOTIONAL_USD = 5.0
   MAX_NOTIONAL_USD = 200
   MAX_DRAWDOWN_PCT = 0.15
   ```
3. `Manual Deploy` → `Deploy latest commit`
4. بعد از دیپلوی، اگر باز هم equity پایین است:
   - `Shell` تب → `rm state/bot_state.json` → ریست baseline

## گزینه C: من با API دیپلوی کنم (نیاز به توکن GitHub شما)

اگر توکن GitHub خود را بدهید (با دسترسی Contents write به repo lundry)، من مستقیم پوش می‌کنم:

```bash
# شما باید توکن را در چت بدهید یا به عنوان env var
# سپس من:
cd /tmp
git clone https://<token>@github.com/abi18636/lundry.git
cp /home/user/zenith_trader_bot/app/sleeves.py lundry/app/
cp /home/user/zenith_trader_bot/app/config.py lundry/app/
cp /home/user/zenith_trader_bot/render.yaml lundry/
cd lundry
git add app/sleeves.py app/config.py render.yaml
git commit -m "fix v002 profitable: short bug + flat-preserve + 2x"
git push origin main
```

---

## بعد از دیپلوی چه انتظاری داشته باشیم؟

### فوری (1-2 ساعت):
- `order_error_backoff` برطرف می‌شود (چون REBALANCE 5$)
- SHORT درست کار می‌کند (فیکس net_book)
- Equity از 166$ نباید بیشتر کم شود، چون flat در بازار رنج

### 1 روز:
- با lev 2x، DD از 29% → 10-15%
- معاملات کمتر ولی با WR بالاتر (54% vs 40%)

### 1 هفته:
- اگر بازار روند بگیرد، سود +5 تا +10%
- اگر بازار رنج بماند، FLAT → حفظ سرمایه (بهتر از -16%)

---

## اگر باز هم ضررده بود؟

### پلن B: کاهش به 1x + فقط BTC/ETH
```yaml
LEV_CAP=1.0
ASSETS=BTC,ETH
SLEEVE_WEIGHTS=diversified_5:0.50,almasi_primary:0.50
```

### پلن C: اضافه کردن حد ضرر/سود
در `app/execution.py` اضافه کن:
```python
# بعد از هر fill، stop-loss 2% و take-profit 4%
sl_price = entry * (0.98 if long else 1.02)
tp_price = entry * (1.04 if long else 0.96)
```

---

## فایل‌های گزارش

- `ANALYSIS_BACKTEST_VS_LIVE_FA.md` - تحلیل کامل 6 دلیل ضرر
- `FIX_BACKTEST_VS_LIVE_v002_FA.md` - فیکس‌های v002
- `DEPLOY_INSTRUCTIONS_FA.md` - همین فایل

همه در `/home/user/zenith_trader_bot/`

---

## سوال؟

اگر توکن GitHub را بدهید، من در همین چت دیپلوی می‌کنم و نتیجه را با curl چک می‌کنم.
اگر نه، خودتان گزینه A یا B را انجام دهید و لینک health را بفرستید تا من تایید کنم.
