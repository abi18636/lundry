# Zenith SUPER Bot — آستین‌های مستقل

## قانون طلایی
**DNAها مخلوط نمی‌شوند.** هر استراتژی:
1. سرمایهٔ خودش را دارد
2. سیگنال خودش را می‌سازد
3. notional خودش را حساب می‌کند
4. فقط در لایهٔ سفارش Deribit با بقیه **جمع جبری** می‌شود

## آستین‌های live

| ID | استراتژی lab | وزن پیش‌فرض | سرمایه از $100 |
|---|---|---:|---:|
| `zenith_apex` | zenith-v001 REBIRTH apex (compress80/impulse20) | ۲۵٪ | $25 |
| `almasi_primary` | almasi 177-v001 PRIMARY (TQ70/VQ30) | ۲۵٪ | $25 |
| `inst_v3_stable` | institutional-v3 stable (TSMOM 70/30) | ۳۰٪ | $30 |
| `inst_v3_primary` | institutional-v3 primary (TSMOM consensus) | ۱۰٪ | $10 |
| `zenith_endurance` | zenith endurance (impulse only) | ۱۰٪ | $10 |

## چه چیزی live نیست
- RCPE-1 / DonchianGuard vendor bot
- zenith multiz2 legacy (عمداً با REBIRTH جایگزین شد)
- پروفایل‌های فرعی almasi high_wr / balanced (فقط primary)

## کنترل وزن
Env: `SLEEVE_WEIGHTS` و `SLEEVES_ENABLED`
