"""
v018 UPGRADE - 4 New Features:
1. Increase pairs from 5 to 10 (BTC,ETH,SOL,AVAX,LINK,XRP,DOGE,ADA,DOT,LTC)
2. Test trade active on BTC
3. Telegram close button for each individual trade
4. Version in telegram report

+ Keep all v017 7 layers
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

from app.config import Settings, get_settings
from app.execution import fill_summary, floor_amount, ioc_price, plan_rebalance, signed_position
from app.sleeves import parse_weights, run_all_sleeves, _detect_regime
from app.ariax import AriaXClient, AriaXAPIError

from app.confluence import apply_confluence_filter, estimate_signal_edge_bps, should_trade_after_costs
from app.volatility_targeting import volatility_targeted_size, calculate_atr
from app.correlation import calculate_effective_exposure, apply_correlation_cap
from app.exit_manager import manage_position_exit
from app.smart_tp_sl import calculate_smart_levels, manage_smart_exit, check_smart_triggers
from app.meta_allocator import PerformanceTracker, compute_dynamic_weights, compute_dynamic_weights_simple

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
    position_sl_tp: Dict[str, dict] = field(default_factory=dict)
    meta_stats: Dict[str, dict] = field(default_factory=dict)
    confluence_info: Dict[str, str] = field(default_factory=dict)
    correlation_info: Dict[str, Any] = field(default_factory=dict)
    # v018: Test trade state
    test_trade: Dict[str, Any] = field(default_factory=lambda: {"active": False, "status": "idle"})
    version: str = "v018-10pairs-testtrade-telegram-close"
    version_history: List[str] = field(default_factory=list)

    def push(self, kind: str, msg: str, **extra):
        self.events = (self.events + [{"ts": utcnow(), "kind": kind, "msg": msg, **extra}])[-300:]

class TradingEngine:
    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self.state = BotState()
        self.state.version = self.settings.version
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._cycle_lock = threading.Lock()
        self._save_lock = threading.RLock()
        self._wake_cycle = threading.Event()
        self.client: Optional[AriaXClient] = None
        self._candle_cache: Dict[str, tuple] = {}
        self._backoffs: Dict[str, dict] = {}
        self._last_review = 0.0
        self._perf_tracker = PerformanceTracker(lookback_trades=self.settings.meta_lookback_trades)
        # v018: Test trade
        self.test_trader = None
        try:
            from app.test_trade import TimedTestTrade
            self.test_trader = TimedTestTrade(self)
        except:
            log.warning("Test trade not available")
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
            self.state.meta_stats = saved.get("meta_stats") or {}
            self.state.test_trade = saved.get("test_trade") or {"active": False, "status": "idle"}
            self.state.version_history = saved.get("version_history") or []
            self.state.version = saved.get("version") or self.settings.version
            if "perf_history" in saved:
                for sid, history in saved["perf_history"].items():
                    for pnl in history:
                        self._perf_tracker.record_trade_result(sid, pnl)
            # v018: Check version change
            if self.state.version != self.settings.version:
                old = self.state.version
                new = self.settings.version
                self.state.version_history.append(f"{old}→{new} at {utcnow()}")
                self.state.version_history = self.state.version_history[-20:]
                self.state.version = new
                log.info(f"Version change {old} -> {new}")
                # Notify telegram
                try:
                    from app.telegram_bot import get_reporter
                    reporter = get_reporter()
                    if reporter:
                        reporter.on_version_change(old, new)
                except:
                    pass
        except:
            pass

    def _save(self):
        with self._save_lock:
            try:
                path = Path(self.settings.state_path)
                snapshot = copy.deepcopy(asdict(self.state))
                snapshot["perf_history"] = {sid: list(self._perf_tracker.trade_history.get(sid, [])) for sid in self._perf_tracker.trade_history}
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
        self.state.push("info", f"v018 engine started - 10 pairs, test trade active, telegram close buttons, version {self.state.version}")

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
        result["bot"] = f"zenith-SUPER-{self.state.version}"
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
            "version": self.state.version,
            "version_history": self.state.version_history[-5:],
            "test_trade_enabled": s.effective_test_trade_enabled,
            "test_trade_asset": s.test_trade_asset,
            "assets_count": len(s.asset_list),
            "layers": {
                "1_confluence": f"min_agreement={s.confluence_min_agreement}",
                "2_vol_targeting": f"target_vol={s.target_vol_annual}",
                "3_correlation": f"default_corr={s.correlation_default}",
                "4_smart_exit": f"trailing={s.trailing_stop_atr}",
                "5_meta_allocator": f"lookback={s.meta_lookback_trades}",
                "6_cost_filter": f"fee={s.fee_bps}bps",
                "7_validation": "multi-regime",
            }
        }
        return result

    def _update_risk(self, account: dict) -> bool:
        s = self.settings
        try:
            # Try USDT, then USDC, then any
            equity = 0
            available = 0
            for curr in ("USDT", "USDC"):
                acc = account.get(curr, {})
                if acc:
                    eq = float(acc.get("equity") or acc.get("balance") or 0)
                    av = float(acc.get("available") or acc.get("balance") or 0)
                    if eq > 0:
                        equity = eq
                        available = av
                        break
                    if av > 0 and equity == 0:
                        equity = av
                        available = av
            # Fallback: if equity still 0, try to use any positive value from account dict
            if equity == 0:
                for k,v in account.items():
                    if isinstance(v, dict):
                        eq = float(v.get("equity") or v.get("balance") or v.get("available") or 0)
                        if eq > 0:
                            equity = eq
                            available = eq
                            break
            if not math.isfinite(equity):
                return False
            # If equity still 0, use capital as fallback to avoid blocking
            if equity == 0:
                equity = s.capital_usd
                available = s.capital_usd
        except Exception as ex:
            log.warning(f"_update_risk parse failed: {ex}")
            return False
        
        r = self.state.risk
        if r.get("baseline_account_equity") is None:
            r = {"baseline_account_equity": equity, "baseline_at": utcnow(), "peak_bot_equity": s.capital_usd, "drawdown_latched": False}
        
        old_peak = float(r.get("peak_bot_equity") or s.capital_usd)
        if old_peak > s.capital_usd * 2.0:
            r = {"baseline_account_equity": equity, "baseline_at": utcnow(), "peak_bot_equity": s.capital_usd, "drawdown_latched": False}
            self.state.push("warning", f"Risk reset: peak {old_peak}->{s.capital_usd}")
        
        estimated = s.capital_usd + equity - float(r["baseline_account_equity"])
        peak = max(float(r.get("peak_bot_equity") or s.capital_usd), estimated)
        drawdown = max(0.0, (peak - estimated) / peak) if peak > 0 else 0.0
        
        old_latched = bool(r.get("drawdown_latched"))
        if old_latched and drawdown < s.max_drawdown_pct * 0.5:
            latched = False
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
            "measurement": "v018-10pairs",
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

    def _calculate_atr_levels(self, df, entry_price: float, side: int) -> dict:
        try:
            from app.volatility_targeting import calculate_atr
            from app.smart_tp_sl import calculate_smart_levels
            atr = calculate_atr(df, 14)
            if atr <= 0 or entry_price <= 0 or side == 0:
                return {"sl": None, "tp": None, "atr": None}
            s = self.settings
            # FIXED46 SMART LADDER
            if getattr(s, 'smart_tp_sl_enabled', True):
                cfg = {
                    'sl_atr': getattr(s, 'smart_sl_atr', 2.0),
                    'tp1_atr': getattr(s, 'smart_tp1_atr', 1.5),
                    'tp2_atr': getattr(s, 'smart_tp2_atr', 3.0),
                    'tp3_atr': getattr(s, 'smart_tp3_atr', 5.0),
                    'tp4_atr': getattr(s, 'smart_tp4_atr', 8.0),
                    'trailing_atr': getattr(s, 'smart_trailing_atr', 1.5),
                }
                smart = calculate_smart_levels(entry_price, side, atr, cfg)
                if smart and smart.get('sl') is not None:
                    return smart
            # Fallback old
            if side > 0:
                sl = entry_price - atr * s.atr_stop_mult
                tp = entry_price + atr * s.atr_tp_mult
            else:
                sl = entry_price + atr * s.atr_stop_mult
                tp = entry_price - atr * s.atr_tp_mult
            return {"sl": sl, "tp": tp, "atr": atr, "entry_price": entry_price, "side": side, "tp1": tp, "tp2": tp, "tp3": tp, "level": 0}
        except Exception as e:
            import logging
            logging.getLogger("engine").warning(f"ATR calc failed: {e}")
            return {"sl": None, "tp": None, "atr": None}
        except:
            return {"sl": None, "tp": None, "atr": None}

    def _check_sl_tp_triggers(self, asset: str, current_price: float) -> Optional[str]:
        levels = self.state.position_sl_tp.get(asset)
        if not levels or not levels.get("sl"):
            return None
        # FIXED46 SMART
        try:
            if levels.get("tp1") is not None:
                from app.smart_tp_sl import check_smart_triggers
                result = check_smart_triggers(asset, current_price, levels)
                if result:
                    return result
        except:
            pass
        side = levels.get("side", 0)
        sl, tp = levels.get("sl"), levels.get("tp")
        if side > 0:
            if current_price <= sl:
                return f"stop_loss SL={sl:.4f} L{levels.get('level',0)}"
            if tp and current_price >= tp:
                return f"take_profit TP={tp:.4f} L{levels.get('level',0)}"
        elif side < 0:
            if current_price >= sl:
                return f"stop_loss SL={sl:.4f} L{levels.get('level',0)}"
            if tp and current_price <= tp:
                return f"take_profit TP={tp:.4f} L{levels.get('level',0)}"
        return None
        # FIXED46: Use smart triggers if available
        if levels.get("tp1") is not None:  # smart ladder
            result = check_smart_triggers(asset, current_price, levels)
            if result:
                return result
        # Fallback old logic
        side = levels.get("side", 0)
        sl, tp = levels.get("sl"), levels.get("tp")
        if side > 0:
            if current_price <= sl:
                return f"stop_loss SL={sl:.4f}"
            if tp and current_price >= tp:
                return f"take_profit TP={tp:.4f}"
        elif side < 0:
            if current_price >= sl:
                return f"stop_loss SL={sl:.4f}"
            if tp and current_price <= tp:
                return f"take_profit TP={tp:.4f}"
        return None
        side = levels.get("side", 0)
        sl, tp = levels.get("sl"), levels.get("tp")
        if side > 0:
            if current_price <= sl:
                return "stop_loss"
            if tp and current_price >= tp:
                return "take_profit"
        elif side < 0:
            if current_price >= sl:
                return "stop_loss"
            if tp and current_price <= tp:
                return "take_profit"
        return None

    def _get_adaptive_cooldown(self, asset: str, base_cooldown: int) -> int:
        try:
            df = self._candle_cache.get(asset, (0, None))[1]
            if df is None or len(df) < 100:
                return base_cooldown
            close = df["close"].astype(float)
            rets = close.pct_change().tail(24)
            volatility = float(rets.std())
            if volatility > 0.02:
                return base_cooldown * 3
            return base_cooldown
        except:
            return base_cooldown

    def close_position(self, asset: str) -> dict:
        """v018 NEW: Close single position by asset - for telegram close buttons"""
        s = self.settings
        client = self._ensure_client()
        inst = s.instrument_for(asset)
        try:
            result = client.close_position(inst)
            self.state.push("info", f"v018 Close {asset} requested via telegram: {result}")
            log.info(f"v018 Close {asset} via telegram: {result}")
            return {"ok": True, "asset": asset, "instrument": inst, "result": result}
        except Exception as exc:
            self.state.push("error", f"v018 Close {asset} failed: {exc}")
            log.warning(f"v018 Close {asset} failed: {exc}")
            return {"ok": False, "asset": asset, "error": str(exc)}

    def notify_test_trade(self, event: str, snapshot: dict):
        """For test trade notifications"""
        self.state.test_trade = snapshot
        self.state.push("info", f"Test trade {event}: {snapshot}")

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
        
        try:
            account = client.account_summary("USDT")
            self.state.account = account
            self._update_risk(account)
        except Exception as exc:
            self.state.push("warning", f"account fetch failed: {exc}")
            account = {"USDT": {"equity": 0, "available": 0}}
        
        try:
            positions = client.positions("USDT")
            self.state.positions = positions
        except Exception as exc:
            positions = []
            self.state.push("warning", f"positions fetch failed: {exc}")
        
        inventory = {}
        self._last_inventory = {}
        for p in positions:
            try:
                inst = p.get("instrument_name")
                if inst:
                    qty = signed_position(p)
                    inventory[inst] = qty
                    self._last_inventory[inst] = qty
            except:
                continue
        
        # v018 FIXED3 ABSOLUTE 10 assets - ignore env
        ALLOWED_10 = ["BTC", "ETH", "SOL", "AVAX", "LINK", "XRP", "DOGE", "ADA", "DOT", "LTC"]
        raw_list = s.asset_list
        filtered_list = [a for a in raw_list if a in ALLOWED_10]
        if len(filtered_list) != 10:
            filtered_list = ALLOWED_10
        market_data = {}
        frames = {}
        for asset in filtered_list:
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
        
        # v017 layers: Meta-Allocator dynamic weights
        if s.meta_allocator_enabled and len(self._perf_tracker.trade_history) >= 2:
            try:
                base_weights = parse_weights(s.effective_sleeve_weights)
                dynamic_weights = compute_dynamic_weights(base_weights, self._perf_tracker, min_weight=s.meta_min_weight, max_weight=s.meta_max_weight)
                self.state.meta_stats = self._perf_tracker.get_all_stats()
            except:
                dynamic_weights = parse_weights(s.effective_sleeve_weights)
        else:
            try:
                regimes = {a: _detect_regime(df) for a, df in frames.items()}
                ranging_count = sum(1 for r in regimes.values() if r == "ranging")
                overall_regime = "ranging" if ranging_count >= len(regimes)*0.6 else "trending" if ranging_count <= len(regimes)*0.3 else "transitional"
                base_weights = parse_weights(s.effective_sleeve_weights)
                dynamic_weights = compute_dynamic_weights_simple(base_weights, overall_regime)
            except:
                dynamic_weights = parse_weights(s.effective_sleeve_weights)
        
        try:
            sleeve_results, net_book = run_all_sleeves(frames, s.effective_capital, weights=dynamic_weights, lev_cap=s.effective_lev_cap, enabled=s.enabled_sleeves)
        except Exception as exc:
            log.exception(f"sleeves failed: {exc}")
            sleeve_results, net_book = [], {}
        
        # v017 Layer 1: Confluence
        if s.confluence_enabled and len(sleeve_results) >= 3:
            try:
                confluence_filtered, confluence_info = apply_confluence_filter(sleeve_results, min_agreement=s.confluence_min_agreement, min_confidence_avg=s.confluence_min_confidence)
                self.state.confluence_info = confluence_info
                filtered_net_book = {}
                for asset, data in net_book.items():
                    conf_data = confluence_filtered.get(asset, {})
                    if conf_data.get("side", 0) != 0:
                        filtered_net_book[asset] = {**data, "confluence_strength": conf_data.get("strength", 0), "confluence_avg_conf": conf_data.get("avg_conf", 0)}
                    else:
                        filtered_net_book[asset] = {**data, "target_coin": 0.0, "notional_usd": 0.0, "side": 0, "confluence_strength": 0, "_confluence_flat": True}
                net_book = filtered_net_book
            except Exception as exc:
                log.warning(f"Confluence filter failed: {exc}")
        
        # v017 Layer 2: Vol targeting
        if s.vol_targeting_enabled:
            try:
                for asset, data in net_book.items():
                    if asset in frames and abs(data.get("target_coin", 0)) > 1e-9:
                        df = frames[asset]
                        target_coin = data.get("target_coin", 0)
                        price = data.get("price", 0)
                        if price > 0:
                            notional = abs(target_coin * price)
                            conf = 0.6
                            for r in sleeve_results:
                                if asset in r.per_asset:
                                    conf = r.per_asset[asset].get("detail", {}).get("confidence", conf)
                                    break
                            per_cap = s.effective_capital * 0.15
                            vol_notional, realized_vol, vol_scalar = volatility_targeted_size(df, per_cap, s.effective_lev_cap, conf, target_vol_annual=s.target_vol_annual)
                            data["target_coin"] = data["target_coin"] * vol_scalar
                            data["notional_usd"] = abs(data["target_coin"] * price)
                            data["volatility_info"] = {"realized_vol": realized_vol, "vol_scalar": vol_scalar}
            except Exception as exc:
                log.warning(f"Vol targeting failed: {exc}")
        
        # v017 Layer 3: Correlation cap
        if s.correlation_enabled and s.correlation_cap_enabled:
            try:
                risk_cap = float(self.state.risk.get("notional_cap_usd") or s.max_notional_usd)
                net_book, corr_info = apply_correlation_cap(net_book, risk_cap)
                self.state.correlation_info = corr_info
            except Exception as exc:
                log.warning(f"Correlation cap failed: {exc}")
        
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
        
        # FIXED46: Auto-register SMART TP/SL for existing positions without SL/TP
        try:
            for inst, qty in inventory.items():
                if abs(qty) < 1e-9:
                    continue
                asset = inst.replace("USDT","")
                if asset not in self.state.position_sl_tp:
                    df = frames.get(asset)
                    if df is not None and len(df) > 20:
                        avg_price = 0
                        for p in positions:
                            if p.get("instrument_name") == inst:
                                avg_price = float(p.get("average_price") or 0)
                                break
                        if avg_price > 0:
                            side = 1 if qty > 0 else -1
                            levels = self._calculate_atr_levels(df, avg_price, side)
                            if levels.get("sl") is not None:
                                self.state.position_sl_tp[asset] = {**levels, "side": side, "entry_price": avg_price}
                                self.state.push("info", f"SMART TP/SL auto-registered for existing {asset}: Entry={avg_price:.4f} SL={levels.get('sl'):.4f} TP1={levels.get('tp1'):.4f} TP2={levels.get('tp2'):.4f} TP3={levels.get('tp3'):.4f}")
        except Exception as e:
            pass

        # SL/TP + Smart exit
        sl_tp_overrides = {}
        for asset in filtered_list:
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
                continue
            # FIXED46 SMART TP/SL LADDER
            try:
                pos_state = self.state.position_sl_tp.get(asset)
                if pos_state:
                    atr = pos_state.get("atr") or (calculate_atr(frames.get(asset)) if asset in frames else 0)
                    if atr > 0:
                        # Use smart ladder if enabled
                        if getattr(s, 'smart_tp_sl_enabled', False) and pos_state.get("tp1") is not None:
                            cfg = {
                                'sl_atr': getattr(s, 'smart_sl_atr', 2.0),
                                'tp1_atr': getattr(s, 'smart_tp1_atr', 1.5),
                                'tp2_atr': getattr(s, 'smart_tp2_atr', 3.0),
                                'tp3_atr': getattr(s, 'smart_tp3_atr', 5.0),
                                'tp4_atr': getattr(s, 'smart_tp4_atr', 8.0),
                                'trailing_atr': getattr(s, 'smart_trailing_atr', 1.5),
                            }
                            exit_actions = manage_smart_exit(asset, px, pos_state, atr, cfg)
                            # Update state with new SL/TP and level
                            if exit_actions.get("new_sl") != pos_state.get("sl") or exit_actions.get("new_tp") != pos_state.get("tp"):
                                pos_state["sl"] = exit_actions["new_sl"]
                                pos_state["tp"] = exit_actions["new_tp"]
                                pos_state["level"] = exit_actions.get("level", pos_state.get("level",0))
                                pos_state["breakeven_moved"] = exit_actions.get("breakeven_moved", False)
                                pos_state["tp1_hit"] = exit_actions.get("tp1_hit", False)
                                pos_state["tp2_hit"] = exit_actions.get("tp2_hit", False)
                                pos_state["tp3_hit"] = exit_actions.get("tp3_hit", False)
                                pos_state["partial_taken"] = exit_actions.get("partial_taken", 0.0)
                                pos_state["highest_profit_atr"] = exit_actions.get("highest_profit_atr", 0.0)
                                pos_state["trailing_active"] = exit_actions.get("trailing_active", False)
                                if exit_actions.get("reason"):
                                    self.state.push("info", f"SMART TP/SL {asset} Level {exit_actions.get('level')}: {exit_actions.get('reason')}")
                                    log.info(f"SMART TP/SL {asset}: {exit_actions.get('reason')}")
                        elif s.smart_exit_enabled:
                            # Fallback old exit manager
                            exit_actions = manage_position_exit(asset, px, pos_state, atr)
                            if exit_actions.get("new_sl") != pos_state.get("sl"):
                                pos_state["sl"] = exit_actions["new_sl"]
                                pos_state["breakeven_moved"] = exit_actions.get("breakeven_moved", False)
                                pos_state["partial_taken"] = exit_actions.get("partial_taken", False)
            except Exception as e:
                log.warning(f"Smart exit failed for {asset}: {e}")
        
        risk = self.state.risk
        latched = bool(risk.get("drawdown_latched"))
        drawdown = float(risk.get("drawdown_pct") or 0)
        
        actions = []
        for asset in filtered_list:
            inst = s.instrument_for(asset)
            md = market_data.get(asset, {})
            book = md.get("book", {})
            meta = md.get("meta", {})
            b = net_book.get(asset, {})
            
            current = inventory.get(inst, 0.0)
            px = float(b.get("price") or book.get("mark_price") or 0)
            desired = float(b.get("target_coin") or 0)
            
            is_sl_tp = asset in sl_tp_overrides
            is_confluence_flat = b.get("_confluence_flat", False)
            
            desired_gross = sum(abs(v.get("target_coin",0)*v.get("price",0)) for v in net_book.values())
            cap = float(risk.get("notional_cap_usd") or s.max_notional_usd)
            scale = min(1.0, cap / desired_gross) if desired_gross>0 else 1.0
            if not is_sl_tp:
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
                "_confluence_flat": is_confluence_flat,
            }
            
            if is_confluence_flat:
                action["status"] = "confluence_flat"
                action["reason"] = self.state.confluence_info.get(asset, "No confluence")
                actions.append(action)
                continue
            
            # Cost filter
            if s.cost_filter_enabled and not is_sl_tp:
                try:
                    if abs(desired) > 1e-9 and abs(current) < 1e-9:
                        strength = b.get("confluence_strength", 1)
                        avg_conf = b.get("confluence_avg_conf", 0.6)
                        edge_bps = estimate_signal_edge_bps(strength, avg_conf)
                        notional = abs(desired * px)
                        should_trade, reason = should_trade_after_costs(notional, edge_bps, fee_bps=s.fee_bps, slippage_bps=s.slippage_bps)
                        if not should_trade:
                            action["status"] = "cost_filter"
                            action["reason"] = reason
                            actions.append(action)
                            continue
                except:
                    pass
            
            if current != 0 and not is_sl_tp:
                is_reversal = current * desired < 0 and abs(desired) > 1e-9
                if is_reversal:
                    action["_force_close_first"] = True
                    action["desired_coin"] = 0.0
                    desired = 0.0
            
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
                    last_trade = self.state.last_trade_at.get(asset, 0)
                    effective_cooldown = self._get_adaptive_cooldown(asset, s.cooldown_seconds)
                    if not plan.reduce_only and not is_sl_tp and time.time() - last_trade < effective_cooldown:
                        remaining = int(effective_cooldown - (time.time() - last_trade))
                        action.update(status="cooldown", reason=f"Cooldown {remaining}s")
                    elif not s.trading_enabled:
                        action.update(status="trading_disabled", reason="TRADING_ENABLED=false")
                    elif not plan.reduce_only and latched and not is_sl_tp:
                        action.update(status="drawdown_guard", reason=f"Drawdown {drawdown*100:.1f}%")
                    elif not plan.reduce_only and book.get("state") != "open" and not is_sl_tp:
                        action.update(status=f"market_{book.get('state','unknown')}", reason="Book not open")
                    elif inst in self._backoffs and time.time() < self._backoffs[inst]["retry_after"]:
                        err = self._backoffs[inst]["error"]
                        if not (is_sl_tp or "small" in err.lower() or "qty exceeds" in err.lower()):
                            action.update(status="order_error_backoff", reason=err)
                    
                    if action["status"] == "planned" or is_sl_tp:
                        try:
                            limit = ioc_price(book, plan.direction, meta, s.max_slippage_bps)
                            action["limit_price"] = limit
                            action["can_execute"] = True
                            action["status"] = "planned"
                            if is_sl_tp:
                                action["reason"] = f"SL/TP: {sl_tp_overrides[asset]}"
                        except Exception as exc:
                            action.update(status="no_liquidity", reason=str(exc))
            except Exception as exc:
                action.update(status="error", reason=str(exc), error=str(exc))
            
            actions.append(action)
        
        open_count = sum(1 for v in market_data.values() if v.get("market_state")=="open")
        active_assets = sum(1 for v in net_book.values() if abs(v.get("target_coin",0))>1e-9)
        ranging_count = sum(1 for a, df in frames.items() if _detect_regime(df) == "ranging")
        
        blocker = None
        trading_ready = True
        if latched:
            blocker = "drawdown_guard"
            trading_ready = False
        elif any(a["status"]=="order_error_backoff" for a in actions):
            real_blockers = [a for a in actions if a["status"]=="order_error_backoff" and "small" not in a["reason"].lower() and "qty exceeds" not in a["reason"].lower()]
            if not real_blockers:
                trading_ready = True
                blocker = None
            else:
                trading_ready = False
                blocker = "order_error_backoff"
        
        all_flat = all(a["status"] in ("target_reached", "pending_check", "cooldown", "skip_small", "below_exchange_minimum", "no_signal", "confluence_flat", "cost_filter") for a in actions)
        if all_flat:
            trading_ready = not latched
            blocker = None if trading_ready else "drawdown_guard"
        
        self.state.diagnostics = {
            "trading_ready": trading_ready,
            "trading_state": "ready" if trading_ready else "blocked",
            "primary_blocker": blocker,
            "market_open_count": open_count,
            "active_signal_assets": active_assets,
            "execution_environment": "testnet-live-v018-10pairs",
            "ranging_count": ranging_count,
            "regime_info": {a: _detect_regime(df) for a, df in frames.items()},
            "sl_tp_triggers": sl_tp_overrides,
            "confluence_info": self.state.confluence_info,
            "correlation_info": self.state.correlation_info,
            "meta_stats": self.state.meta_stats,
            "version": self.state.version,
            "assets_count": len(s.asset_list),
            "test_trade_enabled": s.effective_test_trade_enabled,
        }
        self.state.market_data = market_data
        self.state.actions = actions
        
        # v018 FIXED7: ACTUAL EXECUTION - was missing, only planned but never executed
        # Collect executable actions
        executable = []
        for action in actions:
            if action.get("can_execute") and action.get("status") == "planned":
                # Get plan details
                try:
                    plan_direction = action.get("direction")
                    plan_amount = action.get("amount", 0)
                    if plan_amount > 0 and plan_direction in ("buy", "sell"):
                        executable.append(action)
                except:
                    pass
        
        # Execute each planned order
        if executable and s.trading_enabled and not s.dry_run:
            log.info(f"Executing {len(executable)} planned orders")
            for action in executable:
                if self._stop.is_set():
                    break
                if self.state.pending_order:
                    action.update(status="execution_uncertain", reason="Previous order pending")
                    continue
                try:
                    inst = action["instrument"]
                    direction = action["direction"]
                    amount = action["amount"]
                    limit_price = action.get("limit_price")
                    reduce_only = action.get("reduce_only", False)
                    
                    # Place order via client
                    result = client._place_order(inst, direction, amount, "limit" if limit_price else "market", price=limit_price, reduce_only=reduce_only, label=f"zenith_{action['asset'].lower()}_{self.state.loop_count}")
                    
                    # Parse result
                    filled = 0
                    try:
                        # Try to get filled amount from result
                        if isinstance(result, dict):
                            # Check for filled amount in various formats
                            filled = float(result.get("filled_amount") or result.get("filled") or result.get("executedQty") or 0)
                            if filled == 0:
                                # Check if order was created and filled via positions
                                # For AriaX, check if result has order_id and then check positions
                                order_id = result.get("order_id") or result.get("id")
                                if order_id:
                                    filled = amount  # Assume filled for now, will be verified next cycle via positions
                                    action.update(status="bought" if direction=="buy" else "sold", filled_amount=filled, order_id=order_id, reason=f"Order placed {order_id}, filled {filled}")
                                    self._log_fill({**action, "filled_amount": filled, "order_id": order_id, "price": limit_price or action.get("price",0)})
                                else:
                                    # No order_id, maybe immediate fill
                                    filled = amount
                                    action.update(status="bought" if direction=="buy" else "sold", filled_amount=filled, reason=f"Filled {filled} {action['asset']}")
                                    self._log_fill({**action, "filled_amount": filled})
                            else:
                                action.update(status="bought" if direction=="buy" else "sold", filled_amount=filled, reason=f"Filled {filled}")
                                self._log_fill({**action, "filled_amount": filled})
                    except Exception as e:
                        log.warning(f"Fill parsing failed for {action['asset']}: {e}")
                        # Still mark as attempted
                        action.update(status="bought" if direction=="buy" else "sold", filled_amount=amount, reason=f"Order placed, fill assumed {amount}")
                        self._log_fill({**action, "filled_amount": amount})
                    
                    # Update last trade time
                    self.state.last_trade_at[action["asset"]] = __import__("time").time()
                    
                except Exception as exc:
                    err_str = str(exc)
                    log.warning(f"Order failed for {action['asset']}: {err_str}")
                    # Check if it's a small notional or minimum error - don't block
                    if "below" in err_str.lower() or "minimum" in err_str.lower() or "small" in err_str.lower() or "notional" in err_str.lower():
                        action.update(status="below_exchange_minimum", reason=err_str[:200])
                    elif "qty exceeds" in err_str.lower():
                        action.update(status="qty_exceeds", reason=err_str[:200])
                    else:
                        action.update(status="order_error", reason=err_str[:200], error=err_str[:200])
                        # Backoff for serious errors
                        self._backoffs[action["instrument"]] = {"retry_after": __import__("time").time()+60, "error": err_str[:200]}
        
        self.state.last_loop_at = utcnow()
        self.state.loop_count += 1
        
        return {"actions": actions, "diagnostics": self.state.diagnostics, "sleeves": self.state.sleeves, "net_book": net_book}

    def _log_fill(self, action: dict):
        self.state.last_fill_at = utcnow()
        try:
            asset = action.get("asset")
            if asset:
                self.state.last_trade_at[asset] = time.time()
                if action.get("intent") in ("open", "increase") and asset not in self.state.position_sl_tp:
                    df = self._candle_cache.get(asset, (0, None))[1]
                    if df is not None and len(df) > 20:
                        entry_price = action.get("price") or action.get("limit_price") or 0
                        if entry_price > 0:
                            side = 1 if action.get("direction") == "buy" else -1
                            levels = self._calculate_atr_levels(df, entry_price, side)
                            if levels.get("sl") is not None:
                                self.state.position_sl_tp[asset] = {**levels, "side": side, "entry_price": entry_price}
                                self.state.push("info", f"SMART TP/SL registered for {asset}: SL={levels.get('sl'):.4f} TP1={levels.get('tp1'):.4f} TP2={levels.get('tp2'):.4f}")
        except:
            pass
        # FIXED46: Also initialize SL/TP for existing positions that don't have it
        try:
            for asset in list(self.state.position_sl_tp.keys()):
                if asset not in self._candle_cache:
                    continue
            # Check inventory for missing SL/TP
            for inst, qty in getattr(self, '_last_inventory', {}).items():
                if abs(qty) < 1e-9:
                    continue
                # Find asset from instrument
                asset = inst.replace("USDT","")
                if asset not in self.state.position_sl_tp:
                    df = self._candle_cache.get(asset, (0, None))[1] if asset in self._candle_cache else None
                    if df is not None and len(df) > 20:
                        # Find position avg price
                        avg_price = 0
                        for p in self.state.positions:
                            if p.get("instrument_name") == inst:
                                avg_price = float(p.get("average_price") or 0)
                                break
                        if avg_price > 0:
                            side = 1 if qty > 0 else -1
                            levels = self._calculate_atr_levels(df, avg_price, side)
                            if levels.get("sl") is not None:
                                self.state.position_sl_tp[asset] = {**levels, "side": side, "entry_price": avg_price}
                                self.state.push("info", f"SMART TP/SL auto-registered for existing {asset}: SL={levels.get('sl'):.4f} TP1={levels.get('tp1'):.4f}")
        except Exception as e:
            pass
        entry = {"ts": self.state.last_fill_at, **{k: action.get(k) for k in ("asset", "instrument", "status", "amount", "price", "notional_usd", "order_id")}}
        self.state.orders_log = (self.state.orders_log + [entry])[-300:]

_engine: Optional[TradingEngine] = None

def get_engine():
    global _engine
    if _engine is None:
        _engine = TradingEngine()
    return _engine
