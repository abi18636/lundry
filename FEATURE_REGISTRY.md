# 📋 REGISTRY کامل ویژگی‌های ربات Zenith Trader Bot — 1000+ مورد
# هر آپدیت باید 999 مورد دیگر چک شود

## نسخه فعلی: v018-FIXED48

---

### 1. استراتژی‌ها و Sleeves (1-100)
1. diversified_5 — وزن 15%
2. zenith_apex — وزن 15%
3. almasi_primary — وزن 15% (almasi 177-v001)
4. inst_v3_stable — وزن 15%
5. inst_v3_primary — وزن 10%
6. zenith_endurance — وزن 10%
7. mean_reversion — وزن 20%
8. parse_weights — تجزیه وزن‌ها
9. run_all_sleeves — اجرای همه sleeves
10. _detect_regime — تشخیص رژیم بازار
11. Sleeve per_asset side — long/short/flat per asset
12. Sleeve confidence — اطمینان هر سیگنال
13. Sleeve capital allocation — تخصیص سرمایه
14. Sleeve weight dynamic — وزن پویا
15. 7 sleeves count — تعداد آستین‌ها
16. Sleeve notes — یادداشت‌ها
17. Net book aggregation — جمع سیگنال‌ها
18. Target coin calculation — محاسبه هدف
19. Price from market_data — قیمت از بازار
20. Notional USD calculation — ارزش دلاری

### 2. Confluence Filter — لایه 1 (101-200)
101. confluence_enabled — فعال‌سازی
102. confluence_min_agreement — حداقل توافق 2
103. confluence_min_confidence — حداقل اطمینان 0.50
104. apply_confluence_filter — اعمال فیلتر
105. Confluence strength — قدرت توافق
106. Confluence avg_conf — میانگین اطمینان
107. Confluence flat detection — تشخیص عدم توافق
108. Confluence info per asset — اطلاعات هر ارز
109. Confluence 3/7, 5/7, 6/7 — سطوح توافق
110. Confluence filter reason — دلیل فیلتر

### 3. Volatility Targeting — لایه 2 (201-300)
201. vol_targeting_enabled
202. target_vol_annual 0.40
203. volatility_targeted_size — سایز بر اساس نوسان
204. calculate_atr — محاسبه ATR
205. Realized vol calculation — نوسان واقعی
206. Vol scalar — ضریب نوسان
207. Vol info per asset — اطلاعات نوسان
208. Per cap allocation — سرمایه هر ارز
209. Lev cap — سقف اهرم 2x
210. Conf adjustment — تنظیم با اطمینان

### 4. Correlation Cap — لایه 3 (301-400)
301. correlation_enabled
302. correlation_default 0.7
303. correlation_cap_enabled
304. calculate_effective_exposure — اکسپوژر موثر
305. apply_correlation_cap — اعمال سقف همبستگی
306. Naive exposure — اکسپوژر ساده
307. Effective exposure — اکسپوژر موثر
308. Concentration ratio — نسبت تمرکز
309. Long/short exposure — اکسپوژر لانگ/شورت
310. Net exposure — اکسپوژر خالص
311. Notionals per asset — ارزش هر ارز
312. Scale applied — ضریب مقیاس

### 5. Smart Exit & Trailing — لایه 4 (401-500)
401. smart_exit_enabled
402. trailing_stop_atr 1.5
403. breakeven_atr 1.0
404. partial_take_atr 2.0
405. partial_take_fraction 0.5
406. manage_position_exit — مدیریت خروج
407. Breakeven move — انتقال SL به نقطه ورود
408. Partial take 50% — سیو سود 50%
409. Trailing SL — حد ضرر متحرک
410. Exit triggers — تریگرهای خروج

### 6. Meta Allocator — لایه 5 (501-600)
501. meta_allocator_enabled
502. meta_lookback_trades 20
503. meta_min_weight 0.03
504. meta_max_weight 0.35
505. PerformanceTracker — ردیاب عملکرد
506. compute_dynamic_weights — وزن پویا بر اساس عملکرد
507. compute_dynamic_weights_simple — وزن ساده بر اساس رژیم
508. Trade history — تاریخچه معاملات
509. Win/loss stats — آمار برد/باخت
510. Dynamic weight adjustment — تنظیم وزن پویا

### 7. Cost Filter — لایه 6 (601-700)
601. cost_filter_enabled
602. fee_bps 5.0
603. slippage_bps 10.0
604. min_edge_bps 2.0
605. estimate_signal_edge_bps — تخمین لبه سیگنال
606. should_trade_after_costs — آیا بعد از هزینه معامله کنیم؟
607. Notional vs edge — ارزش vs لبه
608. Cost filter reason — دلیل فیلتر هزینه

### 8. Regime Filter — لایه 7 (701-800)
701. regime_filter_enabled
702. adx_trending_threshold 25.0
703. adx_ranging_threshold 20.0
704. mom_threshold 0.008
705. _detect_regime — تشخیص رژیم trending/ranging
706. Regime per asset — رژیم هر ارز
707. Overall regime — رژیم کلی
708. Ranging count — تعداد رنج
709. Trending count — تعداد ترند

### 9. ATR SL/TP — مدیریت ریسک پایه (801-900)
801. use_stop_loss
802. use_take_profit
803. sl_tp_enabled
804. atr_stop_mult 2.0
805. atr_tp_mult 4.0
806. _calculate_atr_levels — محاسبه سطوح ATR
807. SL calculation — حد ضرر بر اساس ATR
808. TP calculation — حد سود بر اساس ATR
809. Position SL/TP storage — ذخیره SL/TP
810. _check_sl_tp_triggers — چک تریگر SL/TP

### 10. SMART TP/SL LADDER — پله‌ای هوشمند FIXED37-48 (901-1000)
901. smart_tp_sl_enabled
902. smart_sl_atr 2.0
903. smart_tp1_atr 1.5
904. smart_tp2_atr 3.0
905. smart_tp3_atr 5.0
906. smart_tp4_atr 8.0
907. smart_trailing_atr 1.5
908. smart_tp1_close_pct 0.30
909. smart_tp2_close_pct 0.30
910. smart_tp3_close_pct 0.15
911. smart_breakeven_atr 1.0
912. calculate_smart_levels — محاسبه سطوح پله‌ای
913. TP1, TP2, TP3, TP4 — 4 سطح TP
914. Level 0 initial — SL 2xATR, TP1 1.5xATR
915. Level 1 breakeven — سود 1xATR → SL=Entry +20% close
916. Level 2 TP1 hit — 30% close, SL=TP1, TP=TP2 (tp=tp1, sl=tp)
917. Level 3 TP2 hit — 30% close, SL=TP2, TP=TP3
918. Level 4 TP3 hit — 15% close, trailing 1.5xATR
919. manage_smart_exit — مدیریت خروج پله‌ای
920. check_smart_triggers — چک تریگر هوشمند
921. Breakeven moved flag — پرچم انتقال به نقطه ورود
922. TP1/TP2/TP3 hit flags — پرچم‌های برخورد به TP
923. Partial taken tracking — ردیابی بستن پله‌ای
924. Profit locked — سود قفل شده
925. Highest profit ATR — بیشترین سود بر حسب ATR
926. Trailing active — فعال بودن تریلینگ
927. Auto-register for existing positions — ثبت خودکار برای پوزیشن‌های موجود
928. SMART panel in dashboard — پنل نمایش در داشبورد
929. Telegram SMART display — نمایش در تلگرام

### 11. Execution & Order Management (1001-1100)
1001. plan_rebalance — برنامه ریزی ریبالانس
1002. floor_amount — کف مقدار بر اساس step
1003. signed_position — سایز علامت‌دار پوزیشن
1004. ioc_price — قیمت IOC با slippage
1005. rebalance_notional_usd 1.0 — آستانه ریبالانس
1006. min_notional_usd 5.0 — حداقل ارزش صرافی
1007. max_spread_bps 200.0
1008. max_slippage_bps 100.0
1009. cooldown_seconds 60
1010. backoff_seconds 60
1011. skip_small — دلتای کوچک < threshold
1012. below_exchange_minimum — زیر حداقل صرافی
1013. target_reached — هدف رسیده
1014. no_price — بدون قیمت
1015. no_liquidity — بدون نقدینگی
1016. cooldown — دوره انتظار
1017. trading_disabled — معامله غیرفعال
1018. drawdown_guard — محافظ افت سرمایه
1019. market_halted — بازار متوقف
1020. order_error_backoff — خطای سفارش و انتظار
1021. Close tiny positions fix — فیکس بستن پوزیشن‌های کوچک FIXED43-48
1022. Ceil notional fix — فیکس ceil برای حداقل 5.1$ FIXED47
1023. Reduce only close — بستن با reduce_only FIXED46
1024. Min qty + notional both — حداقل qty و notional هر دو FIXED45

### 12. AriaX Client — صرافی (1101-1200)
1101. AriaXClient — کلاینت صرافی
1102. ariax_base_url
1103. ariax_api_key
1104. ariax_api_secret
1105. _request — درخواست با امضا
1106. account_summary — خلاصه حساب USDT
1107. positions — پوزیشن‌ها
1108. order_book — دفتر سفارشات
1109. instrument — اطلاعات ابزار
1110. candles — کندل‌ها
1111. _place_order — ثبت سفارش
1112. close_position — بستن پوزیشن FIXED36
1113. cancel_order — لغو سفارش
1114. open_orders — سفارشات باز
1115. V5 API fallback — استفاده از v5 اگر /api شکست خورد FIXED48
1116. Min qty and notional handling — مدیریت حداقل‌ها

### 13. Config & Settings (1201-1300)
1201. Settings class — تنظیمات
1202. capital_usd 2000 — سرمایه
1203. effective_capital — سرمایه موثر max(400,10000) FIXED48
1204. lev_cap 2.0 — سقف اهرم
1205. long_only false
1206. assets 10 pairs — 10 جفت ارز
1207. asset_list — لیست ارزها
1208. sleeve_weights — وزن آستین‌ها
1209. enabled_sleeves — آستین‌های فعال
1210. loop_seconds 90 — هر 90 ثانیه
1211. max_notional_usd 1000
1212. market_data_max_age_seconds 180
1213. candle_lookback_hours 1000
1214. dashboard_token — توکن داشبورد
1215. telegram_enabled
1216. telegram_bot_token
1217. telegram_chat_id
1218. telegram_bot_username TestTraid_bot
1219. version — ورژن
1220. get_settings — گرفتن تنظیمات

### 14. Engine & State Management (1301-1400)
1301. TradingEngine — موتور معامله
1302. BotState — وضعیت ربات
1303. running flag — روشن/خاموش
1304. last_loop_at — آخرین چرخه
1305. loop_count — تعداد چرخه
1306. mode — حالت
1307. sleeves — آستین‌ها
1308. net_book — دفتر خالص
1309. positions — پوزیشن‌ها
1310. account — حساب
1311. orders_log — لاگ سفارشات (300 آخر)
1312. events — رویدادها (300 آخر)
1313. actions — اقدامات
1314. diagnostics — عیب‌یابی
1315. market_data — داده بازار
1316. risk — ریسک
1317. position_sl_tp — SL/TP پوزیشن‌ها
1318. confluence_info — اطلاعات توافق
1319. correlation_info — اطلاعات همبستگی
1320. test_trade — معامله تست
1321. version_history — تاریخچه ورژن
1322. last_trade_at — آخرین معامله هر ارز
1323. _candle_cache — کش کندل‌ها
1324. _backoffs — انتظار بعد از خطا
1325. _last_inventory — آخرین موجودی FIXED38
1326. _ensure_client — اطمینان از کلاینت
1327. once() — یک چرخه
1328. start() — شروع
1329. stop() — توقف
1330. snapshot() — اسنپ‌شات
1331. close_position() — بستن تکی FIXED36

### 15. Test Trade (1401-1500)
1401. test_trade_enabled
1402. test_trade_asset BTC
1403. test_trade_hold_seconds 60
1404. test_trade_max_notional_usd 10.0
1405. TimedTestTrade — معامله تست زمان‌دار
1406. Test trade start — شروع تست
1407. Test trade close — بستن تست
1408. Test trade status — وضعیت تست
1409. Test trade auto close after 60s — بستن خودکار بعد 60 ثانیه

### 16. Telegram Bot (1501-1700)
1501. TelegramReporter — گزارشگر تلگرام
1502. enabled flag — bool(token and chat_id)
1503. bot_token — توکن ربات
1504. chat_id — آیدی چت
1505. poll_commands — دریافت دستورات
1506. main_keyboard — کیبورد اصلی
1507. Close buttons per asset — دکمه بستن برای هر ارز close:ASSET FIXED36
1508. Close all button — دکمه بستن همه
1509. Panel buttons — دکمه‌های پنل
1510. Start/Stop/Tick buttons — دکمه‌های کنترل
1511. Status button — وضعیت
1512. Positions button — پوزیشن‌ها
1513. Balance button — موجودی
1514. Sleeves button — آستین‌ها
1515. Test trade buttons — دکمه‌های تست
1516. File report button — گزارش فایل
1517. Health button — سلامت
1518. _api — ارسال به API تلگرام
1519. send — ارسال پیام
1520. send_document — ارسال فایل
1521. send_panel — ارسال پنل
1522. format_status — فرمت وضعیت با ورژن
1523. format_positions — فرمت پوزیشن‌ها با ورژن + SMART TP/SL
1524. format_balance — فرمت موجودی
1525. format_sleeves — فرمت آستین‌ها
1526. format_test_trade — فرمت تست
1527. format_full_report — گزارش جامع
1528. build_full_report_text — متن گزارش کامل با ورژن
1529. send_full_file_report — ارسال گزارش فایل
1530. on_version_change — تغییر ورژن با گزارش تلگرام
1531. answer_callback — پاسخ به کلیک
1532. handle_callback — مدیریت کلیک close:ASSET و panel:*
1533. _poll_loop — حلقه دریافت دستورات FIXED9 polling
1534. _bg_loop — حلقه پس‌زمینه گزارش هر 6 ساعت
1535. start_background — شروع پس‌زمینه + ارسال پنل خودکار FIXED34
1536. stop — توقف
1537. init_reporter_from_settings — مقداردهی از تنظیمات
1538. get_reporter — گرفتن گزارشگر
1539. Individual trade reports — گزارش تکی هر معامله (BROKEN in FIXED48, need fix)
1540. Heartbeat every 30 min — ضربان هر 30 دقیقه
1541. Full report every 6h — گزارش کامل هر 6 ساعت
1542. Trade only reporting — فقط معامله واقعی/خطا
1543. Version in all reports — ورژن در همه گزارشات FIXED37

### 17. Dashboard & API (1701-1900)
1701. FastAPI app — اپ فست
1702. lifespan — چرخه حیات
1703. /health — سلامت با version, build, trading_ready, telegram_enabled, smart_tpsl
1704. /api/status — وضعیت با positions, actions, risk, config, sltp, position_sl_tp
1705. /api/financial — مالی با account, positions, risk, version, sltp
1706. /api/sleeves — آستین‌ها
1707. /api/positions — پوزیشن‌ها
1708. /api/close/{asset} — بستن تکی FIXED36
1709. /api/close-all — بستن همه
1710. /api/test-trade/start — شروع تست
1711. /api/test-trade/close — بستن تست
1712. /api/start — شروع موتور
1713. /api/stop — توقف موتور
1714. /api/tick — یک چرخه
1715. /api/debug/settings — دیباگ تنظیمات
1716. /api/debug/telegram — دیباگ تلگرام
1717. /api/debug/wallet — دیباگ کیف پول
1718. /api/debug/positions — دیباگ پوزیشن‌ها
1719. /api/debug/ariax — دیباگ آریاایکس
1720. /api/debug/tmp — دیباگ tmp
1721. _check_token — چک توکن داشبورد
1722. Dashboard public control — کنترل عمومی بدون توکن FIXED35
1723. Dashboard HTML — صفحه اصلی
1724. Cards: markets, signals, fills, capital, pnl, loops — کارت‌ها
1725. Matrix table — جدول ماتریس 7 sleeves + target + market + action + close button
1726. Positions table — جدول پوزیشن‌ها با بستن
1727. Financial table — جدول مالی با ورژن
1728. SMART TP/SL panel — پنل هوشمند FIXED40
1729. Risk panel — پنل ریسک
1730. Operations panel — پنل عملیات
1731. Version info — اطلاعات ورژن
1732. Test trade panel — پنل تست
1733. JS refresh() — تازه‌سازی هر 15 ثانیه
1734. JS closePosition() — بستن پوزیشن
1735. JS closeAllPositions() — بستن همه
1736. JS setToken() — تنظیم توکن
1737. JS control() — کنترل موتور
1738. JS testTrade() — تست
1739. USDT fix — فیکس USDC→USDT FIXED34
1740. Financial fetch — دریافت مالی از /api/financial FIXED34

### 18. GitHub & Deployment (1901-2000)
1901. GitHub repo abi18636/lundry
1902. Render deployment — دیپلوی در رندر
1903. render.yaml — تنظیمات رندر
1904. requirements.txt — وابستگی‌ها
1905. GitHub PAT handling — مدیریت کلید گیت‌هاب
1906. Force rebuild via requirements.txt — بازسازی اجباری
1907. BUILD env var — ورژن بیلد از RENDER_GIT_COMMIT
1908. Version in health — ورژن در سلامت
1909. Loop count — تعداد چرخه
1910. Last loop at — آخرین چرخه

