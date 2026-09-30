# وضعیت کارگروه Zenith Live

## هشدار امنیتی (فوری)
توکن GitHub / کلید Render / Client Secret دریبیت در چت قرار گرفتند.
**حتماً بعد از این کار rotate کنید** (GitHub PAT · Render API key · Deribit secret).

## خروجی کارگروه

| Agent | کار | وضعیت |
|---|---|---|
| A1 Strategy | DNA zenith-v001 REBIRTH داخل `strategies/` | ✅ |
| A2 Execution | long-only · lev≤1 · sleeve $100 · min notional | ✅ |
| A3 Venue | Deribit **testnet** USDC linear perps | ✅ |
| A4 Ops | GitHub push + Render free deploy | ✅ LIVE |
| A5 Risk | dashboard + /health + order log | ✅ |

## لینک‌ها

- **GitHub:** https://github.com/abi18636/lundry  
  (ریپوی خالی موجود با PAT فعلی؛ ساخت repo جدید با این PAT ممکن نبود — Contents روی `lundry` مجاز بود)
- **Render dashboard:** https://dashboard.render.com/web/srv-daukknojo6nc73dib9gg
- **Live URL:** https://zenith-trader-bot.onrender.com
- **Health:** https://zenith-trader-bot.onrender.com/health

## رفتار فعلی (testnet)

- پروفایل: **apex** = ۸۰٪ compress + ۲۰٪ impulse
- دارایی: BTC/ETH/SOL → `BTC_USDC-PERPETUAL` / `ETH_USDC-PERPETUAL` / `SOL_USDC-PERPETUAL`
- حلقه هر ۶۰s · capital sleeve $100 · lev 1
- سیگنال‌ها از کندل ۱h زنده Deribit
- Free tier Render ممکن است sleep کند؛ با باز کردن URL بیدار می‌شود

## کنترل

```bash
curl https://zenith-trader-bot.onrender.com/health
curl https://zenith-trader-bot.onrender.com/api/status
curl -X POST https://zenith-trader-bot.onrender.com/api/tick
curl -X POST https://zenith-trader-bot.onrender.com/api/stop
curl -X POST https://zenith-trader-bot.onrender.com/api/start
```

## محدودیت PAT گیت‌هاب
Fine-grained token فقط به repo `abi18636/lundry` دسترسی Contents داشت و **create repository** نداشت.
اگر repo جدا با نام `zenith-trader-bot` می‌خواهید: در GitHub یک repo جدید بسازید و PAT را با permission Contents+Metadata روی آن repo بدهید، یا classic PAT با scope `repo`.

## Local
کد: `/home/user/zenith_trader_bot`


## به‌روزرسانی نظارت ساعتی (2026-09-30)

### یافته‌ها
- Deribit **testnet** برای همهٔ `*_USDC-PERPETUAL` (و inverse) در حالت `state=halted`
- خطای قبلی `400 / 10031 invalid_args_for_instrument` ناشی از halt صرافی بود، نه باگ استراتژی
- dust position BTC باقی‌مانده قابل بستن نیست تا بازار open شود

### اصلاحات live (commit e4cb6ca+)
- `market_state` gate → `status=market_halted` بدون ارسال سفارش
- quantize amount به `contract_size` + `_fmt_amount`
- تلگرام: حداکثر یک اعلان halt / ۶ ساعت
- `scripts/hourly_watchdog.py` + حلقهٔ ساعتی در workspace
- health: فیلدهای `build` و `n_assets`

### وضعیت فعلی
- LIVE: 25 جفت · 5 sleeve · act_err=0 · 25× market_halted
- سفارش‌ها وقتی `state=open` شود با step تمیز ادامه می‌یابند
- secrets (GitHub/Render/Deribit) باید rotate شوند

