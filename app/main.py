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
BUILD = os.getenv("RENDER_GIT_COMMIT") or os.getenv("GIT_SHA") or "v016-company-fixes-7fd9e15"


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
        from app.telegram_bot import init_reporter_from_settings, get_reporter
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


app = FastAPI(title="Zenith SUPER", version="v016-company-fixes", lifespan=lifespan)


@app.get("/health")
def health():
    engine, settings = get_engine(), get_settings()
    state = engine.state
    diagnostic = state.diagnostics
    last = state.last_loop_at
    try:
        age = (datetime.now(timezone.utc)-datetime.fromisoformat(last)).total_seconds() if last else None
    except (TypeError, ValueError):
        age = None
    fresh = age is not None and age < max(600, 5 * settings.loop_seconds)
    
    # v016 FIX: No test_trader, no financial - optional
    try:
        test = engine.test_trader.snapshot() if hasattr(engine, 'test_trader') else {"active": False, "status": "disabled"}
    except:
        test = {"active": False, "status": "disabled"}
    
    try:
        from app.telegram_bot import get_reporter
        reporter = get_reporter()
    except:
        reporter = None
    
    try:
        fin = engine.financial.snapshot() if hasattr(engine, 'financial') else {}
        fin_summary = {k: fin.get(k) for k in ("allocated_capital_usdc", "estimated_bot_equity_usdc", "verified_closed_net_usdc", "period_fees_usdc", "unrealized_gross_usdc", "execution_count", "closed_event_count", "wins", "losses")} if fin else {"allocated_capital_usdc": settings.capital_usd, "estimated_bot_equity_usdc": state.risk.get("estimated_bot_equity", settings.capital_usd)}
    except Exception:
        fin_summary = {"allocated_capital_usdc": settings.capital_usd, "estimated_bot_equity_usdc": state.risk.get("estimated_bot_equity", settings.capital_usd), "unrealized_gross_usdc": 0}
    
    return {
        "ok": True, "liveness_ok": True, "bot": "zenith-SUPER-v016", "build": BUILD,
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
        "confirmed_fill_count": 0,
        "last_fill_at": state.last_fill_at,
        "last_ops_review_at": state.ops_reviews[-1]["at"] if state.ops_reviews else None,
        "test_trade_feature": "disabled-v016",
        "test_trade": {k: test.get(k) for k in ("active", "status", "asset", "close_due_at", "seconds_remaining", "overdue_seconds")} if isinstance(test, dict) else {"active": False, "status": "disabled"},
        "financial_feature": "v016-simple",
        "financial": fin_summary,
        "telegram_enabled": bool(reporter and reporter.enabled) if reporter else False,
        "telegram_commands_enabled": bool(reporter and getattr(reporter, 'poll_commands', False)) if reporter else False,
        "telegram_messages_sent": reporter.stats.get("sent", 0) if reporter and hasattr(reporter, 'stats') else 0,
        "version": "v016-company-fixes",
        "fixes": ["Fix#1 no keep-current", "Fix#2 no 50% hack", "Fix#3 SL/TP real", "Fix#4 flat-aware", "Fix#5 regime ADX", "Fix#6 adaptive Donchian", "Fix#7 adaptive cooldown", "Fix#8 mean-reversion 20%"]
    }


@app.get("/", response_class=HTMLResponse)
def dashboard():
    # Simplified dashboard - load from original file
    try:
        html_path = os.path.join(os.path.dirname(__file__), "..", "dashboard.html")
        if os.path.exists(html_path):
            with open(html_path, "r", encoding="utf-8") as f:
                return HTMLResponse(f.read())
    except:
        pass
    # Fallback - return main.py original dashboard html if exists in app/main.py.bak
    try:
        # Read original main.py and extract HTML (it's embedded)
        with open(os.path.join(os.path.dirname(__file__), "main.py.bak"), "r", encoding="utf-8") as f:
            content = f.read()
            # Find HTML part
            if "<!doctype html>" in content.lower():
                start = content.lower().find("<!doctype html>")
                return HTMLResponse(content[start:])
    except:
        pass
    return HTMLResponse("""
    <html><head><title>Zenith v016</title></head><body>
    <h1>Zenith SUPER v016 Company Fixes</h1>
    <p>Build: v016 7fd9e15 - 7 strategies with mean-reversion</p>
    <p><a href="/health">/health</a> | <a href="/api/status">/api/status</a> | <a href="/api/diagnostics">/api/diagnostics</a></p>
    <p>Fixes: No keep-current bug, No 50% hack, SL/TP real, Flat-aware, Regime ADX, Adaptive Donchian, Adaptive cooldown, Mean-reversion 20%</p>
    </body></html>
    """)


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


@app.get("/api/financial")
def financial(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token)
    eng = get_engine()
    # v016 simple financial from risk
    risk = eng.state.risk
    return {
        "allocated_capital_usdc": eng.settings.capital_usd,
        "estimated_bot_equity_usdc": risk.get("estimated_bot_equity", eng.settings.capital_usd),
        "account": eng.state.account,
        "positions": eng.state.positions,
        "risk": risk,
        "unrealized_gross_usdc": sum(float(p.get("floating_profit_loss") or 0) for p in eng.state.positions),
    }

@app.get("/api/sleeves")
def sleeves(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token)
    snap = get_engine().snapshot()
    return {"sleeves": snap["sleeves"], "net_book": snap["net_book"], "config": snap["config"]}


@app.post("/api/start")
def start(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token, control=True)
    get_engine().start()
    return {"ok": True}

@app.post("/api/stop")
def stop(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token, control=True)
    get_engine().stop()
    return {"ok": True}

@app.post("/api/restart")
def restart(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token, control=True)
    eng = get_engine()
    eng.stop()
    import time
    time.sleep(1)
    eng.start()
    return {"ok": True}
