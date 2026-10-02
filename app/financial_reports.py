"""Read-only financial reporting with exact capital and P&L.

- Separate from execution; never holds execution lock.
- Uses Decimal for USDC.
- Missing fee/funding/average-price evidence stays None, never fabricated zero.
- FIFO accounting per instrument.
- Provides per-trade events and 6h comprehensive file snapshot.
"""
from __future__ import annotations

import copy
import json
import logging
import queue
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
from decimal import Decimal, localcontext, ROUND_HALF_UP
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from app.deribit import DeribitClient
from app.execution import signed_position

log = logging.getLogger("financial-reports")
ZERO = Decimal(0)
EPS = Decimal("0.0000000001")
OWN_PREFIXES = ("sup_", "zt60e_", "zt60x_", "zenith")


def dec(v):
    if v is None:
        return None
    try:
        d = Decimal(str(v))
        return d if d.is_finite() else None
    except Exception:
        return None


def num(v):
    return float(v) if v is not None else None


def fmt_money(v, signed=False):
    d = dec(v)
    if d is None:
        return "نامشخص"
    q = Decimal("0.00000001")
    with localcontext() as ctx:
        ctx.prec = 28
        d = d.quantize(q, rounding=ROUND_HALF_UP)
    return format(d, "+.8f" if signed else ".8f")


def ts_ms(v=None):
    if isinstance(v, (int, float)):
        return int(v if v > 1e11 else v * 1000)
    try:
        return int(datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp() * 1000)
    except Exception:
        return int(time.time() * 1000)


def iso_ms(ms):
    try:
        return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def schedule_window(now=None, tz_name="Europe/Istanbul"):
    now_dt = datetime.fromtimestamp(now or time.time(), timezone.utc).astimezone(ZoneInfo(tz_name))
    end = now_dt.replace(hour=(now_dt.hour // 6) * 6, minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=6)
    return {
        "start_ms": int(start.timestamp() * 1000),
        "end_ms": int(end.timestamp() * 1000),
        "slot_label": end.strftime("%Y-%m-%d %H:%M %Z"),
        "next_at": (end + timedelta(hours=6)).isoformat(),
        "start_utc": start.astimezone(timezone.utc).isoformat(),
        "end_utc": end.astimezone(timezone.utc).isoformat(),
    }


class FinancialJournal:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.data = {"orders": {}, "transactions": {}, "notifications": {}, "delivery": {}, "version": 3}
        try:
            saved = json.loads(self.path.read_text())
            if saved.get("version") == 3:
                self.data.update(saved)
        except Exception:
            pass
        self._rev = 0
        self._cache_rev = -1
        self._cache_events: List[dict] = []
        self._positions: Dict[str, dict] = {}  # instrument -> {q: Decimal, avg: Decimal|None, fee: Decimal, fee_known: bool, basis: str}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, default=str))
        tmp.replace(self.path)

    def _bump(self):
        self._rev += 1
        self._save()

    def register(self, action: dict, before: Optional[dict] = None):
        oid = action.get("order_id")
        amt = dec(action.get("filled_amount") or action.get("amount"))
        if not oid or amt is None or amt <= 0:
            return None
        with self._lock:
            prev = self.data["orders"].get(oid) or {}
            old_amt = dec(prev.get("filled_amount")) or ZERO
            if amt < old_amt:
                return None
            trades = action.get("native_trades") or []
            t_times = [t.get("timestamp") for t in trades if t.get("timestamp")]
            recorded = action.get("fill_timestamp") or (min(t_times) if t_times else action.get("ts") or action.get("timestamp"))
            fee_cur = action.get("fee_currencies") or []
            fee_known = action.get("fee_known")
            if fee_known is None:
                fee_known = bool(action.get("trade_ids")) and "fee" in action and fee_cur == ["USDC"]
            row = {
                **prev,
                "order_id": oid,
                "instrument": action.get("instrument") or action.get("instrument_name"),
                "asset": action.get("asset") or str(action.get("instrument") or "").split("_")[0],
                "direction": action.get("direction"),
                "filled_amount": num(amt),
                "price": action.get("price") or action.get("average_price"),
                "fee": action.get("fee") if fee_known else prev.get("fee"),
                "fee_known": bool(fee_known) or bool(prev.get("fee_known")),
                "fee_currencies": fee_cur or prev.get("fee_currencies", []),
                "trade_ids": list(dict.fromkeys((prev.get("trade_ids") or []) + (action.get("trade_ids") or []))),
                "label": action.get("label") or prev.get("label") or "",
                "status": action.get("status") or prev.get("status"),
                "reduce_only": bool(action.get("reduce_only")),
                "test_id": action.get("test_id") or prev.get("test_id"),
                "timestamp": prev.get("timestamp") or ts_ms(recorded),
                "owned": action.get("owned", True),
                "order_state": action.get("order_state"),
                "before": prev.get("before") or before,
            }
            self.data["orders"][oid] = row
            self._bump()
            if amt > old_amt:
                return {"order_id": oid, "quantity_level": str(amt), "previous_quantity": num(old_amt)}
            return None

    def import_trades(self, trades: List[dict]):
        grouped = defaultdict(list)
        for t in trades:
            if t.get("order_id") and t.get("trade_id"):
                grouped[str(t["order_id"])].append(t)
        for oid, raw in grouped.items():
            raw = list({str(x["trade_id"]): x for x in raw}.values())
            q = sum((dec(x.get("amount")) or ZERO for x in raw), ZERO)
            if q <= 0:
                continue
            quote = sum((dec(x.get("amount")) * dec(x.get("price")) for x in raw if dec(x.get("amount")) is not None and dec(x.get("price")) is not None), ZERO)
            fees_known = all("fee" in x and x.get("fee_currency") == "USDC" for x in raw)
            label = next((x.get("label") for x in raw if x.get("label")), "")
            inst = raw[0].get("instrument_name") or ""
            self.register({
                "order_id": oid,
                "instrument": inst,
                "direction": raw[0].get("direction"),
                "filled_amount": num(q),
                "price": num(quote / q) if q else None,
                "fee": num(sum((dec(x.get("fee")) or ZERO for x in raw), ZERO)),
                "fee_known": fees_known,
                "fee_currencies": sorted({x.get("fee_currency") for x in raw if x.get("fee_currency")}),
                "trade_ids": [x["trade_id"] for x in raw],
                "label": label,
                "reduce_only": any(x.get("reduce_only") for x in raw),
                "native_trades": raw,
                "owned": str(label).startswith(OWN_PREFIXES),
                "status": "history_fill",
            })

    def import_transactions(self, logs: List[dict]):
        allowed = ("id", "currency", "timestamp", "type", "instrument_name", "order_id", "trade_id", "side", "amount", "commission", "cashflow", "change", "balance", "equity", "interest_pl", "profit_as_cashflow")
        with self._lock:
            for lg in logs:
                if lg.get("id") is not None and lg.get("currency") == "USDC":
                    self.data["transactions"][str(lg["id"])] = {k: lg.get(k) for k in allowed if k in lg}
            self._bump()

    def notification_sent(self, key: str) -> bool:
        with self._lock:
            return key in self.data["notifications"]

    def mark_notification(self, key: str):
        with self._lock:
            self.data["notifications"][key] = iso_ms(ts_ms())
            self._save()

    def delivery(self):
        with self._lock:
            return copy.deepcopy(self.data["delivery"])

    def mark_delivery(self, **fields):
        with self._lock:
            self.data["delivery"].update(fields)
            self._save()

    def events(self) -> List[dict]:
        with self._lock:
            if self._cache_rev == self._rev:
                return copy.deepcopy(self._cache_events)
            with localcontext() as ctx:
                ctx.prec = 40
                orders = sorted(self.data["orders"].values(), key=lambda x: (x["timestamp"], x["order_id"]))
                books: Dict[str, dict] = {}
                events: List[dict] = []
                for o in orders:
                    inst = o.get("instrument")
                    if not inst or "_USDC-" not in inst:
                        continue
                    b = books.setdefault(inst, {"q": ZERO, "avg": None, "fee": ZERO, "fee_known": True, "basis": "execution_fills"})
                    before = o.get("before") or {}
                    exp_q = dec(before.get("quantity"))
                    if str(o.get("label") or "").startswith("zt60e_") and exp_q is None:
                        exp_q = ZERO
                    if exp_q is not None and abs(exp_q - b["q"]) > EPS:
                        b.update(q=exp_q, avg=dec(before.get("average_price")), fee=ZERO, fee_known=(exp_q == 0), basis="exchange_position_snapshot" if exp_q != 0 else "execution_fills")
                    amt = dec(o.get("filled_amount"))
                    price = dec(o.get("price"))
                    if amt is None or amt <= 0 or price is None or price <= 0 or o.get("direction") not in ("buy", "sell"):
                        continue
                    sign = Decimal(1) if o["direction"] == "buy" else Decimal(-1)
                    fee = dec(o.get("fee")) if o.get("fee_known") and o.get("fee_currencies") == ["USDC"] else None
                    q_before = b["q"]
                    avg_before = b["avg"]
                    closed_q = ZERO
                    gross = None
                    fee_alloc = None
                    exit_fee = None
                    pnl_fee = None
                    is_close_hint = o.get("reduce_only") or o.get("status") in ("closed", "reduced", "test_closed_fill") or str(o.get("label") or "").startswith("zt60x_")
                    if q_before == 0 and is_close_hint:
                        kind = "close_unknown_basis"
                        closed_q = amt
                        q_after = ZERO
                        b.update(q=ZERO, avg=None, fee=ZERO, fee_known=True, basis="execution_fills")
                    elif q_before == 0 or q_before * sign > 0:
                        kind = "open" if q_before == 0 else "increase"
                        q_after = q_before + sign * amt
                        if q_before == 0:
                            b["avg"] = price
                        else:
                            if avg_before is not None:
                                b["avg"] = (abs(q_before) * avg_before + amt * price) / abs(q_after)
                            else:
                                b["avg"] = None
                        b["q"] = q_after
                        if fee is None:
                            b["fee_known"] = False
                        else:
                            b["fee"] += fee
                        pnl_fee = -fee if fee is not None else None
                    else:
                        closed_q = min(abs(q_before), amt)
                        ratio = closed_q / abs(q_before) if abs(q_before) > 0 else ZERO
                        if avg_before is not None:
                            gross = closed_q * (price - avg_before) * (Decimal(1) if q_before > 0 else Decimal(-1))
                        fee_alloc = b["fee"] * ratio if b["fee_known"] else None
                        exit_fee = fee * (closed_q / amt) if fee is not None else None
                        if gross is not None and fee_alloc is not None and exit_fee is not None:
                            pnl_fee = gross - fee_alloc - exit_fee
                        q_after = q_before + sign * amt
                        if abs(q_after) <= EPS:
                            kind = "close"
                            b.update(q=ZERO, avg=None, fee=ZERO, fee_known=True, basis="execution_fills")
                        elif q_before * q_after > 0:
                            kind = "reduce"
                            b["fee"] *= (Decimal(1) - ratio)
                            b["q"] = q_after
                        else:
                            kind = "reverse"
                            # remaining flips side
                            b.update(avg=price, fee=(fee * (abs(q_after) / amt) if fee is not None else ZERO), fee_known=fee is not None, q=q_after, basis="execution_fills")
                    # Funding: sum interest_pl from transaction log matching trade_ids or instrument+time
                    funding = ZERO
                    funding_known = True
                    try:
                        # Match via trade_id if available
                        tids = set(o.get("trade_ids") or [])
                        if tids:
                            for tx in self.data.get("transactions", {}).values():
                                if tx.get("trade_id") in tids and tx.get("instrument_name") == inst:
                                    ip = dec(tx.get("interest_pl"))
                                    if ip is not None:
                                        funding += ip
                                    else:
                                        # If any matching tx has no interest_pl field, we don't know funding
                                        if tx.get("interest_pl") is None and "interest_pl" not in tx:
                                            funding_known = False
                        else:
                            # No trade_ids, try to find funding for instrument around event time (within 1h)
                            # This is best-effort, funding may stay unknown
                            funding_known = False
                    except Exception:
                        funding_known = False

                    # Net final = price+fees + funding (if known)
                    net_final = None
                    funding_val = None
                    if pnl_fee is not None:
                        if funding_known:
                            net_final = pnl_fee + funding
                            funding_val = funding
                        else:
                            # Funding unknown, keep price+fees only but mark quality
                            net_final = pnl_fee
                            funding_val = None

                    ev = {
                        **copy.deepcopy(o),
                        "kind": kind,
                        "quantity_before": num(q_before),
                        "quantity_after": num(q_after),
                        "entry_price": num(avg_before),
                        "execution_notional_usdc": num(amt * price),
                        "position_notional_at_fill_usdc": num(abs(q_after) * price),
                        "closed_quantity": num(closed_q),
                        "position_side": "LONG" if (q_before if closed_q else sign) > 0 else "SHORT",
                        "realized_gross_usdc": num(gross),
                        "entry_fee_allocated_usdc": num(fee_alloc),
                        "exit_fee_usdc": num(exit_fee),
                        "paid_fee_usdc": num(fee),
                        "net_price_fees_usdc": num(pnl_fee),
                        "funding_usdc": num(funding_val) if funding_known else None,
                        "funding_known": funding_known,
                        "net_final_usdc": num(net_final),
                        "basis_source": b["basis"],
                        "pnl_quality": "complete" if (pnl_fee is not None and funding_known) or kind in ("open", "increase") else "incomplete_entry_or_fee_or_funding_evidence" if not funding_known else "incomplete_entry_or_fee_evidence",
                        "source": "manual_test" if o.get("test_id") or str(o.get("label") or "").startswith(("zt60e_", "zt60x_")) else "bot" if o.get("owned") else "external/manual",
                    }
                    if closed_q and avg_before is not None and avg_before != 0:
                        entry_basis = closed_q * avg_before
                        ev["return_pct_price_fees"] = num((pnl_fee / entry_basis * Decimal(100)) if pnl_fee is not None else None)
                        if net_final is not None:
                            ev["return_pct_final"] = num((net_final / entry_basis * Decimal(100)) if net_final is not None else None)
                    events.append(ev)
                self._cache_events = events
                self._cache_rev = self._rev
                return copy.deepcopy(events)

    def event(self, order_id: str) -> Optional[dict]:
        for e in self.events():
            if e["order_id"] == order_id:
                return e
        return None


class FinancialReporting:
    def __init__(self, engine):
        self.engine = engine
        self.journal = FinancialJournal(Path(engine.settings.state_path).with_suffix(".financial.json"))
        self._queue = queue.Queue()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._read_client: Optional[DeribitClient] = None
        self._lock = threading.RLock()
        self.live = {"account": {}, "positions": [], "observed_at": None}
        self.status = {"schema": "financial-reports-v3", "history_synced": False, "history_complete": False, "errors": [], "last_refresh_at": None, "timezone": engine.settings.report_timezone}
        self._last_sync = 0.0

    def start(self, notifier=None):
        self._notifier = notifier
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="financial-reporting", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._read_client:
            try:
                self._read_client.close()
            except Exception:
                pass
            self._read_client = None

    def _client(self) -> DeribitClient:
        if self._read_client is None:
            s = self.engine.settings
            if not s.deribit_client_id or not s.deribit_client_secret:
                raise RuntimeError("Reporting credentials not configured")
            self._read_client = DeribitClient(s.deribit_base_url, s.deribit_client_id, s.deribit_client_secret, timeout=12)
        return self._read_client

    def record(self, action: dict, before: Optional[dict] = None):
        key = self.journal.register(action, before)
        if key:
            self._queue.put(key)
        return self.journal.event(action.get("order_id"))

    def refresh(self) -> DeribitClient:
        c = self._client()
        acct = c.account_summary("USDC")
        poss = c.positions("USDC")
        if not isinstance(poss, list):
            raise RuntimeError("Invalid positions response")
        with self._lock:
            self.live = {
                "account": {"equity": acct.get("equity"), "balance": acct.get("balance"), "available": acct.get("available_funds")},
                "positions": poss,
                "observed_at": iso_ms(ts_ms()),
            }
            self.status["last_refresh_at"] = self.live["observed_at"]
        return c

    def sync_history(self):
        c = self.refresh()
        now = int(time.time() * 1000)
        start = now - self.engine.settings.report_history_hours * 3600000
        trades, more = c.user_trades_window(start, now)
        allowed = {self.engine.settings.instrument_for(a) for a in self.engine.settings.asset_list}
        trades = [t for t in trades if t.get("instrument_name") in allowed]
        self.journal.import_trades(trades)
        try:
            logs, tx_complete = c.transaction_window(start, now)
            self.journal.import_transactions(logs)
        except Exception as exc:
            with self._lock:
                self.status["errors"] = [str(exc)]
            tx_complete = False
        with self._lock:
            self.status.update(history_synced=True, history_complete=not more, funding_log_complete=tx_complete)
        self._last_sync = time.time()

    def _run(self):
        try:
            self.sync_history()
        except Exception as exc:
            with self._lock:
                self.status["errors"] = [str(exc)[:500]]
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                if time.time() - self._last_sync >= 900:
                    try:
                        self.sync_history()
                    except Exception as exc:
                        self._last_sync = time.time()
                        with self._lock:
                            self.status["errors"] = [str(exc)[:500]]
                continue
            key = f"{item['order_id']}:{item['quantity_level']}"
            if self.journal.notification_sent(key):
                continue
            # Try to enrich fee with live client, but notification must work even if refresh fails
            c = None
            try:
                c = self.refresh()
            except Exception as exc:
                with self._lock:
                    self.status["errors"] = [str(exc)[:500]]
            try:
                ev = self.journal.event(item["order_id"])
                if ev and c and (not ev.get("fee_known") or not ev.get("trade_ids")):
                    try:
                        self.journal.import_trades(c.trades_by_order(item["order_id"]))
                    except Exception:
                        pass
                    ev = self.journal.event(item["order_id"])
                if ev:
                    ev["execution_update_quantity"] = num((dec(ev["filled_amount"]) or ZERO) - (dec(item.get("previous_quantity")) or ZERO))
                    if self._notifier:
                        try:
                            if self._notifier(ev, self.snapshot()):
                                self.journal.mark_notification(key)
                                continue
                        except Exception as exc:
                            log.warning("financial notifier failed: %s", exc)
                            with self._lock:
                                self.status["errors"] = [str(exc)[:500]]
                # If notifier returned False (e.g. telegram disabled locally), still mark as attempted to avoid infinite loop blocking new trades
                # but re-queue with backoff for production retry
            except Exception as exc:
                with self._lock:
                    self.status["errors"] = [str(exc)[:500]]
            # Backoff retry, but don't block forever - if notifier keeps returning False, we still retry
            self._stop.wait(5)
            self._queue.put(item)

    def snapshot(self, start_ms: Optional[int] = None, end_ms: Optional[int] = None) -> dict:
        with self._lock:
            live = copy.deepcopy(self.live)
            status = copy.deepcopy(self.status)
        if not live.get("observed_at"):
            acct = self.engine.state.account.get("USDC") or {}
            live = {"account": acct, "positions": copy.deepcopy(self.engine.state.positions), "observed_at": self.engine.state.last_loop_at}
        events = self.journal.events()
        now = int(time.time() * 1000)
        if start_ms is None:
            start_ms = now - 6 * 3600000
        if end_ms is None:
            end_ms = now
        period = [e for e in events if start_ms <= e["timestamp"] < end_ms]
        closes = [e for e in period if (e.get("closed_quantity") or 0) > 0]
        verified = [e for e in closes if e.get("net_final_usdc") is not None]
        positions = [p for p in live.get("positions", []) if abs(float(p.get("size_currency") or 0)) > 1e-12]
        unreal_vals = [dec(p.get("floating_profit_loss")) for p in positions]
        unreal = sum(unreal_vals, ZERO) if all(v is not None for v in unreal_vals) else None
        fees = [dec(e.get("paid_fee_usdc")) for e in period]
        return {
            "schema": "financial-reports-v3",
            "timezone": self.engine.settings.report_timezone,
            "window": {"start_ms": start_ms, "end_ms": end_ms, "start_utc": iso_ms(start_ms), "end_utc": iso_ms(end_ms)},
            "allocated_capital_usdc": self.engine.settings.capital_usd,
            "estimated_bot_equity_usdc": self.engine.state.risk.get("estimated_bot_equity"),
            "account": live.get("account") or {},
            "account_observed_at": live.get("observed_at"),
            "positions": positions,
            "open_notional_usdc": sum(abs(float(p.get("size_currency") or 0)) * float(p.get("mark_price") or 0) for p in positions),
            "initial_margin_usdc": sum(float(p.get("initial_margin") or 0) for p in positions),
            "unrealized_gross_usdc": num(unreal),
            "execution_count": len(period),
            "closed_event_count": len(closes),
            "verified_closed_net_usdc": num(sum((dec(e["net_final_usdc"]) for e in verified), ZERO)),
            "period_fees_usdc": num(sum(fees, ZERO)) if all(x is not None for x in fees) else None,
            "wins": sum(1 for e in verified if (e.get("net_final_usdc") or 0) > 0),
            "losses": sum(1 for e in verified if (e.get("net_final_usdc") or 0) < 0),
            "events": period,
            "all_events": events,
            "status": status,
            "delivery": self.journal.delivery(),
            "next_periodic_report_at": schedule_window(tz_name=self.engine.settings.report_timezone)["next_at"],
            "scope_note": "PnL uses actual fills and allocated entry fees + exit fees. Funding is included only when native transaction evidence is complete. Account equity != bot $100 allocation.",
        }
