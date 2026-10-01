"""Owner-confirmed, minimum-lot Deribit TESTNET round-trip with a 60-second exit.

This is deliberately NOT a strategy signal. Normal strategy orders are paused while
one test is active. No existing position in the selected instrument is allowed.
Exchange labels support restart reconciliation; a timer cannot guarantee execution
when the exchange, liquidity, network or hosting service is unavailable.
"""
from __future__ import annotations

import copy
import math
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_UP
from urllib.parse import urlparse

from app.deribit import DeribitAPIError
from app.execution import fill_summary, floor_amount, ioc_price, signed_position

TERMINAL_ORDERS = {"filled", "cancelled", "rejected"}
ENTRY_PREFIX = "zt60e_"
EXIT_PREFIX = "zt60x_"


def iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


class TestTradeRejected(RuntimeError):
    __test__ = False

    def __init__(self, code, message, http_status=409):
        self.code, self.http_status = code, http_status
        super().__init__(message)


class TimedTestTrade:
    def __init__(self, engine, clock=None):
        self.engine = engine
        self.clock = clock or time.time
        self._thread = None
        self._shutdown = threading.Event()
        self._wake = threading.Event()
        self._force_close = threading.Event()
        self.closed_event = threading.Event()
        self.recovery_in_progress = False
        self._last_warning = 0.0
        self._last_warning_code = ""
        self._recovery_retry_at = 0.0

    @property
    def state(self):
        return self.engine.state.test_trade

    def snapshot(self):
        state = copy.deepcopy(self.state)
        due = state.get("close_due_timestamp")
        state["seconds_remaining"] = max(0, float(due)-self.clock()) if due else None
        state["overdue_seconds"] = max(0, self.clock()-float(due)) if due and state.get("active") else 0
        state["hold_seconds"] = self.engine.settings.test_trade_hold_seconds
        state["recovery_in_progress"] = self.recovery_in_progress
        return state

    def is_active(self):
        return bool(self.state.get("active"))

    def _set(self, **fields):
        self.engine.state.test_trade = {**self.state, **fields, "updated_at": iso(self.clock())}
        self.engine._save()

    def _notify(self, event):
        self.engine.notify_test_trade(event, self.snapshot())

    def _warn(self, code, reason):
        if code != self._last_warning_code or self.clock()-self._last_warning >= 300:
            self._last_warning_code, self._last_warning = code, self.clock()
            self._set(warning_code=code, warning=reason)
            self._notify("warning")

    def _ensure_worker(self):
        if self._thread and self._thread.is_alive():
            self._wake.set()
            return
        self._shutdown.clear()
        self._thread = threading.Thread(target=self._run, name="test-trade-60s", daemon=True)
        self._thread.start()

    def begin_recovery(self):
        # Called before the main strategy thread starts; no normal orders until checked.
        self.recovery_in_progress = True
        self._ensure_worker()

    def _run(self):
        while not self._shutdown.is_set():
            try:
                if self.recovery_in_progress:
                    self.recover_on_boot()
                else:
                    self.step()
            except Exception as exc:
                self._warn("timer_error", f"کنترل خروج نیازمند بررسی است: {type(exc).__name__}")
            self._wake.wait(1.0)
            self._wake.clear()

    def _reject(self, code, message):
        raise TestTradeRejected(code, message)

    def _positions(self, client):
        positions = client.positions("USDC")
        if not isinstance(positions, list):
            self._reject("positions_unavailable", "پوزیشن‌های حساب قابل تأیید نیستند؛ سفارش ارسال نشد.")
        self.engine.state.positions = positions
        gross = 0.0
        for position in positions:
            quantity = signed_position(position)
            mark = float(position.get("mark_price") or 0)
            if quantity and mark <= 0:
                self._reject("unknown_position_risk", "ارزش یکی از پوزیشن‌ها قابل تأیید نیست.")
            gross += abs(quantity) * mark
        self.engine.state.risk = {**self.engine.state.risk, "current_gross_notional_usd": gross}
        return positions

    def _quantity(self, positions, instrument):
        position = next((p for p in positions if p.get("instrument_name") == instrument), None)
        return signed_position(position)

    def _fresh_book(self, client, instrument, opening):
        book = client.order_book(instrument, 5)
        if book.get("state") != "open":
            self._reject("market_not_open", f"بازار {instrument} باز نیست ({book.get('state') or 'unknown'}).")
        age = self.clock()-float(book.get("timestamp") or 0)/1000
        if age > self.engine.settings.market_data_max_age_seconds or age < -10:
            self._reject("stale_quote", "دادهٔ دفتر سفارش تازه نیست؛ تست انجام نشد.")
        bids, asks = book.get("bids") or [], book.get("asks") or []
        if not bids:
            self._reject("no_exit_liquidity", "خریدار قابل اجرا برای خروج وجود ندارد.")
        if opening:
            if not asks:
                self._reject("no_entry_liquidity", "فروشنده قابل اجرا برای ورود وجود ندارد.")
            bid, ask = float(bids[0][0]), float(asks[0][0])
            spread = (ask-bid)/((ask+bid)/2)*10000 if ask >= bid > 0 else math.inf
            if spread > self.engine.settings.max_spread_bps:
                self._reject("wide_spread", "اسپرد برای تست کم‌ریسک بیش از حد مجاز است.")
        if getattr(client, "last_testnet", None) is not True:
            self._reject("testnet_not_confirmed", "پاسخ صرافی باید صریحاً testnet=true باشد.")
        return book

    def start(self, background=True):
        if not self.engine._cycle_lock.acquire(blocking=False):
            self._reject("engine_busy", "یک عملیات دیگر در حال اجراست؛ چند ثانیه بعد دوباره تأیید کنید.")
        notify = None
        try:
            s = self.engine.settings
            if self.is_active() or self.recovery_in_progress:
                self._reject("already_active", "تست قبلی یا بررسی بازیابی هنوز فعال است؛ تست تکراری ایجاد نمی‌شود.")
            if not s.test_trade_enabled:
                self._reject("disabled", "قابلیت معامله تست غیرفعال است.")
            if urlparse(s.deribit_base_url).hostname != "test.deribit.com" or not s.use_usdc_linear:
                self._reject("testnet_only", "این دکمه فقط برای Deribit testnet و USDC linear است.")
            if s.dry_run or not s.trading_enabled or self.engine._stop.is_set():
                self._reject("execution_disabled", "اجرای واقعی تست غیرفعال است؛ ابتدا وضعیت ربات را بررسی کنید.")
            if self.engine.state.pending_order:
                self._reject("unconfirmed_order", "نتیجه سفارش قبلی نامشخص است؛ ابتدا تطبیق سفارش لازم است.")
            client = self.engine._ensure_client()
            asset = s.test_trade_asset.upper()
            if asset not in s.asset_list:
                self._reject("asset_not_authorized", "نماد تست باید در فهرست مجاز ربات باشد.")
            instrument = s.instrument_for(asset)
            meta = client.instrument(instrument)
            if meta.get("instrument_type") != "linear" or meta.get("settlement_currency") != "USDC":
                self._reject("instrument_not_authorized", "نماد تست USDC linear نیست.")
            book = self._fresh_book(client, instrument, opening=True)
            positions = self._positions(client)
            if abs(self._quantity(positions, instrument)) > 1e-12:
                self._reject("existing_position", "روی نماد تست پوزیشن قبلی وجود دارد؛ برای حفظ آن، تست رد شد.")
            orders = client.open_orders()
            if not isinstance(orders, list):
                self._reject("orders_unavailable", "سفارش‌های باز قابل تأیید نیستند.")
            if any(o.get("instrument_name") == instrument for o in orders):
                self._reject("existing_order", "روی نماد تست سفارش باز قبلی وجود دارد.")
            acct = client.account_summary("USDC")
            account = {**self.engine.state.account, "USDC": {
                "equity": acct.get("equity"), "balance": acct.get("balance"), "available": acct.get("available_funds")}}
            self.engine.state.account = account
            if not self.engine._update_risk(account):
                self._reject("account_unavailable", "موجودی حساب قابل تأیید نیست.")
            if self.engine.state.risk.get("drawdown_latched"):
                self._reject("drawdown_guard", "محافظ افت سرمایه فعال است؛ تست جدید مجاز نیست.")
            step = float(meta.get("contract_size") or 0)
            minimum = float(meta.get("min_trade_amount") or 0)
            if step <= 0 or minimum <= 0:
                self._reject("invalid_lot", "حداقل لات صرافی معتبر نیست.")
            lots = (Decimal(str(minimum))/Decimal(str(step))).to_integral_value(rounding=ROUND_UP)
            amount = float(lots*Decimal(str(step)))
            price = ioc_price(book, "buy", meta, s.max_slippage_bps)
            notional = amount*price
            if notional > s.test_trade_max_notional_usd + 1e-8:
                self._reject("test_budget", "کوچک‌ترین لات از سقف ارزش معامله تست بیشتر است.")
            if sum(float(q[1]) for q in book.get("asks", []) if float(q[0]) <= price) < amount:
                self._reject("insufficient_depth", "عمق سفارش در سقف لغزش برای حداقل لات کافی نیست.")
            gross = 0.0
            for p in positions:
                q = signed_position(p)
                mark = float(p.get("mark_price") or 0)
                if q and mark <= 0:
                    self._reject("unknown_position_risk", "ارزش ریسک یکی از پوزیشن‌های قبلی نامشخص است.")
                gross += abs(q)*mark
            reserve = 0.0
            for order in orders:
                if order.get("reduce_only"):
                    continue
                remaining = max(0, float(order.get("amount") or 0)-float(order.get("filled_amount") or 0))
                try:
                    order_price = float(order.get("price") or 0)
                except (TypeError, ValueError):
                    order_price = 0
                if remaining and order_price <= 0:
                    self._reject("unknown_order_risk", "ریسک یک سفارش قبلی نامشخص است.")
                reserve += remaining*order_price
            if gross+reserve+notional > float(self.engine.state.risk["notional_cap_usd"])+1e-8:
                self._reject("risk_cap", "تست سقف بودجهٔ تخصیصی و اهرم ۱× را نقض می‌کند.")
            if notional > float(self.engine.state.risk.get("available_funds") or 0):
                self._reject("insufficient_funds", "موجودی آزاد برای تست کافی نیست.")
            session = uuid.uuid4().hex[:16]
            now = self.clock()
            self.closed_event.clear(); self._force_close.clear()
            self.engine.state.test_trade = {
                "id": session, "active": True, "status": "entry_pending", "environment": "testnet-live",
                "asset": asset, "instrument": instrument, "requested_at": iso(now),
                "requested_amount": amount, "reference_position": 0.0, "entry_filled_amount": 0.0,
                "exit_filled_amount": 0.0, "exit_attempts": 0, "entry": None, "exits": [],
                "max_notional_usd": s.test_trade_max_notional_usd,
                "pending": {"phase": "entry", "label": ENTRY_PREFIX+session, "sent_at": now,
                            "requested_amount": amount, "before_exit_filled": 0.0},
                "next_check_at": now,
            }
            self.engine._save()  # durable checkpoint before submitting any order
            try:
                result = client.limit_ioc(instrument, "buy", amount, price, ENTRY_PREFIX+session, reduce_only=False)
                self._consume(result, client)
                notify = "opened" if self.state.get("entry_filled_amount", 0) > 0 else "unfilled"
            except DeribitAPIError as exc:
                if exc.code not in ("transport", "protocol", 500, 502, 503, 504, 10001):
                    self._set(active=False, status="failed", pending=None, reason=str(exc), error_code=exc.code)
                    notify = "failed"
                else:
                    self._set(status="entry_uncertain", reason=str(exc), next_check_at=self.clock()+3)
                    notify = "uncertain"
            except Exception as exc:
                self._set(status="entry_uncertain", reason=type(exc).__name__, next_check_at=self.clock()+3)
                notify = "uncertain"
            if self.is_active() and background:
                self._ensure_worker()  # timer is armed BEFORE potentially slow Telegram notifications
        except TestTradeRejected as exc:
            if not self.is_active():
                self._set(active=False, status="blocked", reason=str(exc), rejection_code=exc.code)
            raise
        finally:
            self.engine._cycle_lock.release()
        if notify:
            self._notify(notify)
        return self.snapshot()

    def _record(self, fill, phase):
        action = {**fill, "asset": self.state["asset"], "instrument": self.state["instrument"],
                  "status": "test_opened" if phase == "entry" else "test_closed_fill",
                  "amount": fill["filled_amount"], "price": fill.get("average_price") or 0,
                  "notional_usd": fill["filled_amount"]*float(fill.get("average_price") or 0),
                  "direction": "buy" if phase == "entry" else "sell", "reduce_only": phase != "entry",
                  "label": (self.state.get("pending") or {}).get("label"), "test_id": self.state["id"]}
        existing = next((x for x in self.engine.state.orders_log if x.get("order_id") == fill.get("order_id")), None)
        if existing and fill["filled_amount"] > float(existing.get("filled_amount") or 0):
            updated = {**existing, **action}
            updated["trade_ids"] = list(dict.fromkeys((existing.get("trade_ids") or []) + (fill.get("trade_ids") or [])))
            if not fill.get("trade_ids"):
                updated["fee"] = existing.get("fee")
            self.engine.state.orders_log = [updated if x is existing else x for x in self.engine.state.orders_log]
        else:
            self.engine._log_fill(action)
        self.engine.state.diagnostics = {**self.engine.state.diagnostics,
            "last_fill_at": self.engine.state.last_fill_at,
            "confirmed_fill_count": len(self.engine.state.orders_log)}

    def _consume(self, result, client):
        pending = self.state.get("pending") or {}
        phase = pending.get("phase")
        fill = fill_summary(result)
        if phase == "entry":
            if fill["filled_amount"] > 0:
                opened = self.state.get("opened_timestamp")
                if opened is None:
                    opened = self.clock()
                    trade_times = [float(t.get("timestamp") or 0)/1000 for t in (result.get("trades") or [])]
                    plausible = [t for t in trade_times if abs(self.clock()-t) <= 180]
                    if not plausible:
                        order = result.get("order") or {}
                        broker_time = float(order.get("last_update_timestamp") or order.get("creation_timestamp") or 0)/1000
                        if abs(self.clock()-broker_time) <= 180:
                            plausible = [broker_time]
                    if plausible:
                        opened = min(plausible)
                self._set(entry=fill, entry_filled_amount=fill["filled_amount"], opened_timestamp=opened,
                          opened_at=iso(opened), close_due_timestamp=opened+60, close_due_at=iso(opened+60))
                self._record(fill, "entry")
        else:
            exited = float(pending.get("before_exit_filled") or 0)+fill["filled_amount"]
            self._set(exit_filled_amount=exited,
                      exits=[x for x in self.state.get("exits", []) if x.get("order_id") != fill.get("order_id")]+[fill])
            if fill["filled_amount"] > 0:
                self._record(fill, "exit")
        if fill["order_state"] not in TERMINAL_ORDERS:
            self._set(status="entry_uncertain" if phase == "entry" else "exit_uncertain", next_check_at=self.clock()+3)
            return
        self._set(pending=None)
        if phase == "entry":
            if self.state.get("entry_filled_amount", 0) <= 0:
                self._set(active=False, status="unfilled", reason="سفارش ورود پر نشد؛ معامله و تایمر خروج ایجاد نشد.")
            else:
                self._set(status="holding", reason="ورود با filled_amount مثبت تأیید شد.", next_check_at=self.clock())
                try:
                    self._positions(client)
                except Exception:
                    pass
        else:
            self._set(status="verifying" if self._remaining() <= 1e-12 else "exit_blocked",
                      next_check_at=self.clock()+2)
            if self._remaining() <= 1e-12:
                self._verify_finished(client)

    def _remaining(self):
        return max(0.0, float(self.state.get("entry_filled_amount") or 0)-float(self.state.get("exit_filled_amount") or 0))

    def request_close(self):
        if self.is_active():
            self._force_close.set()
            self._set(close_requested=True, next_check_at=self.clock())
            self._ensure_worker()
            self._wake.set()
        return self.snapshot()

    def _verify_finished(self, client):
        positions = self._positions(client)
        quantity = self._quantity(positions, self.state["instrument"])
        self._set(active=False, status="closed", pending=None, closed_at=iso(self.clock()),
                  residual_position=quantity, reason="خروج تست با رسید پرشدن سفارش تأیید شد؛ پوزیشن اضافی احتمالی دست‌نخورده ماند.")
        self.closed_event.set()
        self.engine.request_cycle()
        self._notify("closed")

    def step(self):
        if not self.is_active() or self.clock() < float(self.state.get("next_check_at") or 0):
            return
        if not self.engine._cycle_lock.acquire(blocking=False):
            return
        try:
            client = self.engine._ensure_client()
            pending = self.state.get("pending")
            if pending:
                orders = client.orders_by_label(pending["label"])
                if isinstance(orders, dict):
                    orders = [orders]
                if len(orders) != 1:
                    self._set(status=pending["phase"]+"_uncertain", next_check_at=self.clock()+5)
                    self._warn("unconfirmed_order", "نتیجه سفارش نامشخص است؛ پیش از تأیید، سفارش تکراری ارسال نمی‌شود.")
                    return
                order = orders[0]
                if order.get("order_state") not in TERMINAL_ORDERS:
                    if order.get("order_id"):
                        client.cancel_order(order["order_id"])  # only this test's unique-labelled order
                    self._set(next_check_at=self.clock()+3)
                    return
                self._consume({"order": order}, client)
                if self.state.get("entry_filled_amount", 0) and pending["phase"] == "entry":
                    self._notify("opened_reconciled")
                return
            if self._remaining() <= 1e-12:
                self._verify_finished(client)
                return
            due = float(self.state.get("close_due_timestamp") or self.clock())
            if not self._force_close.is_set() and self.clock() < due:
                return
            self._set(status="closing", exit_started_at=self.state.get("exit_started_at") or iso(self.clock()))
            instrument = self.state["instrument"]
            positions = self._positions(client)
            quantity = self._quantity(positions, instrument)
            if quantity <= 0:
                # Require two reads to avoid calling a lagging snapshot an exit receipt.
                seen = self.state.get("flat_seen_timestamp")
                if seen is None:
                    self._set(status="verifying_external_flat", flat_seen_timestamp=self.clock(), next_check_at=self.clock()+3)
                elif self.clock()-float(seen) >= 3:
                    self._set(active=False, status="closed_external", closed_at=iso(self.clock()),
                              reason="حساب روی نماد تست flat/بدون long است؛ خروج به سفارش این تایمر نسبت داده نشد.")
                    self.closed_event.set(); self.engine.request_cycle(); self._notify("closed_external")
                return
            self._set(flat_seen_timestamp=None)
            meta = client.instrument(instrument)
            book = self._fresh_book(client, instrument, opening=False)
            amount = abs(floor_amount(min(self._remaining(), quantity), float(meta.get("contract_size") or 0)))
            minimum = float(meta.get("min_trade_amount") or meta.get("contract_size") or 0)
            if amount + 1e-12 < minimum:
                self._reject("exit_below_minimum", "باقی‌مانده تست زیر حداقل لات است؛ خروج دستی باید بررسی شود.")
            price = ioc_price(book, "sell", meta, self.engine.settings.max_slippage_bps)
            attempt = int(self.state.get("exit_attempts") or 0)+1
            label = f"{EXIT_PREFIX}{self.state['id']}_{attempt}"[:32]
            self._set(exit_attempts=attempt, pending={"phase": "exit", "label": label, "sent_at": self.clock(),
                       "requested_amount": amount, "before_exit_filled": self.state.get("exit_filled_amount", 0)})
            try:
                result = client.limit_ioc(instrument, "sell", amount, price, label, reduce_only=True)
                self._consume(result, client)
                if self.is_active() and not self.state.get("pending"):
                    self._set(next_check_at=self.clock()+5)
                    self._warn("exit_not_filled", "خروج شروع شده ولی کامل پر نشده؛ باقی‌مانده با reduce-only دوباره بررسی می‌شود.")
            except DeribitAPIError as exc:
                ambiguous = exc.code in ("transport", "protocol", 500, 502, 503, 504, 10001)
                self._set(status="exit_uncertain" if ambiguous else "exit_blocked",
                          pending=self.state.get("pending") if ambiguous else None,
                          reason=str(exc), next_check_at=self.clock()+5)
                self._warn("exit_api_error", "صرافی خروج را تأیید نکرده است؛ تست هنوز بسته گزارش نمی‌شود.")
        except TestTradeRejected as exc:
            self._set(status="exit_blocked", reason=str(exc), next_check_at=self.clock()+5)
            self._warn(exc.code, str(exc))
        except Exception as exc:
            self._set(status="exit_uncertain" if self.state.get("pending") else "exit_blocked",
                      reason=type(exc).__name__, next_check_at=self.clock()+5)
            self._warn("exit_unavailable", "خروج تست تأیید نشده؛ حساب/شبکه باید بررسی شود.")
        finally:
            self.engine._cycle_lock.release()

    def _recovery_complete(self):
        self.recovery_in_progress = False
        self._recovery_retry_at = 0.0
        if self.state.get("warning_code") == "recovery_check":
            self._set(warning_code=None, warning=None, recovery_error=None,
                      status="idle" if self.state.get("status") in ("idle", "recovery_blocked") else self.state.get("status"))
        self.engine.request_cycle()

    def recover_on_boot(self):
        if self.clock() < self._recovery_retry_at:
            return
        if not self.engine._cycle_lock.acquire(blocking=False):
            return
        try:
            if self.is_active():
                # State retained: continue the old deadline, NEVER open another test.
                self._set(next_check_at=self.clock(), recovered=True)
                self._recovery_complete()
                return
            s = self.engine.settings
            if not s.test_trade_enabled or urlparse(s.deribit_base_url).hostname != "test.deribit.com":
                self._recovery_complete(); return
            c = self.engine._ensure_client()
            instrument = s.instrument_for(s.test_trade_asset)
            positions = self._positions(c)
            if self._quantity(positions, instrument) <= 0:
                self._recovery_complete(); return
            orders = c.recent_orders(instrument)
            if not any(re.fullmatch(ENTRY_PREFIX+r"[0-9a-f]{16}", str(o.get("label") or "")) for o in orders):
                orders += c.recent_orders(instrument, historical=True)
            orders += [o for o in c.open_orders() if o.get("instrument_name") == instrument]
            # Historical/recent responses can overlap. Never count one fill twice.
            orders = list({o.get("order_id"): o for o in orders if o.get("order_id")}.values())
            candidates = []
            for entry in orders:
                match = re.fullmatch(ENTRY_PREFIX+r"([0-9a-f]{16})", str(entry.get("label") or ""))
                filled = float(entry.get("filled_amount") or 0)
                if not match or filled <= 0 or entry.get("direction") != "buy":
                    continue
                session = match.group(1)
                exits = [o for o in orders if str(o.get("label") or "").startswith(EXIT_PREFIX+session+"_")]
                exited = sum(float(o.get("filled_amount") or 0) for o in exits)
                if filled-exited > 1e-12:
                    candidates.append((entry, session, exits, exited))
            if len(candidates) > 1:
                self._reject("recovery_conflict", "بیش از یک تست نیمه‌تمام پیدا شد؛ تطبیق دستی لازم است.")
            if candidates:
                entry, session, exits, exited = candidates[0]
                opened = float(entry.get("last_update_timestamp") or entry.get("creation_timestamp") or 0)/1000
                if opened <= 0:
                    self._reject("unknown_deadline", "زمان ورود تست قابل تأیید نیست.")
                current = self._quantity(positions, instrument)
                remaining = float(entry["filled_amount"])-exited
                if remaining > current+1e-12:
                    self._reject("recovery_inventory", "باقی‌مانده برچسب تست با موجودی واقعی تطبیق ندارد.")
                self.engine.state.test_trade = {
                    "id": session, "active": True, "status": "holding", "asset": s.test_trade_asset.upper(),
                    "instrument": instrument, "environment": "testnet-live", "recovered": True,
                    "entry": fill_summary({"order": entry}), "entry_filled_amount": float(entry["filled_amount"]),
                    "exit_filled_amount": exited, "exits": [fill_summary({"order": o}) for o in exits],
                    "opened_timestamp": opened, "opened_at": iso(opened), "close_due_timestamp": opened+60,
                    "close_due_at": iso(opened+60), "exit_attempts": len(exits), "pending": None,
                    "next_check_at": self.clock(), "reference_position": 0.0,
                }
                unfinished = [o for o in exits if o.get("order_state") not in TERMINAL_ORDERS]
                if unfinished:
                    order = unfinished[-1]
                    already = float(order.get("filled_amount") or 0)
                    self._set(status="exit_uncertain", pending={"phase": "exit", "label": order["label"],
                              "sent_at": self.clock(), "requested_amount": order.get("amount"),
                              "before_exit_filled": max(0.0, exited-already)})
                elif entry.get("order_state") not in TERMINAL_ORDERS:
                    self._set(status="entry_uncertain", pending={"phase": "entry", "label": entry["label"],
                              "sent_at": self.clock(), "requested_amount": entry.get("amount"),
                              "before_exit_filled": 0})
                self.engine._save(); self._notify("recovered")
            self._recovery_complete()
        except Exception as exc:
            self._recovery_retry_at = self.clock()+30
            self._set(status="recovery_blocked", recovery_error=str(exc), recovery_retry_at=iso(self._recovery_retry_at))
            self._warn("recovery_check", "مالکیت تست روی نماد منتخب هنوز تأیید نشده؛ فقط همان نماد موقتاً مسدود است.")
            self.engine.request_cycle()
        finally:
            self.engine._cycle_lock.release()

    def shutdown(self, timeout=12):
        if self.is_active():
            self.request_close()
            self.closed_event.wait(timeout)
        self._shutdown.set(); self._wake.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)
