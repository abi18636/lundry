from __future__ import annotations

import copy
import json
import logging
import math
import os
import threading
import time
import traceback
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from app.config import Settings, get_settings
from app.deribit import DeribitAPIError, DeribitClient
from app.execution import fill_summary, floor_amount, ioc_price, plan_rebalance, signed_position
from app.financial_reports import FinancialReporting
from app.sleeves import parse_weights, prepare_frames, run_all_sleeves
from app.telegram_bot import get_reporter
from app.test_trade import TimedTestTrade

log = logging.getLogger("engine")


def utcnow() -> str:
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
    diagnostics: Dict[str, Any] = field(default_factory=lambda: {
        "trading_ready": False, "trading_state": "initializing", "primary_blocker": "initializing"})
    market_data: Dict[str, Any] = field(default_factory=dict)
    risk: Dict[str, Any] = field(default_factory=dict)
    pending_order: Optional[dict] = None
    last_fill_at: Optional[str] = None
    ops_reviews: List[dict] = field(default_factory=list)
    test_trade: Dict[str, Any] = field(default_factory=lambda: {"active": False, "status": "idle", "environment": "testnet-live"})

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
        self.client: Optional[DeribitClient] = None
        self._candle_cache: Dict[str, tuple] = {}
        self._signal_cache: Optional[tuple] = None
        self._backoffs: Dict[str, dict] = {}
        self._last_review = 0.0
        self._previous_open_count: Optional[int] = None
        self._recovered_fills: List[dict] = []
        self._before_positions: Dict[str, dict] = {}
        Path(self.settings.state_path).parent.mkdir(parents=True, exist_ok=True)
        self._restore()
        self.test_trader = TimedTestTrade(self)
        self.financial = FinancialReporting(self)

    def _restore(self):
        try:
            saved = json.loads(Path(self.settings.state_path).read_text())
            # Never restore a stale position/account snapshot as current inventory.
            self.state.risk = saved.get("risk") or {}
            self.state.pending_order = saved.get("pending_order")
            self.state.orders_log = (saved.get("orders_log") or [])[-300:]
            self.state.last_fill_at = saved.get("last_fill_at")
            self.state.ops_reviews = (saved.get("ops_reviews") or [])[-24:]
            self.state.test_trade = saved.get("test_trade") or self.state.test_trade
        except FileNotFoundError:
            pass
        except Exception as exc:
            self.state.push("warning", f"state restore unavailable: {type(exc).__name__}")

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.state.running, self.state.mode = True, "starting"
        self.test_trader.begin_recovery()
        try:
            self.financial.start(notifier=self._on_financial_event)
        except Exception as exc:
            self.state.push("warning", f"financial reporting not started: {type(exc).__name__}")
        self._thread = threading.Thread(target=self._loop, name="superbot-loop", daemon=True)
        self._thread.start()
        self.state.push("info", "SUPER engine started; execution requires fresh open books")

    def stop(self):
        self._stop.set()
        self._wake_cycle.set()
        self.test_trader.request_close()
        try:
            self.financial.stop()
        except Exception:
            pass
        self.state.running, self.state.mode = False, "stopping" if self.state.cycle_in_progress else "stopped"
        self.state.push("info", "engine stop requested; no new orders after stop")

    def _on_financial_event(self, event: dict, snapshot: dict) -> bool:
        try:
            rep = get_reporter()
            if rep and hasattr(rep, "on_financial_event"):
                return bool(rep.on_financial_event(event, snapshot))
        except Exception:
            log.warning("financial event notification unavailable", exc_info=True)
        return False

    def snapshot(self) -> dict:
        # No network/cycle lock here: /health must stay responsive during a slow API request.
        s = self.settings
        result = copy.deepcopy(asdict(self.state))
        result["bot"] = "zenith-SUPER"
        if hasattr(self, "test_trader"):
            result["test_trade"] = self.test_trader.snapshot()
        if hasattr(self, "financial"):
            try:
                result["financial"] = self.financial.snapshot()
            except Exception as exc:
                result["financial"] = {"error": str(exc), "schema": "financial-reports-v3"}
        result["events"] = result["events"][-60:]
        result["config"] = {
            "capital_usd": s.capital_usd, "lev_cap": s.lev_cap, "long_only": s.long_only,
            "dry_run": s.dry_run, "trading_enabled": s.trading_enabled, "assets": s.asset_list,
            "sleeve_weights": s.sleeve_weights, "sleeves_enabled": s.enabled_sleeves,
            "base_url": s.deribit_base_url, "loop_seconds": s.loop_seconds,
            "architecture": "independent_sleeves_net_at_order_layer",
            "execution": "slippage_bounded_limit_ioc", "max_notional_usd": s.max_notional_usd,
            "effective_leverage_cap": min(s.lev_cap, 1.0), "max_drawdown_pct": s.max_drawdown_pct,
            "legacy_min_notional_usd": s.min_notional_usd,
            "min_notional_policy": "exchange_lots_only_never_inflate_allocations",
            "rebalance_notional_usd": s.rebalance_notional_usd,
            "max_spread_bps": s.max_spread_bps, "max_slippage_bps": s.max_slippage_bps,
            "ops_review_seconds": s.ops_review_seconds,
            "signal_timing": "completed_1h_bars_next_bar_decision",
            "test_trade_enabled": s.test_trade_enabled,
            "test_trade_asset": s.test_trade_asset.upper(),
            "test_trade_hold_seconds": s.test_trade_hold_seconds,
            "test_trade_max_notional_usd": s.test_trade_max_notional_usd,
            "telegram_test_url": f"https://t.me/{s.telegram_bot_username}?start=test60",
        }
        return result

    def _save(self):
        with self._save_lock:
            try:
                path = Path(self.settings.state_path)
                # Keep more fill history than the dashboard event tail. Atomic replacement.
                snapshot = self.snapshot()
                snapshot["orders_log"] = self.state.orders_log[-300:]
                temp = path.with_suffix(".tmp")
                temp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, default=str))
                os.replace(temp, path)
            except Exception as exc:
                log.warning("state save failed: %s", type(exc).__name__)


    def _ensure_client(self) -> DeribitClient:
        if self.client is None:
            s = self.settings
            if not s.deribit_client_id or not s.deribit_client_secret:
                raise RuntimeError("DERIBIT_CLIENT_ID / DERIBIT_CLIENT_SECRET not configured")
            self.client = DeribitClient(s.deribit_base_url, s.deribit_client_id, s.deribit_client_secret)
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
                self.state.diagnostics = {
                    "trading_ready": False, "trading_state": "blocked", "primary_blocker": "cycle_error",
                    "error": str(exc), "execution_environment": self._environment(),
                }
                self.state.push("error", str(exc), tb=traceback.format_exc()[-1500:])
                log.exception("cycle failed")
                try:
                    rep = get_reporter()
                    if rep:
                        rep.on_cycle({}, loop_count=self.state.loop_count, error=str(exc))
                except Exception:
                    log.warning("error notification unavailable")
            self._save()
            self._wake_cycle.wait(max(5, int(self.settings.loop_seconds)))
            self._wake_cycle.clear()
        self.state.running, self.state.mode = False, "stopped"
        if self.client and not self.test_trader.is_active():
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None

    def _environment(self) -> str:
        return "testnet-live" if urlparse(self.settings.deribit_base_url).hostname == "test.deribit.com" else "mainnet-live"

    def once(self) -> dict:
        if not self._cycle_lock.acquire(blocking=False):
            raise EngineBusyError("a cycle is already in progress; no overlapping orders")
        self.state.cycle_in_progress = True
        self.state.cycle_started_at = utcnow()
        try:
            result = self._once_unlocked()
            self._save()
            return result
        finally:
            self.state.cycle_in_progress = False
            self._cycle_lock.release()

    def _update_risk(self, account: dict) -> bool:
        """Equity-delta guard requires a dedicated USDC account/subaccount.

        This is a stop threshold, not a guarantee against gaps/slippage. Render free
        storage is ephemeral; durable risk history requires an external state store.
        """
        s = self.settings
        try:
            equity = float(account["USDC"]["equity"])
            available = float(account["USDC"]["available"])
            if not math.isfinite(equity) or not math.isfinite(available):
                return False
        except (KeyError, TypeError, ValueError):
            return False
        r = self.state.risk
        if r.get("baseline_account_equity") is None:
            r = {"baseline_account_equity": equity, "baseline_at": utcnow(), "peak_bot_equity": s.capital_usd,
                 "drawdown_latched": False}
        estimated = s.capital_usd + equity - float(r["baseline_account_equity"])
        peak = max(float(r.get("peak_bot_equity") or s.capital_usd), estimated)
        drawdown = max(0.0, (peak - estimated) / peak) if peak > 0 else 1.0
        latched = bool(r.get("drawdown_latched")) or drawdown >= s.max_drawdown_pct
        cap = min(s.capital_usd, max(0.0, estimated)) * min(s.lev_cap, 1.0)
        if s.max_notional_usd > 0:
            cap = min(cap, s.max_notional_usd)
        self.state.risk = {**r, "estimated_bot_equity": estimated, "peak_bot_equity": peak,
                           "drawdown_pct": drawdown, "drawdown_limit_pct": s.max_drawdown_pct,
                           "drawdown_latched": latched, "notional_cap_usd": cap,
                           "available_funds": available,
                           "measurement": "dedicated_USDC_account_equity_delta",
                           "persistence_warning": "Render free filesystem is ephemeral; threshold is NOT a guaranteed max loss"}
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
            out["errors"].append({"stage": "instrument", "error": str(exc), "code": getattr(exc, "code", None)})
            return out
        try:
            out["book"] = c.order_book(inst, 5)
        except Exception as exc:
            out["errors"].append({"stage": "book", "error": str(exc), "code": getattr(exc, "code", None)})
        try:
            cached = self._candle_cache.get(asset)
            now = time.time()
            hour = int(now // 3600)
            if cached and cached[1] == hour and now - cached[0] < 300:
                out["candles"] = cached[2]
            else:
                frame = c.candles(inst, hours=s.candle_lookback_hours, resolution="60")
                self._candle_cache[asset] = (now, hour, frame)
                out["candles"] = frame
        except Exception as exc:
            out["errors"].append({"stage": "candles", "error": str(exc), "code": getattr(exc, "code", None)})
        return out

    def _reconcile_pending(self, client: DeribitClient) -> Optional[str]:
        pending = self.state.pending_order
        if not pending:
            return None
        try:
            orders = client.orders_by_label(pending["label"])
            if isinstance(orders, dict):
                orders = [orders]
            if not orders:
                return "execution_uncertain: order label not yet confirmed; blind retry prohibited"
            terminal = {"filled", "cancelled", "rejected"}
            for order in orders:
                if order.get("order_state") not in terminal:
                    if order.get("order_id"):
                        client.cancel_order(order["order_id"])
                    return "execution_uncertain: awaiting confirmation of own IOC cancellation"
            for order in orders:
                fill = fill_summary({"order": order})
                if fill["filled_amount"] > 0:
                    recovered = {**pending, **fill, "status": "recovered_fill", "amount": fill["filled_amount"],
                                 "price": fill.get("average_price") or pending.get("price"),
                                 "notional_usd": fill["filled_amount"] * float(fill.get("average_price") or pending.get("price") or 0)}
                    self._log_fill(recovered)
                    self._recovered_fills.append(recovered)
            self.state.pending_order = None
            self.state.push("recovery", "Ambiguous order reconciled by its unique label; inventory will be re-read")
            return "reconcile_complete: wait one cycle before further orders"
        except Exception as exc:
            return f"execution_uncertain: reconciliation unavailable ({exc})"

    def request_cycle(self):
        self._wake_cycle.set()

    def notify_test_trade(self, event: str, snapshot: dict):
        self.state.push("test_trade", f"test {event}: {snapshot.get('status')}", test_id=snapshot.get("id"))
        reporter = get_reporter()
        if reporter and hasattr(reporter, "on_test_trade"):
            # Telegram must never delay the 60-second exit timer.
            threading.Thread(target=reporter.on_test_trade, args=(event, copy.deepcopy(snapshot)),
                             name="test-trade-notice", daemon=True).start()

    def _paused_test_cycle(self):
        self.state.mode = "test_recovery" if self.test_trader.recovery_in_progress else "test_trade"
        self.state.last_loop_at = utcnow()
        self.state.loop_count += 1
        self.state.actions = [{"asset": a, "instrument": self.settings.instrument_for(a),
                               "status": "test_trade_paused", "reason": "Strategy orders paused while the isolated timed test is active",
                               "can_execute": False, "market_state": (self.state.market_data.get(a) or {}).get("market_state", "unknown")}
                              for a in self.settings.asset_list]
        self.state.diagnostics = {**self.state.diagnostics, "trading_ready": False, "trading_state": "manual_test",
                                  "primary_blocker": "test_recovery" if self.test_trader.recovery_in_progress else "test_trade_active",
                                  "last_fill_at": self.state.last_fill_at, "confirmed_fill_count": len(self.state.orders_log)}
        return {"sleeves": self.state.sleeves, "net_book": self.state.net_book, "actions": self.state.actions,
                "diagnostics": self.state.diagnostics, "test_trade": self.test_trader.snapshot()}

    def _once_unlocked(self) -> dict:
        if self.test_trader.is_active():
            return self._paused_test_cycle()
        s = self.settings
        self.state.mode = "running"
        if self._environment() != "testnet-live" and not s.allow_mainnet_trading:
            # Do not even send testnet credentials to production when execution is not authorized.
            blocked_actions = [{"asset": a, "instrument": s.instrument_for(a), "status": "safety_blocked",
                                "reason": "mainnet_not_authorized", "can_execute": False, "market_state": "unknown"}
                               for a in s.asset_list]
            return self._finish(blocked_actions, {a: {"market_state": "unknown"} for a in s.asset_list},
                                ["mainnet_not_authorized"])
        c = self._ensure_client()
        fatal = []
        if not s.use_usdc_linear:
            fatal.append("unsupported_execution_units: this engine requires USDC linear futures")
        if self._environment() != "testnet-live" and not s.allow_mainnet_trading:
            fatal.append("mainnet_not_authorized")
        account = {}
        for cur in ("USDC", "BTC", "ETH"):
            try:
                a = c.account_summary(cur)
                account[cur] = {"equity": a.get("equity"), "balance": a.get("balance"),
                                "available": a.get("available_funds")}
            except Exception as exc:
                account[cur] = {"error": str(exc)}
        self.state.account = account
        if not self._update_risk(account):
            fatal.append("account_unavailable")
        try:
            positions = c.positions("USDC")
            if not isinstance(positions, list):
                raise RuntimeError("invalid position response")
            self.state.positions = positions
        except Exception as exc:
            # KEEP the last known inventory for display; never assume a failed read means flat.
            positions = self.state.positions
            fatal.append(f"positions_unavailable: {exc}")
        by_inst = {p.get("instrument_name"): p for p in positions}
        try:
            open_orders = c.open_orders()
            if not isinstance(open_orders, list):
                raise RuntimeError("invalid open-order response")
        except Exception as exc:
            open_orders = []
            fatal.append(f"open_orders_unavailable: {exc}")
        # Do not cancel another operator's orders. Reserve their possible increasing exposure.
        orders_by_inst = {}
        for order in open_orders:
            if not order.get("reduce_only"):
                orders_by_inst.setdefault(order.get("instrument_name"), []).append(order)
        self._recovered_fills = []
        reconcile = self._reconcile_pending(c)
        if reconcile:
            fatal.append(reconcile)
        samples = {}
        workers = min(max(1, s.max_assets_parallel_candles), max(1, len(s.asset_list)), 8)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self._fetch_asset, a): a for a in s.asset_list}
            for future in as_completed(futures):
                a = futures[future]
                try:
                    samples[a] = future.result()
                except Exception as exc:
                    samples[a] = {"asset": a, "instrument": s.instrument_for(a), "meta": {}, "book": {},
                                  "candles": None, "errors": [{"stage": "fetch", "error": str(exc)}]}
        if getattr(c, "last_testnet", None) is False and not s.allow_mainnet_trading:
            fatal.append("mainnet_response_not_authorized")
        ohlc = {a: d["candles"] for a, d in samples.items() if d.get("candles") is not None}
        fingerprint = (s.sleeve_weights, tuple(s.enabled_sleeves), s.capital_usd, s.lev_cap,
                       tuple((a, len(df), str(df["dt"].iloc[-1]) if len(df) else "", float(df["close"].iloc[-1]) if len(df) else 0)
                             for a, df in sorted(ohlc.items())))
        if self._signal_cache and self._signal_cache[0] == fingerprint:
            sleeves_pub, net_book = copy.deepcopy(self._signal_cache[1:])
        else:
            frames = prepare_frames(ohlc, s.asset_list)
            results, net_book = run_all_sleeves(frames, total_capital=s.capital_usd,
                                              weights=parse_weights(s.sleeve_weights), lev_cap=s.lev_cap,
                                              enabled=s.enabled_sleeves)
            sleeves_pub = [asdict(r) for r in results]
            for r in sleeves_pub:
                r["id"] = r.pop("sleeve_id")
            self._signal_cache = (fingerprint, copy.deepcopy(sleeves_pub), copy.deepcopy(net_book))
        if any(str(r.get("notes", "")).startswith("ERROR:") for r in sleeves_pub):
            fatal.append("strategy_error: allocation incomplete; orders blocked")
        if not sleeves_pub or set(r["id"] for r in sleeves_pub) != set(s.enabled_sleeves):
            fatal.append("strategy_configuration_incomplete")
        self.state.sleeves, self.state.net_book = sleeves_pub, net_book
        risk_cap = float(self.state.risk.get("notional_cap_usd", 0))
        desired_gross = sum(abs(float(b.get("target_coin") or 0)) * float(b["price"]) for b in net_book.values())
        scale = min(1.0, risk_cap / desired_gross) if desired_gross > 0 else 1.0
        latched = bool(self.state.risk.get("drawdown_latched"))
        inventory = {}
        try:
            self._before_positions = {}
            for inst, position in by_inst.items():
                inventory[inst] = signed_position(position)
                self._before_positions[inst] = {
                    "quantity": inventory[inst],
                    "average_price": position.get("average_price"),
                    "mark_price": position.get("mark_price"),
                }
        except Exception as exc:
            fatal.append(f"position_units_invalid: {exc}")
        market_data, actions, executable = {}, [], []
        now_ms = time.time() * 1000
        for a in s.asset_list:
            d = samples[a]
            inst, meta, book = d["instrument"], d["meta"], d["book"]
            frame = d.get("candles")
            b = net_book.get(a) or {}
            current = inventory.get(inst, 0.0)
            px = float(b.get("price") or book.get("mark_price") or by_inst.get(inst, {}).get("mark_price") or 0)
            desired = float(b.get("target_coin") or 0) * scale
            if s.long_only:
                desired = max(0.0, desired)
            if latched:
                desired = 0.0
            timestamp = book.get("timestamp")
            age = max(0, (now_ms-float(timestamp))/1000) if timestamp else None
            state = str(book.get("state") or "unknown").lower()
            bids, asks = book.get("bids") or [], book.get("asks") or []
            bid, ask = (float(bids[0][0]) if bids else 0), (float(asks[0][0]) if asks else 0)
            spread = (ask-bid)/((ask+bid)/2)*10000 if bid > 0 and ask >= bid else None
            quality = {"instrument": inst, "market_state": state, "book_age_seconds": age,
                       "best_bid": bid or None, "best_ask": ask or None, "spread_bps": spread,
                       "candles": len(frame) if frame is not None else 0, "errors": d["errors"]}
            if frame is not None and len(frame):
                quality["last_completed_bar"] = str(frame["dt"].iloc[-1])
                traded = frame.loc[frame["volume"] > 0]
                last_trade_bar = traded["dt"].iloc[-1] if len(traded) else None
                quality["last_nonzero_volume_bar"] = str(last_trade_bar) if last_trade_bar is not None else None
                quality["signal_no_trade_hours"] = (now_ms/1000-last_trade_bar.timestamp())/3600 if last_trade_bar is not None else None
                quality["trailing_zero_volume_bars"] = int(len(frame)-1-traded.index[-1]) if len(traded) else len(frame)
            market_data[a] = quality
            action = {"asset": a, "instrument": inst, "desired_coin": desired,
                      "target_amt": desired, "current_size": current, "delta": desired-current,
                      "notional_usd": abs(desired)*px, "contributors": b.get("contributors") or {},
                      "price": px, "market_state": state, "book_age_seconds": age,
                      "can_execute": False, "status": "pending_check", "reason": ""}
            action["minimum_amount"] = meta.get("min_trade_amount") or meta.get("contract_size")
            action["contract_size"] = meta.get("contract_size")
            # Hypothetical lot feasibility is observable even while the venue is halted;
            # it is not an order, not an acknowledgement and never a reported fill.
            try:
                draft = plan_rebalance(current, desired, px, float(meta.get("contract_size") or 0),
                                       float(meta.get("min_trade_amount") or meta.get("contract_size") or 0),
                                       s.rebalance_notional_usd)
                action.update(planned_target_amt=draft.target, preflight_status=draft.status,
                              preflight_intent=draft.intent, preflight_amount=draft.amount)
            except (ValueError, TypeError):
                action["preflight_status"] = "not_validated"
            actions.append(action)
            def block(status, reason):
                action.update(status=status, reason=reason)
            if self.test_trader.recovery_in_progress and a == s.test_trade_asset.upper():
                block("test_recovery_pending", "Only this instrument is reserved until old test ownership is reconciled")
            elif not meta:
                block("instrument_unavailable", "Instrument metadata failed; not a market halt")
            elif meta.get("instrument_type") != "linear" or meta.get("settlement_currency") != "USDC":
                block("unsupported_instrument", "Only USDC linear perpetuals are authorized")
            elif any(x["stage"] == "book" for x in d["errors"]):
                block("market_api_error", " | ".join(x["error"] for x in d["errors"] if x["stage"] == "book"))
            elif state != "open":
                block(f"market_{state}", "Exchange order book is not open; no order submitted")
            elif fatal and not (latched and current and all(x.startswith("strategy_") for x in fatal)):
                block("safety_blocked", " | ".join(fatal))
            elif age is None or age > s.market_data_max_age_seconds:
                block("stale_market_data", "Exchange order book timestamp is stale or absent")
            elif px <= 0 or (a not in net_book and not (latched and current)):
                block("insufficient_candles", "No valid strategy input; an existing position is not assumed to be a zero target")
            elif any(x["stage"] == "candles" for x in d["errors"]) and not (latched and current):
                block("candle_api_error", "Candle fetch failed; no stale signal execution")
            else:
                try:
                    step = float(meta.get("contract_size") or 0)
                    minimum = float(meta.get("min_trade_amount") or step)
                    plan = plan_rebalance(current, desired, px, step, minimum, s.rebalance_notional_usd)
                    action.update(target_amt=plan.target, delta=plan.delta, amount=plan.amount,
                                  direction=plan.direction, reduce_only=plan.reduce_only, intent=plan.intent,
                                  notional_usd=abs(plan.target)*px, status=plan.status, reason=plan.reason,
                                  minimum_amount=minimum, contract_size=step)
                    if plan.status == "planned":
                        if not s.trading_enabled:
                            block("trading_disabled", "TRADING_ENABLED=false")
                        elif not plan.reduce_only and latched:
                            block("drawdown_guard", "Drawdown threshold is latched; new exposure prohibited")
                        elif not plan.reduce_only and (not bids or not asks or spread is None):
                            block("no_liquidity", "A two-sided executable book is required for entry")
                        elif not plan.reduce_only and spread > s.max_spread_bps:
                            block("spread_too_wide", f"Entry spread {spread:.1f} bps exceeds {s.max_spread_bps:g}")
                        elif not plan.reduce_only and (quality.get("signal_no_trade_hours") is None or quality["signal_no_trade_hours"] > s.max_signal_no_trade_hours):
                            block("stale_signal_data", "Recent chart candles are zero-volume fillers, not fresh traded data")
                        elif not plan.reduce_only and orders_by_inst.get(inst):
                            block("existing_open_order", "Existing increasing order on this instrument must be reconciled first; not cancelled blindly")
                        elif not plan.reduce_only and float(self.state.risk.get("available_funds", 0)) <= 0:
                            block("insufficient_funds", "Account has no available collateral")
                        elif inst in self._backoffs and time.time() < self._backoffs[inst]["retry_after"]:
                            block("order_error_backoff", self._backoffs[inst]["error"])
                        else:
                            limit = ioc_price(book, plan.direction, meta, s.max_slippage_bps)
                            action["limit_price"] = limit
                            action["can_execute"] = True
                            executable.append((plan, action))
                except Exception as exc:
                    block("execution_validation_error", str(exc))
        self.state.market_data = market_data
        # Account-wide gross, including positions outside the configured universe.
        reference_prices = {s.instrument_for(a): float((net_book.get(a) or {}).get("price") or (samples[a].get("book") or {}).get("mark_price") or 0) for a in s.asset_list}
        for inst, position in by_inst.items():
            reference_prices[inst] = max(reference_prices.get(inst, 0), float(position.get("mark_price") or 0))
        gross = sum(abs(q)*reference_prices.get(inst, 0) for inst, q in inventory.items())
        reserved_order_notional = 0.0
        for inst, orders in orders_by_inst.items():
            for order in orders:
                remaining = max(0, float(order.get("amount") or 0)-float(order.get("filled_amount") or 0))
                price = max(float(order.get("price") or 0), reference_prices.get(inst, 0))
                if remaining and not price:
                    fatal.append("open_order_risk_unknown")
                reserved_order_notional += remaining * price
        self.state.risk["reserved_open_order_notional_usd"] = reserved_order_notional
        # Release risk first. An acknowledgement with no fill releases NO notional room.
        available_room = max(0.0, float(self.state.risk.get("available_funds", 0)))
        for plan, action in sorted(executable, key=lambda x: not x[0].reduce_only):
            inst = action["instrument"]
            if self._stop.is_set():
                action.update(status="engine_stopping", reason="Stop requested before submission", can_execute=False)
                continue
            if self.state.pending_order:
                action.update(status="execution_uncertain", reason="Previous order outcome unconfirmed; no duplicate retry", can_execute=False)
                continue
            risk_price = max(reference_prices.get(inst, 0), action["limit_price"])
            projected = gross + (plan.amount*risk_price if not plan.reduce_only else -min(abs(inventory.get(inst, 0)), plan.amount)*reference_prices.get(inst, 0))
            if not plan.reduce_only and (projected + reserved_order_notional > risk_cap + 1e-8 or "open_order_risk_unknown" in fatal):
                action.update(status="risk_cap_blocked", reason="Actual inventory + requested order would exceed allocated <=1x budget", can_execute=False)
                continue
            if not plan.reduce_only and plan.amount * risk_price > available_room + 1e-8:
                action.update(status="insufficient_funds", reason="Available collateral is less than the unlevered order notional", can_execute=False)
                continue
            if s.dry_run:
                action["status"] = "dry_run_close" if plan.intent == "close" else f"dry_run_{plan.direction}"
                action["reason"] = "Simulation only; no exchange order or real fill"
                continue
            filled = self._submit(c, plan, action)
            if filled > 0:
                if not plan.reduce_only:
                    available_room = max(0.0, available_room - filled * risk_price)
                # Do not assume a reduction released collateral until the next account read.
                sign = 1 if plan.direction == "buy" else -1
                old = inventory.get(inst, 0)
                inventory[inst] = old + sign*filled
                gross += (abs(inventory[inst])-abs(old))*reference_prices.get(inst, 0)
        self.state.risk["current_gross_notional_usd"] = gross
        self.state.risk["desired_gross_notional_usd"] = desired_gross
        self.state.risk["target_scale"] = scale
        if any(float(a.get("filled_amount") or 0) > 0 for a in actions):
            try:
                self.state.positions = c.positions("USDC")
            except Exception as exc:
                fatal.append(f"post_order_positions_unavailable: {exc}")
        return self._finish(actions, market_data, fatal)

    def _submit(self, client: DeribitClient, plan, action: dict) -> float:
        label = f"sup_{action['asset'].lower()}_{uuid.uuid4().hex[:18]}"[:32]
        self.state.pending_order = {"asset": action["asset"], "instrument": action["instrument"],
                                    "label": label, "direction": plan.direction, "requested_amount": plan.amount,
                                    "price": action["limit_price"], "submitted_at": utcnow()}
        # Snapshot before-position for precise FIFO P&L basis
        inst = action["instrument"]
        if inst not in self._before_positions:
            self._before_positions[inst] = {"quantity": float(action.get("current_size") or 0), "average_price": None}
        self._save()  # persist BEFORE crossing the execution boundary
        try:
            result = client.limit_ioc(action["instrument"], plan.direction, plan.amount, action["limit_price"],
                                      label=label, reduce_only=plan.reduce_only)
            fill = fill_summary(result)
            action.update(fill)
            action["label"] = label
            action["native_trades"] = result.get("trades") or []
            filled = fill["filled_amount"]
            if filled > 0:
                complete = filled + 1e-12 >= plan.amount
                action["status"] = ("closed" if plan.intent == "close" else "reduced" if plan.reduce_only else
                                    "bought" if plan.direction == "buy" else "sold") if complete else "partially_filled"
                action["amount"] = filled
                action["price"] = fill.get("average_price") or action["limit_price"]
                action["notional_usd"] = filled*action["price"]
                action["reason"] = "Confirmed exchange fill" if complete else "Confirmed partial fill; unfilled IOC remainder cancelled"
                self._log_fill(action)
            else:
                action.update(status="unfilled", reason="Order acknowledged but filled_amount=0; NOT counted as a trade")
            if fill["order_state"] in ("filled", "cancelled", "rejected"):
                self.state.pending_order = None
            else:
                action.update(status="order_pending", reason="Awaiting terminal IOC state; further entries blocked")
            self._backoffs.pop(action["instrument"], None)
            return filled
        except Exception as exc:
            ambiguous = not isinstance(exc, DeribitAPIError) or exc.code in ("transport", "protocol", 500, 502, 503, 504, 10001)
            action.update(status="execution_uncertain" if ambiguous else "error", error=str(exc),
                          error_code=getattr(exc, "code", None), reason="No blind retry or fallback close")
            if not ambiguous:
                self.state.pending_order = None
                self._backoffs[action["instrument"]] = {"retry_after": time.time()+600, "error": str(exc)}
            self.state.push("error", f"order {action['asset']}: {exc}")
            return 0.0
        finally:
            self._save()

    def _log_fill(self, action: dict):
        order_id = action.get("order_id")
        if order_id and any(o.get("order_id") == order_id for o in self.state.orders_log):
            return
        self.state.last_fill_at = utcnow()
        entry = {"ts": self.state.last_fill_at, **{k: action.get(k) for k in (
            "asset", "instrument", "status", "amount", "price", "notional_usd", "order_id", "order_state",
            "filled_amount", "trade_ids", "fee", "fee_currencies", "direction", "reduce_only", "label", "test_id")}}
        self.state.orders_log = (self.state.orders_log+[entry])[-300:]
        # Financial reporting — separate accounting with before-position basis
        try:
            before = self._before_positions.get(action.get("instrument")) if hasattr(self, "_before_positions") else None
            # Enrich with fee_known flag for precise accounting
            enriched = {
                **entry,
                "fee_known": bool(entry.get("trade_ids")) and entry.get("fee_currencies") == ["USDC"],
                "owned": str(entry.get("label") or "").startswith(("sup_", "zt60e_", "zt60x_", "zenith")),
                "native_trades": action.get("native_trades") or [],
            }
            self.financial.record(enriched, before)
        except Exception as exc:
            log.warning("financial record failed: %s", type(exc).__name__)

    def _finish(self, actions: list, market_data: dict, fatal: list) -> dict:
        self.state.actions = actions
        counts = dict(Counter(a["status"] for a in actions))
        open_count = sum(d["market_state"] == "open" for d in market_data.values())
        halted_count = sum(d["market_state"] == "halted" for d in market_data.values())
        active_count = sum(abs(float(b.get("target_coin") or 0)) > 0 for b in self.state.net_book.values())
        blockers = {k: v for k, v in counts.items() if k not in {
            "no_signal", "target_reached", "skip_small", "bought", "sold", "closed", "reduced", "partially_filled"}}
        severe = {"error", "execution_uncertain", "order_pending", "safety_blocked", "execution_validation_error",
                  "instrument_unavailable", "market_api_error", "market_unknown", "candle_api_error", "order_error_backoff", "risk_cap_blocked"}
        any_fill = any(float(a.get("filled_amount") or 0) > 0 for a in actions)
        usable = sum(a.get("can_execute", False) or a["status"] in ("no_signal", "target_reached", "skip_small")
                     for a in actions if a.get("market_state") == "open")
        primary = ""
        if fatal:
            primary = fatal[0].split(":", 1)[0]
        elif self.state.pending_order:
            primary = "execution_uncertain"
        elif self.state.risk.get("drawdown_latched"):
            primary = "drawdown_guard"
        elif not self.settings.trading_enabled:
            primary = "trading_disabled"
        elif halted_count == len(self.settings.asset_list):
            primary = "venue_halted"
        elif open_count == 0:
            # API/protocol failures are operational errors, NOT an exchange maintenance halt.
            primary = next((k for k in ("market_api_error", "instrument_unavailable", "market_unknown") if counts.get(k)),
                           "no_open_markets")
        elif active_count and not any(
                abs(float(a.get("desired_coin") or 0)) > 0 and
                (a.get("can_execute") or a["status"] in ("target_reached", "skip_small"))
                for a in actions):
            active_blockers = Counter(a["status"] for a in actions if abs(float(a.get("desired_coin") or 0)) > 0)
            primary = active_blockers.most_common(1)[0][0] if active_blockers else "no_eligible_active_signals"
        elif any(a["status"] == "unfilled" for a in actions) and not any_fill:
            primary = "unfilled_orders"
        elif not usable and blockers:
            primary = max(blockers, key=blockers.get)
        elif any(k in severe for k in blockers):
            primary = next(k for k in blockers if k in severe)
        ready = not primary and usable > 0 and not self.settings.dry_run
        trading_state = "blocked" if primary else "dry_run" if self.settings.dry_run else "filled" if any_fill else "ready" if active_count else "waiting_for_signal"
        if self._previous_open_count == 0 and open_count > 0:
            self.state.push("recovery", f"Venue reopened {open_count} markets; fresh quotes/data/risk checks still required")
        self._previous_open_count = open_count
        self.state.last_loop_at = utcnow()
        self.state.loop_count += 1
        self.state.last_error = " | ".join(fatal) if fatal else None
        self.state.diagnostics = {
            "trading_ready": ready, "trading_state": trading_state, "primary_blocker": primary or None,
            "execution_environment": "dry-run" if self.settings.dry_run else self._environment(),
            "market_open_count": open_count, "market_halted_count": halted_count,
            "active_signal_assets": active_count, "action_counts": counts, "blocker_counts": blockers,
            "fatal_errors": fatal, "last_fill_at": self.state.last_fill_at,
            "confirmed_fill_count": len(self.state.orders_log),
            "recovery": "Automatic recheck every cycle; never change exchange or switch to real money",
        }
        self.state.push("loop", f"cycle {self.state.loop_count}: trading={trading_state} blocker={primary or 'none'} open={open_count} active={active_count} fills={int(any_fill)}")
        if time.time()-self._last_review >= self.settings.ops_review_seconds:
            review = {"at": utcnow(), **self.state.diagnostics,
                      "gross_notional_usd": self.state.risk.get("current_gross_notional_usd"),
                      "drawdown_pct": self.state.risk.get("drawdown_pct")}
            self.state.ops_reviews = (self.state.ops_reviews+[review])[-24:]
            self._last_review = time.time()
            log.info("hourly operational review: %s", json.dumps(review, ensure_ascii=False))
        out = {"sleeves": self.state.sleeves, "net_book": self.state.net_book, "actions": actions,
               "account": self.state.account, "diagnostics": self.state.diagnostics, "risk": self.state.risk,
               "recovered_fills": self._recovered_fills}
        try:
            reporter = get_reporter()
            if reporter:
                reporter.on_cycle(out, loop_count=self.state.loop_count)
        except Exception:
            log.warning("telegram cycle notification unavailable", exc_info=True)
        return out


# Backwards-compatible helpers for old smoke scripts, now always floor quantities.
def _round_to_step(x: float, step: float) -> float:
    return floor_amount(x, step)


def _clean_amt(x: float) -> float:
    return float(f"{float(x):.10f}")


def _round_amt(x: float, step: float, min_amt: float) -> float:
    return floor_amount(x, step if step > 0 else min_amt)


_engine: Optional[TradingEngine] = None


def get_engine() -> TradingEngine:
    global _engine
    if _engine is None:
        _engine = TradingEngine()
    return _engine
