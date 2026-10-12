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

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("main")
BUILD = os.getenv("RENDER_GIT_COMMIT") or os.getenv("GIT_SHA") or "v018-10pairs-7sleeves-testtrade-close-FIXED7-TRADING-EXECUTION"


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
    log.info("SUPER boot: build=%s version=%s capital=%s assets=%s sleeves=%s test_trade=%s", BUILD, settings.version, settings.capital_usd, settings.asset_list, settings.enabled_sleeves, settings.effective_test_trade_enabled)
    try:
        from app.telegram_bot import init_reporter_from_settings
        reporter = init_reporter_from_settings(settings)
        if reporter:
            reporter.bind_engine(engine)
            reporter.start_background()
    except Exception:
        log.exception("telegram initialization failed")
    engine.start()
    yield
    try:
        from app.telegram_bot import get_reporter
        reporter = get_reporter()
        if reporter:
            reporter.stop()
    except:
        pass
    engine.stop()


app = FastAPI(title="Zenith SUPER v018", version="v018-10pairs", lifespan=lifespan)


@app.get("/health")
def health():
    engine, settings = get_engine(), get_settings()
    state = engine.state
    diagnostic = state.diagnostics
    last = state.last_loop_at
    try:
        age = (datetime.now(timezone.utc)-datetime.fromisoformat(last)).total_seconds() if last else None
    except:
        age = None
    fresh = age is not None and age < max(600, 5 * settings.loop_seconds)
    
    try:
        from app.telegram_bot import get_reporter
        reporter = get_reporter()
    except:
        reporter = None
    
    fin_summary = {
        "allocated_capital_usdc": settings.capital_usd,
        "estimated_bot_equity_usdc": state.risk.get("estimated_bot_equity", settings.capital_usd),
        "unrealized_gross_usdc": sum(float(p.get("floating_profit_loss") or 0) for p in state.positions),
    }
    
    return {
        "ok": True, "liveness_ok": True, "bot": f"zenith-SUPER-{state.version}", "build": BUILD,
        "version": state.version,
        "version_history": state.version_history[-5:],
        "running": state.running, "mode": state.mode, "loop_count": state.loop_count,
        "last_loop_at": last, "loop_age_seconds": age, "last_error": state.last_error,
        "n_sleeves": len(state.sleeves), "n_assets": len(settings.asset_list),
        "assets": settings.asset_list,
        "assets_count": len(settings.asset_list),
        "trading_ready": bool(state.running and fresh and diagnostic.get("trading_ready")),
        "trading_state": diagnostic.get("trading_state", "initializing"),
        "primary_blocker": diagnostic.get("primary_blocker"),
        "market_open_count": diagnostic.get("market_open_count", 0),
        "market_halted_count": diagnostic.get("market_halted_count", 0),
        "active_signal_assets": diagnostic.get("active_signal_assets", 0),
        "confirmed_fill_count": 0,
        "last_fill_at": state.last_fill_at,
        "test_trade_enabled": settings.effective_test_trade_enabled,
        "test_trade_asset": settings.test_trade_asset,
        "test_trade": state.test_trade,
        "confluence_info": state.confluence_info,
        "correlation_info": state.correlation_info,
        "regime_info": diagnostic.get("regime_info", {}),
        "philosophy": "Signal-Quality-On 30-60% variable + 10 pairs + test trade + telegram close buttons",
        "financial": fin_summary,
        "telegram_enabled": bool(reporter and reporter.enabled) if reporter else False,
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
            "ops_reviews": snap["ops_reviews"], "version": snap.get("version")}


@app.get("/api/financial")
def financial(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token)
    eng = get_engine()
    risk = eng.state.risk
    return {
        "allocated_capital_usdc": eng.settings.capital_usd,
        "estimated_bot_equity_usdc": risk.get("estimated_bot_equity", eng.settings.capital_usd),
        "account": eng.state.account,
        "positions": eng.state.positions,
        "risk": risk,
        "version": eng.state.version,
        "unrealized_gross_usdc": sum(float(p.get("floating_profit_loss") or 0) for p in eng.state.positions),
        "window": {"start_utc": "2026-10-11T00:00:00Z", "end_utc": "2026-10-11T23:59:59Z"},
        "execution_count": len(eng.state.orders_log),
        "closed_event_count": 0,
        "wins": 0,
        "losses": 0,
        "verified_closed_net_usdc": 0,
        "period_fees_usdc": 0,
        "events": [],
    }


@app.get("/api/sleeves")
def sleeves(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token)
    snap = get_engine().snapshot()
    return {"sleeves": snap["sleeves"], "net_book": snap["net_book"], "config": snap["config"], "version": snap.get("version")}


@app.get("/api/positions")
def positions(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token)
    eng = get_engine()
    return {"positions": eng.state.positions, "version": eng.state.version, "assets": eng.settings.asset_list}


@app.post("/api/close/{asset}")
def close_position(asset: str, authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token, control=True)
    eng = get_engine()
    result = eng.close_position(asset.upper())
    return result


@app.post("/api/close-all")
def close_all(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token, control=True)
    eng = get_engine()
    results = {}
    for a in eng.settings.asset_list:
        try:
            results[a] = eng.close_position(a)
        except Exception as e:
            results[a] = {"ok": False, "error": str(e)}
    return {"ok": True, "results": results, "version": eng.state.version}


@app.get("/api/test-trade")
def test_trade_status(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token)
    eng = get_engine()
    s = eng.settings
    test_state = eng.state.test_trade
    if eng.test_trader:
        try:
            test_state = eng.test_trader.snapshot()
        except:
            pass
    return {"test_trade": test_state, "asset": s.test_trade_asset, "enabled": s.effective_test_trade_enabled, "version": eng.state.version, "hold_seconds": s.test_trade_hold_seconds, "max_notional_usd": s.test_trade_max_notional_usd}


@app.post("/api/test-trade/start")
def start_test_trade(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token, control=True)
    eng = get_engine()
    if not eng.settings.effective_test_trade_enabled:
        raise HTTPException(400, "Test trade disabled")
    try:
        if eng.test_trader:
            state = eng.test_trader.start()
            return {"ok": True, "test_trade": state, "version": eng.state.version}
        else:
            raise HTTPException(500, "Test trader not available")
    except Exception as exc:
        raise HTTPException(500, str(exc))


@app.post("/api/test-trade/close")
def close_test_trade(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token, control=True)
    eng = get_engine()
    if eng.test_trader:
        state = eng.test_trader.request_close()
        return {"ok": True, "test_trade": state, "version": eng.state.version}
    return {"ok": False, "error": "Test trader not available"}


@app.post("/api/start")
def start(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token, control=True)
    get_engine().start()
    return {"ok": True, "version": get_engine().state.version}


@app.post("/api/stop")
def stop(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token, control=True)
    get_engine().stop()
    return {"ok": True, "version": get_engine().state.version}


@app.post("/api/tick")
def tick(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token, control=True)
    try:
        result = get_engine().once()
        return {"ok": True, "result": result, "version": get_engine().state.version}
    except EngineBusyError as exc:
        raise HTTPException(409, str(exc))
    except Exception as exc:
        raise HTTPException(500, str(exc))


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return DASHBOARD_HTML


DASHBOARD_HTML = r'''<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Zenith SUPER v018 · مرکز عملیات</title>
<style>
:root{color-scheme:dark;--bg:#070e19;--card:#101c2d;--line:#22324a;--text:#e4eaf4;--muted:#91a2ba;--green:#55deb0;--amber:#f4bf5a;--red:#ff8292;--cyan:#70d9eb}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:Tahoma,system-ui,sans-serif;font-size:13px;line-height:1.8}header{border-bottom:1px solid var(--line);padding:22px 5vw;display:flex;gap:20px;align-items:center;justify-content:space-between;flex-wrap:wrap}h1{margin:0;font-size:23px;letter-spacing:-.5px}h1 span{color:var(--cyan)}.eyebrow{font-size:10px;letter-spacing:2px;color:var(--muted);direction:ltr;text-align:right}main{max-width:1550px;margin:auto;padding:26px 4vw}.chip{border:1px solid var(--line);border-radius:25px;padding:5px 12px;font-size:11px;display:inline-block}.live{color:var(--green)}.amber{color:var(--amber)}.red{color:var(--red)}.muted{color:var(--muted)}.banner{background:linear-gradient(115deg,#1a2637,#142a36);border:1px solid var(--line);border-right:4px solid var(--amber);border-radius:14px;padding:20px 24px;display:flex;align-items:center;gap:18px;flex-wrap:wrap}.banner h2{margin:0;font-size:17px}.banner p{margin:5px 0 0;color:var(--muted);max-width:950px}.cards{display:grid;grid-template-columns:repeat(6,minmax(130px,1fr));gap:12px;margin:20px 0}.card,.panel{border:1px solid var(--line);background:var(--card);border-radius:12px;padding:18px}.card .label{font-size:11px;color:var(--muted)}.card .value{font-size:22px;font-weight:bold;direction:ltr;text-align:right}.card small{color:var(--muted);font-size:10px}.panel{margin-top:18px;padding:0;overflow:hidden}.panel-head{padding:16px 20px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.panel-head h2{font-size:14px;margin:0}.scroll{overflow:auto}table{border-collapse:collapse;min-width:900px;width:100%;font-size:11px}th,td{text-align:right;border-bottom:1px solid #1d2b40;padding:12px 15px;white-space:nowrap}th{color:var(--muted);font-weight:normal;background:#0d1727}td.asset{font-weight:bold;color:var(--cyan);direction:ltr;text-align:right}.number{font-family:ui-monospace,monospace;direction:ltr;display:inline-block}.long{color:var(--green)}.short{color:var(--red)}.note{white-space:normal;min-width:220px;max-width:420px;color:var(--muted)}.button{background:#142b3a;color:var(--cyan);border:1px solid #305165;border-radius:8px;padding:7px 13px;font-family:inherit;font-size:11px;cursor:pointer}.buttons{display:flex;gap:6px;flex-wrap:wrap}.riskrow{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:18px}.riskrow .panel{margin:0;padding:18px}.kv{display:flex;justify-content:space-between;gap:10px;padding:6px 0;border-bottom:1px solid #1d2b40}.foot{font-size:11px;color:var(--muted);margin:22px 0;line-height:2}pre{max-height:220px;overflow:auto;direction:ltr;text-align:left;font-size:10px;white-space:pre-wrap}.receipt table{min-width:700px}#message{color:var(--amber);padding-top:8px;min-height:16px}.meta{font-size:10px;color:var(--muted)}.dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--green);margin-left:5px}
@media(max-width:1000px){.cards{grid-template-columns:repeat(2,1fr)}.riskrow{grid-template-columns:1fr}.banner{padding:16px}.card .value{font-size:20px}header{padding:17px 4vw}}
</style></head><body>
<header><div><div class="eyebrow">INDEPENDENT SLEEVES / OPERATIONS CENTER v018</div><h1><span>Zenith</span> SUPER v018</h1><div class="muted">مرکز عملیات و گزارش مالی دقیق - 10 جفت ارز - تست فعال - بستن مجزا</div></div><div><span class="chip">AriaX Testnet · 10 جفت ارز · v018</span> <span class="chip" id="engine">در حال اتصال</span><div class="meta" id="updated">—</div><div class="meta" id="version">v018</div></div></header>
<main><section class="banner"><div><h2 id="reason-title">در حال دریافت وضعیت واقعی ربات…</h2><p id="reason-text">سالم‌بودن وب‌سرویس با آماده‌بودن مسیر معامله یکسان نیست.</p></div></section>
<div class="cards"><div class="card"><div class="label">بازار باز / کل</div><div class="value" id="markets">—</div><small id="halted">—</small></div><div class="card"><div class="label">نماد دارای سیگنال</div><div class="value" id="signals">—</div><small>سیگنال ≠ معامله - Confluence Filter</small></div><div class="card"><div class="label">پُرشدن تأییدشده</div><div class="value live" id="fills">—</div><small>فقط filled_amount</small></div><div class="card"><div class="label">سرمایه تخصیصی / سقف</div><div class="value" id="capital">—</div><small id="capital-sub">—</small></div><div class="card"><div class="label">سود/زیان بسته‌شده</div><div class="value" id="pnl6h">—</div><small id="pnl6h-sub">—</small></div><div class="card"><div class="label">چرخه / ورژن</div><div class="value" id="loops">—</div><small id="ops">—</small></div></div>
<section class="panel"><div class="panel-head"><h2>🧪 معامله تست — خروج خودکار پس از ۶۰ ثانیه — v018 فعال</h2><span class="meta" id="test-spec">فقط تست‌نت · حداقل لات BTC · سقف ارزش ۱۰ دلار · فعال</span></div><div style="padding:18px"><div class="buttons"><button class="button" id="test-start" onclick="testTrade()">شروع معامله تست ۶۰ثانیه BTC</button><button class="button" onclick="closeTest()">بستن همین تست اکنون</button><button class="button" onclick="closeAllPositions()">🛑 بستن همه پوزیشن‌ها</button><a class="button" id="test-tg" href="https://t.me/TestTraid_bot?start=test60" target="_blank" rel="noopener noreferrer" style="text-decoration:none">تأیید مالک در تلگرام ↗</a></div><div id="test-clock" style="font-size:22px;color:var(--cyan);margin-top:12px">تست فعال نیست</div><div id="test-info" class="meta">پس از تأیید مالک، کوچک‌ترین حجم مجاز BTC باز می‌شود؛ تایمر از پرشدن واقعی ورود شروع می‌شود. v018 تست فعال است.</div><div id="test-message" class="amber"></div><p class="meta">با توکن کنترل می‌توانید از همین صفحه اجرا کنید؛ بدون توکن، دکمه به تأیید مالک در تلگرام هدایت می‌شود. v018: دکمه بستن برای هر معامله مجزا در تلگرام و اینجا.</p></div></section>
<section class="panel"><div class="panel-head"><h2>💼 پوزیشن‌های باز — بستن مجزا برای هر معامله</h2><span class="meta">v018 جدید: دکمه بستن برای هر پوزیشن</span></div><div class="scroll"><table><thead><tr><th>نماد</th><th>جهت / سایز</th><th>میانگین / مارک</th><th>PnL</th><th>بستن مجزا</th></tr></thead><tbody id="positions-table"><tr><td colspan="5">در حال دریافت…</td></tr></tbody></table></div></section>
<section class="panel"><div class="panel-head"><h2>💰 گزارش مالی دقیق — سرمایه و سود/زیان هر معامله — ورژن در گزارش</h2><span class="meta" id="financial-meta">هر ورود/خروج با سرمایه و سود دقیق؛ ورژن v018 در گزارش</span></div><div class="scroll"><table><thead><tr><th>زمان UTC</th><th>نماد / نوع</th><th>مقدار / قیمت</th><th>سرمایه پس از fill</th><th>بسته‌شده / میانگین ورود</th><th>سود ناخالص / کارمزد / سود خالص</th><th>Order ID</th><th>ورژن</th></tr></thead><tbody id="financial-rows"><tr><td colspan="8">در حال دریافت…</td></tr></tbody></table></div><div style="padding:12px 18px" class="meta" id="financial-summary">—</div></section>
<section class="panel"><div class="panel-head"><h2>ماتریس استراتژی‌ها و علت اجرای / عدم اجرای سفارش — 7 استراتژی - 10 جفت ارز</h2><span class="meta">همهٔ نمادها · کندل بسته‌شدهٔ یک‌ساعته · بدون ترکیب DNA · Confluence Filter 3 توافق · 10 جفت ارز</span></div><div class="scroll"><table><thead><tr><th>نماد</th><th>Diversified 15%</th><th>Apex 15%</th><th>Almasi 15%</th><th>IV3 Stable 15%</th><th>IV3 Primary 10%</th><th>Endurance 10%</th><th>MeanRev 20%</th><th>هدف / لات حداقل</th><th>وضعیت بازار / سن داده</th><th>مانع یا نتیجهٔ سفارش</th><th>بستن</th></tr></thead><tbody id="matrix"><tr><td colspan="12">در حال دریافت…</td></tr></tbody></table></div></section>
<div class="riskrow"><section class="panel"><h2 style="font-size:14px;margin-top:0">کنترل ریسک و سرمایه — ورژن</h2><div id="risk">—</div><div id="version-info" style="margin-top:12px;padding:10px;background:#0d1727;border-radius:8px"></div><p class="meta">آستانه افت سرمایه، تضمین حداکثر زیان نیست. v018: 10 جفت ارز، تست فعال BTC، بستن مجزا، ورژن در گزارش.</p></section><section class="panel"><h2 style="font-size:14px;margin-top:0">عملیات و بازیابی</h2><div id="operations">—</div><div class="buttons" style="margin-top:14px"><button class="button" onclick="setToken()">توکن کنترل</button><button class="button" onclick="control('tick')">بررسی اکنون</button><button class="button" onclick="control('start')">شروع</button><button class="button" onclick="control('stop')">توقف</button><button class="button" onclick="refresh()">تازه‌سازی</button><button class="button" onclick="closeAllPositions()">🛑 بستن همه</button></div><div id="message"></div><p class="meta">کنترل عمومی بسته است. برای دکمه‌های مدیریتی، DASHBOARD_TOKEN را در Render تنظیم کنید؛ کنترل مالک در پنل تلگرام مستقل است. v018: دکمه بستن مجزا برای هر معامله.</p></section></div>
<section class="panel receipt"><div class="panel-head"><h2>رسیدهای واقعی سفارش — ورژن v018</h2><span class="meta">فقط filled_amount مثبت؛ شامل شناسه سفارش و معامله و ورژن</span></div><div class="scroll"><table><thead><tr><th>زمان UTC</th><th>نماد / سمت</th><th>مقدار پُرشده</th><th>قیمت اجرا</th><th>Order ID</th><th>نتیجه</th><th>ورژن</th></tr></thead><tbody id="receipts"></tbody></table></div></section>
<section class="panel"><div class="panel-head"><h2>شواهد آخرین بررسی هماهنگ — ورژن</h2><span class="meta" id="build">—</span></div><pre id="evidence" style="padding:18px">—</pre></section>
<div class="foot">هر استراتژی بودجه و سیگنال مستقل دارد؛ جمع هدف‌ها فقط در لایه سفارش انجام می‌شود. اجرای واقعی تنها با بازار باز، داده تازه، حجم معتبر، کنترل ریسک و سپس تأیید پُرشدن سفارش ثبت می‌شود. v018: 10 جفت ارز، تست فعال BTC، بستن مجزا برای هر معامله، ورژن در گزارش. فلسفه: Signal-Quality-On 30-60% متغیر، روز بدون معامله سالم است.</div>
</main><script>
const esc=v=>String(v==null?'—':v).replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));
const fmt=(v,n=2)=>Number.isFinite(Number(v))&&v!=null?Number(v).toFixed(n):'—';
let adminToken='';
let testAsset='BTC',testBudget=10;
let testView={},testFetchedAt=0,telegramTestUrl='https://t.me/TestTraid_bot?start=test60';
let currentVersion='v018';
const headers=()=>adminToken?{'X-Token':adminToken}:{};
function setToken(){const t=prompt('DASHBOARD_TOKEN فقط برای همین صفحه (در فایل یا سرور ثبت نمی‌شود):');if(t!==null){adminToken=t;refresh()}}
async function closePosition(asset){if(!adminToken){alert('برای بستن، توکن کنترل را وارد کنید');return;}if(!confirm('پوزیشن '+asset+' بسته شود؟'))return;try{const r=await fetch('/api/close/'+asset,{method:'POST',headers:headers()});const x=await r.json();if(!r.ok)throw new Error(x.detail||'HTTP '+r.status);document.getElementById('message').textContent='بستن '+asset+' ارسال شد: '+JSON.stringify(x).slice(0,200);refresh();}catch(e){document.getElementById('message').textContent='خطا بستن '+asset+': '+e.message;}}
async function closeAllPositions(){if(!adminToken){alert('برای بستن همه، توکن کنترل را وارد کنید');return;}if(!confirm('همه پوزیشن‌ها بسته شوند؟'))return;try{const r=await fetch('/api/close-all',{method:'POST',headers:headers()});const x=await r.json();if(!r.ok)throw new Error(x.detail||'HTTP '+r.status);document.getElementById('message').textContent='بستن همه ارسال شد';refresh();}catch(e){document.getElementById('message').textContent='خطا بستن همه: '+e.message;}}
const titles={test_trade_active:['تست یک‌دقیقه‌ای فعال است؛ استراتژی‌ها موقتاً در انتظارند','تایمر خروج مستقل از چرخه است.'],venue_halted:['بازار تست‌نت متوقف است؛ معاملات واقعی مسدودند','صرافی state=halted اعلام می‌کند.'],no_open_markets:['هیچ بازار قابل معامله‌ای باز نیست','وضعیت صرافی و خطاهای API در جدول تفکیک شده‌اند.'],account_unavailable:['حساب قابل خواندن نیست','کلید، مجوز حساب و اتصال صرافی را بررسی کنید.'],positions_unavailable:['پوزیشن‌ها قابل تأیید نیستند','برای جلوگیری از سفارش تکراری، مسیر معامله بسته است.'],execution_uncertain:['نتیجه یک سفارش نامشخص است','ابتدا سفارش با برچسب یکتا و پوزیشن واقعی تطبیق داده می‌شود.'],drawdown_guard:['محافظ افت سرمایه فعال شده است','افزایش ریسک متوقف است؛ فقط کاهش / بستن reduce-only مجاز است.'],cycle_error:['خطا در چرخه اجرا','شواهد و خطای واقعی را در بخش عملیات ببینید.'],trading_disabled:['معامله غیرفعال است','TRADING_ENABLED=false؛ فقط سیگنال محاسبه می‌شود.']};
function side(pa){if(!pa)return '<span class="muted">—</span>';const label=pa.side>0?'LONG':pa.side<0?'SHORT':'FLAT';return '<span class="'+(pa.side>0?'long':pa.side<0?'short':'muted')+'">'+label+'</span><br><span class="number muted">$'+fmt(pa.notional_usd)+'</span><br><small>'+esc((pa.detail||{}).reason||'').slice(0,40)+'</small>'}
function kv(k,v,cls=''){return '<div class="kv"><span class="muted">'+esc(k)+'</span><span class="number '+cls+'\">'+esc(v)+'</span></div>'}
async function refresh(){try{const r=await fetch('/api/status',{headers:headers()});if(!r.ok)throw new Error(r.status===401?'برای مشاهده وضعیت، توکن کنترل را وارد کنید.':'HTTP '+r.status);const d=await r.json();const h=await fetch('/health').then(r=>r.json());const dg=d.diagnostics||{},cfg=d.config||{},risk=d.risk||{},md=d.market_data||{};const fin=d.financial||{};currentVersion=cfg.version||h.version||'v018';document.getElementById('engine').textContent=d.running?'● موتور روشن '+currentVersion:'موتور متوقف';document.getElementById('engine').className='chip '+(d.running?'live':'red');document.getElementById('updated').textContent='آخرین چرخه: '+(d.last_loop_at||'—');document.getElementById('version').textContent='ورژن: '+currentVersion+' | assets: '+(cfg.assets||[]).length+' | '+cfg.assets_count+' جفت ارز';const reason=dg.primary_blocker;const t=titles[reason]||[reason?'مسیر معامله محدود است: '+reason:dg.trading_state==='ready'?'✅ مسیر معامله آماده - Signal-Quality-On':'مسیر معامله بررسی شده است',reason?'جزئیات مانع هر نماد در ماتریس پایین مشخص است.':'ثبت معامله فقط با رسید filled_amount مثبت صرافی انجام می‌شود.'];document.getElementById('reason-title').textContent=t[0];document.getElementById('reason-text').textContent=t[1]+' | ورژن: '+currentVersion;document.getElementById('markets').textContent=(dg.market_open_count||0)+' / '+(cfg.assets||[]).length;document.getElementById('halted').textContent=(dg.market_halted_count||0)+' halted | ورژن '+currentVersion;document.getElementById('signals').textContent=dg.active_signal_assets||0;document.getElementById('fills').textContent=dg.confirmed_fill_count||0;document.getElementById('capital').textContent='$'+fmt(cfg.capital_usd,0)+' / $'+fmt(risk.notional_cap_usd,0);document.getElementById('capital-sub').textContent='برآورد ربات $'+fmt(risk.estimated_bot_equity_usd,2)+' · حساب $'+fmt((d.account.USDC||{}).equity,2)+' · ورژن '+currentVersion;document.getElementById('pnl6h').textContent=(fin.verified_closed_net_usdc==null?'نامشخص':'$'+fmt(fin.verified_closed_net_usdc,4));document.getElementById('pnl6h-sub').textContent='کارمزد $'+fmt(fin.period_fees_usdc,4)+' · غیرمحقق $'+fmt(fin.unrealized_gross_usdc,4);document.getElementById('loops').textContent=(d.loop_count||0)+' | '+currentVersion;document.getElementById('ops').textContent=(h.last_ops_review_at||'هنوز ثبت نشده')+' | '+currentVersion;const amap=Object.fromEntries((d.actions||[]).map(a=>[a.asset,a]));const smap=Object.fromEntries((d.sleeves||[]).map(s=>[s.id,s.per_asset||{}]));document.getElementById('matrix').innerHTML=(cfg.assets||[]).map(a=>{const action=amap[a]||{},q=md[a]||{};const closeBtn='<button class="button" onclick="closePosition(\''+a+'\')">🛑 بستن '+a+'</button>';return '<tr><td class="asset\">'+esc(a)+'</td>'+['diversified_5','zenith_apex','almasi_primary','inst_v3_stable','inst_v3_primary','zenith_endurance','mean_reversion'].map(s=>'<td>'+side((smap[s]||{})[a])+'</td>').join('')+'<td><span class="number\">'+fmt(action.target_amt==null?action.target_coin:action.target_amt,6)+'</span><br><span class="number muted\">min '+fmt(action.minimum_amount,6)+'</span></td><td class="'+(q.market_state==='open'?'live':'amber')+'\">'+esc(q.market_state)+'<br><span class="number muted\">'+fmt((q.book_age_seconds||0)/3600,1)+'h</span></td><td class="note\"><b class="'+(action.filled_amount>0?'live':'amber')+'\">'+esc(action.status)+'</b><br>'+esc(action.reason)+'</td><td>'+closeBtn+'</td></tr>'}).join('');// Positions table
const positions = d.positions||[];
document.getElementById('positions-table').innerHTML = positions.length ? positions.map(p=>{
  const asset = (p.instrument_name||'').replace('USDT','');
  return '<tr><td class="asset\">'+esc(p.instrument_name)+'</td><td>'+esc(p.direction)+' '+esc(p.size_currency||p.size)+'</td><td>'+esc(p.average_price)+' / '+esc(p.mark_price)+'</td><td class="number '+(parseFloat(p.total_profit_loss||0)>0?'long':'short')+'\">'+esc(p.total_profit_loss)+'</td><td><button class="button" onclick="closePosition(\''+asset+'\')">🛑 بستن '+esc(asset)+'</button></td></tr>';
}).join('') : '<tr><td colspan="5" class="muted">بدون پوزیشن - flat - ورژن '+currentVersion+'</td></tr>';
document.getElementById('risk').innerHTML=kv('سرمایه تخصیصی','$'+fmt(cfg.capital_usd,0))+kv('برآورد ربات','$'+fmt(risk.estimated_bot_equity_usd,2))+kv('حساب USDC eq','$'+fmt((d.account.USDC||{}).equity,2))+kv('اکسپوژر واقعی فعلی','$'+fmt(risk.current_gross_notional_usd))+kv('سقف اهرم',fmt(cfg.lev_cap)+'×')+kv('افت برآوردشده',(100*(risk.drawdown_pct||0)).toFixed(2)+'%')+kv('آستانه توقف',(100*(risk.drawdown_limit_pct||.15)).toFixed(0)+'%')+kv('جفت ارزها', (cfg.assets||[]).length+' ('+(cfg.assets||[]).join(',')+')')+kv('ورژن', currentVersion);
document.getElementById('version-info').innerHTML='<b>🔖 ورژن:</b> '+esc(currentVersion)+'<br><b>📜 تاریخچه:</b> '+esc((cfg.version_history||[]).join(' → ')||'اولین')+'<br><b>📊 فلسفه:</b> '+(dg.philosophy||'Signal-Quality-On')+'<br><b>🧩 لایه‌ها:</b> Confluence 3 توافق, Vol Targeting, Correlation, Smart Exit, Meta-Allocator, Cost Filter, Validation<br><b>🧪 تست:</b> '+(cfg.test_trade_enabled?'فعال '+cfg.test_trade_asset:'غیرفعال')+'<br><b>💼 بستن مجزا:</b> فعال - دکمه بستن برای هر معامله در تلگرام و اینجا';
document.getElementById('operations').innerHTML=kv('آمادگی واقعی معامله',h.trading_ready?'READY':'NOT READY',h.trading_ready?'live':'amber')+kv('محیط اجرا',dg.execution_environment||'—')+kv('ورژن',currentVersion)+kv('خطای چرخه',d.last_error||'none',d.last_error?'red':'')+kv('آخرین پُرشدن',d.last_fill_at||'—')+kv('تست فعال', (h.test_trade&&h.test_trade.active)?'YES '+h.test_trade.asset:'NO')+kv('جفت ارزها', (cfg.assets||[]).length);
const orders=(d.orders_log||[]).slice(-20).reverse();document.getElementById('receipts').innerHTML=orders.length?orders.map(o=>'<tr><td class="number\">'+esc(o.ts)+'</td><td>'+esc(o.asset)+' / '+esc(o.direction)+'</td><td class="number\">'+fmt(o.filled_amount,6)+'</td><td class="number\">'+fmt(o.price,4)+'</td><td>'+esc(o.order_id)+'</td><td class="live\">'+esc(o.status)+'</td><td>'+esc(currentVersion)+'</td></tr>').join(''):'<tr><td colspan="7" class="muted">بدون رسید - ورژن '+currentVersion+'</td></tr>';
document.getElementById('financial-rows').innerHTML='<tr><td colspan="8" class="muted">گزارش مالی دقیق - ورژن '+currentVersion+' - در حال دریافت از /api/financial...</td></tr>';
document.getElementById('financial-summary').textContent='پنجره: '+(fin.window?fin.window.start_utc+' تا '+fin.window.end_utc:'—')+' · موجودی: $'+fmt((fin.account||{}).equity,2)+' · سرمایه ربات: $'+fmt(fin.allocated_capital_usdc,0)+' · برآورد: $'+fmt(fin.estimated_bot_equity_usdc,2)+' · ورژن: '+currentVersion;
document.getElementById('build').textContent='build '+h.build+' | version '+currentVersion+' | assets '+ (cfg.assets||[]).length+' | '+ (cfg.assets||[]).join(',');
document.getElementById('evidence').textContent=JSON.stringify({version: currentVersion, build: h.build, financial_status: fin.status, diagnostics: dg, confluence_info: h.confluence_info, correlation_info: h.correlation_info, regime_info: h.regime_info, version_history: h.version_history},null,2);
renderTest(h.test_trade||{},cfg);
}catch(e){document.getElementById('reason-title').textContent='وضعیت قابل دریافت نیست - '+currentVersion;document.getElementById('reason-text').textContent=e.message;}}
function renderTest(state,cfg){testAsset=cfg.test_trade_asset||testAsset;testBudget=cfg.test_trade_max_notional_usd||testBudget;document.getElementById('test-spec').textContent='فقط تست‌نت · حداقل لات '+testAsset+' · سقف ارزش $'+testBudget+' · ورژن '+currentVersion+' · '+(cfg.test_trade_enabled?'فعال':'غیرفعال');testView=state;testFetchedAt=Date.now();const entry=state.entry||{},exits=state.exits||[];document.getElementById('test-info').textContent='وضعیت: '+(state.status||'idle')+' · ورود پرشده: '+fmt(state.entry_filled_amount,6)+' · خروج پرشده: '+fmt(state.exit_filled_amount,6)+' · ورژن: '+currentVersion+' · '+(state.reason||state.warning||'');renderTestClock();}
function renderTestClock(){const s=testView,el=document.getElementById('test-clock');if(!s.active){el.textContent=s.status==='closed'?'✅ تست بسته شد · '+(s.closed_at||'')+' | ورژن '+currentVersion:s.status==='closed_external'?'حساب بدون long تست است':s.status==='unfilled'?'ورود پر نشد':'تست فعال نیست - ورژن '+currentVersion+' - دکمه شروع را بزنید';return;}if(s.seconds_remaining==null){el.textContent='در انتظار تأیید سفارش ورود - ورژن '+currentVersion;return;}const left=Math.max(0,Number(s.seconds_remaining)-(Date.now()-testFetchedAt)/1000);el.textContent=left>0?'⏱ '+Math.ceil(left)+' ثانیه تا خروج خودکار | ورژن '+currentVersion:'خروج در حال اجرا - ورژن '+currentVersion;}
async function testTrade(){const el=document.getElementById('test-message');if(!adminToken){el.textContent='برای اجرای امن، در تلگرام با حساب مالک تأیید کنید. v018 تست فعال BTC.';window.open('https://t.me/TestTraid_bot?start=test60','_blank','noopener,noreferrer');return;}if(!confirm('یک خرید واقعی با پول آزمایشی روی '+testAsset+'، به حداقل لات و سقف $'+testBudget+' انجام شود؟ ورژن '+currentVersion))return;el.textContent='در حال بررسی و ارسال تست… ورژن '+currentVersion;try{const r=await fetch('/api/test-trade/start',{method:'POST',headers:headers()});const x=await r.json();if(!r.ok)throw new Error((x.detail&&x.detail.message)||x.detail||'HTTP '+r.status);el.textContent=x.test_trade.active?'تست فعال است؛ ورژن '+currentVersion:'تست: '+(x.test_trade.reason||x.test_trade.status)+' | ورژن '+currentVersion;refresh();}catch(e){el.textContent=e.message+' | ورژن '+currentVersion;refresh();}}
async function closeTest(){if(!adminToken){document.getElementById('test-message').textContent='در پنل تلگرام، دکمه «بستن تست» را بزنید. ورژن '+currentVersion;return;}try{const r=await fetch('/api/test-trade/close',{method:'POST',headers:headers()});const x=await r.json();if(!r.ok)throw new Error(x.detail||'HTTP '+r.status);document.getElementById('test-message').textContent='خروج فقط همین تست درخواست شد - ورژن '+currentVersion;refresh();}catch(e){document.getElementById('test-message').textContent=e.message;}}
setInterval(renderTestClock,1000);
async function control(name){const el=document.getElementById('message');el.textContent='در حال ارسال… ورژن '+currentVersion;try{const r=await fetch('/api/'+name,{method:'POST',headers:headers()});const x=await r.json();if(!r.ok)throw new Error(x.detail||'HTTP '+r.status);el.textContent='درخواست انجام شد - ورژن '+currentVersion;refresh()}catch(e){el.textContent=e.message+' | ورژن '+currentVersion}}
refresh();setInterval(refresh,15000);
</script></body></html>'''
