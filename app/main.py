"""
app/main.py - Zenith Bot v019 STABLE PRODUCTION
Async FastAPI + Trading Engine
Fixes: Maker, Candle Gating, Sizing Guard, Smart TP/SL, Async Engine
"""
from __future__ import annotations
import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.engine import get_engine

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
log = logging.getLogger("main_v019")

BUILD = __import__("os").getenv("RENDER_GIT_COMMIT", "v019-TELEGRAM-RESTORED-a83e042")
VERSION = "v019-STABLE-ASYNC-MAKER-FIXED56"

settings = get_settings()
# v019 force telegram enabled if tokens exist
try:
    import os
    if os.getenv('TELEGRAM_BOT_TOKEN') and os.getenv('TELEGRAM_CHAT_ID'):
        settings.telegram_enabled = True
    # Also force if env var exists
    if settings.telegram_bot_token and settings.telegram_chat_id:
        settings.telegram_enabled = True
except:
    pass
engine = get_engine(settings)

# Telegram reporter
try:
    from app.telegram_bot import get_reporter, init_reporter
    try:
        telegram_reporter = init_reporter(engine)
    except Exception as e2:
        log.warning(f"Telegram init failed inner: {e2}")
        telegram_reporter = None
    # Force enabled if token exists
    try:
        if telegram_reporter and telegram_reporter.bot_token and telegram_reporter.chat_id:
            telegram_reporter.enabled = True
    except:
        pass
    engine.set_telegram_reporter(telegram_reporter)
except Exception as e:
    log.warning(f"Telegram init failed: {e}")
    telegram_reporter = None

background_task = None
stop_event = asyncio.Event()

async def trading_loop():
    log.info(f"Zenith v019 trading loop started. Assets: {settings.effective_assets} Capital: ${settings.capital_usd} PostOnly: {settings.post_only}")
    while not stop_event.is_set():
        try:
            start = time.time()
            # Run async cycle
            await engine.run_cycle_async()
            elapsed = time.time() - start
            log.info(f"Cycle done in {elapsed:.3f}s - next in {settings.loop_seconds}s")
            
            # Poll telegram commands
            if telegram_reporter and getattr(telegram_reporter, 'enabled', False):
                try:
                    telegram_reporter.poll_commands()
                except Exception as e:
                    log.warning(f"Telegram poll failed: {e}")
            
            await asyncio.sleep(settings.loop_seconds)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.exception(f"Trading loop error: {e}")
            await asyncio.sleep(10)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global background_task
    log.info(f"Starting {VERSION} - Maker Post-Only, Candle Gating $15, Sizing Guard, Smart TP/SL No Zero-Distance, Async 800ms")
    background_task = asyncio.create_task(trading_loop())
    yield
    log.info("Shutting down...")
    stop_event.set()
    if background_task:
        background_task.cancel()
        try:
            await background_task
        except:
            pass
    if engine._async_client:
        try:
            await engine._async_client.close()
        except:
            pass

app = FastAPI(title="Zenith Trader Bot v019", version=VERSION, lifespan=lifespan)

def _check_token(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    token = settings.dashboard_token
    if not token:
        return
    provided = None
    if authorization and authorization.startswith("Bearer "):
        provided = authorization[7:]
    elif x_token:
        provided = x_token
    if provided != token:
        raise HTTPException(status_code=401, detail="Unauthorized")

@app.get("/")
def root():
    return {"ok": True, "version": VERSION, "build": BUILD, "assets": settings.effective_assets, "capital": settings.capital_usd, "post_only": settings.post_only, "threshold": settings.rebalance_notional_usd}

@app.get("/health")
def health():
    return {
        "ok": True,
        "liveness_ok": True,
        "bot": f"zenith-SUPER-{VERSION}",
        "build": BUILD,
        "version": VERSION,
        "running": True,
        "mode": "running" if not stop_event.is_set() else "stopping",
        "loop_count": engine.state.loop_count,
        "last_loop_at": engine.state.last_loop_at,
        "n_assets": len(settings.effective_assets),
        "assets": settings.effective_assets,
        "assets_count": len(settings.effective_assets),
        "trading_ready": True,
        "capital_usd": settings.capital_usd,
        "effective_assets": settings.effective_assets,
        "post_only": settings.post_only,
        "rebalance_threshold": settings.rebalance_notional_usd,
        "smart_tp_sl": settings.smart_tp_sl_enabled,
        "telegram_enabled": getattr(telegram_reporter, 'enabled', False) if telegram_reporter else False,
        "philosophy": "SMART TP/SL LADDER v019 Maker Post-Only + Candle Close Gating $15 + Sizing Guard + Smart TP/SL No Zero-Distance 1.5 ATR + Async 800ms"
    }

@app.get("/api/status")
def api_status(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token)
    return {
        "ok": True,
        "version": VERSION,
        "build": BUILD,
        "loop_count": engine.state.loop_count,
        "positions": engine._last_async_positions if hasattr(engine, '_last_async_positions') else [],
        "positions_state": engine.positions_state,
        "position_sl_tp": engine.state.position_sl_tp,
        "events": engine.state.events[-20:],
        "orders_log": engine.state.orders_log[-20:],
        "last_candle_checked": engine.last_candle_checked,
        "effective_assets": settings.effective_assets,
        "capital_usd": settings.capital_usd,
        "settings": {
            "post_only": settings.post_only,
            "loop_seconds": settings.loop_seconds,
            "trade_on_candle_close_only": settings.trade_on_candle_close_only,
            "rebalance_notional_usd": settings.rebalance_notional_usd,
            "smart_sl_atr": settings.smart_sl_atr,
            "smart_tp1_atr": settings.smart_tp1_atr,
            "smart_be_trigger_atr": settings.smart_be_trigger_atr,
        }
    }

@app.get("/api/debug/env")
def debug_env(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token)
    import os
    return {
        "ok": True,
        "env": {
            "TELEGRAM_BOT_TOKEN_exists": bool(os.getenv("TELEGRAM_BOT_TOKEN")),
            "TELEGRAM_BOT_TOKEN_len": len(os.getenv("TELEGRAM_BOT_TOKEN","")),
            "TELEGRAM_CHAT_ID_exists": bool(os.getenv("TELEGRAM_CHAT_ID")),
            "TELEGRAM_CHAT_ID_preview": (os.getenv("TELEGRAM_CHAT_ID","")[:3] + "...") if os.getenv("TELEGRAM_CHAT_ID") else None,
            "TELEGRAM_ENABLED": os.getenv("TELEGRAM_ENABLED"),
            "DASHBOARD_TOKEN_exists": bool(os.getenv("DASHBOARD_TOKEN")),
            "ARIAX_API_KEY_exists": bool(os.getenv("ARIAX_API_KEY")),
            "capital_usd": settings.capital_usd,
            "telegram_bot_token_settings": bool(settings.telegram_bot_token),
            "telegram_chat_id_settings": bool(settings.telegram_chat_id),
            "telegram_enabled_settings": settings.telegram_enabled,
        },
        "build": BUILD,
        "version": VERSION
    }

@app.get("/api/debug/telegram")
def debug_telegram(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token)
    try:
        from app.telegram_bot import get_reporter
        reporter = get_reporter()
        if not reporter:
            return {"ok": False, "error": "no reporter"}
        return {
            "ok": True,
            "enabled": reporter.enabled,
            "has_token": bool(reporter.bot_token),
            "has_chat_id": bool(reporter.chat_id),
            "chat_id_preview": str(reporter.chat_id)[:6] + "..." if reporter.chat_id else None,
            "poll_commands": True,
            "stats": reporter.stats,
            "build": BUILD,
            "version": VERSION
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/api/debug/telegram/send_test")
def debug_telegram_send_test(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token)
    try:
        from app.telegram_bot import get_reporter
        reporter = get_reporter()
        if not reporter:
            return {"ok": False, "error": "no reporter"}
        import httpx
        url = f"https://api.telegram.org/bot{reporter.bot_token}/sendMessage"
        payload = {"chat_id": reporter.chat_id, "text": "TEST v019 STABLE plain text"}
        resp = httpx.post(url, json=payload, timeout=10)
        direct = {"status_code": resp.status_code, "text": resp.text[:1000], "ok": resp.status_code==200}
        ok = reporter.send("TEST via reporter.send plain v019", parse_mode=None)
        return {"ok": True, "direct_test": direct, "reporter_send_ok": ok, "stats": reporter.stats, "build": BUILD}
    except Exception as e:
        import traceback
        return {"ok": False, "error": str(e), "tb": traceback.format_exc()[:2000]}

@app.get("/api/debug/telegram/send_trade_test")
def debug_telegram_send_trade_test(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token)
    try:
        from app.telegram_bot import get_reporter
        from app.trade_reporter import format_single_trade
        reporter = get_reporter()
        if not reporter:
            return {"ok": False, "error": "no reporter"}
        sample = {
            "asset": "BTC",
            "instrument": "BTCUSDT",
            "direction": "buy",
            "filled_amount": 0.001,
            "amount": 0.001,
            "price": 80000.0,
            "notional_usd": 80.0,
            "order_id": "test_v019",
            "status": "filled",
            "intent": "open",
            "ts": "2026-10-15T00:00:00"
        }
        text = format_single_trade(sample, version="v019-STABLE-TEST")
        import httpx
        url = f"https://api.telegram.org/bot{reporter.bot_token}/sendMessage"
        payload = {"chat_id": reporter.chat_id, "text": text}
        resp = httpx.post(url, json=payload, timeout=10)
        direct = {"status_code": resp.status_code, "text": resp.text[:1000], "ok": resp.status_code==200, "sent_text": text[:500]}
        ok = reporter.send(text, parse_mode=None)
        return {"ok": True, "direct_test": direct, "reporter_send_ok": ok, "stats": reporter.stats, "build": BUILD}
    except Exception as e:
        import traceback
        return {"ok": False, "error": str(e), "tb": traceback.format_exc()[:2000]}

@app.get("/api/financial")
def api_financial(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token)
    return {
        "ok": True,
        "version": VERSION,
        "capital_usd": settings.capital_usd,
        "effective_assets": settings.effective_assets,
        "positions": engine._last_async_positions if hasattr(engine, '_last_async_positions') else [],
        "equity": 200.0,
        "available": 200.0
    }

@app.get("/api/close/{asset}")
def api_close_alias(asset: str, authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token)
    engine.state.push("info", f"Manual close requested for {asset} via /api/close/")
    return {"ok": True, "asset": asset, "msg": f"Close requested for {asset}"}

@app.post("/api/control/close/{asset}")
def close_asset(asset: str, authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token)
    # Trigger close via engine state
    engine.state.push("info", f"Manual close requested for {asset}")
    return {"ok": True, "asset": asset, "msg": f"Close requested for {asset}"}
