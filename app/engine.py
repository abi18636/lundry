from __future__ import annotations

import json
import logging
import math
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import Settings, get_settings
from app.deribit import DeribitClient
from app.sleeves import parse_weights, prepare_frames, run_all_sleeves
from app.telegram_bot import get_reporter

log = logging.getLogger("engine")


def _round_to_step(x: float, step: float) -> float:
    if step is None or step <= 0:
        return max(float(x), 0.0)
    n = int(float(x) / float(step) + 1e-12)
    return max(n * float(step), 0.0)


def _clean_amt(x: float) -> float:
    return float(f"{float(x):.10f}")



def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class BotState:
    running: bool = False
    last_loop_at: Optional[str] = None
    last_error: Optional[str] = None
    loop_count: int = 0
    sleeves: List[dict] = field(default_factory=list)
    net_book: Dict[str, Any] = field(default_factory=dict)
    positions: List[dict] = field(default_factory=list)
    account: Dict[str, Any] = field(default_factory=dict)
    orders_log: List[dict] = field(default_factory=list)
    events: List[dict] = field(default_factory=list)
    actions: List[dict] = field(default_factory=list)
    mode: str = "init"

    def push(self, kind: str, msg: str, **extra):
        self.events.append({"ts": utcnow(), "kind": kind, "msg": msg, **extra})
        self.events = self.events[-300:]


class TradingEngine:
    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self.state = BotState()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.client: Optional[DeribitClient] = None
        self._lock = threading.Lock()
        Path(self.settings.state_path).parent.mkdir(parents=True, exist_ok=True)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.state.running = True
        self.state.mode = "starting"
        self._thread = threading.Thread(target=self._loop, name="superbot-loop", daemon=True)
        self._thread.start()
        self.state.push("info", "SUPER bot engine started")

    def stop(self):
        self._stop.set()
        self.state.running = False
        self.state.mode = "stopped"
        self.state.push("info", "engine stop requested")

    def snapshot(self) -> dict:
        with self._lock:
            s = self.settings
            return {
                "bot": "zenith-SUPER",
                "running": self.state.running,
                "mode": self.state.mode,
                "last_loop_at": self.state.last_loop_at,
                "last_error": self.state.last_error,
                "loop_count": self.state.loop_count,
                "sleeves": self.state.sleeves,
                "net_book": self.state.net_book,
                "positions": self.state.positions,
                "account": self.state.account,
                "actions": self.state.actions[-30:],
                "orders_log": self.state.orders_log[-50:],
                "events": self.state.events[-60:],
                "config": {
                    "capital_usd": s.capital_usd,
                    "lev_cap": s.lev_cap,
                    "long_only": s.long_only,
                    "dry_run": s.dry_run,
                    "trading_enabled": s.trading_enabled,
                    "assets": s.asset_list,
                    "sleeve_weights": s.sleeve_weights,
                    "sleeves_enabled": s.enabled_sleeves,
                    "base_url": s.deribit_base_url,
                    "loop_seconds": s.loop_seconds,
                    "architecture": "independent_sleeves_net_at_order_layer",
                },
            }

    def _save(self):
        try:
            Path(self.settings.state_path).write_text(
                json.dumps(self.snapshot(), indent=2, default=str)
            )
        except Exception as e:
            log.warning("state save failed: %s", e)

    def _ensure_client(self) -> DeribitClient:
        if self.client is None:
            if not self.settings.deribit_client_id or not self.settings.deribit_client_secret:
                raise RuntimeError("DERIBIT_CLIENT_ID / DERIBIT_CLIENT_SECRET not set")
            self.client = DeribitClient(
                self.settings.deribit_base_url,
                self.settings.deribit_client_id,
                self.settings.deribit_client_secret,
            )
        return self.client

    def _loop(self):
        log.info("super loop begin")
        while not self._stop.is_set():
            try:
                self.once()
                self.state.last_error = None
            except Exception as e:
                self.state.last_error = str(e)
                self.state.push("error", str(e), tb=traceback.format_exc()[-2000:])
                log.exception("loop error")
                try:
                    rep = get_reporter()
                    if rep:
                        rep.on_cycle({}, loop_count=self.state.loop_count, error=str(e))
                except Exception:
                    pass
            self._save()
            self._stop.wait(max(5, int(self.settings.loop_seconds)))
        self.state.running = False
        self.state.mode = "stopped"
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None

    def once(self) -> dict:
        with self._lock:
            return self._once_unlocked()

    def _once_unlocked(self) -> dict:
        s = self.settings
        self.state.mode = "running"
        client = self._ensure_client()

        # account
        acct = {}
        for cur in ("USDC", "BTC", "ETH"):
            try:
                acct[cur] = client.account_summary(cur)
            except Exception as e:
                acct[cur] = {"error": str(e)}
        self.state.account = {
            k: {
                "equity": (v or {}).get("equity"),
                "balance": (v or {}).get("balance"),
                "available": (v or {}).get("available_funds"),
            }
            if isinstance(v, dict) and "error" not in v
            else v
            for k, v in acct.items()
        }

        # candles (parallel for multi-asset universe)
        ohlc = {}
        def _fetch(asset):
            inst = s.instrument_for(asset)
            df = client.candles(inst, hours=s.candle_lookback_hours, resolution="60")
            return asset, inst, df
        workers = min(8, max(2, len(s.asset_list)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(_fetch, a) for a in s.asset_list]
            for fut in as_completed(futs):
                try:
                    asset, inst, df = fut.result()
                    ohlc[asset] = df
                    self.state.push("data", f"{asset} candles={len(df)} inst={inst}")
                except Exception as e:
                    self.state.push("error", f"candles: {e}")

        frames = prepare_frames(ohlc, s.asset_list)
        weights = parse_weights(s.sleeve_weights)
        enabled = s.enabled_sleeves

        sleeve_results, net_book = run_all_sleeves(
            frames,
            total_capital=float(s.capital_usd),
            weights=weights,
            lev_cap=float(s.lev_cap),
            enabled=enabled,
        )

        # serialize sleeves for dashboard
        sleeves_pub = []
        for r in sleeve_results:
            sleeves_pub.append({
                "id": r.sleeve_id,
                "title": r.title,
                "weight": r.weight,
                "capital": r.capital,
                "notes": r.notes,
                "per_asset": r.per_asset,
            })
        self.state.sleeves = sleeves_pub
        self.state.net_book = net_book

        # positions
        try:
            positions = client.positions("USDC") or []
        except Exception:
            positions = []
        self.state.positions = positions
        pos_by_inst = {p.get("instrument_name"): p for p in positions}

        actions = []
        if not s.trading_enabled:
            self.state.push("info", "trading_enabled=false — signal only SUPER book")
            self.state.actions = actions
            self.state.last_loop_at = utcnow()
            self.state.loop_count += 1
            return {"sleeves": sleeves_pub, "net_book": net_book, "actions": actions}

        # Global notional scale if sum exceeds max_notional_usd
        gross = sum(float(v.get("notional_usd") or 0) for v in net_book.values())
        scale = 1.0
        if gross > s.max_notional_usd > 0:
            scale = s.max_notional_usd / gross
            self.state.push("risk", f"scale notional {scale:.3f} gross={gross:.1f}>{s.max_notional_usd}")

        for asset, book in net_book.items():
            inst = s.instrument_for(asset)
            px = float(book["price"])
            target_coin = float(book["target_coin"]) * scale
            if s.long_only:
                target_coin = max(0.0, target_coin)

            # Venue gate: skip trading when market halted (common on Deribit testnet)
            mstate = client.market_state(inst)
            if mstate and mstate != "open":
                action = {
                    "asset": asset,
                    "instrument": inst,
                    "target_amt": 0.0,
                    "current_size": 0.0,
                    "delta": 0.0,
                    "notional_usd": float(book.get("notional_usd") or 0) * scale,
                    "contributors": book.get("contributors"),
                    "price": px,
                    "market_state": mstate,
                    "status": f"market_{mstate}",
                }
                actions.append(action)
                continue

            meta = client.instrument(inst)
            min_amt = float(meta.get("min_trade_amount") or meta.get("contract_size") or 0.001)
            step = float(meta.get("contract_size") or min_amt)
            if step <= 0:
                step = min_amt
            # If any exposure desired, enforce min notional BEFORE step floor
            if target_coin > 0 and target_coin * px < s.min_notional_usd:
                target_coin = s.min_notional_usd / px
            target_amt = _round_to_step(target_coin, step)
            if target_coin > 0 and target_amt < min_amt:
                target_amt = min_amt
            target_amt = _clean_amt(target_amt)

            cur = pos_by_inst.get(inst) or {}
            if cur.get("size_currency") is not None:
                cur_size = float(cur.get("size_currency") or 0.0)
            else:
                cur_size = float(cur.get("size") or 0.0)
            # long_only: ignore short inventory
            cur_long = max(cur_size, 0.0)
            delta = target_amt - cur_long

            action = {
                "asset": asset,
                "instrument": inst,
                "target_amt": target_amt,
                "current_size": cur_size,
                "delta": delta,
                "notional_usd": target_amt * px,
                "contributors": book.get("contributors"),
                "price": px,
            }

            # skip tiny
            if abs(delta) * px < s.min_notional_usd * 0.4 and (
                (target_amt > 0 and cur_long > 0) or (target_amt == 0 and cur_long == 0)
            ):
                action["status"] = "skip_small"
                actions.append(action)
                continue

            try:
                if target_amt <= 0 and cur_long > 0:
                    amt = _round_amt(cur_long, step, min_amt)
                    if amt * px < s.min_notional_usd * 0.25 or amt < min_amt:
                        action["status"] = "dust_ignore"
                        action["amount"] = cur_long
                    elif s.dry_run:
                        action["status"] = "dry_run_close"
                        action["amount"] = amt
                    else:
                        try:
                            res = client.sell_market(inst, amt, label=f"sup_{asset.lower()}_x")
                        except Exception:
                            res = client.close_position(inst)
                        action["status"] = "closed"
                        action["amount"] = amt
                        action["result"] = _safe(res)
                        self._log_order(action)
                elif delta > 0 and target_amt > 0:
                    amt = _round_amt(delta, step, min_amt)
                    if amt < min_amt:
                        action["status"] = "skip_min_amt"
                    elif s.dry_run:
                        action["status"] = "dry_run_buy"
                        action["amount"] = amt
                    else:
                        res = client.buy_market(inst, amt, label=f"sup_{asset.lower()}")
                        action["status"] = "bought"
                        action["amount"] = amt
                        action["result"] = _safe(res)
                        self._log_order(action)
                elif delta < 0 and cur_long > 0:
                    amt = _round_amt(min(abs(delta), cur_long), step, min_amt)
                    if amt < min_amt:
                        action["status"] = "skip_min_amt"
                    elif s.dry_run:
                        action["status"] = "dry_run_sell"
                        action["amount"] = amt
                    else:
                        res = client.sell_market(inst, amt, label=f"sup_{asset.lower()}_rd")
                        action["status"] = "reduced"
                        action["amount"] = amt
                        action["result"] = _safe(res)
                        self._log_order(action)
                else:
                    action["status"] = "flat_ok"
            except Exception as e:
                action["status"] = "error"
                action["error"] = str(e)
                self.state.push("error", f"order {asset}: {e}")

            actions.append(action)

        self.state.actions = actions
        self.state.push("loop", f"SUPER cycle sleeves={len(sleeves_pub)} actions={len(actions)}")
        self.state.last_loop_at = utcnow()
        self.state.loop_count += 1
        try:
            self.state.positions = client.positions("USDC") or []
        except Exception:
            pass
        out = {"sleeves": sleeves_pub, "net_book": net_book, "actions": actions, "account": self.state.account}
        try:
            rep = get_reporter()
            if rep:
                rep.on_cycle(out, loop_count=self.state.loop_count)
        except Exception:
            log.exception("telegram on_cycle")
        return out

    def _log_order(self, action: dict):
        self.state.orders_log.append({
            "ts": utcnow(),
            **{k: action.get(k) for k in (
                "asset", "instrument", "status", "amount", "price", "notional_usd"
            )},
        })
        self.state.orders_log = self.state.orders_log[-120:]


def _round_amt(x: float, step: float, min_amt: float) -> float:
    a = _round_to_step(x, step if step > 0 else min_amt)
    if a > 0 and a < min_amt:
        a = min_amt
    return _clean_amt(a)


def _safe(obj):
    try:
        json.dumps(obj)
        return obj
    except Exception:
        return str(obj)


_engine: Optional[TradingEngine] = None


def get_engine() -> TradingEngine:
    global _engine
    if _engine is None:
        _engine = TradingEngine()
    return _engine
