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
