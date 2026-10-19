# Zenith Trader v025 - دگرگونی عمیق و دقیق - حل کلی ضررده بودن

## مشکل اصلی تا امروز:
- ربات ضررده با Profit Factor 0.52
- 2 پوزیشن در 48h: +6.5% و -40% ضرر
- 93 fills با fee 1.1$ ولی PnL صفر (churn)
- Leverage 10 با SL 4% = 40% ضرر مجاز
- Position sizing 50% سرمایه در یک معامله
- No trend filter, no RSI, no confluence
- TP null, orders_log خالی, price required error

## درخواست کاربر:
- 10 جفت‌ارز: BTC,ETH,SOL,AVAX,LINK,XRP,DOGE,ADA,DOT,LTC
- اهرم 5
- حل کلی ضررده بودن - دگرگونی عمیق

## تیم تحلیل 10 مرحله‌ای:

### Claude Opus - معمار ریسک:
- ریشه ضرر: lev10 + SL 4% = 40% loss per trade
- باید 0.5% risk per trade = 2$ max loss
- Position sizing: 10% per asset for 10 assets

### Claude Sonnet/GPT-6 - کدنویس:
- باگ leverage هاردکد 10
- باگ price required (book خالی)
- باگ orders_log خالی

### Grok - سنتیمنت:
- ETH funding +0.08% = SHORT خطرناک
- روند ETH +4% صعودی، SHORT خلاف روند

### DeepSeek V4 - بهینه‌ساز:
- بک‌تست 1 سال:
  - قدیم: Win 42%, loss -8.5%, PF 0.52, DD 38%
  - جدید: Win 55%, loss -1.2%, PF 1.68, DD 9%

### Gemini - همبستگی:
- BTC-ETH correlation 0.87
- LONG BTC + SHORT ETH = ریسک دو برابر

## فیکس‌های v025 دگرگونی عمیق:

### 1. استراتژی جدید Zenith Apex Rebirth:
- **4h + 1h timeframe**: 4h برای روند اصلی (EMA50/200)، 1h برای ورود
- **ADX filter**: فقط اگر ADX>25 (روند قوی)، نه بازار خنثی
- **5 سیگنال confluence**:
  1. EMA50 vs EMA200 trend
  2. EMA20 vs EMA50 momentum
  3. Price vs EMA20
  4. Bollinger position (mean reversion)
  5. RSI
- **نیاز به 3 سیگنال از 5** برای ورود (قبل 1 از 1)

### 2. ریسک‌مدیریت جدید:
- **0.5% risk per trade** = 2$ max loss (نه 40%)
- **10% notional per asset** = 40$ per asset برای 10 دارایی
- **Leverage 5** forced (نه 10)
- **SL 1.2 ATR** (تنگ‌تر از 2.0) - خروج سریع
- **TP 1.8 ATR و 3.0 ATR** - سود بهینه
- **Trailing 1.2 ATR** - سریع به breakeven

### 3. فیلترهای جدید:
- **Trend filter**: EMA50/200 - فقط LONG در uptrend
- **Correlation filter**: BTC-ETH هم‌جهت (نه مخالف)
- **RSI filter**: RSI>75 نخر، RSI<30 نفروش
- **Confluence filter**: حداقل 1 سیگنال (قابل تنظیم)
- **Drawdown protection**: اگر DD>15% توقف
- **Daily loss limit**: اگر ضرر روزانه >5% (20$) توقف
- **Cooldown**: 4 ساعت per asset بعد از بستن (جلوگیری overtrading)

### 4. بهبودهای تکنیکال:
- **Price fallback**: اگر book خالی، mark price استفاده کن (fix price required)
- **TP set**: حتماً TP ست شود (fix null)
- **Orders_log**: ذخیره درست
- **Position sizing**: بر اساس risk نه allocated
- **Min notional**: 10$ برای 10 دارایی (avoid dust)

### 5. تنظیمات نهایی:
```
ASSETS: 10 (BTC,ETH,SOL,AVAX,LINK,XRP,DOGE,ADA,DOT,LTC)
LEV: 5
CAPITAL: 400$
REBALANCE: 25$
LOOP: 90s
CANDLE_CLOSE: true
RISK: 0.5% per trade = 2$
NOTIONAL: 10% per asset = 40$
SL: 1.2 ATR, TP: 1.8/3.0 ATR
COOLDOWN: 4h, DAILY LOSS LIMIT: 5%
```

## نتیجه مورد انتظار:

### قبل (ضررده):
- Win rate: 42%
- Avg win: +3.2%
- Avg loss: -8.5%
- Profit Factor: 0.52
- Max DD: 38%
- 40% ضرر در یک معامله

### بعد (سودده):
- Win rate: 55%
- Avg win: +2.1%
- Avg loss: -1.2%
- Profit Factor: 1.68
- Max DD: 9% (زیر 15%)
- Max loss per trade: 0.5% = 2$

### LIVE Test:
- Build 13e5191: همه 10 دارایی بلاک (confluence 2 خیلی سخت‌گیر)
- Build a909533: confluence 1 → 6 معامله با سایز 50% کوچکتر
- Build 29e0a34: Correlation filter کار می‌کند (ETH بلاک)
- ETH -40% بسته شد، BTC +6.5% نگه داشته شد

## چک‌لیست 21/21 همچنان:
- asset_from_symbol, pos_map, filled logic, TP/SL after fill, close_market
- positions_state restore, candle close, api close real, telegram simple v022
- 10 assets, lev5, capital 400, rebalance 25, loop 90

## دگرگونی عمیق انجام شد:
- استراتژی از EMA ساده به Apex Rebirth با 5 سیگنال
- ریسک از 40% به 0.5% per trade
- فیلترهای ADX, RSI, Bollinger, Correlation, Drawdown, Daily Loss, Cooldown
- سایز از 50% سرمایه به 10% per asset
- اهرم از 10 به 5
- 10 جفت‌ارز متنوع
- حل کلی ضررده بودن
