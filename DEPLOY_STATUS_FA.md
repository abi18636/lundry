# آخرین قابلیت — تست یک‌دقیقه‌ای (۱ اکتبر ۲۰۲۶)

- بازار پس از گزارش قبلی باز شده است. اجرای واقعی نسخه قبلی: خروج BTC به مقدار 0.0001، سفارش `USDC-210049233929301753`، معامله `USDC-55431958` در 09:25:50 UTC. این، تست ETH جدید نیست.
- دکمه تست ۶۰ثانیه‌ای در وب و تلگرام اضافه شده؛ حداقل لات ETH، سقف ۵ دلار، فقط testnet، تأیید مالک و خروج reduce-only.
- زمان از fill ورود محاسبه می‌شود و تایمر مستقل از چرخه ۹۰ثانیه‌ای است.
- سفارش‌های استراتژی در مدت تست متوقف‌اند؛ روی نمادِ دارای پوزیشن/سفارش قبلی، تست ایجاد نمی‌شود.
- فایل state و برچسب‌های صرافی برای بازیابی تست نیمه‌تمام استفاده می‌شوند؛ خروج با قطع صرافی/شبکه/host در همان ثانیه تضمین نمی‌شود.
- ۱۰۶ آزمون از جمله تایمر wall-clock ۶۰ثانیه‌ای روی صرافی شبیه‌سازی‌شده پاس شده‌اند. سفارش ETH جدید فقط پس از فشار دادن دکمه و تأیید مالک ارسال می‌شود.
- راهنما: `TIMED_TEST_TRADE_FA.md`. SHA نسخه زنده و وضعیت تست از `/health` و `/api/test-trade` قابل مشاهده‌اند.

---

# آخرین وضعیت — بررسی هماهنگ ۱ اکتبر ۲۰۲۶

> این بخش، گزارش‌های قدیمی پایین فایل را اصلاح/تکمیل می‌کند. توضیح قبلیِ نسبت‌دادن تمام خطاهای 10031 به توقف صرافی، قطعی نبود؛ 10031 خطای آرگومان است و باید جدا بررسی شود.

- GitHub/Render: نخستین نسخه اصلاح‌شده `fd5f39b` زنده تأیید شد؛ hardening نهایی در main منتشر می‌شود و SHA اجرای زنده در `/health` قابل مشاهده است.
- پنج sleeve مستقل، ۲۵ نماد. ۱۵ نماد سیگنال فعال؛ هر ۲۵ دفتر صرافی halted. تأیید مستقل REST + WebSocket.
- `trading_ready=false` و `primary_blocker=venue_halted`؛ HTTP200 فقط liveness است.
- ۷۶ آزمون خودکار پاس شده‌اند؛ روی صرافی متوقف‌شده fill واقعی ادعا نمی‌شود.
- Apex impulse-only اصلاح شده، حجم به پایین گرد می‌شود، اکسپوژر ≤۱× بودجه، خروج reduce-only، اجرای IOC با سقف لغزش و reconciliation بعد از timeout.
- داشبورد زنده، حداقل لات، سن داده، مانع هر نماد و رسید fill واقعی را تفکیک می‌کند.
- کنترل HTTP عمومی بسته است؛ DASHBOARD_TOKEN لازم است. کنترل مالک تلگرام و اجرای خودکار مستقل باقی می‌مانند.
- بازبینی ساعتی داخلی فعال است. GitHub Actions **نصب نشده**: توکن فعلی scope workflow ندارد. فایل آمادهٔ نصب در workspace موجود است.
- برای state دائمی و اندازه‌گیری افت سرمایه معتبر، حساب USDC اختصاصی و ذخیره‌سازی پایدار لازم است؛ آستانه ۱۵٪ تضمین زیان نیست.
- کلیدها/توکن‌های افشاشده در سابقه چت باید rotate شوند.

گزارش فنی: `COORDINATED_REPAIR_FA.md`.

---
## سوابق قبلی (تاریخی؛ نه وضعیت نهایی)

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

