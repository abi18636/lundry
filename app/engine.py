"""
v016 COMPANY FIXES - Engine
Fix #1 CRITICAL: Remove keep current that blocks close/reduce/reversal - implement close-first for reversal
Fix #2: No 50% hack, real close
Fix #3: Real SL/TP with ATR
Fix #7: Adaptive cooldown based on volatility
Fix #5: Regime detection already in sleeves, but log it
"""
from __future__ import annotations

import copy
import json
import logging
import math
import os
import threading
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from app.config_v016 import Settings, get_settings
from app.execution_v016 import fill_summary, floor_amount, ioc_price, plan_rebalance, signed_position
from app.sleeves_v016 import parse_weights, run_all_sleeves, _detect_regime
from app.ariax_v016 import AriaXClient, AriaXAPIError

log = logging.getLogger("engine")

def utcnow():
    return datetime.now(timezone.utc).isoformat()

class EngineBusyError(RuntimeError):
    pass

@dataclass
class BotState:
    running: bool = False
    last_loop_at: Optional[str] = None
    last_error: Optional[str] = None
    loop_count: int = 0
    mode: str = "init"
    cycle_in_progress: bool = False
    cycle_started_at: Optional[str] = None
    sleeves: List[dict] = field(default_factory=list)
    net_book: Dict[str, Any] = field(default_factory=dict)
    positions: List[dict] = field(default_factory=list)
    account: Dict[str, Any] = field(default_factory=dict)
    orders_log: List[dict] = field(default_factory=list)
    events: List[dict] = field(default_factory=list)
    actions: List[dict] = field(default_factory=list)
    diagnostics: Dict[str, Any] = field(default_factory=lambda: {"trading_ready": False, "trading_state": "initializing", "primary_blocker": "initializing"})
    market_data: Dict[str, Any] = field(default_factory=dict)
    risk: Dict[str, Any] = field(default_factory=dict)
    pending_order: Optional[dict] = None
    last_fill_at: Optional[str] = None
    ops_reviews: List[dict] = field(default_factory=list)
    last_trade_at: Dict[str, float] = field(default_factory=dict)
    position_sl_tp: Dict[str, dict] = field(default_factory=dict)  # v016 NEW Fix #3

    def push(self, kind: str, msg: str, **extra):
        self.events = (self.events + [{"ts": utcnow(), "kind": kind, "msg": msg, **extra}])[-300:]

class TradingEngine:
    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self.state = BotState()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._cycle_lock = threading.Lock()
        self._save_lock = threading.RLock()
        self._wake_cycle = threading.Event()
        self.client: Optional[AriaXClient] = None
        self._candle_cache: Dict[str, tuple] = {}
        self._backoffs: Dict[str, dict] = {}
        self._last_review = 0.0
        Path(self.settings.state_path).parent.mkdir(parents=True, exist_ok=True)
        self._restore()

    def _restore(self):
        try:
            saved = json.loads(Path(self.settings.state_path).read_text())
            self.state.risk = saved.get("risk") or {}
            self.state.orders_log = (saved.get("orders_log") or [])[-300:]
            self.state.last_fill_at = saved.get("last_fill_at")
            self.state.last_trade_at = saved.get("last_trade_at") or {}
            self.state.position_sl_tp = saved.get("position_sl_tp") or {}
        except:
            pass

    def _save(self):
        with self._save_lock:
            try:
                path = Path(self.settings.state_path)
                snapshot = copy.deepcopy(asdict(self.state))
                temp = path.with_suffix(".tmp")
                temp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, default=str))
                os.replace(temp, path)
            except Exception as exc:
                log.warning(f"state save failed: {exc}")

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.state.running, self.state.mode = True, "starting"
        self._thread = threading.Thread(target=self._loop, name="superbot-loop", daemon=True)
        self._thread.start()
        self.state.push("info", "v016 engine started - company fixes: flat-aware, regime, SL/TP, no qty 50% hack")

    def stop(self):
        self._stop.set()
        self._wake_cycle.set()
        self.state.running, self.state.mode = False, "stopped"

    def _ensure_client(self):
        if self.client is None:
            s = self.settings
            if not s.ariax_api_key or not s.ariax_api_secret:
                raise RuntimeError("ARIAX keys not configured")
            self.client = AriaXClient(s.ariax_base_url, s.ariax_api_key, s.ariax_api_secret, fallback_url=s.ariax_fallback_url)
        return self.client

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.once()
            except EngineBusyError:
                pass
            except Exception as exc:
                self.state.last_error = str(exc)
                self.state.mode = "error"
                self.state.diagnostics = {"trading_ready": False, "trading_state": "blocked", "primary_blocker": "cycle_error", "error": str(exc)}
                self.state.push("error", str(exc), tb=traceback.format_exc()[-1000:])
                log.exception("cycle failed")
            self._save()
            self._wake_cycle.wait(max(5, int(self.settings.loop_seconds)))
            self._wake_cycle.clear()
        self.state.running, self.state.mode = False, "stopped"

    def snapshot(self):
        s = self.settings
        result = copy.deepcopy(asdict(self.state))
        result["bot"] = "zenith-SUPER-v016-company-fixes"
        result["config"] = {
            "capital_usd": s.capital_usd,
            "lev_cap": s.effective_lev_cap,
            "assets": s.asset_list,
            "sleeve_weights": s.effective_sleeve_weights,
            "sleeves_enabled": s.enabled_sleeves,
            "loop_seconds": s.loop_seconds,
            "max_notional_usd": s.max_notional_usd,
            "max_drawdown_pct": s.max_drawdown_pct,
            "cooldown_seconds": s.cooldown_seconds,
            "backoff_seconds": s.backoff_seconds,
            "version": "v016-company-fixes",
            "fixes": ["Fix#1 no keep current", "Fix#2 no 50% hack", "Fix#3 SL/TP real", "Fix#4 flat-aware", "Fix#5 regime ADX", "Fix#6 adaptive Donchian", "Fix#7 adaptive cooldown", "Fix#8 mean reversion 20%"]
        }
        return result

    def _update_risk(self, account: dict) -> bool:
        s = self.settings
        try:
            equity = float(account.get("USDT", {}).get("equity") or 0)
            available = float(account.get("USDT", {}).get("available") or 0)
            if not math.isfinite(equity):
                return False
        except:
            return False
        
        r = self.state.risk
        if r.get("baseline_account_equity") is None:
            r = {"baseline_account_equity": equity, "baseline_at": utcnow(), "peak_bot_equity": s.capital_usd, "drawdown_latched": False}
        
        old_peak = float(r.get("peak_bot_equity") or s.capital_usd)
        if old_peak > s.capital_usd * 2.0:
            log.warning(f"Resetting risk: old peak {old_peak} vs capital {s.capital_usd}")
            r = {"baseline_account_equity": equity, "baseline_at": utcnow(), "peak_bot_equity": s.capital_usd, "drawdown_latched": False}
            self.state.push("warning", f"Risk reset: peak {old_peak}->{s.capital_usd}")
        
        estimated = s.capital_usd + equity - float(r["baseline_account_equity"])
        peak = max(float(r.get("peak_bot_equity") or s.capital_usd), estimated)
        drawdown = max(0.0, (peak - estimated) / peak) if peak > 0 else 0.0
        
        old_latched = bool(r.get("drawdown_latched"))
        if old_latched and drawdown < s.max_drawdown_pct * 0.5:
            latched = False
            log.info(f"Drawdown recovered {drawdown*100:.1f}% < {s.max_drawdown_pct*50:.1f}%, unlatching")
        else:
            latched = old_latched or drawdown >= s.max_drawdown_pct
        
        cap = min(s.capital_usd, max(0.0, estimated)) * min(s.effective_lev_cap, 5.0) * 1.5
        
        if s.max_notional_usd > 0:
            cap = min(cap, s.max_notional_usd * 1.5)
        
        self.state.risk = {
            **r,
            "estimated_bot_equity": estimated,
            "peak_bot_equity": peak,
            "drawdown_pct": drawdown,
            "drawdown_limit_pct": s.max_drawdown_pct,
            "drawdown_latched": latched,
            "notional_cap_usd": cap,
            "available_funds": available,
            "measurement": "v016-company-fixes",
        }
        return True

    def _fetch_asset(self, asset: str) -> dict:
        s, c = self.settings, self.client
        inst = s.instrument_for(asset)
        out = {"asset": asset, "instrument": inst, "book": {}, "meta": {}, "candles": None, "errors": []}
        try:
            out["meta"] = c.instrument(inst)
            if not out["meta"]:
                raise RuntimeError("instrument metadata missing")
        except Exception as exc:
            out["errors"].append({"stage": "instrument", "error": str(exc)})
            return out
        try:
            out["book"] = c.order_book(inst, 5)
        except Exception as exc:
            out["errors"].append({"stage": "book", "error": str(exc)})
        try:
            cached = self._candle_cache.get(asset)
            now = time.time()
            if cached and now - cached[0] < 60:
                out["candles"] = cached[1]
            else:
                frame = c.candles(inst, hours=s.candle_lookback_hours, resolution="60")
                self._candle_cache[asset] = (now, frame)
                out["candles"] = frame
        except Exception as exc:
            out["errors"].append({"stage": "candles", "error": str(exc)})
        return out

    def _calculate_atr_levels(self, df, entry_price: float, side: float) -> dict:
        """v016 NEW Fix #3: Calculate SL/TP based on ATR"""
        s = self.settings
        try:
            high = df["high"].astype(float)
            low = df["low"].astype(float)
            close = df["close"].astype(float)
            tr = pd.concat([
                (high - low), 
                (high - close.shift()).abs(), 
                (low - close.shift()).abs()
            ], axis=1).max(axis=1)
            atr = float(tr.rolling(14).mean().iloc[-1])
            
            if side > 0:  # LONG
                sl = entry_price - atr * s.atr_stop_mult
                tp = entry_price + atr * s.atr_tp_mult
            else:  # SHORT
                sl = entry_price + atr * s.atr_stop_mult
                tp = entry_price - atr * s.atr_tp_mult
            
            return {"sl": sl, "tp": tp, "atr": atr}
        except Exception:
            return {"sl": None, "tp": None, "atr": None}

    def _check_sl_tp_triggers(self, asset: str, current_price: float) -> Optional[str]:
        """v016 NEW Fix #3: Check if SL or TP triggered"""
        levels = self.state.position_sl_tp.get(asset)
        if not levels or not levels.get("sl"):
            return None
        
        side = levels.get("side", 0)
        sl, tp = levels.get("sl"), levels.get("tp")
        
        if side > 0:  # LONG
            if current_price <= sl:
                return "stop_loss"
            if tp and current_price >= tp:
                return "take_profit"
        elif side < 0:  # SHORT
            if current_price >= sl:
                return "stop_loss"
            if tp and current_price <= tp:
                return "take_profit"
        return None

    def _get_adaptive_cooldown(self, asset: str, base_cooldown: int) -> int:
        """v016 NEW Fix #7: Adaptive cooldown based on volatility"""
        try:
            df = self._candle_cache.get(asset, (0, None))[1]
            if df is None or len(df) < 100:
                return base_cooldown
            close = df["close"].astype(float)
            rets = close.pct_change().tail(24)
            volatility = float(rets.std())
            
            if volatility > 0.02:  # High vol >2% per hour
                return base_cooldown * 3
            return base_cooldown
        except:
            return base_cooldown

    def once(self) -> dict:
        if not self._cycle_lock.acquire(blocking=False):
            raise EngineBusyError("cycle in progress")
        self.state.cycle_in_progress = True
        self.state.cycle_started_at = utcnow()
        try:
            result = self._once_unlocked()
            self._save()
            return result
        finally:
            self.state.cycle_in_progress = False
            self._cycle_lock.release()

    def _once_unlocked(self):
        s = self.settings
        client = self._ensure_client()
        
        # Fetch account
        try:
            account = client.account_summary("USDT")
            self.state.account = account
            self._update_risk(account)
        except Exception as exc:
            self.state.push("warning", f"account fetch failed: {exc}")
            account = {"USDT": {"equity": 0, "available": 0}}
        
        # Fetch positions
        try:
            positions = client.positions("USDT")
            self.state.positions = positions
        except Exception as exc:
            positions = []
            self.state.push("warning", f"positions fetch failed: {exc}")
        
        # Inventory map
        inventory = {}
        for p in positions:
            try:
                inst = p.get("instrument_name")
                if inst:
                    inventory[inst] = signed_position(p)
            except:
                continue
        
        # Fetch market data for all assets
        market_data = {}
        frames = {}
        for asset in s.asset_list:
            md = self._fetch_asset(asset)
            market_data[asset] = {
                "instrument": md["instrument"],
                "market_state": md["book"].get("state", "unknown"),
                "book": md["book"],
                "meta": md["meta"],
                "candles": len(md["candles"]) if md["candles"] is not None and len(md["candles"])>0 else 0,
                "errors": md["errors"],
            }
            if md["candles"] is not None and len(md["candles"]) >= 50:
                try:
                    df = md["candles"]
                    if not isinstance(df, pd.DataFrame):
                        continue
                    close = df["close"].astype(float)
                    df["ema20"] = close.ewm(span=20).mean()
                    df["ema50"] = close.ewm(span=50).mean()
                    frames[asset] = df
                except Exception as exc:
                    log.warning(f"frame enrich failed for {asset}: {exc}")
        
        # Run sleeves - 7 strategies with mean_reversion
        try:
            sleeve_results, net_book = run_all_sleeves(frames, s.effective_capital, lev_cap=s.effective_lev_cap, enabled=s.enabled_sleeves)
        except Exception as exc:
            log.exception(f"sleeves failed: {exc}")
            sleeve_results, net_book = [], {}
        
        # Store sleeves
        self.state.sleeves = []
        for r in sleeve_results:
            self.state.sleeves.append({
                "id": r.sleeve_id,
                "title": r.title,
                "capital": r.capital,
                "weight": r.weight,
                "per_asset": r.per_asset,
                "notes": r.notes,
            })
        self.state.net_book = net_book
        
        # v016 NEW Fix #3: Check SL/TP triggers BEFORE processing signals
        sl_tp_overrides = {}
        for asset in s.asset_list:
            inst = s.instrument_for(asset)
            current_qty = inventory.get(inst, 0.0)
            if current_qty == 0:
                self.state.position_sl_tp.pop(asset, None)
                continue
            
            b = net_book.get(asset, {})
            px = float(b.get("price") or 0)
            if px <= 0:
                continue
            
            trigger = self._check_sl_tp_triggers(asset, px)
            if trigger:
                self.state.push("warning", f"{asset} {trigger} triggered at {px}")
                sl_tp_overrides[asset] = trigger
                net_book[asset] = {**b, "target_coin": 0.0, "_sl_tp_override": trigger}
        
        # Risk checks
        risk = self.state.risk
        latched = bool(risk.get("drawdown_latched"))
        drawdown = float(risk.get("drawdown_pct") or 0)
        
        # Build actions - v016 FIX #1 CRITICAL: Allow real close/reduce/reversal
        actions = []
        
        for asset in s.asset_list:
            inst = s.instrument_for(asset)
            md = market_data.get(asset, {})
            book = md.get("book", {})
            meta = md.get("meta", {})
            b = net_book.get(asset, {})
            
            current = inventory.get(inst, 0.0)
            px = float(b.get("price") or book.get("mark_price") or 0)
            desired = float(b.get("target_coin") or 0)
            
            # Check if SL/TP override
            is_sl_tp = asset in sl_tp_overrides
            
            # Scale by risk cap
            desired_gross = sum(abs(v.get("target_coin",0)*v.get("price",0)) for v in net_book.values())
            cap = float(risk.get("notional_cap_usd") or s.max_notional_usd)
            scale = min(1.0, cap / desired_gross) if desired_gross>0 else 1.0
            if not is_sl_tp:  # Don't scale SL/TP closes
                desired = desired * scale
            
            action = {
                "asset": asset,
                "instrument": inst,
                "desired_coin": desired,
                "current_size": current,
                "price": px,
                "market_state": book.get("state", "unknown"),
                "can_execute": False,
                "status": "pending_check",
                "reason": "",
                "_sl_tp_override": sl_tp_overrides.get(asset),
            }
            
            # v016 FIX #1 CRITICAL: Remove keep current bug, implement close-first for reversal
            # OLD BUGGY CODE REMOVED:
            # if current !=0 and (is_reduction or is_reversal or is_close):
            #     desired = current  # BUG - does nothing!
            
            # NEW FIX: For reversal, close first, then open opposite next cycle
            if current != 0 and not is_sl_tp:
                is_reversal = current * desired < 0 and abs(desired) > 1e-9
                if is_reversal:
                    # Close first to avoid qty exceeds on direct reversal
                    action["_force_close_first"] = True
                    action["desired_coin"] = 0.0
                    desired = 0.0
                    log.info(f"v016 reversal detected for {asset}: closing first {current}, will open opposite next cycle")
            
            # Validate
            try:
                step = float(meta.get("contract_size") or 0.001)
                minimum = float(meta.get("min_trade_amount") or step)
                plan = plan_rebalance(current, desired, px, step, minimum, s.rebalance_notional_usd)
                action.update({
                    "target_amt": plan.target,
                    "delta": plan.delta,
                    "amount": plan.amount,
                    "direction": plan.direction,
                    "reduce_only": plan.reduce_only,
                    "intent": plan.intent,
                    "notional_usd": abs(plan.target)*px,
                    "status": plan.status,
                    "reason": plan.reason,
                    "minimum_amount": minimum,
                    "contract_size": step,
                })
                
                if plan.status == "planned":
                    # v016 FIX #7: Adaptive cooldown
                    last_trade = self.state.last_trade_at.get(asset, 0)
                    effective_cooldown = self._get_adaptive_cooldown(asset, s.cooldown_seconds)
                    if not plan.reduce_only and not is_sl_tp and time.time() - last_trade < effective_cooldown:
                        remaining = int(effective_cooldown - (time.time() - last_trade))
                        action.update(status="cooldown", reason=f"Cooldown {remaining}s (adaptive base {s.cooldown_seconds}s)")
                    elif not s.trading_enabled:
                        action.update(status="trading_disabled", reason="TRADING_ENABLED=false")
                    elif not plan.reduce_only and latched and not is_sl_tp:
                        action.update(status="drawdown_guard", reason=f"Drawdown {drawdown*100:.1f}% >= {s.max_drawdown_pct*100:.0f}% latched")
                    elif not plan.reduce_only and book.get("state") != "open" and not is_sl_tp:
                        action.update(status=f"market_{book.get('state','unknown')}", reason="Book not open")
                    elif inst in self._backoffs and time.time() < self._backoffs[inst]["retry_after"]:
                        # v016 FIX: Skip backoff for SL/TP and small/qty errors
                        err = self._backoffs[inst]["error"]
                        if is_sl_tp or "small" in err.lower() or "qty exceeds" in err.lower() or "minimum" in err.lower():
                            # Skip backoff for these
                            pass
                        else:
                            action.update(status="order_error_backoff", reason=err)
                    
                    if action["status"] == "planned" or is_sl_tp:
                        try:
                            limit = ioc_price(book, plan.direction, meta, s.max_slippage_bps)
                            action["limit_price"] = limit
                            action["can_execute"] = True
                            action["status"] = "planned"
                            if is_sl_tp:
                                action["reason"] = f"SL/TP override: {sl_tp_overrides[asset]}"
                        except Exception as exc:
                            action.update(status="no_liquidity", reason=str(exc))
            except Exception as exc:
                action.update(status="error", reason=str(exc), error=str(exc))
            
            actions.append(action)
        
        # Diagnostics
        open_count = sum(1 for v in market_data.values() if v.get("market_state")=="open")
        active_assets = sum(1 for v in net_book.values() if abs(v.get("target_coin",0))>1e-9)
        ranging_count = sum(1 for a, df in frames.items() if _detect_regime(df) == "ranging")
        
        blocker = None
        trading_ready = True
        if latched:
            blocker = "drawdown_guard"
            trading_ready = False
        elif any(a["status"]=="order_error_backoff" for a in actions):
            blocker = "order_error_backoff"
            # Check if all backoffs are for small/qty
            real_blockers = [a for a in actions if a["status"]=="order_error_backoff" and "small" not in a["reason"].lower() and "qty exceeds" not in a["reason"].lower()]
            if not real_blockers:
                trading_ready = True
                blocker = None
            else:
                trading_ready = False
        
        if all(a["status"] in ("target_reached", "pending_check", "cooldown", "skip_small", "below_exchange_minimum", "no_signal") for a in actions):
            trading_ready = not latched
            blocker = None if trading_ready else "drawdown_guard"
        
        self.state.diagnostics = {
            "trading_ready": trading_ready,
            "trading_state": "ready" if trading_ready else "blocked",
            "primary_blocker": blocker,
            "market_open_count": open_count,
            "active_signal_assets": active_assets,
            "execution_environment": "testnet-live-v016-company-fixes",
            "ranging_count": ranging_count,
            "regime_info": {a: _detect_regime(df) for a, df in frames.items()},
            "sl_tp_triggers": sl_tp_overrides,
        }
        self.state.market_data = market_data
        self.state.actions = actions
        self.state.last_loop_at = utcnow()
        self.state.loop_count += 1
        
        return {"actions": actions, "diagnostics": self.state.diagnostics, "sleeves": self.state.sleeves, "net_book": net_book}

    def _log_fill(self, action: dict):
        self.state.last_fill_at = utcnow()
        try:
            asset = action.get("asset")
            if asset:
                self.state.last_trade_at[asset] = time.time()
                # v016 NEW Fix #3: Record SL/TP levels on open/increase
                if action.get("intent") in ("open", "increase") and asset not in self.state.position_sl_tp:
                    df = self._candle_cache.get(asset, (0, None))[1]
                    if df is not None and len(df) > 20:
                        entry_price = action.get("price") or action.get("limit_price") or 0
                        if entry_price > 0:
                            side = 1 if action.get("direction") == "buy" else -1
                            levels = self._calculate_atr_levels(df, entry_price, side)
                            self.state.position_sl_tp[asset] = {**levels, "side": side, "entry_price": entry_price}
                            log.info(f"v016 SL/TP set for {asset}: entry {entry_price}, SL {levels.get('sl')}, TP {levels.get('tp')}, side {side}")
        except Exception as exc:
            log.warning(f"_log_fill SL/TP failed: {exc}")
        
        entry = {"ts": self.state.last_fill_at, **{k: action.get(k) for k in ("asset", "instrument", "status", "amount", "price", "notional_usd", "order_id")}}
        self.state.orders_log = (self.state.orders_log + [entry])[-300:]

# Singleton
_engine: Optional[TradingEngine] = None

def get_engine():
    global _engine
    if _engine is None:
        _engine = TradingEngine()
    return _engine
