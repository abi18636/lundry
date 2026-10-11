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
BUILD = os.getenv("RENDER_GIT_COMMIT") or os.getenv("GIT_SHA") or "v018-10pairs-testtrade-telegram-close"


def _check_token(authorization: Optional[str] = None, x_token: Optional[str] = None, *, control: bool = False):
    expected = get_settings().dashboard_token
    if not expected:
        if control:
            raise HTTPException(403, "Public control is disabled.")
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
        log.exception("telegram init failed")
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
        "active_signal_assets": diagnostic.get("active_signal_assets", 0),
        "test_trade_enabled": settings.effective_test_trade_enabled,
        "test_trade_asset": settings.test_trade_asset,
        "test_trade": state.test_trade,
        "confluence_info": state.confluence_info,
        "correlation_info": state.correlation_info,
        "regime_info": diagnostic.get("regime_info", {}),
        "philosophy": "Signal-Quality-On 30-60% variable + 10 pairs + test trade + telegram close buttons",
        "financial": fin_summary,
    }


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return HTMLResponse("""
    <html><head><title>Zenith v018 10 Pairs</title></head><body style="font-family: sans-serif; background: #070e19; color: #e4eaf4; padding: 20px;">
    <h1>Zenith SUPER v018 - 10 Pairs + Test Trade + Telegram Close</h1>
    <p>Build: v018 10pairs-testtrade-telegram-close</p>
    <p>Version: v018 | Assets: 10 (BTC,ETH,SOL,AVAX,LINK,XRP,DOGE,ADA,DOT,LTC)</p>
    <p>Features:</p>
    <ul>
        <li>✅ 10 trading pairs (was 5)</li>
        <li>✅ Test trade active on BTC (60s)</li>
        <li>✅ Telegram close button for each individual trade</li>
        <li>✅ Version in telegram report</li>
        <li>✅ 7 layers from v017: confluence, vol targeting, correlation, smart exit, meta-allocator, cost filter, validation</li>
    </ul>
    <p><a href="/health" style="color: #70d9eb;">/health</a> | <a href="/api/status" style="color: #70d9eb;">/api/status</a> | <a href="/api/positions">/api/positions</a></p>
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
            "risk": snap["risk"], "actions": snap["actions"], "version": snap.get("version")}

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
    for asset in eng.settings.asset_list:
        try:
            results[asset] = eng.close_position(asset)
        except Exception as e:
            results[asset] = {"ok": False, "error": str(e)}
    return {"ok": True, "results": results, "version": eng.state.version}

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

# Test trade endpoints
@app.get("/api/test-trade")
def test_trade_status(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token)
    eng = get_engine()
    s = eng.settings
    test_state = eng.state.test_trade if hasattr(eng.state, 'test_trade') else {"active": False}
    if eng.test_trader:
        try:
            test_state = eng.test_trader.snapshot()
        except:
            pass
    return {"test_trade": test_state, "asset": s.test_trade_asset, "enabled": s.effective_test_trade_enabled, "version": eng.state.version}

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
