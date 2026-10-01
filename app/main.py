from __future__ import annotations

import hmac
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse

from app.config import get_settings
from app.engine import EngineBusyError, get_engine
from app.test_trade import TestTradeRejected
from app.telegram_bot import get_reporter, init_reporter_from_settings

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
# Telegram URLs contain the bot token. Never log HTTP request URLs at INFO.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("main")
BUILD = os.getenv("RENDER_GIT_COMMIT") or os.getenv("GIT_SHA") or "2026-10-01-coordinated-repair-v1"


def _check_token(authorization: Optional[str] = None, x_token: Optional[str] = None, *, control: bool = False):
    expected = get_settings().dashboard_token
    if not expected:
        if control:
            raise HTTPException(403, "Public control is disabled. Set DASHBOARD_TOKEN in Render; Telegram owner controls remain available.")
        return
    token = x_token or (authorization.split(" ", 1)[1].strip() if authorization and authorization.lower().startswith("bearer ") else "")
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(401, "unauthorized")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings, engine = get_settings(), get_engine()
    log.info("SUPER boot: build=%s allocated_capital=%s sleeves=%s dry_run=%s", BUILD, settings.capital_usd, settings.enabled_sleeves, settings.dry_run)
    try:
        reporter = init_reporter_from_settings(settings)
        if reporter:
            reporter.bind_engine(engine)
            reporter.start_background()
    except Exception:
        log.exception("telegram initialization failed")
    engine.start()
    yield
    reporter = get_reporter()
    if reporter:
        reporter.stop()
    engine.stop()
    engine.test_trader.shutdown(timeout=12)


app = FastAPI(title="Zenith SUPER Trader", version="3.0.0", description="Independent sleeves; observable and risk-capped testnet execution", lifespan=lifespan)


@app.get("/health")
def health():
    engine, settings = get_engine(), get_settings()
    # Read only small atomic state references, without holding the execution lock.
    state = engine.state
    diagnostic = state.diagnostics
    last = state.last_loop_at
    try:
        age = (datetime.now(timezone.utc)-datetime.fromisoformat(last)).total_seconds() if last else None
    except (TypeError, ValueError):
        age = None
    fresh = age is not None and age < max(600, 5 * settings.loop_seconds)
    test = engine.test_trader.snapshot()
    reporter = get_reporter()
    try:
        fin = engine.financial.snapshot()
        fin_summary = {k: fin.get(k) for k in ("allocated_capital_usdc", "estimated_bot_equity_usdc", "verified_closed_net_usdc", "period_fees_usdc", "unrealized_gross_usdc", "execution_count", "closed_event_count", "wins", "losses")}
    except Exception:
        fin_summary = {}
    return {
        "ok": True, "liveness_ok": True, "bot": "zenith-SUPER", "build": BUILD,
        "running": state.running, "mode": state.mode, "loop_count": state.loop_count,
        "last_loop_at": last, "loop_age_seconds": age, "last_error": state.last_error,
        "cycle_in_progress": state.cycle_in_progress, "cycle_started_at": state.cycle_started_at,
        "n_sleeves": len(state.sleeves), "n_assets": len(settings.asset_list),
        "trading_ready": bool(state.running and fresh and diagnostic.get("trading_ready")),
        "trading_state": diagnostic.get("trading_state", "initializing"),
        "primary_blocker": diagnostic.get("primary_blocker"),
        "market_open_count": diagnostic.get("market_open_count", 0),
        "market_halted_count": diagnostic.get("market_halted_count", 0),
        "active_signal_assets": diagnostic.get("active_signal_assets", 0),
        "confirmed_fill_count": diagnostic.get("confirmed_fill_count", 0),
        "last_fill_at": state.last_fill_at,
        "last_ops_review_at": state.ops_reviews[-1]["at"] if state.ops_reviews else None,
        "test_trade_feature": "minimum-lot-60s-v1",
        "test_trade": {k: test.get(k) for k in ("active", "status", "asset", "close_due_at", "seconds_remaining", "overdue_seconds")},
        "financial_feature": "capital-pnl-v3",
        "financial": fin_summary,
        "telegram_enabled": bool(reporter and reporter.enabled),
        "telegram_commands_enabled": bool(reporter and reporter.poll_commands),
        "telegram_messages_sent": reporter.stats.get("sent", 0) if reporter else 0,
    }


@app.get("/api/status")
def status(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token)
    return get_engine().snapshot()


@app.get("/api/diagnostics")
def diagnostics(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token)
    snap = get_engine().snapshot()
    return {"build": BUILD, "diagnostics": snap["diagnostics"], "market_data": snap["market_data"],
            "risk": snap["risk"], "actions": snap["actions"], "pending_order": snap["pending_order"],
            "ops_reviews": snap["ops_reviews"]}


@app.get("/api/test-trade")
def test_trade_status(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token)
    s = get_settings()
    return {"test_trade": get_engine().test_trader.snapshot(), "asset": s.test_trade_asset.upper(),
            "hold_seconds": s.test_trade_hold_seconds, "max_notional_usd": s.test_trade_max_notional_usd,
            "testnet_only": True, "telegram_url": f"https://t.me/{s.telegram_bot_username}?start=test60"}


@app.post("/api/test-trade/start")
def start_test_trade(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token, control=True)
    try:
        return {"ok": True, "test_trade": get_engine().test_trader.start()}
    except TestTradeRejected as exc:
        raise HTTPException(exc.http_status, {"code": exc.code, "message": str(exc)}) from None
    except Exception as exc:
        raise HTTPException(502, {"code": "test_trade_failed", "message": str(exc)}) from None


@app.post("/api/test-trade/close")
def close_test_trade(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token, control=True)
    return {"ok": True, "test_trade": get_engine().test_trader.request_close()}


@app.get("/api/financial")
def financial(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token)
    eng = get_engine()
    return eng.financial.snapshot()

@app.get("/api/sleeves")
def sleeves(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token)
    snap = get_engine().snapshot()
    return {"sleeves": snap["sleeves"], "net_book": snap["net_book"], "config": snap["config"]}


@app.post("/api/start")
def start(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token, control=True)
    get_engine().start()
    return {"started": True}


@app.post("/api/stop")
def stop(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token, control=True)
    get_engine().stop()
    return {"stopped": True}


@app.post("/api/tick")
def tick(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token, control=True)
    try:
        return {"ok": True, "result": get_engine().once()}
    except EngineBusyError as exc:
        raise HTTPException(409, str(exc)) from None
    except Exception as exc:
        raise HTTPException(500, str(exc)) from None


@app.get("/api/healthz")
def healthz():
    return {"ok": True}


@app.post("/api/telegram/test")
def telegram_test(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token, control=True)
    reporter = get_reporter()
    if not reporter or not reporter.enabled:
        raise HTTPException(400, "telegram not configured")
    reporter.send_panel()
    return {"ok": True, "stats": reporter.stats}


@app.post("/api/telegram/report")
def telegram_report(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token, control=True)
    reporter = get_reporter()
    if not reporter or not reporter.enabled:
        raise HTTPException(400, "telegram not configured")
    reporter.send(reporter.format_status(get_engine().snapshot()))
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return DASHBOARD_HTML


DASHBOARD_HTML = r'''<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Zenith SUPER · مرکز عملیات</title>
<style>
:root{color-scheme:dark;--bg:#070e19;--card:#101c2d;--line:#22324a;--text:#e4eaf4;--muted:#91a2ba;--green:#55deb0;--amber:#f4bf5a;--red:#ff8292;--cyan:#70d9eb}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:Tahoma,system-ui,sans-serif;font-size:13px;line-height:1.8}header{border-bottom:1px solid var(--line);padding:22px 5vw;display:flex;gap:20px;align-items:center;justify-content:space-between;flex-wrap:wrap}h1{margin:0;font-size:23px;letter-spacing:-.5px}h1 span{color:var(--cyan)}.eyebrow{font-size:10px;letter-spacing:2px;color:var(--muted);direction:ltr;text-align:right}main{max-width:1550px;margin:auto;padding:26px 4vw}.chip{border:1px solid var(--line);border-radius:25px;padding:5px 12px;font-size:11px;display:inline-block}.live{color:var(--green)}.amber{color:var(--amber)}.red{color:var(--red)}.muted{color:var(--muted)}.banner{background:linear-gradient(115deg,#1a2637,#142a36);border:1px solid var(--line);border-right:4px solid var(--amber);border-radius:14px;padding:20px 24px;display:flex;align-items:center;gap:18px;flex-wrap:wrap}.banner h2{margin:0;font-size:17px}.banner p{margin:5px 0 0;color:var(--muted);max-width:950px}.cards{display:grid;grid-template-columns:repeat(6,minmax(130px,1fr));gap:12px;margin:20px 0}.card,.panel{border:1px solid var(--line);background:var(--card);border-radius:12px;padding:18px}.card .label{font-size:11px;color:var(--muted)}.card .value{font-size:22px;font-weight:bold;direction:ltr;text-align:right}.card small{color:var(--muted);font-size:10px}.panel{margin-top:18px;padding:0;overflow:hidden}.panel-head{padding:16px 20px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.panel-head h2{font-size:14px;margin:0}.scroll{overflow:auto}table{border-collapse:collapse;min-width:900px;width:100%;font-size:11px}th,td{text-align:right;border-bottom:1px solid #1d2b40;padding:12px 15px;white-space:nowrap}th{color:var(--muted);font-weight:normal;background:#0d1727}td.asset{font-weight:bold;color:var(--cyan);direction:ltr;text-align:right}.number{font-family:ui-monospace,monospace;direction:ltr;display:inline-block}.long{color:var(--green)}.short{color:var(--red)}.note{white-space:normal;min-width:220px;max-width:420px;color:var(--muted)}.button{background:#142b3a;color:var(--cyan);border:1px solid #305165;border-radius:8px;padding:7px 13px;font-family:inherit;font-size:11px;cursor:pointer}.buttons{display:flex;gap:6px;flex-wrap:wrap}.riskrow{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:18px}.riskrow .panel{margin:0;padding:18px}.kv{display:flex;justify-content:space-between;gap:10px;padding:6px 0;border-bottom:1px solid #1d2b40}.foot{font-size:11px;color:var(--muted);margin:22px 0;line-height:2}pre{max-height:220px;overflow:auto;direction:ltr;text-align:left;font-size:10px;white-space:pre-wrap}.receipt table{min-width:700px}#message{color:var(--amber);padding-top:8px;min-height:16px}.meta{font-size:10px;color:var(--muted)}.dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--green);margin-left:5px}
@media(max-width:1000px){.cards{grid-template-columns:repeat(2,1fr)}.riskrow{grid-template-columns:1fr}.banner{padding:16px}.card .value{font-size:20px}header{padding:17px 4vw}}
</style></head><body>
<header><div><div class="eyebrow">INDEPENDENT SLEEVES / OPERATIONS CENTER</div><h1><span>Zenith</span> SUPER</h1><div class="muted">مرکز عملیات و گزارش مالی دقیق</div></div><div><span class="chip">Deribit testnet · پول آزمایشی</span> <span class="chip" id="engine">در حال اتصال</span><div class="meta" id="updated">—</div></div></header>
<main><section class="banner"><div><h2 id="reason-title">در حال دریافت وضعیت واقعی ربات…</h2><p id="reason-text">سالم‌بودن وب‌سرویس با آماده‌بودن مسیر معامله یکسان نیست.</p></div></section>
<div class="cards"><div class="card"><div class="label">بازار باز / کل</div><div class="value" id="markets">—</div><small id="halted">—</small></div><div class="card"><div class="label">نماد دارای سیگنال</div><div class="value" id="signals">—</div><small>سیگنال ≠ معامله</small></div><div class="card"><div class="label">پُرشدن تأییدشده</div><div class="value live" id="fills">—</div><small>فقط filled_amount</small></div><div class="card"><div class="label">سرمایه تخصیصی / سقف</div><div class="value" id="capital">—</div><small id="capital-sub">—</small></div><div class="card"><div class="label">سود/زیان بسته‌شده ۶ساعت</div><div class="value" id="pnl6h">—</div><small id="pnl6h-sub">—</small></div><div class="card"><div class="label">چرخه / گزارش جامع</div><div class="value" id="loops">—</div><small id="ops">—</small></div></div>
<section class="panel"><div class="panel-head"><h2>🧪 معامله تست — خروج خودکار پس از ۶۰ ثانیه</h2><span class="meta" id="test-spec">فقط تست‌نت · حداقل لات BTC · سقف ارزش ۱۰ دلار</span></div><div style="padding:18px"><div class="buttons"><button class="button" id="test-start" onclick="testTrade()">شروع معامله تست ۶۰ثانیه</button><button class="button" onclick="closeTest()">بستن همین تست اکنون</button><a class="button" id="test-tg" href="https://t.me/TestTraid_bot?start=test60" target="_blank" rel="noopener noreferrer" style="text-decoration:none">تأیید مالک در تلگرام ↗</a></div><div id="test-clock" style="font-size:22px;color:var(--cyan);margin-top:12px">تست فعال نیست</div><div id="test-info" class="meta">پس از تأیید مالک، کوچک‌ترین حجم مجاز باز می‌شود؛ تایمر از پرشدن واقعی ورود شروع می‌شود، نه از کلیک دکمه.</div><div id="test-message" class="amber"></div><p class="meta">با توکن کنترل می‌توانید از همین صفحه اجرا کنید؛ بدون توکن، دکمه به تأیید مالک در تلگرام هدایت می‌شود. هنگام تست، سفارش‌های استراتژی‌ها موقتاً متوقف‌اند.</p></div></section>
<section class="panel"><div class="panel-head"><h2>💰 گزارش مالی دقیق — سرمایه و سود/زیان هر معامله</h2><span class="meta" id="financial-meta">هر ورود/خروج با سرمایه و سود دقیق؛ نامشخص هرگز صفر نمایش داده نمی‌شود</span></div><div class="scroll"><table><thead><tr><th>زمان UTC</th><th>نماد / نوع</th><th>مقدار / قیمت</th><th>سرمایه پس از fill</th><th>بسته‌شده / میانگین ورود</th><th>سود ناخالص / کارمزد / سود خالص</th><th>Order ID</th><th>منبع</th></tr></thead><tbody id="financial-rows"><tr><td colspan="8">در حال دریافت…</td></tr></tbody></table></div><div style="padding:12px 18px" class="meta" id="financial-summary">—</div></section>
<section class="panel"><div class="panel-head"><h2>ماتریس پنج استراتژی و علت اجرای / عدم اجرای سفارش</h2><span class="meta">همهٔ نمادها · کندل بسته‌شدهٔ یک‌ساعته · بدون ترکیب DNA</span></div><div class="scroll"><table><thead><tr><th>نماد</th><th>Apex · ۲۵٪</th><th>almasi 177-v001 · ۲۵٪</th><th>IV3 Stable · ۳۰٪</th><th>IV3 Primary · ۱۰٪</th><th>Endurance · ۱۰٪</th><th>هدف / لات حداقل</th><th>وضعیت بازار / سن داده</th><th>مانع یا نتیجهٔ سفارش</th></tr></thead><tbody id="matrix"><tr><td colspan="9">در حال دریافت…</td></tr></tbody></table></div></section>
<div class="riskrow"><section class="panel"><h2 style="font-size:14px;margin-top:0">کنترل ریسک و سرمایه</h2><div id="risk">—</div><p class="meta">آستانه افت سرمایه، تضمین حداکثر زیان نیست. محاسبه تغییر موجودی به حساب USDC اختصاصی نیاز دارد؛ دیسک Render رایگان دائمی نیست.</p></section><section class="panel"><h2 style="font-size:14px;margin-top:0">عملیات و بازیابی</h2><div id="operations">—</div><div class="buttons" style="margin-top:14px"><button class="button" onclick="setToken()">توکن کنترل</button><button class="button" onclick="control('tick')">بررسی اکنون</button><button class="button" onclick="control('start')">شروع</button><button class="button" onclick="control('stop')">توقف</button><button class="button" onclick="refresh()">تازه‌سازی</button></div><div id="message"></div><p class="meta">کنترل عمومی بسته است. برای دکمه‌های مدیریتی، DASHBOARD_TOKEN را در Render تنظیم کنید؛ کنترل مالک در پنل تلگرام مستقل است.</p></section></div>
<section class="panel receipt"><div class="panel-head"><h2>رسیدهای واقعی سفارش</h2><span class="meta">فقط filled_amount مثبت؛ شامل شناسه سفارش و معامله</span></div><div class="scroll"><table><thead><tr><th>زمان UTC</th><th>نماد / سمت</th><th>مقدار پُرشده</th><th>قیمت اجرا</th><th>Order ID</th><th>نتیجه</th></tr></thead><tbody id="receipts"></tbody></table></div></section>
<section class="panel"><div class="panel-head"><h2>شواهد آخرین بررسی هماهنگ</h2><span class="meta" id="build">—</span></div><pre id="evidence" style="padding:18px">—</pre></section>
<div class="foot">هر استراتژی بودجه و سیگنال مستقل دارد؛ جمع هدف‌ها فقط در لایه سفارش انجام می‌شود. اجرای واقعی تنها با بازار باز، داده تازه، حجم معتبر، کنترل ریسک و سپس تأیید پُرشدن سفارش ثبت می‌شود. گزارش مالی با رسید واقعی صرافی و حسابداری FIFO دقیق محاسبه می‌شود؛ مقدار نامشخص هرگز صفر نشان داده نمی‌شود.</div>
</main><script>
const esc=v=>String(v==null?'—':v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=(v,n=2)=>Number.isFinite(Number(v))&&v!=null?Number(v).toFixed(n):'—';
let adminToken='';
let testAsset='BTC',testBudget=10;
let testView={},testFetchedAt=0,telegramTestUrl='https://t.me/TestTraid_bot?start=test60';
const headers=()=>adminToken?{'X-Token':adminToken}:{};
function setToken(){const t=prompt('DASHBOARD_TOKEN فقط برای همین صفحه (در فایل یا سرور ثبت نمی‌شود):');if(t!==null){adminToken=t;refresh()}}
const titles={test_trade_active:['تست یک‌دقیقه‌ای فعال است؛ استراتژی‌ها موقتاً در انتظارند','تایمر خروج مستقل از چرخه ۹۰ثانیه‌ای است. رسید و شمارش معکوس در بخش تست نمایش داده می‌شوند.'],test_recovery:['مالکیت تست قبلی در حال بررسی است','پیش از سفارش جدید، برچسب‌های تست و پوزیشن واقعی تطبیق داده می‌شوند.'],venue_halted:['بازار تست‌نت متوقف است؛ معاملات واقعی مسدودند','صرافی state=halted اعلام می‌کند. ربات سیگنال دارد، اما نمی‌تواند توقف صرافی را باز کند. هر چرخه وضعیت دوباره بررسی می‌شود و بعد از بازشدن، تازه‌بودن داده و ریسک نیز کنترل می‌شود.'],no_open_markets:['هیچ بازار قابل معامله‌ای باز نیست','وضعیت صرافی و خطاهای API در جدول تفکیک شده‌اند؛ پاسخ نامعتبر دیگر توقف بازار فرض نمی‌شود.'],account_unavailable:['حساب قابل خواندن نیست','کلید، مجوز حساب و اتصال صرافی را بررسی کنید. ربات هنگام خطای خواندن حساب یا پوزیشن، پوزیشن را صفر فرض نمی‌کند.'],positions_unavailable:['پوزیشن‌ها قابل تأیید نیستند','برای جلوگیری از سفارش تکراری، مسیر معامله بسته است.'],execution_uncertain:['نتیجه یک سفارش نامشخص است','ابتدا سفارش با برچسب یکتا و پوزیشن واقعی تطبیق داده می‌شود؛ ارسال مجدد کورکورانه ممنوع است.'],drawdown_guard:['محافظ افت سرمایه فعال شده است','افزایش ریسک متوقف است؛ فقط کاهش / بستن reduce-only در بازار باز مجاز است.'],cycle_error:['خطا در چرخه اجرا','شواهد و خطای واقعی را در بخش عملیات ببینید؛ سلامت HTTP به معنای سلامت معامله نیست.'],trading_disabled:['معامله غیرفعال است','TRADING_ENABLED=false؛ فقط سیگنال و عیب‌یابی محاسبه می‌شود.']};
function side(pa){if(!pa)return '<span class="muted">—</span>';if(pa.reason&&!(pa.side>0))return '<span class="amber" title="'+esc(pa.reason)+'">تاریخچه ناکافی</span>';const label=pa.side>0?'LONG':pa.side<0?'SHORT':'FLAT';return '<span class="'+(pa.side>0?'long':pa.side<0?'short':'muted')+'">'+label+'</span><br><span class="number muted">$'+fmt(pa.notional_usd)+'</span>'}
function kv(k,v,cls=''){return '<div class="kv"><span class="muted">'+esc(k)+'</span><span class="number '+cls+'">'+esc(v)+'</span></div>'}
async function refresh(){try{const r=await fetch('/api/status',{headers:headers()});if(!r.ok)throw new Error(r.status===401?'برای مشاهده وضعیت، توکن کنترل را وارد کنید.':'HTTP '+r.status);const d=await r.json();const h=await fetch('/health').then(r=>r.json());const dg=d.diagnostics||{},cfg=d.config||{},risk=d.risk||{},md=d.market_data||{};const fin=d.financial||{};document.getElementById('engine').textContent=d.running?'● موتور روشن':'موتور متوقف';document.getElementById('engine').className='chip '+(d.running?'live':'red');document.getElementById('updated').textContent='آخرین چرخه: '+(d.last_loop_at||'—');const reason=dg.primary_blocker;const t=titles[reason]||[reason?'مسیر معامله محدود است: '+reason:dg.trading_state==='dry_run'?'حالت شبیه‌سازی؛ معامله واقعی نیست':dg.trading_state==='waiting_for_signal'?'بازار آماده است؛ منتظر سیگنال معتبر':'مسیر معامله بررسی شده است',reason?'جزئیات مانع هر نماد در ماتریس پایین مشخص است.':'ثبت معامله فقط با رسید filled_amount مثبت صرافی انجام می‌شود.'];document.getElementById('reason-title').textContent=t[0];document.getElementById('reason-text').textContent=t[1];document.getElementById('markets').textContent=(dg.market_open_count||0)+' / '+(cfg.assets||[]).length;document.getElementById('halted').textContent=(dg.market_halted_count||0)+' بازار halted';document.getElementById('signals').textContent=dg.active_signal_assets||0;document.getElementById('fills').textContent=dg.confirmed_fill_count||0;document.getElementById('capital').textContent='$'+fmt(cfg.capital_usd,0)+' / $'+fmt(risk.notional_cap_usd,0);document.getElementById('capital-sub').textContent='برآورد ربات $'+fmt(risk.estimated_bot_equity_usd,2)+' · حساب $'+fmt((d.account.USDC||{}).equity,2);document.getElementById('pnl6h').textContent=(fin.verified_closed_net_usdc==null?'نامشخص':'$'+fmt(fin.verified_closed_net_usdc,4));document.getElementById('pnl6h-sub').textContent='کارمزد $'+fmt(fin.period_fees_usdc,4)+' · غیرمحقق $'+fmt(fin.unrealized_gross_usdc,4)+' · برد/باخت '+fin.wins+'/'+fin.losses;document.getElementById('loops').textContent=d.loop_count||0;document.getElementById('ops').textContent=h.last_ops_review_at||'هنوز ثبت نشده';const amap=Object.fromEntries((d.actions||[]).map(a=>[a.asset,a]));const smap=Object.fromEntries((d.sleeves||[]).map(s=>[s.id,s.per_asset||{}]));document.getElementById('matrix').innerHTML=(cfg.assets||[]).map(a=>{const action=amap[a]||{},q=md[a]||{};return '<tr><td class="asset">'+esc(a)+'</td>'+['zenith_apex','almasi_primary','inst_v3_stable','inst_v3_primary','zenith_endurance'].map(s=>'<td>'+side((smap[s]||{})[a])+'</td>').join('')+'<td><span class="number">'+fmt(action.planned_target_amt==null?action.target_amt:action.planned_target_amt,6)+'</span><br><span class="number muted">min '+fmt(action.minimum_amount,6)+'</span></td><td class="'+(q.market_state==='open'?'live':'amber')+'">'+esc(q.market_state)+'<br><span class="number muted">'+fmt((q.book_age_seconds||0)/3600,1)+'h old</span></td><td class="note"><b class="'+(action.filled_amount>0?'live':'amber')+'">'+esc(action.status)+'</b><br>'+esc(action.reason)+'</td></tr>'}).join('');document.getElementById('risk').innerHTML=kv('سرمایه تخصیصی','$'+fmt(cfg.capital_usd,0))+kv('برآورد ربات','$'+fmt(risk.estimated_bot_equity_usd,2))+kv('حساب USDC eq','$'+fmt((d.account.USDC||{}).equity,2))+kv('اکسپوژر واقعی فعلی','$'+fmt(risk.current_gross_notional_usd))+kv('سقف اهرم',fmt(cfg.lev_cap)+'×')+kv('افت برآوردشده',(100*(risk.drawdown_pct||0)).toFixed(2)+'%')+kv('آستانه توقف',(100*(risk.drawdown_limit_pct||.15)).toFixed(0)+'%')+kv('حداکثر لغزش مجاز سفارش',fmt(cfg.max_slippage_bps,0)+' bps')+kv('سفارش خروج','reduce-only');document.getElementById('operations').innerHTML=kv('آمادگی واقعی معامله',h.trading_ready?'READY':'NOT READY',h.trading_ready?'live':'amber')+kv('محیط اجرا',dg.execution_environment||'—')+kv('خطای چرخه',d.last_error||'none',d.last_error?'red':'')+kv('آخرین پُرشدن تأییدشده',d.last_fill_at||'—')+kv('سفارش نتیجه‌نامشخص',d.pending_order?'YES':'none')+kv('گزارش مالی',fin.status?JSON.stringify(fin.status).slice(0,120):'—');const orders=(d.orders_log||[]).slice(-20).reverse();document.getElementById('receipts').innerHTML=orders.length?orders.map(o=>'<tr><td class="number">'+esc(o.ts)+'</td><td>'+esc(o.asset)+' / '+esc(o.direction)+'</td><td class="number">'+fmt(o.filled_amount,6)+'</td><td class="number">'+fmt(o.price,4)+'</td><td>'+esc(o.order_id)+'</td><td class="live">'+esc(o.status)+'</td></tr>').join(''):'<tr><td colspan="6" class="muted">در دفتر ثبت فعلی، پُرشدن سفارش جدید تأیید نشده است.</td></tr>';const fevents=(fin.events||[]).slice(-20).reverse();document.getElementById('financial-rows').innerHTML=fevents.length?fevents.map(ev=>'<tr><td class="number">'+esc(new Date(ev.timestamp).toISOString())+'</td><td><b>'+esc(ev.asset)+'</b> '+esc(ev.kind)+'</td><td class="number">'+fmt(ev.filled_amount,6)+' @ '+fmt(ev.price,4)+'</td><td class="number">$'+fmt(ev.position_notional_at_fill_usdc,2)+'</td><td class="number">'+(ev.closed_quantity?fmt(ev.closed_quantity,6)+' @ '+fmt(ev.entry_price,4):'—')+'</td><td class="note">'+(ev.realized_gross_usdc==null&&ev.kind!=='open'&&ev.kind!=='increase'?'نامشخص':('gross $'+fmt(ev.realized_gross_usdc,4)+' fee $'+fmt(ev.paid_fee_usd,4)+' net $'+fmt(ev.net_final_usdc==null?ev.net_price_fees_usdc:ev.net_final_usdc,4)))+'</td><td>'+esc(ev.order_id)+'</td><td>'+esc(ev.source)+'</td></tr>').join(''):'<tr><td colspan="8" class="muted">هنوز معامله با سرمایه و سود دقیق ثبت نشده است.</td></tr>';document.getElementById('financial-summary').textContent='پنجره: '+(fin.window?fin.window.start_utc+' تا '+fin.window.end_utc:'—')+' · موجودی حساب: $'+fmt((fin.account||{}).equity,2)+' · سرمایه تخصیصی ربات: $'+fmt(fin.allocated_capital_usdc,0)+' · برآورد: $'+fmt(fin.estimated_bot_equity_usdc,2)+' · غیرمحقق: $'+fmt(fin.unrealized_gross_usdc,4)+' · بسته‌شده تأییدشده: $'+fmt(fin.verified_closed_net_usdc,4)+' · کارمزد دوره: $'+fmt(fin.period_fees_usdc,4);document.getElementById('financial-meta').textContent='پنجره '+(fin.window?fin.window.start_utc.slice(11,16)+' تا '+fin.window.end_utc.slice(11,16):'—')+' · برد/باخت '+fin.wins+'/'+fin.losses+' · نامشخص هرگز صفر نیست';renderTest(d.test_trade||{},cfg);document.getElementById('build').textContent='build '+h.build;document.getElementById('evidence').textContent=JSON.stringify({financial_status:fin.status,diagnostics:dg,latest_hourly_review:(d.ops_reviews||[]).slice(-1),recent_events:(d.events||[]).slice(-5)},null,2);}catch(e){document.getElementById('reason-title').textContent='وضعیت قابل دریافت نیست';document.getElementById('reason-text').textContent=e.message;}}
function renderTest(state,cfg){testAsset=cfg.test_trade_asset||testAsset;testBudget=cfg.test_trade_max_notional_usd||testBudget;document.getElementById('test-spec').textContent='فقط تست‌نت · حداقل لات '+testAsset+' · سقف ارزش $'+testBudget;testView=state;testFetchedAt=Date.now();telegramTestUrl=cfg.telegram_test_url||telegramTestUrl;document.getElementById('test-tg').href=telegramTestUrl;const entry=state.entry||{},exits=state.exits||[];document.getElementById('test-info').textContent='وضعیت: '+(state.status||'idle')+' · ورود پرشده: '+fmt(state.entry_filled_amount,6)+' · خروج پرشده: '+fmt(state.exit_filled_amount,6)+' · order ورود: '+(entry.order_id||'—')+' · order خروج: '+(exits.map(x=>x.order_id).filter(Boolean).join(', ')||'—')+' · '+(state.reason||state.warning||'');renderTestClock();}
function renderTestClock(){const s=testView,el=document.getElementById('test-clock');if(!s.active){el.textContent=s.status==='closed'?'✅ تست بسته شد · '+(s.closed_at||''):s.status==='closed_external'?'حساب بدون long تست است؛ خروج تایمر تأیید نشد':s.status==='unfilled'?'ورود پر نشد؛ معامله تست ایجاد نشد':'تست فعال نیست';return;}if(s.seconds_remaining==null){el.textContent='در انتظار تأیید سفارش ورود';return;}const left=Math.max(0,Number(s.seconds_remaining)-(Date.now()-testFetchedAt)/1000);el.textContent=left>0?'⏱ '+Math.ceil(left)+' ثانیه تا شروع خروج خودکار':'خروج در حال اجرا / تطبیق است؛ تأیید صرافی لازم است';}
async function testTrade(){const el=document.getElementById('test-message');if(!adminToken){el.textContent='برای اجرای امن، در تلگرام با حساب مالک تأیید کنید. سفارش عمومی بدون مجوز ارسال نمی‌شود.';window.open(telegramTestUrl,'_blank','noopener,noreferrer');return;}if(!confirm('یک خرید واقعی با پول آزمایشی روی '+testAsset+'، به حداقل لات و سقف $'+testBudget+' انجام شود؟ خروج ۶۰ ثانیه بعد از پرشدن ورود شروع می‌شود.'))return;el.textContent='در حال بررسی و ارسال تست…';try{const r=await fetch('/api/test-trade/start',{method:'POST',headers:headers()});const x=await r.json();if(!r.ok)throw new Error((x.detail&&x.detail.message)||x.detail||'HTTP '+r.status);el.textContent=x.test_trade.active?'تست فعال است؛ رسید و تایمر را بررسی کنید.':(x.test_trade.reason||x.test_trade.status);refresh();}catch(e){el.textContent=e.message;refresh();}}
async function closeTest(){if(!adminToken){document.getElementById('test-message').textContent='در پنل تلگرام، دکمه «بستن تست» را بزنید.';window.open(telegramTestUrl.replace('test60','teststatus'),'_blank','noopener,noreferrer');return;}try{const r=await fetch('/api/test-trade/close',{method:'POST',headers:headers()});const x=await r.json();if(!r.ok)throw new Error(x.detail||'HTTP '+r.status);document.getElementById('test-message').textContent='خروج فقط همین تست درخواست شد؛ پوزیشن‌های دیگر بسته نمی‌شوند.';refresh();}catch(e){document.getElementById('test-message').textContent=e.message;}}
setInterval(renderTestClock,1000);
async function control(name){const el=document.getElementById('message');el.textContent='در حال ارسال…';try{const r=await fetch('/api/'+name,{method:'POST',headers:headers()});const x=await r.json();if(!r.ok)throw new Error(x.detail||'HTTP '+r.status);el.textContent='درخواست انجام شد؛ هیچ معامله‌ای خارج از قواعد ریسک تحمیل نشد.';refresh()}catch(e){el.textContent=e.message}}
refresh();setInterval(refresh,15000);
</script></body></html>'''
