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
BUILD = os.getenv("RENDER_GIT_COMMIT") or os.getenv("GIT_SHA") or "v017-7layers-upgrade"


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
    log.info("SUPER boot: build=%s capital=%s sleeves=%s dry_run=%s", BUILD, settings.capital_usd, settings.enabled_sleeves, settings.dry_run)
    engine.start()
    yield
    engine.stop()


app = FastAPI(title="Zenith SUPER v017", version="v017-7layers", lifespan=lifespan)


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
        "ok": True, "liveness_ok": True, "bot": "zenith-SUPER-v017-7layers", "build": BUILD,
        "running": state.running, "mode": state.mode, "loop_count": state.loop_count,
        "last_loop_at": last, "loop_age_seconds": age, "last_error": state.last_error,
        "cycle_in_progress": state.cycle_in_progress, "cycle_started_at": state.cycle_started_at,
        "n_sleeves": len(state.sleeves), "n_assets": len(settings.asset_list),
        "trading_ready": bool(state.running and fresh and diagnostic.get("trading_ready")),
        "trading_state": diagnostic.get("trading_state", "initializing"),
        "primary_blocker": diagnostic.get("primary_blocker"),
        "market_open_count": diagnostic.get("market_open_count", 0),
        "active_signal_assets": diagnostic.get("active_signal_assets", 0),
        "confirmed_fill_count": 0,
        "last_fill_at": state.last_fill_at,
        "version": "v017-7layers-upgrade",
        "philosophy": "Signal-Quality-On 30-60% variable, no trade is healthy",
        "layers": diagnostic.get("layers") or {
            "1_confluence": f"min_agreement={settings.confluence_min_agreement}",
            "2_vol_targeting": f"target_vol={settings.target_vol_annual}",
            "3_correlation": "effective exposure",
            "4_smart_exit": "trailing + partial",
            "5_meta_allocator": "dynamic weights",
            "6_cost_filter": "edge > costs",
            "7_validation": "multi-regime",
        },
        "confluence_info": state.confluence_info,
        "correlation_info": state.correlation_info,
        "meta_stats": state.meta_stats,
        "regime_info": diagnostic.get("regime_info", {}),
        "financial": fin_summary,
    }


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return HTMLResponse("""
    <html><head><title>Zenith v017 7 Layers</title></head><body style="font-family: sans-serif; background: #070e19; color: #e4eaf4; padding: 20px;">
    <h1>Zenith SUPER v017 - 7 Layers Upgrade</h1>
    <p>Build: v017 7 layers - Signal-Quality-On 30-60% variable</p>
    <p>Philosophy: No trade is healthy, not bug</p>
    <ul>
        <li>Layer 1: Confluence Filter - min 3 agreement</li>
        <li>Layer 2: Volatility Targeting - risk constant</li>
        <li>Layer 3: Correlation-Aware - effective exposure</li>
        <li>Layer 4: Smart Exit - trailing + partial TP</li>
        <li>Layer 5: Meta-Allocator - dynamic weights</li>
        <li>Layer 6: Cost Filter - edge > costs</li>
        <li>Layer 7: Validation - multi-regime</li>
    </ul>
    <p><a href="/health" style="color: #70d9eb;">/health</a> | <a href="/api/status" style="color: #70d9eb;">/api/status</a> | <a href="/api/diagnostics" style="color: #70d9eb;">/api/diagnostics</a></p>
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
            "risk": snap["risk"], "actions": snap["actions"], "confluence_info": snap.get("confluence_info"),
            "correlation_info": snap.get("correlation_info"), "meta_stats": snap.get("meta_stats")}

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
    }

@app.get("/api/sleeves")
def sleeves(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token)
    snap = get_engine().snapshot()
    return {"sleeves": snap["sleeves"], "net_book": snap["net_book"], "config": snap["config"], "confluence_info": snap.get("confluence_info")}

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
