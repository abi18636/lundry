"""
app/engine.py - Core Async Trading Engine v019 STABLE PRODUCTION
Integrated Fixes 1,2,3,4,5 + FIXED56 telegram + state management
Architecture: Asynchronous High-Availability Core (AriaX / Bybit V5 Compatible)
Fixes:
- Fix1 Maker/Post-Only Best Bid/Ask
- Fix2 Candle Close Gating $15 threshold
- Fix3 Sizing Guard BTC/ETH if <1000
- Fix4 Smart TP/SL No Zero-Distance 1.5 ATR
- Fix5 Full Async Engine 800ms
"""
from __future__ import annotations
import asyncio
import logging
import time
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List
import pandas as pd

from app.config import Settings, get_settings
from app.ariax import AriaXAsyncClient, AriaXClient
from app.execution import maker_limit_price, plan_rebalance
from app.smart_tp_sl import calculate_initial_levels, evaluate_smart_exit

# === v023 RISK MANAGEMENT FIXES (Team 10x Analysis) ===
MAX_RISK_PER_TRADE_PCT = 0.01
MAX_NOTIONAL_PER_ASSET_PCT = 0.20
LEVERAGE_FORCED = 2
TREND_FILTER_ENABLED = True


log = logging.getLogger("engine_v019")

def utcnow():
    return datetime.now(timezone.utc).isoformat()

class BotState:
    def __init__(self, path: str = "state/bot_state.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.last_fill_at = None
        self.last_trade_at = {}
        self.position_sl_tp = {}
        self.events = []
        self.orders_log = []
        self.last_loop_at = None
        self.loop_count = 0
        self.load()

    def load(self):
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text())
                self.position_sl_tp = data.get("position_sl_tp", {})
                self.last_trade_at = data.get("last_trade_at", {})
        except Exception as e:
            log.warning(f"State load failed: {e}")

    def save(self):
        try:
            data = {
                "position_sl_tp": self.position_sl_tp,
                "last_trade_at": self.last_trade_at,
                "last_loop_at": utcnow(),
                "loop_count": self.loop_count
            }
            self.path.write_text(json.dumps(data, indent=2))
        except Exception as e:
            log.warning(f"State save failed: {e}")

    def push(self, level: str, msg: str):
        entry = {"ts": utcnow(), "level": level, "msg": msg}
        self.events.append(entry)
        self.events = self.events[-100:]
        log.info(f"[{level}] {msg}")


class SimpleAriaXTestTrader:
    def __init__(self, engine):
        self.engine = engine
        self._state = {"active": False, "status": "idle", "asset": "BTC", "seconds_remaining": None}
        self._timer = None
        self._lock = __import__("threading").Lock()
    def snapshot(self):
        import time
        s = self._state.copy()
        if s.get("active") and s.get("close_due"):
            remaining = max(0, s["close_due"] - time.time())
            s["seconds_remaining"] = remaining
        else:
            s["seconds_remaining"] = None
        s["hold_seconds"] = 60
        return s
    def start(self):
        with self._lock:
            if self._state.get("active"):
                return self.snapshot()
            try:
                import time, uuid, datetime
                asset = "BTC"
                instrument = "BTCUSDT"
                self._state = {
                    "active": True, "status": "holding_dry", "asset": asset, "instrument": instrument,
                    "id": uuid.uuid4().hex[:8], "opened_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "close_due": time.time() + 60, "amount": 0.001, "price": 80000.0, "reason": "dry_run simulated"
                }
                self._schedule_close()
                self.engine.state.push("info", f"🧪 Test trade started: {asset}")
                return self.snapshot()
            except Exception as e:
                self._state = {"active": False, "status": f"failed: {e}", "asset": "BTC"}
                return self.snapshot()
    def _schedule_close(self):
        import threading
        if self._timer:
            try: self._timer.cancel()
            except: pass
        def auto_close():
            try: self.request_close()
            except: pass
        self._timer = threading.Timer(60, auto_close)
        self._timer.daemon = True
        self._timer.start()
    def request_close(self):
        with self._lock:
            if not self._state.get("active"):
                return self.snapshot()
            try:
                import datetime
                asset = self._state.get("asset", "BTC")
                instrument = self._state.get("instrument", "BTCUSDT")
                self._state = {
                    "active": False, "status": "closed", "asset": asset, "instrument": instrument,
                    "closed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "reason": "closed"
                }
                if self._timer:
                    try: self._timer.cancel()
                    except: pass
                self.engine.state.push("info", f"🧪 Test trade closed: {asset}")
                return self.snapshot()
            except Exception as e:
                self._state["active"] = False
                self._state["status"] = f"close_failed: {e}"
                return self.snapshot()


def _check_trend_filter(df, asset: str) -> int:
    try:
        if df.empty or len(df) < 200:
            return 0
        close = df["close"].astype(float)
        ema50 = close.ewm(span=50).mean().iloc[-1]
        ema200 = close.ewm(span=200).mean().iloc[-1]
        ema20 = close.ewm(span=20).mean().iloc[-1]
        if ema50 > ema200 and ema20 > ema50:
            return 1
        elif ema50 < ema200 and ema20 < ema50:
            return -1
        else:
            return 0
    except:
        return 0

def _check_correlation_filter(desired_direction: int, existing_positions: dict, asset: str) -> bool:
    try:
        for existing_asset, pos in existing_positions.items():
            if existing_asset == asset:
                continue
            existing_qty = float(pos.get("size", 0))
            if abs(existing_qty) < 1e-9:
                continue
            existing_dir = 1 if existing_qty > 0 else -1
            if {existing_asset, asset} <= {"BTC", "ETH"}:
                if existing_dir != desired_direction:
                    return False
        return True
    except:
        return True

def _calculate_position_size_risk_managed(entry: float, sl: float, capital: float, max_risk_pct: float = 0.01, max_notional_pct: float = 0.20) -> float:
    try:
        risk_usd = capital * max_risk_pct
        sl_distance = abs(entry - sl)
        if sl_distance <= 0:
            sl_distance = entry * 0.02
        qty_by_risk = risk_usd / sl_distance
        max_notional = capital * max_notional_pct
        qty_by_notional = max_notional / entry
        qty = min(qty_by_risk, qty_by_notional)
        if qty * entry < 5.0:
            qty = 5.0 / entry
        return max(0.0, qty)
    except:
        return 0.0

class TradingEngine:
    """
    v023 RISK-MANAGED - 10x Team Analysis Fix
    """
    def __init__(self, settings: Settings = None):
        self.settings = settings or get_settings()
        self.state = BotState(self.settings.state_path)
        self.positions_state = dict(self.state.position_sl_tp or {})  # Phase6: Restore after restart
        self.last_candle_checked: Dict[str, str] = {}
        self._telegram_reporter = None
        self._reported_order_ids = set()
        self.pending_telegram_reports = []
        self._async_client: AriaXAsyncClient | None = None
        self._sync_client: AriaXClient | None = None
        self._last_async_market = {}
        self._last_async_positions = []
        self.candle_cache = {}
        try:
            self.test_trader = SimpleAriaXTestTrader(self)
        except:
            self.test_trader = None

    def set_telegram_reporter(self, reporter):
        self._telegram_reporter = reporter

    def snapshot(self):
        """Snapshot for telegram panel and dashboard - v021 modular compatible"""
        try:
            import copy
            from dataclasses import asdict
            s = self.settings
            # Try to get state dict
            try:
                result = copy.deepcopy(asdict(self.state))
            except Exception:
                result = {
                    "positions": getattr(self.state, 'positions', []),
                    "position_sl_tp": getattr(self.state, 'position_sl_tp', {}),
                    "orders_log": getattr(self.state, 'orders_log', []),
                    "events": getattr(self.state, 'events', []),
                    "risk": getattr(self.state, 'risk', {}),
                    "last_fill_at": getattr(self.state, 'last_fill_at', None),
                    "version": getattr(self.state, 'version', 'v021'),
                }
            result["bot"] = f"zenith-SUPER-{getattr(self.state, 'version', 'v021')}"
            try:
                result["config"] = {
                    "capital_usd": getattr(s, 'capital_usd', 400),
                    "lev_cap": getattr(s, 'lev_cap', 1),
                    "assets": getattr(s, 'effective_assets', getattr(s, 'asset_list', ['BTC','ETH'])),
                    "loop_seconds": getattr(s, 'loop_seconds', 90),
                    "max_drawdown_pct": getattr(s, 'max_drawdown_pct', 0.15),
                    "version": getattr(self.state, 'version', 'v021'),
                    "trade_on_candle_close_only": getattr(s, 'trade_on_candle_close_only', True),
                }
            except Exception:
                result["config"] = {"capital_usd": 400, "assets": ["BTC","ETH"]}
            # Add positions from last async
            try:
                if hasattr(self, '_last_async_positions') and self._last_async_positions:
                    result["positions"] = self._last_async_positions
            except:
                pass
            return result
        except Exception as e:
            import logging
            logging.getLogger("engine").warning(f"snapshot failed: {e}")
            return {"positions": [], "config": {"capital_usd": 400, "assets": ["BTC","ETH"]}, "bot": "zenith-v021"}

    def _get_sync_client(self):
        if self._sync_client is None:
            self._sync_client = AriaXClient(
                self.settings.ariax_base_url,
                self.settings.ariax_api_key,
                self.settings.ariax_api_secret,
                self.settings.ariax_fallback_url
            )
        return self._sync_client

    def _get_async_client(self):
        if self._async_client is None:
            self._async_client = AriaXAsyncClient(
                self.settings.ariax_base_url,
                self.settings.ariax_api_key,
                self.settings.ariax_api_secret,
                self.settings.ariax_fallback_url
            )
        return self._async_client

    def _log_fill(self, action: dict):
        oid = action.get("order_id")
        if oid and oid in self._reported_order_ids:
            return
        if oid:
            self._reported_order_ids.add(oid)
            if len(self._reported_order_ids) > 100:
                self._reported_order_ids = set(list(self._reported_order_ids)[-100:])
        self.state.last_fill_at = utcnow()
        self.state.orders_log.append(action)
        self.state.orders_log = self.state.orders_log[-100:]
        try:
            if "ts" not in action:
                action["ts"] = utcnow()
            if "filled_amount" not in action and "amount" in action:
                action["filled_amount"] = action["amount"]
            self.pending_telegram_reports.append(action)
            self.pending_telegram_reports = self.pending_telegram_reports[-20:]
            self.state.push("info", f"📝 Trade queued: {action.get('asset')} {action.get('direction')} {action.get('amount')} @ {action.get('price')}")
        except Exception as e:
            import logging
            logging.getLogger("engine").warning(f"Queue failed: {e}")
        try:
            asset = action.get("asset")
            if asset:
                self.state.last_trade_at[asset] = time.time()
        except Exception:
            pass

    def flush_telegram_reports(self):
        if not self.pending_telegram_reports:
            return 0
        sent = 0
        try:
            reporter = getattr(self, '_telegram_reporter', None)
            if not reporter:
                try:
                    from app.telegram_bot import get_reporter
                    reporter = get_reporter()
                except Exception:
                    reporter = None
            if not reporter or not getattr(reporter, 'enabled', False):
                self.pending_telegram_reports = []
                return 0
            reports = self.pending_telegram_reports[:]
            self.pending_telegram_reports = []
            for action in reports:
                try:
                    ok = reporter.send_trade_report(action)
                    if ok:
                        self.state.push("info", f"✅ Trade report sent: {action.get('asset')} {action.get('direction')} {action.get('amount')} @ {action.get('price')}")
                        sent += 1
                    else:
                        self.state.push("error", f"❌ Trade report failed: {action.get('asset')} err={getattr(reporter, 'last_error', 'unknown')}")
                except Exception as e:
                    self.state.push("error", f"flush failed {action.get('asset')}: {e}")
            return sent
        except Exception as e:
            import logging
            logging.getLogger("engine").warning(f"Flush failed: {e}")
            return sent

    def _calculate_atr_levels(self, df: pd.DataFrame, entry_price: float, side: int):
        """Backward compat wrapper for old calls"""
        try:
            from app.smart_tp_sl import _calculate_atr_levels_compat
            return _calculate_atr_levels_compat(df, entry_price, side)
        except:
            try:
                high = df["high"]
                low = df["low"]
                close = df["close"]
                tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
                atr = tr.rolling(14).mean().iloc[-1]
                if pd.isna(atr):
                    atr = entry_price * 0.02
                cfg = {"smart_sl_atr": 2.0, "smart_tp1_atr": 2.5, "smart_tp2_atr": 4.0}
                levels = calculate_initial_levels(entry_price, side, atr, cfg)
                return {"sl": levels["sl"], "tp1": levels["tp1"], "tp2": levels["tp2"], "atr": atr}
            except:
                atr = entry_price * 0.02
                return {"sl": entry_price * (0.98 if side>0 else 1.02), "tp1": entry_price * (1.05 if side>0 else 0.95), "tp2": entry_price * (1.08 if side>0 else 0.92), "atr": atr}

    # === v019 ASYNC CORE (Fix 5) ===
    async def run_cycle_async(self):
        """v019 Fix 5: Concurrent parallel execution for all instruments - 800ms target"""
        start_ts = time.time()
        try:
            settings = self.settings
            client = self._get_async_client()
            
            # Fix 3: Dynamic assets
            active_assets = settings.effective_assets if hasattr(settings, 'effective_assets') else [a.strip() for a in settings.assets.split(",") if a.strip()]
            
            # FIX: Cancel all open orders if >10 to prevent 69 orders accumulation
            try:
                import httpx
                base = settings.ariax_base_url
                headers = {"X-API-Key": settings.ariax_api_key, "X-API-Secret": settings.ariax_api_secret}
                try:
                    r = httpx.get(f"{base}/api/orders", headers=headers, timeout=5)
                    orders = r.json().get("data", []) if r.status_code == 200 else []
                    if len(orders) > 10:
                        import logging
                        logging.getLogger("engine").info(f"Found {len(orders)} open orders, canceling all")
                        for o in orders:
                            try:
                                httpx.post(f"{base}/api/cancel", headers=headers, json={"id": o.get("id")}, timeout=5)
                            except:
                                pass
                        self.state.push("info", f"🧹 Canceled {len(orders)} old orders")
                except Exception as e:
                    import logging
                    logging.getLogger("engine").warning(f"Cancel all failed: {e}")
            except Exception as e:
                import logging
                logging.getLogger("engine").warning(f"Order cleanup failed: {e}")
            
            # 1. Concurrently fetch account and positions
            account_task = client.account_summary("USDT")
            positions_task = client.positions("USDT")
            account, positions = await asyncio.gather(account_task, positions_task)

            # 2. Concurrently fetch orderbook and candles for all active assets
            tasks = []
            for asset in active_assets:
                symbol = settings.instrument_for(asset)
                order_sym = settings.order_symbol_for(asset) if hasattr(settings, 'order_symbol_for') else symbol
                tasks.append(client.order_book(symbol))
                tasks.append(client.candles(symbol, hours=100))

            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            market_data = {}
            for i, asset in enumerate(active_assets):
                try:
                    book_res = results[i*2]
                    candle_res = results[i*2+1]
                    
                    if isinstance(book_res, Exception):
                        book = {"best_bid": 0, "best_ask": 0, "state": "unknown", "bids": [], "asks": []}
                    else:
                        book = book_res
                    
                    if isinstance(candle_res, Exception) or not isinstance(candle_res, pd.DataFrame):
                        df = pd.DataFrame()
                    else:
                        df = candle_res
                    
                    market_data[asset] = {"book": book, "candles": df}
                except Exception as e:
                    log.warning(f"Market data parse failed for {asset}: {e}")
                    market_data[asset] = {"book": {"best_bid":0,"best_ask":0,"state":"unknown"}, "candles": pd.DataFrame()}

            pos_map = {}
            for p in positions:
                raw_sym = p.get("instrument_name") or p.get("symbol") or ""
                asset_key = settings.asset_from_symbol(raw_sym) if hasattr(settings, 'asset_from_symbol') else raw_sym.replace("USDT", "").replace("USD", "").replace("/", "")
                if asset_key:
                    pos_map[asset_key] = p
            self._last_async_market = market_data
            self._last_async_positions = positions

            # 3. Process each asset with v019 logic
            for asset in active_assets:
                symbol = settings.instrument_for(asset)
                order_sym = settings.order_symbol_for(asset) if hasattr(settings, 'order_symbol_for') else symbol
                book = market_data[asset]["book"]
                df = market_data[asset]["candles"]

                if df.empty or book.get("state") not in ("open", "unknown") and len(book.get("bids",[])) == 0:
                    # Allow unknown state for dryclean mock
                    if df.empty:
                        continue

                current_pos = pos_map.get(asset, {})
                current_qty = float(current_pos.get("size", 0.0))
                mark_price = book.get("best_bid", 0.0) or book.get("best_ask", 0.0) or 0.0
                if mark_price <= 0:
                    # Fallback to last close
                    try:
                        mark_price = float(df["close"].iloc[-1]) if not df.empty else 0.0
                    except:
                        mark_price = 0.0
                
                if mark_price <= 0:
                    continue

                # Fix 4: Check Active TP/SL & Trailing Management
                if abs(current_qty) > 1e-8 and asset in self.positions_state:
                    atr = self.positions_state[asset].get("atr", mark_price * 0.02)
                    exit_eval = evaluate_smart_exit(mark_price, self.positions_state[asset], atr)
                    
                    if exit_eval.get("trigger_exit"):
                        log.warning(f"Closing position on {asset} due to {exit_eval['trigger_exit']} @ {mark_price}")
                        close_side = "sell" if current_qty > 0 else "buy"
                        try:
                            await client.close_market(order_sym, close_side, abs(current_qty))
                            self._log_fill({
                                "asset": asset,
                                "instrument": symbol,
                                "direction": close_side,
                                "amount": abs(current_qty),
                                "price": price,
                                "notional_usd": abs(current_qty) * price,
                                "order_id": f"sl_{asset}_{int(time.time())}",
                                "status": "closed",
                                "intent": "close"
                            })
                        except Exception as e:
                            log.error(f"Failed to close {asset}: {e}")
                        self.positions_state.pop(asset, None)
                        self.state.position_sl_tp.pop(asset, None)
                        continue

                    if exit_eval.get("close_fraction", 0) > 0:
                        partial_qty = round(abs(current_qty) * exit_eval["close_fraction"], 4)
                        close_side = "sell" if current_qty > 0 else "buy"
                        try:
                            price = maker_limit_price(book, close_side)
                            await client.place_order(order_sym, close_side, partial_qty, price, post_only=settings.post_only, reduce_only=True)
                            self._log_fill({
                                "asset": asset,
                                "instrument": symbol,
                                "direction": close_side,
                                "amount": partial_qty,
                                "price": price,
                                "notional_usd": partial_qty * price,
                                "order_id": f"tp_{asset}_{int(time.time())}",
                                "status": "partial",
                                "intent": "close"
                            })
                        except Exception as e:
                            log.error(f"Failed partial close {asset}: {e}")

                    # Update SL in state
                    if exit_eval.get("new_sl") and exit_eval["new_sl"] != self.positions_state[asset].get("sl"):
                        self.positions_state[asset]["sl"] = exit_eval["new_sl"]
                        self.state.position_sl_tp[asset] = self.positions_state[asset]

                # Fix 2: Candle Close Gating - only trade on hourly close
                if settings.trade_on_candle_close_only and not df.empty:
                    try:
                        last_dt = str(df["dt"].iloc[-1])
                        if self.last_candle_checked.get(asset) == last_dt:
                            continue  # Already analyzed this candle
                        self.last_candle_checked[asset] = last_dt
                    except:
                        pass

                # Quantitative Signal: EMA 20/50
                if df.empty or len(df) < 50:
                    continue

                try:
                    close = df["close"]
                    ema20 = close.ewm(span=20).mean().iloc[-1]
                    ema50 = close.ewm(span=50).mean().iloc[-1]
                except:
                    continue
                
                # === v023 TREND FILTER (Grok) ===
                trend_dir = _check_trend_filter(df, asset)
                if TREND_FILTER_ENABLED and trend_dir != 0:
                    desired_direction = trend_dir
                else:
                    desired_direction = 1 if ema20 > ema50 else (-1 if ema20 < ema50 else 0)
                
                if desired_direction == 0:
                    continue

                # === v023 CORRELATION FILTER (Gemini) ===
                if not _check_correlation_filter(desired_direction, {k: v for k, v in pos_map.items() if isinstance(v, dict)}, asset):
                    log.info(f"Correlation filter blocked {asset} {desired_direction}")
                    self.state.push("info", f"🚫 Correlation blocked {asset} opposite to correlated asset")
                    continue

                # Sizing - Fix 3 + v023 RISK MANAGED
                # Old: 50% capital per asset -> 40% loss
                # New: Max 20% capital, 1% risk per trade, lev 2
                allocated_usd = (settings.capital_usd / len(active_assets)) * min(settings.lev_cap, LEVERAGE_FORCED)
                allocated_usd = min(allocated_usd, settings.capital_usd * MAX_NOTIONAL_PER_ASSET_PCT)
                
                # Risk-managed qty based on 1% risk
                temp_atr = mark_price * 0.02
                try:
                    if not df.empty and len(df) >= 14:
                        high = df["high"].astype(float)
                        low = df["low"].astype(float)
                        tr = (high - low).abs()
                        temp_atr = float(tr.rolling(14).mean().iloc[-1] or mark_price*0.02)
                except:
                    pass
                sl_price_temp = mark_price - (1.2 * temp_atr * desired_direction)
                desired_qty = _calculate_position_size_risk_managed(
                    entry=mark_price,
                    sl=sl_price_temp,
                    capital=settings.capital_usd,
                    max_risk_pct=MAX_RISK_PER_TRADE_PCT,
                    max_notional_pct=MAX_NOTIONAL_PER_ASSET_PCT
                ) * desired_direction
                
                # Cap by allocated
                max_qty_alloc = allocated_usd / mark_price if mark_price>0 else 0
                if abs(desired_qty) > max_qty_alloc and max_qty_alloc>0:
                    desired_qty = max_qty_alloc * (1 if desired_qty>0 else -1)

                # Execute Maker Rebalance - Fix 1 & 2
                plan = plan_rebalance(
                    current=current_qty,
                    desired=desired_qty,
                    price=mark_price,
                    contract_step=0.001,
                    min_trade_amount=0.001,
                    rebalance_threshold_usd=settings.rebalance_notional_usd
                )

                if plan.status == "planned" and plan.amount > 0:
                    try:
                        # v023 FIX price required error
                        try:
                            limit_px = maker_limit_price(book, plan.direction)
                            if limit_px <= 0:
                                limit_px = mark_price * (0.999 if plan.direction=="buy" else 1.001)
                        except:
                            limit_px = mark_price * (0.999 if plan.direction=="buy" else 1.001)
                        if limit_px <= 0:
                            limit_px = mark_price
                        result = None
                        order_id = f"{asset}_{int(time.time())}"
                        status = "dry_run"

                        if not settings.effective_dry_run and settings.trading_enabled:
                            result = await client.place_order(
                                symbol=order_sym,
                                side=plan.direction,
                                qty=plan.amount,
                                price=limit_px,
                                post_only=settings.post_only,
                                reduce_only=plan.reduce_only
                            )

                            order_id = (
                                result.get("order_id")
                                or result.get("id")
                                or result.get("data", {}).get("id")
                                or order_id
                            )

                            status = (
                                result.get("status")
                                or result.get("order_state")
                                or result.get("data", {}).get("status")
                                or "submitted"
                            )

                        filled_like = str(status).lower() in (
                            "filled",
                            "closed",
                            "partial",
                            "partially_filled"
                        )

                        if filled_like or settings.effective_dry_run:
                            self._log_fill({
                                "asset": asset,
                                "instrument": symbol,
                                "direction": plan.direction,
                                "amount": plan.amount,
                                "filled_amount": plan.amount if filled_like or settings.effective_dry_run else 0,
                                "price": limit_px,
                                "notional_usd": plan.amount * limit_px,
                                "order_id": order_id,
                                "status": status,
                                "intent": "open" if not plan.reduce_only else "close"
                            })

                        else:
                            self.state.push(
                                "info",
                                f"Order submitted: {asset} {plan.direction} {plan.amount} @ {limit_px} status={status} id={order_id}"
                            )

                        if (
                            filled_like
                            and not plan.reduce_only
                            and settings.smart_tp_sl_enabled
                        ):
                            atr = mark_price * 0.02
                            try:
                                high = df["high"]
                                low = df["low"]
                                close_s = df["close"]
                                tr = pd.concat(
                                    [high - low, (high - close_s.shift()).abs(), (low - close_s.shift()).abs()],
                                    axis=1
                                ).max(axis=1)
                                atr_calc = tr.rolling(14).mean().iloc[-1]
                                if not pd.isna(atr_calc) and atr_calc > 0:
                                    atr = float(atr_calc)
                            except:
                                pass

                            self.positions_state[asset] = calculate_initial_levels(
                                entry=limit_px,
                                side=1 if plan.direction == "buy" else -1,
                                atr=atr,
                                cfg=settings.model_dump() if hasattr(settings, 'model_dump') else settings.__dict__
                            )

                            self.state.position_sl_tp[asset] = self.positions_state[asset]
                            self.state.push(
                                "info",
                                f"SMART TP/SL registered for {asset}: SL={self.positions_state[asset]['sl']:.4f} TP1={self.positions_state[asset]['tp1']:.4f}"
                            )

                    except Exception as ex:
                        log.error(f"Failed to submit order for {asset}: {ex}")
                        self.state.push("error", f"Order failed {asset}: {ex}")

            elapsed = time.time() - start_ts
            log.info(f"v019 cycle completed in {elapsed:.3f}s for {len(active_assets)} assets (target <0.8s)")
            self.state.loop_count += 1
            self.state.last_loop_at = utcnow()
            self.state.save()
            return True

        except Exception as e:
            log.exception(f"Unhandled async cycle error: {e}")
            self.state.push("error", f"Cycle error: {e}")
            return False

    # Sync wrapper for backward compatibility with main.py
    def run_cycle(self):
        """Sync wrapper that runs async cycle"""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(self.run_cycle_async())
            loop.close()
            return result
        except Exception as e:
            log.exception(f"Sync wrapper error: {e}")
            # Fallback to old sync logic if async fails
            try:
                return self._run_cycle_sync_fallback()
            except Exception as e2:
                log.exception(f"Fallback also failed: {e2}")
                return False

    def _run_cycle_sync_fallback(self):
        """Minimal sync fallback to keep bot alive if async fails"""
        try:
            client = self._get_sync_client()
            settings = self.settings
            active_assets = settings.effective_assets if hasattr(settings, 'effective_assets') else [a.strip() for a in settings.assets.split(",")]
            
            account = client.account_summary("USDT")
            positions = client.positions("USDT")
            pos_map = {p.get("instrument_name"): p for p in positions}
            
            for asset in active_assets[:2]:  # Only 2 for fallback speed
                symbol = settings.instrument_for(asset)
                order_sym = settings.order_symbol_for(asset) if hasattr(settings, 'order_symbol_for') else symbol
                try:
                    book = client.order_book(symbol)
                    df = client.candles(symbol, hours=100)
                    if df.empty:
                        continue
                    
                    current_pos = pos_map.get(asset, {})
                    current_qty = float(current_pos.get("size", 0))
                    mark_price = book.get("best_bid", 0) or 0
                    
                    if mark_price <= 0:
                        continue
                    
                    # Simple EMA signal
                    close = df["close"]
                    if len(close) < 50:
                        continue
                    ema20 = close.ewm(span=20).mean().iloc[-1]
                    ema50 = close.ewm(span=50).mean().iloc[-1]
                    direction = 1 if ema20 > ema50 else -1
                    
                    allocated = (settings.capital_usd / len(active_assets)) * settings.lev_cap
                    desired = (allocated / mark_price) * direction
                    
                    plan = plan_rebalance(current_qty, desired, mark_price, 0.001, 0.001, settings.rebalance_notional_usd)
                    if plan.status == "planned" and plan.amount > 0:
                        limit_px = maker_limit_price(book, plan.direction)
                        if not settings.dry_run:
                            client.place_order(order_sym, plan.direction, plan.amount, limit_px, post_only=True, reduce_only=plan.reduce_only)
                        self._log_fill({
                            "asset": asset,
                            "instrument": symbol,
                            "direction": plan.direction,
                            "amount": plan.amount,
                            "price": limit_px,
                            "notional_usd": plan.amount * limit_px,
                            "order_id": f"{asset}_{int(time.time())}",
                            "status": "filled",
                            "intent": "open" if not plan.reduce_only else "close"
                        })
                except Exception as e:
                    log.warning(f"Fallback asset {asset} failed: {e}")
                    continue
            
            self.state.loop_count += 1
            self.state.save()
            return True
        except Exception as e:
            log.exception(f"Fallback cycle error: {e}")
            return False

    def close_position(self, asset: str):
        """Close position - REAL TRADING with exchange order"""
        try:
            self.state.push("info", f"🛑 Close position requested for {asset}")
            # Try to close on exchange if real trading
            if not self.settings.effective_dry_run and self.settings.trading_enabled:
                try:
                    import httpx
                    base = self.settings.ariax_base_url
                    headers = {"X-API-Key": self.settings.ariax_api_key, "X-API-Secret": self.settings.ariax_api_secret}
                    # Get positions to find size
                    r = httpx.get(f"{base}/api/positions", headers=headers, timeout=10)
                    data = r.json()
                    positions = data.get("data", []) if isinstance(data, dict) else []
                    target_pos = None
                    for pos in positions:
                        sym = pos.get("symbol", "")
                        if asset.upper() in sym.upper():
                            target_pos = pos
                            break
                    if target_pos:
                        size = float(target_pos.get("size", 0))
                        if abs(size) > 1e-8:
                            side = "sell" if size > 0 else "buy"
                            symbol = target_pos.get("symbol")
                            body = {"symbol": symbol, "side": side, "qty": abs(size), "type": "market"}
                            r2 = httpx.post(f"{base}/api/order", headers=headers, json=body, timeout=10)
                            self.state.push("info", f"✅ Close order {asset} {side} {abs(size)} resp={r2.text[:200]}")
                        else:
                            self.state.push("info", f"No position size for {asset}")
                    else:
                        self.state.push("info", f"No position found for {asset} on exchange")
                except Exception as e:
                    self.state.push("error", f"Close exchange failed {asset}: {e}")
            
            # Clean local state
            if asset in self.positions_state:
                self.positions_state.pop(asset)
            if asset in self.state.position_sl_tp:
                self.state.position_sl_tp.pop(asset)
            
            self.state.push("info", f"✅ Position {asset} closed (local state cleared)")
            return True
        except Exception as e:
            self.state.push("error", f"Close failed {asset}: {e}")
            return False

    def auto_register_smart(self, asset: str, entry: float, side: int, atr: float):
        """v018 compat - Auto-register SMART TP/SL"""
        cfg = self.settings.model_dump() if hasattr(self.settings, 'model_dump') else self.settings.__dict__
        self.positions_state[asset] = calculate_initial_levels(entry, side, atr, cfg)
        self.state.position_sl_tp[asset] = self.positions_state[asset]
        self.state.push("info", f"Auto-register SMART TP/SL for {asset}: Entry={entry} SL={self.positions_state[asset]['sl']}")
        return self.positions_state[asset]


# Global engine instance for main.py compatibility
_engine_instance = None

def get_engine(settings=None):
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = TradingEngine(settings)
    return _engine_instance
