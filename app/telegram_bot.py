"""
Telegram control panel for Zenith SUPER bot.

- Glass (inline) keyboard panel
- Trade alerts in compact sample style: OPEN / CLOSE like Donchian example, but with exact capital & P&L
- Full text-file report every N hours or via panel button
- Financial reporting: per-trade with exact capital and P&L, and 6h comprehensive file
- Fix: never suppress fills when financial reporting exists; always report trades
"""

from __future__ import annotations

import html
import json
import logging
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

log = logging.getLogger("telegram")

# Real order statuses that warrant a trade alert
TRADE_STATUSES = {
    "bought", "sold", "reduced", "closed", "partially_filled", "recovered_fill",
    "error", "failed",
}
TEST_STATUSES = {"test_opened", "test_closed_fill"}


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _esc(x: Any) -> str:
    return html.escape(str(x if x is not None else "—"))


def _plain(x: Any) -> str:
    return re.sub(r"<[^>]+>", "", html.unescape(str(x if x is not None else "—")))


def _f(x, d=2) -> str:
    try:
        v = float(x)
        if abs(v) >= 1000:
            return f"{v:,.{d}f}"
        return f"{v:.{d}f}"
    except Exception:
        return "—"


def _f6(x) -> str:
    try:
        return f"{float(x):.6f}".rstrip("0").rstrip(".") if float(x) != 0 else "0"
    except Exception:
        return "—"


def _fmt_money(v):
    if v is None:
        return "نامشخص"
    try:
        return f"{float(v):+.6f}"
    except Exception:
        return "نامشخص"


def _fmt_price(v) -> str:
    try:
        f = float(v)
        if f >= 1000:
            return f"{f:,.4f}"
        if f >= 1:
            return f"{f:.4f}"
        return f"{f:.6f}"
    except Exception:
        return "—"


def _fmt_qty(v) -> str:
    try:
        f = float(v)
        # keep up to 6 decimals but trim
        s = f"{f:.8f}".rstrip("0").rstrip(".")
        return s if s else "0"
    except Exception:
        return "—"


def _fmt_pct(v, d=2) -> str:
    try:
        return f"{float(v):+.{d}f}%"
    except Exception:
        return "—"


def _calc_price_change_pct(entry, exit_price, side_long: bool) -> Optional[float]:
    try:
        e = float(entry)
        x = float(exit_price)
        if e == 0:
            return None
        pct = (x - e) / e * 100
        if not side_long:
            pct = -pct
        return pct
    except Exception:
        return None


def _get_contributors_str(contributors: dict) -> str:
    if not contributors:
        return "سیگنال تجمیعی SUPER"
    parts = []
    for sid, v in contributors.items():
        try:
            side = float((v or {}).get("side") or 0)
            label = "LONG" if side > 0 else "SHORT" if side < 0 else "FLAT"
            parts.append(f"{sid}:{label}")
        except Exception:
            parts.append(str(sid))
    return ", ".join(parts[:5]) if parts else "SUPER"


class TelegramReporter:
    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        enabled: bool = True,
        dashboard_url: str = "",
        send_every_n_loops: int = 1,
        heartbeat_minutes: int = 30,
        full_report_hours: float = 6.0,
        trade_only: bool = True,
        poll_commands: bool = True,
    ):
        self.token = (token or "").strip()
        self.chat_id = str(chat_id or "").strip()
        self.enabled = bool(enabled and self.token and self.chat_id)
        self.dashboard_url = (dashboard_url or "").rstrip("/")
        self.send_every_n_loops = max(1, int(send_every_n_loops or 1))
        self.heartbeat_minutes = max(15, int(heartbeat_minutes or 30))
        self.full_report_hours = float(full_report_hours or 6.0)
        self.trade_only = bool(trade_only)
        self.poll_commands = bool(poll_commands and self.enabled)
        self._http = httpx.Client(timeout=45.0)
        self._offset = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._bg_thread: Optional[threading.Thread] = None
        self._engine = None
        self._last_heartbeat = 0.0
        self._last_full_report = 0.0
        self._last_halt_notice = 0.0
        self._last_problem_notice = 0.0
        self._last_problem = ""
        self._previous_open_count = None
        self._test_alerted_ids = set()
        self._financial_alerted = set()
        self._panel_msg_id: Optional[int] = None
        self.stats = {
            "sent": 0,
            "failed": 0,
            "cycles_ok": 0,
            "cycles_err": 0,
            "orders_ok": 0,
            "orders_err": 0,
            "orders_skip": 0,
            "trades_alerted": 0,
            "files_sent": 0,
        }
        self.journal: List[dict] = []

    # ── API ───────────────────────────────────────────────────────────────
    def _api(self, method: str, *, as_json: bool = False, count_stats: bool = True, **params) -> dict:
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        try:
            if as_json:
                r = self._http.post(url, json=params)
            else:
                r = self._http.post(url, data=params)
            data = r.json()
        except Exception as e:
            data = {"ok": False, "description": str(e).replace(self.token, "<redacted>")}
        if not data.get("ok"):
            if method not in ("getUpdates",):
                log.warning("telegram %s fail: %s", method, str(data)[:400])
            if count_stats and method in ("sendMessage", "sendDocument", "editMessageText"):
                self.stats["failed"] += 1
        else:
            if count_stats and method in ("sendMessage", "sendDocument", "editMessageText"):
                self.stats["sent"] += 1
        return data

    def send(self, text: str, reply_markup: Optional[dict] = None) -> Optional[dict]:
        if not self.enabled:
            return None
        chunks = []
        t = text
        while len(t) > 3500:
            cut = t.rfind("\n", 0, 3400)
            if cut < 400:
                cut = 3400
            chunks.append(t[:cut])
            t = t[cut:]
        chunks.append(t)
        last = None
        for i, ch in enumerate(chunks):
            params = dict(
                chat_id=self.chat_id,
                text=ch,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            if reply_markup and i == len(chunks) - 1:
                params["reply_markup"] = json.dumps(reply_markup)
            data = self._api("sendMessage", **params)
            if not data.get("ok"):
                plain = _plain(ch)
                params = dict(chat_id=self.chat_id, text=plain[:4000], disable_web_page_preview=True)
                if reply_markup and i == len(chunks) - 1:
                    params["reply_markup"] = json.dumps(reply_markup)
                data = self._api("sendMessage", **params)
            last = data
            time.sleep(0.05)
        return last

    def send_document(self, filename: str, content: str, caption: str = "") -> bool:
        if not self.enabled:
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendDocument"
        try:
            files = {"document": (filename, content.encode("utf-8"), "text/plain")}
            data = {"chat_id": self.chat_id}
            if caption:
                data["caption"] = caption[:1000]
            r = self._http.post(url, data=data, files=files, timeout=60)
            j = r.json()
            if j.get("ok"):
                self.stats["sent"] += 1
                self.stats["files_sent"] += 1
                return True
            log.warning("sendDocument fail: %s", j)
            self.stats["failed"] += 1
            self.send(f"<b>{_esc(filename)}</b>\n<pre>{_esc(content[:3500])}</pre>")
            return False
        except Exception as e:
            log.warning("sendDocument failed: %s", type(e).__name__)
            self.stats["failed"] += 1
            return False

    def answer_callback(self, callback_id: str, text: str = "", show_alert: bool = False) -> None:
        self._api(
            "answerCallbackQuery",
            count_stats=False,
            callback_query_id=callback_id,
            text=(text or "")[:200],
            show_alert=show_alert,
        )

    # ── Glass keyboard ────────────────────────────────────────────────────
    def main_keyboard(self) -> dict:
        keyboard = [
            [
                {"text": "📊 وضعیت", "callback_data": "panel:status"},
                {"text": "💼 پوزیشن", "callback_data": "panel:positions"},
                {"text": "💰 موجودی", "callback_data": "panel:balance"},
            ],
            [
                {"text": "🧩 آستین‌ها", "callback_data": "panel:sleeves"},
                {"text": "📈 آمار", "callback_data": "panel:stats"},
                {"text": "🧾 معاملات", "callback_data": "panel:trades"},
            ],
            [
                {"text": "⚡ Tick", "callback_data": "panel:tick"},
                {"text": "📄 گزارش فایل", "callback_data": "panel:file"},
                {"text": "❤️ Health", "callback_data": "panel:health"},
            ],
            [
                {"text": "🧪 تست ۶۰ثانیه", "callback_data": "panel:testtrade"},
                {"text": "⏱ وضعیت تست", "callback_data": "panel:teststatus"},
                {"text": "🛑 بستن تست", "callback_data": "panel:testclose"},
            ],
            [
                {"text": "▶️ Start", "callback_data": "panel:start"},
                {"text": "⏹ Stop", "callback_data": "panel:stop"},
                {"text": "🔄 پنل", "callback_data": "panel:home"},
            ],
        ]
        if self.dashboard_url:
            keyboard.append([{"text": "🌐 داشبورد وب", "url": self.dashboard_url}])
        return {"inline_keyboard": keyboard}

    def _kb(self) -> dict:
        return self.main_keyboard()

    def send_panel(self, text: Optional[str] = None) -> None:
        if text is None:
            text = (
                f"🎛️ <b>ZENITH SUPER — پنل کنترل</b>\n"
                f"🕐 {utcnow()}\n\n"
                f"از دکمه‌های شیشه‌ای زیر استفاده کنید.\n"
                f"• گزارش <b>معامله</b> فقط وقتی سفارش واقعی/خطا باشد\n"
                f"• گزارش <b>کلی فایل</b> هر {self.full_report_hours:g} ساعت یا از دکمه\n"
            )
            if self._engine:
                snap = self._engine.snapshot()
                text += (
                    f"\nوضعیت: <b>{_esc(snap.get('mode'))}</b> · "
                    f"{'🟢' if snap.get('running') else '🔴'} · "
                    f"loops=<code>{_esc(snap.get('loop_count'))}</code>\n"
                    f"جفت‌ها: <code>{len((snap.get('config') or {}).get('assets') or [])}</code> · "
                    f"آستین: <code>{len(snap.get('sleeves') or [])}</code>"
                )
        self.send(text, reply_markup=self._kb())

    # ── bind / lifecycle ──────────────────────────────────────────────────
    def bind_engine(self, engine) -> None:
        self._engine = engine

    def start_background(self) -> None:
        if not self.enabled:
            return
        if self.poll_commands and (self._thread is None or not self._thread.is_alive()):
            self._stop.clear()
            self._thread = threading.Thread(target=self._poll_loop, name="tg-poll", daemon=True)
            self._thread.start()
        if self._bg_thread is None or not self._bg_thread.is_alive():
            self._bg_thread = threading.Thread(target=self._bg_loop, name="tg-bg", daemon=True)
            self._bg_thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ── COMPACT TRADE FORMATTERS (sample style) ───────────────────────────
    def _strategy_label(self, ev_or_action: dict, snapshot: Optional[dict] = None) -> str:
        # Try contributors from snapshot net_book for this asset
        asset = ev_or_action.get("asset")
        if snapshot:
            try:
                nb = snapshot.get("net_book") or {}
                if asset in nb:
                    contrib = nb[asset].get("contributors") or {}
                    if contrib:
                        # pick dominant sleeve by abs notional or side
                        # contributors: {sleeve_id: {side, notional...}}
                        # choose first LONG or highest
                        for sid in ["zenith_apex", "almasi_primary", "inst_v3_stable", "inst_v3_primary", "zenith_endurance"]:
                            if sid in contrib:
                                return sid
                        return list(contrib.keys())[0]
            except Exception:
                pass
        # Fallback to source or label
        src = ev_or_action.get("source") or ""
        if src == "manual_test":
            return "Test60"
        if src == "bot":
            return "SUPER"
        label = ev_or_action.get("label") or ""
        if label.startswith("sup_"):
            return "SUPER"
        return src or "SUPER"

    def format_compact_open(self, ev: dict, snapshot: Optional[dict] = None) -> str:
        """
        🎯 OPEN BUY • zenith_apex [💵 LIVE]
        BTCUSD @ 83941.1
        Qty 0.0001 (~$8.39)
        💰 سرمایه پس از معامله: $8.39
        💼 سرمایه کل: $100 | موجودی حساب: $99982.19
        ℹ️ سیگنال تجمیعی SUPER
        """
        asset = ev.get("asset") or str(ev.get("instrument") or "").split("_")[0]
        side_raw = ev.get("direction") or ""
        side = "BUY" if side_raw == "buy" else "SELL" if side_raw == "sell" else str(side_raw).upper()
        strategy = self._strategy_label(ev, snapshot)
        price = _fmt_price(ev.get("price"))
        qty = _fmt_qty(ev.get("filled_amount") or ev.get("amount"))
        notional = ev.get("execution_notional_usdc") or ev.get("notional_usd")
        notional_s = f"${_f(notional)}" if notional is not None else "نامشخص"
        capital_after = ev.get("position_notional_at_fill_usdc")
        capital_after_s = f"${_f(capital_after)}" if capital_after is not None else "نامشخص"

        # account equity
        acct_eq = None
        if snapshot:
            try:
                acct_eq = (snapshot.get("financial") or {}).get("account", {}).get("equity")
                if acct_eq is None:
                    acct_eq = (snapshot.get("account", {}).get("USDC") or {}).get("equity")
                if acct_eq is None:
                    acct_eq = (snapshot.get("financial") or {}).get("account", {}).get("balance")
            except Exception:
                pass
        acct_s = f"${_f(acct_eq)}" if acct_eq is not None else "نامشخص"
        allocated = None
        if snapshot:
            allocated = (snapshot.get("config") or {}).get("capital_usd") or (snapshot.get("financial") or {}).get("allocated_capital_usdc")
        alloc_s = f"${_f(allocated,0)}" if allocated else "$100"

        contrib_str = _get_contributors_str(ev.get("contributors") or {})
        # if we have snapshot, try better
        if snapshot and asset:
            try:
                nb = snapshot.get("net_book") or {}
                if asset in nb:
                    contrib_str = _get_contributors_str(nb[asset].get("contributors") or {})
            except Exception:
                pass

        icon = "🎯"
        lines = [
            f"{icon} <b>OPEN {side} • {_esc(strategy)} [💵 LIVE]</b>",
            f"{_esc(asset)}USD @ {_esc(price)}",
            f"Qty {_esc(qty)} (~{_esc(notional_s)})",
            f"💰 سرمایه پس از معامله: <b>{_esc(capital_after_s)}</b>",
            f"💼 سرمایه کل: {_esc(alloc_s)} | موجودی حساب: {_esc(acct_s)}",
            f"ℹ️ {_esc(contrib_str)}",
        ]
        if ev.get("order_id"):
            lines.append(f"🆔 <code>{_esc(ev.get('order_id'))}</code> | src={_esc(ev.get('source') or 'bot')}")
        return "\n".join(lines)

    def format_compact_close(self, ev: dict, snapshot: Optional[dict] = None) -> str:
        """
        🟢 CLOSE SELL • zenith_apex • BTCUSD [💵 LIVE]
        Entry 83941.1 → Exit 83960.3 (+0.02%)
        💵 PnL +0.0019$ ناخالص | کارمزد $0.0083 | خالص -0.0064$ (-0.07% از $8.39)
        📦 بسته‌شده: 0.0001 | سرمایه پس از بستن: $0.00
        ⏱️ نگهداری: — | 💼 موجودی: $99982.19
        """
        asset = ev.get("asset") or str(ev.get("instrument") or "").split("_")[0]
        pos_side = ev.get("position_side") or ("LONG" if ev.get("direction") == "sell" else "SHORT")
        # For close, side is opposite of position: if LONG closed, side SELL, if SHORT closed, BUY
        if ev.get("kind") in ("close", "reduce", "reverse", "close_unknown_basis"):
            close_side = "SELL" if pos_side == "LONG" else "BUY"
        else:
            close_side = "SELL" if ev.get("direction") == "sell" else "BUY"

        strategy = self._strategy_label(ev, snapshot)

        entry_price = ev.get("entry_price")
        exit_price = ev.get("price")
        entry_s = _fmt_price(entry_price) if entry_price is not None else "نامشخص"
        exit_s = _fmt_price(exit_price)

        # price change %
        price_chg = None
        if entry_price is not None and exit_price is not None:
            try:
                e = float(entry_price)
                x = float(exit_price)
                if e != 0:
                    raw = (x - e) / e * 100
                    # for LONG, profit when x>e, for SHORT when x<e
                    if pos_side == "SHORT":
                        raw = -raw
                    price_chg = raw
            except Exception:
                pass
        price_chg_s = f"{price_chg:+.2f}%" if price_chg is not None else "—"

        gross = ev.get("realized_gross_usdc")
        entry_fee = ev.get("entry_fee_allocated_usdc")
        exit_fee = ev.get("exit_fee_usdc")
        paid_fee = ev.get("paid_fee_usdc")
        net = ev.get("net_final_usdc")
        if net is None:
            net = ev.get("net_price_fees_usdc")
        return_pct = ev.get("return_pct_price_fees")
        closed_qty = ev.get("closed_quantity") or ev.get("filled_amount")
        capital_after = ev.get("position_notional_at_fill_usdc")

        # total fee
        total_fee = None
        try:
            if entry_fee is not None and exit_fee is not None:
                total_fee = float(entry_fee) + float(exit_fee)
            elif paid_fee is not None:
                total_fee = float(paid_fee)
        except Exception:
            pass

        # entry basis for % calc
        entry_basis = None
        try:
            if entry_price is not None and closed_qty is not None:
                entry_basis = float(entry_price) * float(closed_qty)
        except Exception:
            pass

        # icon based on net
        try:
            net_f = float(net) if net is not None else None
            if net_f is not None:
                icon = "🟢" if net_f > 0 else "🔴" if net_f < 0 else "🟡"
            else:
                # fallback to gross
                gross_f = float(gross) if gross is not None else 0
                icon = "🟢" if gross_f > 0 else "🔴" if gross_f < 0 else "🟡"
        except Exception:
            icon = "🟢"

        acct_eq = None
        if snapshot:
            try:
                acct_eq = (snapshot.get("financial") or {}).get("account", {}).get("equity")
                if acct_eq is None:
                    acct_eq = (snapshot.get("account", {}).get("USDC") or {}).get("equity")
            except Exception:
                pass
        acct_s = f"${_f(acct_eq)}" if acct_eq is not None else "نامشخص"

        # Build lines in sample style
        lines = [
            f"{icon} <b>CLOSE {close_side} • {_esc(strategy)} • {_esc(asset)} [💵 LIVE]</b>",
            f"Entry {_esc(entry_s)} → Exit {_esc(exit_s)} ({_esc(price_chg_s)})",
        ]

        # PnL line
        gross_s = f"{_f(gross,4)}$" if gross is not None else "نامشخص"
        fee_s = f"${_f(total_fee,4)}" if total_fee is not None else ("$" + _f(paid_fee,4) if paid_fee is not None else "نامشخص")
        net_s = f"{_f(net,4)}$" if net is not None else "نامشخص"
        ret_s = f"{_f(return_pct,2)}%" if return_pct is not None else "—"
        basis_s = f"${_f(entry_basis)}" if entry_basis is not None else "—"

        if gross is not None or net is not None:
            lines.append(f"💵 PnL { _esc(gross_s)} ناخالص | کارمزد {_esc(fee_s)} | خالص <b>{_esc(net_s)}</b> ({_esc(ret_s)} از {_esc(basis_s)})")
        else:
            lines.append(f"💵 PnL نامشخص (شواهد ورود ناقص) | کارمزد {_esc(fee_s)}")

        closed_s = _fmt_qty(closed_qty)
        cap_after_s = f"${_f(capital_after)}" if capital_after is not None else "—"
        lines.append(f"📦 بسته‌شده: {_esc(closed_s)} | سرمایه پس از بستن: {_esc(cap_after_s)}")

        # held time if available
        held_str = "—"
        try:
            # ev timestamp vs entry? we don't have entry time, use generic
            # Could compute from order history if available, but keep —
            pass
        except Exception:
            pass
        lines.append(f"⏱️ held {held_str} | 💼 موجودی: {_esc(acct_s)}")

        if ev.get("order_id"):
            lines.append(f"🆔 <code>{_esc(ev.get('order_id'))}</code> | quality={_esc(ev.get('pnl_quality') or '—')}")

        if ev.get("pnl_quality") and ev["pnl_quality"] != "complete":
            lines.append(f"⚠️ کیفیت محاسبه: {_esc(ev['pnl_quality'])}")

        return "\n".join(lines)

    # ── Financial per-trade formatting (now compact) ──────────────────────
    def format_financial_event(self, ev: dict, snapshot: Optional[dict] = None) -> str:
        kind = ev.get("kind")
        if kind in ("close", "reduce", "reverse", "close_unknown_basis"):
            return self.format_compact_close(ev, snapshot)
        else:
            return self.format_compact_open(ev, snapshot)

    def on_financial_event(self, event: dict, snapshot: dict) -> bool:
        if not self.enabled:
            # Even if telegram disabled, we return True to avoid infinite queue block in local dev
            # In production telegram is enabled, so this path not taken
            return True
        # Avoid duplicate alerts for same order+quantity level
        key = f"{event.get('order_id')}:{event.get('filled_amount')}"
        if key in self._financial_alerted:
            return True
        self._financial_alerted.add(key)
        # Update local journal with real P&L
        self.journal.append({
            "ts": utcnow(),
            "asset": event.get("asset"),
            "status": event.get("kind"),
            "amount": event.get("filled_amount"),
            "price": event.get("price"),
            "notional_usd": event.get("execution_notional_usdc"),
            "order_id": event.get("order_id"),
            "pnl": event.get("net_final_usdc") if event.get("net_final_usdc") is not None else event.get("net_price_fees_usdc"),
            "fee": event.get("paid_fee_usdc"),
            "capital": event.get("position_notional_at_fill_usdc"),
            "source": event.get("source"),
        })
        self.journal = self.journal[-500:]
        self.stats["orders_ok"] += 1
        self.stats["trades_alerted"] += 1
        text = self.format_financial_event(event, snapshot)
        self.send(text, reply_markup=self._kb())
        return True

    def format_trade_action_compact(self, action: dict, snapshot: Optional[dict] = None) -> str:
        """
        Fallback compact formatter for immediate fills (before financial enrichment)
        """
        asset = action.get("asset") or str(action.get("instrument") or "").split("_")[0]
        status = (action.get("status") or "").lower()
        direction = action.get("direction") or ("buy" if "bought" in status else "sell" if "sold" in status else "")
        side = "BUY" if direction == "buy" else "SELL" if direction == "sell" else status.upper()
        price = _fmt_price(action.get("price") or action.get("average_price"))
        qty = _fmt_qty(action.get("filled_amount") or action.get("amount"))
        notional = action.get("notional_usd")
        notional_s = f"${_f(notional)}" if notional is not None else "—"

        # Determine if this is close or open based on reduce_only / status
        is_close = action.get("reduce_only") or status in ("closed", "reduced", "close")

        # Strategy label from contributors
        strategy = self._strategy_label(action, snapshot)
        contrib_str = _get_contributors_str(action.get("contributors") or {})
        if snapshot and asset:
            try:
                nb = snapshot.get("net_book") or {}
                if asset in nb:
                    contrib_str = _get_contributors_str(nb[asset].get("contributors") or {})
            except Exception:
                pass

        acct_eq = None
        allocated = None
        if snapshot:
            try:
                acct_eq = (snapshot.get("account", {}).get("USDC") or {}).get("equity")
                if acct_eq is None:
                    acct_eq = (snapshot.get("financial") or {}).get("account", {}).get("equity")
                allocated = (snapshot.get("config") or {}).get("capital_usd")
            except Exception:
                pass
        acct_s = f"${_f(acct_eq)}" if acct_eq is not None else "—"
        alloc_s = f"${_f(allocated,0)}" if allocated else "$100"

        if is_close:
            # For close actions without enriched P&L, show basic
            icon = "🟢"
            lines = [
                f"{icon} <b>CLOSE {side} • {_esc(strategy)} • {_esc(asset)} [💵 LIVE]</b>",
                f"{_esc(asset)} @ {_esc(price)}",
                f"Qty {_esc(qty)} (~{_esc(notional_s)})",
                f"💰 سرمایه تخصیصی: {_esc(alloc_s)} | موجودی: {_esc(acct_s)}",
                f"ℹ️ {_esc(contrib_str)}",
            ]
        else:
            lines = [
                f"🎯 <b>OPEN {side} • {_esc(strategy)} [💵 LIVE]</b>",
                f"{_esc(asset)}USD @ {_esc(price)}",
                f"Qty {_esc(qty)} (~{_esc(notional_s)})",
                f"💰 سرمایه پس از معامله: {_esc(notional_s)}",
                f"💼 سرمایه کل: {_esc(alloc_s)} | موجودی: {_esc(acct_s)}",
                f"ℹ️ {_esc(contrib_str)}",
            ]
        if action.get("order_id"):
            lines.append(f"🆔 <code>{_esc(action.get('order_id'))}</code>")
        if action.get("error"):
            lines.append(f"❌ {_esc(action.get('error'))[:200]}")
        return "\n".join(lines)

    def format_balance(self, snap: dict) -> str:
        acct = snap.get("account") or {}
        fin = snap.get("financial") or {}
        lines = [f"💰 <b>موجودی</b>", f"🕐 {utcnow()}", ""]
        for cur, v in acct.items():
            if not isinstance(v, dict) or "error" in v:
                lines.append(f"{_esc(cur)}: {_esc(v)}")
                continue
            lines.append(
                f"<b>{_esc(cur)}</b> eq=<code>{_esc(v.get('equity'))}</code> "
                f"bal=<code>{_esc(v.get('balance'))}</code> "
                f"avail=<code>{_esc(v.get('available'))}</code>"
            )
        if fin:
            lines.extend([
                "",
                f"<b>گزارش مالی</b>",
                f"سرمایه تخصیصی: ${ _f(fin.get('allocated_capital_usdc'))}",
                f"برآورد ربات: ${ _f(fin.get('estimated_bot_equity_usdc'))}",
                f"حساب USDC eq: ${ _f((fin.get('account') or {}).get('equity'))}",
                f"بازده بسته‌شده ۶ساعت: ${ _f(fin.get('verified_closed_net_usdc'))}",
            ])
        return "\n".join(lines)

    def format_positions(self, snap: dict) -> str:
        pos = snap.get("positions") or []
        fin = snap.get("financial") or {}
        lines = [f"💼 <b>پوزیشن‌ها ({len(pos)})</b>", f"🕐 {utcnow()}", ""]
        if not pos:
            lines.append("flat — بدون پوزیشن")
            return "\n".join(lines)
        total_pnl = 0.0
        for p in pos:
            try:
                total_pnl += float(p.get("total_profit_loss") or 0)
            except Exception:
                pass
            lines.append(
                f"• <b>{_esc(p.get('instrument_name'))}</b>\n"
                f"  dir={_esc(p.get('direction'))} size={_esc(p.get('size_currency') or p.get('size'))}\n"
                f"  avg={_esc(p.get('average_price'))} mark={_esc(p.get('mark_price'))} lev={_esc(p.get('leverage'))}\n"
                f"  pnl={_esc(p.get('total_profit_loss'))} uPnL={_esc(p.get('floating_profit_loss'))} funding={_esc(p.get('realized_funding'))}"
            )
        lines.append(f"\nΣ pnl ≈ <b>{total_pnl:.4f}</b>")
        if fin.get("unrealized_gross_usdc") is not None:
            lines.append(f"غیرمحقق حساب‌شده: ${ _f(fin.get('unrealized_gross_usdc'))}")
        return "\n".join(lines)

    def format_sleeves(self, snap: dict) -> str:
        lines = [f"🧩 <b>آستین‌ها</b>", f"🕐 {utcnow()}", ""]
        for sl in snap.get("sleeves") or []:
            pa = sl.get("per_asset") or {}
            n_long = sum(1 for p in pa.values() if float((p or {}).get("side") or 0) > 0)
            ntl = sum(float((p or {}).get("notional_usd") or 0) for p in pa.values())
            lines.append(
                f"<b>{_esc(sl.get('id'))}</b> {float(sl.get('weight') or 0)*100:.0f}% · "
                f"${float(sl.get('capital') or 0):.0f}\n"
                f"  long={n_long}/{len(pa)} · ntl=${ntl:.1f}"
            )
        nb = snap.get("net_book") or {}
        top = sorted(nb.items(), key=lambda kv: float((kv[1] or {}).get("notional_usd") or 0), reverse=True)[:12]
        if top:
            lines.append("\n<b>Net top</b>")
            for a, b in top:
                n = float((b or {}).get("notional_usd") or 0)
                if n <= 0:
                    continue
                lines.append(f"• {_esc(a)} ${n:.2f}")
        return "\n".join(lines)

    def format_stats(self, snap: dict) -> str:
        st = self.stats
        j = self.journal
        wins = sum(1 for x in j if float(x.get("pnl") or 0) > 0)
        losses = sum(1 for x in j if float(x.get("pnl") or 0) < 0)
        pnl = sum(float(x.get("pnl") or 0) for x in j if x.get("pnl") is not None)
        unknown = sum(1 for x in j if x.get("pnl") is None)
        fin = snap.get("financial") or {}
        lines = [
            f"📈 <b>آمار</b>",
            f"🕐 {utcnow()}",
            f"",
            f"cycles ✅{st['cycles_ok']} ❌{st['cycles_err']}",
            f"orders ✅{st['orders_ok']} ⏭{st['orders_skip']} ❌{st['orders_err']}",
            f"trade alerts: {st['trades_alerted']}",
            f"tg sent/fail: {st['sent']}/{st['failed']}",
            f"files: {st['files_sent']}",
            f"",
            f"<b>ژورنال معاملات</b> n={len(j)}",
            f"wins={wins} losses={losses} unknown_pnl={unknown}",
            f"Σ pnl (verified)={pnl:.6f} USDC",
            f"bot loops={_esc(snap.get('loop_count'))}",
            f"open positions={len(snap.get('positions') or [])}",
        ]
        if fin:
            lines.extend([
                f"",
                f"<b>گزارش مالی ۶ساعت</b> executions={fin.get('execution_count',0)} closes={fin.get('closed_event_count',0)}",
                f"verified_closed_net=${ _f(fin.get('verified_closed_net_usdc'))}",
                f"fees=${ _f(fin.get('period_fees_usdc'))} unreal=${ _f(fin.get('unrealized_gross_usdc'))}",
            ])
        return "\n".join(lines)

    def format_trades_brief(self) -> str:
        j = self.journal[-15:]
        lines = [f"🧾 <b>آخرین معاملات</b> (ژورنال {len(self.journal)})", ""]
        if not j:
            lines.append("هنوز معاملهٔ ثبت‌شده‌ای نیست")
            return "\n".join(lines)
        for x in reversed(j):
            pnl = x.get("pnl")
            pnl_s = f" pnl={_f(pnl,6)}" if pnl is not None else " pnl=نامشخص"
            lines.append(
                f"• {_esc(x.get('ts'))} {_esc(x.get('asset'))} "
                f"<code>{_esc(x.get('status'))}</code> "
                f"${_f(x.get('notional_usd'))}{pnl_s}"
            )
        return "\n".join(lines)

    def format_trade_alert(self, actions: List[dict], *, loop_count: int = 0, snapshot: Optional[dict] = None) -> str:
        # Fallback for actions that have not yet been enriched by financial journal - now uses compact style per trade
        trades = [a for a in actions if (a.get("status") or "").lower() in TRADE_STATUSES]
        if not trades:
            return ""
        # For multiple trades, join compact messages with separator
        msgs = []
        for a in trades:
            # Try to get enriched financial event first
            ev = None
            if self._engine and hasattr(self._engine, "financial"):
                try:
                    ev = self._engine.financial.journal.event(a.get("order_id"))
                except Exception:
                    ev = None
            if ev:
                msgs.append(self.format_financial_event(ev, snapshot))
            else:
                msgs.append(self.format_trade_action_compact(a, snapshot))
            # journal bookkeeping
            self.journal.append({
                "ts": utcnow(),
                "asset": a.get("asset"),
                "status": a.get("status"),
                "amount": a.get("amount") or a.get("filled_amount"),
                "notional_usd": a.get("notional_usd"),
                "price": a.get("price"),
                "error": a.get("error"),
                "pnl": None,
            })
            self.journal = self.journal[-500:]
            st = (a.get("status") or "").lower()
            if st in ("error", "failed"):
                self.stats["orders_err"] += 1
            else:
                self.stats["orders_ok"] += 1
        self.stats["trades_alerted"] += len(msgs)
        return "\n\n".join(msgs)

    def build_full_report_text(self, snap: dict) -> str:
        cfg = snap.get("config") or {}
        acct = snap.get("account") or {}
        pos = snap.get("positions") or []
        sleeves = snap.get("sleeves") or []
        nb = snap.get("net_book") or {}
        actions = snap.get("actions") or []
        events = snap.get("events") or []
        orders = snap.get("orders_log") or []
        fin = snap.get("financial") or {}
        lines = []
        lines.append("=" * 80)
        lines.append("ZENITH SUPER BOT — COMPREHENSIVE 6H REPORT")
        lines.append(f"generated: {utcnow()}  tz={fin.get('timezone') or cfg.get('report_timezone') or 'Europe/Istanbul'}")
        lines.append("=" * 80)
        lines.append("")
        lines.append("[CAPITAL & EQUITY — EXACT]")
        lines.append(f"allocated_bot_capital_usdc={cfg.get('capital_usd')}")
        lines.append(f"estimated_bot_equity_usdc={snap.get('risk', {}).get('estimated_bot_equity')}")
        lines.append(f"peak_bot_equity_usdc={snap.get('risk', {}).get('peak_bot_equity')}")
        lines.append(f"drawdown_pct={snap.get('risk', {}).get('drawdown_pct')}")
        lines.append(f"notional_cap_usdc={snap.get('risk', {}).get('notional_cap_usdc')}")
        lines.append(f"account_USDC_equity={(acct.get('USDC') or {}).get('equity')}")
        lines.append(f"account_USDC_balance={(acct.get('USDC') or {}).get('balance')}")
        lines.append(f"account_USDC_available={(acct.get('USDC') or {}).get('available')}")
        lines.append(f"open_notional_usdc={fin.get('open_notional_usdc')}")
        lines.append(f"initial_margin_usdc={fin.get('initial_margin_usdc')}")
        lines.append(f"unrealized_gross_usdc={fin.get('unrealized_gross_usdc')}")
        lines.append(f"scope_note={fin.get('scope_note') or 'Account equity != bot $100 allocation; funding only when evidence complete'}")
        lines.append("")
        lines.append("[FINANCIAL SUMMARY 6H]")
        lines.append(f"window={fin.get('window')}")
        lines.append(f"execution_count={fin.get('execution_count')} closed={fin.get('closed_event_count')} wins={fin.get('wins')} losses={fin.get('losses')}")
        lines.append(f"verified_closed_net_usdc={fin.get('verified_closed_net_usdc')}")
        lines.append(f"period_fees_usdc={fin.get('period_fees_usdc')}")
        lines.append(f"status={fin.get('status')}")
        lines.append("")
        lines.append("[POSITIONS] n={}".format(len(pos)))
        total_pnl = 0.0
        for p in pos:
            try:
                total_pnl += float(p.get("total_profit_loss") or 0)
            except Exception:
                pass
            lines.append(
                f"  {p.get('instrument_name')} dir={p.get('direction')} "
                f"size={p.get('size_currency') or p.get('size')} avg={p.get('average_price')} "
                f"mark={p.get('mark_price')} pnl={p.get('total_profit_loss')} "
                f"upnl={p.get('floating_profit_loss')} funding={p.get('realized_funding')} lev={p.get('leverage')}"
            )
        lines.append(f"  TOTAL_pnl≈{total_pnl}")
        lines.append("")
        lines.append("[TRADE EVENTS — EXACT CAPITAL & P&L — COMPACT SAMPLE STYLE]")
        for ev in (fin.get("events") or [])[-100:]:
            # Compact sample style also in file
            try:
                side = "BUY" if ev.get("direction") == "buy" else "SELL"
                kind = ev.get("kind")
                if kind in ("close", "reduce", "reverse"):
                    lines.append(
                        f"  {ev.get('timestamp')} CLOSE {side} {ev.get('asset')} "
                        f"Entry {ev.get('entry_price')}→{ev.get('price')} gross={ev.get('realized_gross_usdc')} "
                        f"fee={ev.get('entry_fee_allocated_usdc')}+{ev.get('exit_fee_usdc')} net={ev.get('net_final_usdc')} "
                        f"closed={ev.get('closed_quantity')} cap_after={ev.get('position_notional_at_fill_usdc')} order={ev.get('order_id')}"
                    )
                else:
                    lines.append(
                        f"  {ev.get('timestamp')} OPEN {side} {ev.get('asset')} "
                        f"qty={ev.get('filled_amount')} px={ev.get('price')} notional={ev.get('execution_notional_usdc')} "
                        f"cap_after={ev.get('position_notional_at_fill_usdc')} order={ev.get('order_id')}"
                    )
            except Exception:
                lines.append(f"  {ev}")
        lines.append("")
        lines.append("[TRADE EVENTS — DETAILED]")
        for ev in (fin.get("events") or [])[-100:]:
            lines.append(
                f"  {ev.get('timestamp')} {ev.get('asset')} {ev.get('kind')} "
                f"qty={ev.get('filled_amount')} px={ev.get('price')} "
                f"capital_after=${ev.get('position_notional_at_fill_usdc')} "
                f"closed_qty={ev.get('closed_quantity')} gross={ev.get('realized_gross_usdc')} "
                f"fee_alloc={ev.get('entry_fee_allocated_usdc')} exit_fee={ev.get('exit_fee_usdc')} "
                f"net_fee={ev.get('net_price_fees_usdc')} net_final={ev.get('net_final_usdc')} "
                f"quality={ev.get('pnl_quality')} order={ev.get('order_id')} src={ev.get('source')}"
            )
        lines.append("")
        lines.append("[ALL EVENTS LIFETIME — VERIFIED]")
        for ev in (fin.get("all_events") or [])[-50:]:
            lines.append(f"  {ev}")
        lines.append("")
        lines.append("[MANUAL_60_SECOND_TEST]")
        lines.append(json.dumps(snap.get("test_trade") or {}, ensure_ascii=False, indent=2))
        lines.append("")
        lines.append("[TRADING_READINESS]")
        lines.append(json.dumps(snap.get("diagnostics") or {}, ensure_ascii=False, indent=2))
        lines.append("")
        lines.append("[RISK]")
        lines.append(json.dumps(snap.get("risk") or {}, ensure_ascii=False, indent=2))
        lines.append("")
        lines.append("[STATUS]")
        lines.append(f"running={snap.get('running')} mode={snap.get('mode')} loop={snap.get('loop_count')} last_loop={snap.get('last_loop_at')} last_error={snap.get('last_error')}")
        lines.append(f"architecture={cfg.get('architecture')} capital={cfg.get('capital_usd')} lev={cfg.get('lev_cap')}")
        lines.append(f"assets={','.join(cfg.get('assets') or [])}")
        lines.append(f"sleeves={cfg.get('sleeves_enabled')} weights={cfg.get('sleeve_weights')}")
        lines.append("")
        lines.append("[ACCOUNT RAW]")
        for cur, v in acct.items():
            lines.append(f"  {cur}: {v}")
        lines.append("")
        lines.append(f"[SLEEVES] n={len(sleeves)}")
        for sl in sleeves:
            lines.append(f"  - {sl.get('id')} w={sl.get('weight')} cap={sl.get('capital')} title={sl.get('title')}")
        lines.append("")
        lines.append(f"[ORDERS_LOG] n={len(orders)}")
        for o in orders[-100:]:
            lines.append(f"  {o}")
        lines.append("")
        lines.append(f"[ENGINE EVENTS] n={len(events)}")
        for e in events[-50:]:
            lines.append(f"  {e.get('ts')} [{e.get('kind')}] {e.get('msg')}")
        lines.append("")
        lines.append(f"[TELEGRAM JOURNAL] n={len(self.journal)}")
        for x in self.journal[-100:]:
            lines.append(f"  {x}")
        lines.append("")
        lines.append(f"[TG_STATS] {self.stats}")
        lines.append("")
        lines.append("END OF COMPREHENSIVE REPORT — All PnL uses actual fills; unknown stays 'نامشخص', never fabricated zero.")
        return "\n".join(lines)

    # ── hooks ─────────────────────────────────────────────────────────────
    def on_boot(self, snap: dict) -> None:
        if not self.enabled:
            return
        cfg = snap.get("config") or {}
        assets = cfg.get("assets") or []
        fin = snap.get("financial") or {}
        msg = (
            f"🚀 <b>SUPER BOT ONLINE</b>\n"
            f"🕐 {utcnow()}\n"
            f"pairs: <b>{len(assets)}</b> · sleeves: <b>{len(cfg.get('sleeves_enabled') or [])}</b>\n"
            f"سرمایه تخصیصی: ${ _f(cfg.get('capital_usd'))} · برآورد ربات: ${ _f((snap.get('risk') or {}).get('estimated_bot_equity'))}\n"
            f"حساب USDC eq: ${ _f((snap.get('account', {}).get('USDC') or {}).get('equity'))}\n"
            f"گزارش مالی ۶ساعت: ${ _f(fin.get('verified_closed_net_usdc'))} (بسته‌شده تأییدشده)\n"
            f"trade_alerts=per-fill compact sample style + exact capital & P&L · file every {self.full_report_hours:g}h\n"
            f"Web: {_esc(self.dashboard_url)}"
        )
        self.send(msg)
        self.send_panel()

    def on_cycle(self, result: dict, *, loop_count: int = 0, error: Optional[str] = None) -> None:
        if not self.enabled:
            return
        now = time.time()
        if error:
            self.stats["cycles_err"] += 1
            if error != self._last_problem or now - self._last_problem_notice >= 600:
                self.send(f"🚨 <b>CYCLE ERROR</b>\n🕐 {utcnow()} · loop {_esc(loop_count)}\n<code>{_esc(error)[:1500]}</code>", reply_markup=self._kb())
                self._last_problem, self._last_problem_notice = error, now
            return
        self.stats["cycles_ok"] += 1
        actions = result.get("actions") or []
        for action in actions:
            if (action.get("status") or "").lower() not in TRADE_STATUSES:
                self.stats["orders_skip"] += 1

        fills = [a for a in actions if float(a.get("filled_amount") or 0) > 0]
        fills += result.get("recovered_fills") or []
        failures = [a for a in actions if a.get("status") in ("error", "failed")]

        signature = " | ".join(str(a.get("error")) for a in failures)
        if failures and signature == self._last_problem and now - self._last_problem_notice < 600:
            failures = []
        if failures:
            self._last_problem, self._last_problem_notice = signature, now

        # FIX: Always send fills, never suppress even if financial reporting exists
        # Try to use enriched financial event if available, otherwise compact action
        snapshot = None
        try:
            if self._engine:
                snapshot = self._engine.snapshot()
        except Exception:
            snapshot = result

        for fill in fills:
            key = f"{fill.get('order_id')}:{fill.get('filled_amount')}"
            if key in self._financial_alerted:
                continue
            # Try enriched
            enriched = None
            if self._engine and hasattr(self._engine, "financial"):
                try:
                    enriched = self._engine.financial.journal.event(fill.get("order_id"))
                except Exception:
                    enriched = None
            if enriched:
                text = self.format_financial_event(enriched, snapshot)
                self._financial_alerted.add(key)
            else:
                text = self.format_trade_action_compact(fill, snapshot)
                # Don't add to financial_alerted yet? Add to avoid duplicate immediate, but financial queue may still send enriched later
                # We'll add to a separate set for immediate, and let financial still send later as update
                # For now, add to financial_alerted to avoid double immediate, but financial on_financial_event will skip if already alerted
                # So we need to allow financial to override: we will NOT add here, let financial handle final dedup via its own set
                # Instead track separately
                pass
            self.send(text, reply_markup=self._kb())
            self.journal.append({
                "ts": utcnow(),
                "asset": fill.get("asset"),
                "status": fill.get("status"),
                "amount": fill.get("filled_amount"),
                "notional_usd": fill.get("notional_usd"),
                "price": fill.get("price"),
                "order_id": fill.get("order_id"),
            })
            self.journal = self.journal[-500:]
            self.stats["orders_ok"] += 1
            self.stats["trades_alerted"] += 1

        if failures:
            text = self.format_trade_alert(failures, loop_count=loop_count, snapshot=snapshot)
            if text:
                self.send(text, reply_markup=self._kb())

        dg = result.get("diagnostics") or {}
        opened = dg.get("market_open_count", 0)
        if self._previous_open_count == 0 and opened > 0:
            self.send(f"▶️ <b>بازگشایی بازار تست‌نت تأیید شد</b>\nبازار باز: {opened}\nمسیر معامله: {_esc(dg.get('trading_state'))}\nورود فقط پس از کنترل داده تازه، حداقل لات و ریسک مجاز است.", reply_markup=self._kb())
        self._previous_open_count = opened
        halted = [a for a in actions if a.get("market_state") == "halted"]
        if halted and now - self._last_halt_notice >= 6 * 3600:
            self.send(f"⏸️ <b>معامله واقعی مسدود است؛ وب‌سرویس روشن است</b>\nDeribit testnet: halted={len(halted)}/{len(actions)}\nنماد دارای سیگنال: {dg.get('active_signal_assets', 0)}\nتوقف صرافی قابل رفع از داخل ربات نیست. بررسی خودکار هر چرخه ادامه دارد؛ هیچ معاملهٔ شبیه‌سازی واقعی گزارش نمی‌شود.", reply_markup=self._kb())
            self._last_halt_notice = now
        problem = dg.get("primary_blocker")
        if problem and problem not in ("venue_halted", "no_open_markets") and not failures:
            if problem != self._last_problem or now - self._last_problem_notice >= 6 * 3600:
                self.send(f"🛠️ <b>مانع مسیر معامله</b>\n<code>{_esc(problem)}</code>\nstate={_esc(dg.get('trading_state'))} · loop={loop_count}", reply_markup=self._kb())
                self._last_problem, self._last_problem_notice = problem, now

    def send_full_file_report(self) -> None:
        if not self.enabled or not self._engine:
            return
        snap = self._engine.snapshot()
        content = self.build_full_report_text(snap)
        fname = f"zenith_super_comprehensive_{utc_stamp()}.txt"
        fin = snap.get("financial") or {}
        cap = (
            f"📄 گزارش جامع ۶ساعته SUPER · {utcnow()}\n"
            f"سرمایه تخصیصی ${ _f(snap.get('config', {}).get('capital_usd'))} · برآورد ربات ${ _f((snap.get('risk') or {}).get('estimated_bot_equity'))} · حساب USDC ${ _f((snap.get('account', {}).get('USDC') or {}).get('equity'))}\n"
            f"بسته‌شده تأییدشده ۶ساعت: ${ _f(fin.get('verified_closed_net_usdc'))} · کارمزد: ${ _f(fin.get('period_fees_usdc'))} · غیرمحقق: ${ _f(fin.get('unrealized_gross_usdc'))}\n"
            f"معاملات دوره: {fin.get('execution_count',0)} · پوزیشن باز: {len(snap.get('positions') or [])} · loops={snap.get('loop_count')}"
        )
        self.send_document(fname, content, caption=cap)

    # ── polling ───────────────────────────────────────────────────────────
    def _poll_loop(self) -> None:
        log.info("telegram poll started")
        while not self._stop.is_set():
            try:
                data = self._api(
                    "getUpdates",
                    as_json=True,
                    count_stats=False,
                    offset=self._offset,
                    timeout=25,
                    allowed_updates=["message", "callback_query"],
                )
                for upd in data.get("result") or []:
                    self._offset = max(self._offset, int(upd.get("update_id", 0)) + 1)
                    if "callback_query" in upd:
                        self._on_callback(upd["callback_query"])
                        continue
                    msg = upd.get("message") or {}
                    chat = msg.get("chat") or {}
                    if str(chat.get("id")) != str(self.chat_id):
                        continue
                    text = (msg.get("text") or "").strip()
                    if text.startswith("/"):
                        parts = text.split(maxsplit=1)
                        self._on_command(parts[0].split("@")[0].lower(), parts[1] if len(parts) > 1 else "")
            except Exception:
                log.exception("tg poll")
                time.sleep(3)
            time.sleep(0.2)

    def _on_callback(self, cq: dict) -> None:
        data = cq.get("data") or ""
        cid = cq.get("id")
        msg = cq.get("message") or {}
        chat = msg.get("chat") or {}
        if str(chat.get("id")) != str(self.chat_id):
            self.answer_callback(cid, "unauthorized", True)
            return
        if not data.startswith("panel:"):
            self.answer_callback(cid, "unknown")
            return
        action = data.split(":", 1)[1]
        self.answer_callback(cid, action)
        eng = self._engine
        try:
            if action == "home":
                self.send_panel()
            elif action == "status":
                self.send(self.format_status(eng.snapshot() if eng else {}), reply_markup=self._kb())
            elif action == "balance":
                self.send(self.format_balance(eng.snapshot() if eng else {}), reply_markup=self._kb())
            elif action == "positions":
                self.send(self.format_positions(eng.snapshot() if eng else {}), reply_markup=self._kb())
            elif action == "sleeves":
                self.send(self.format_sleeves(eng.snapshot() if eng else {}), reply_markup=self._kb())
            elif action == "stats":
                self.send(self.format_stats(eng.snapshot() if eng else {}), reply_markup=self._kb())
            elif action == "trades":
                self.send(self.format_trades_brief(), reply_markup=self._kb())
            elif action == "health":
                snap = eng.snapshot() if eng else {}
                self.send(
                    f"❤️ running={_esc(snap.get('running'))} mode={_esc(snap.get('mode'))}\n"
                    f"loops={_esc(snap.get('loop_count'))} err={_esc(snap.get('last_error') or 'none')}\n"
                    f"pairs={len((snap.get('config') or {}).get('assets') or [])}",
                    reply_markup=self._kb(),
                )
            elif action == "file":
                self.send("⏳ ساخت گزارش جامع ۶ساعته…")
                self.send_full_file_report()
            elif action == "tick":
                if not eng:
                    self.send("engine offline")
                    return
                self.send("⏳ tick…")
                res = eng.once()
                acts = res.get("actions") or []
                trades = [a for a in acts if (a.get("status") or "").lower() in TRADE_STATUSES]
                if trades:
                    snap = eng.snapshot()
                    self.send(self.format_trade_alert(trades, loop_count=eng.state.loop_count, snapshot=snap), reply_markup=self._kb())
                else:
                    n_long = 0
                    for sl in res.get("sleeves") or []:
                        for p in (sl.get("per_asset") or {}).values():
                            if float((p or {}).get("side") or 0) > 0:
                                n_long += 1
                    self.send(
                        f"⚡ tick done · no new trades\n"
                        f"sleeve-longs≈{n_long} · actions={len(acts)}",
                        reply_markup=self._kb(),
                    )
            elif action == "testtrade":
                self.test_confirmation()
            elif action == "testconfirm":
                if not eng:
                    self.send("موتور در دسترس نیست.")
                    return
                state = eng.test_trader.start()
                if not state.get("active"):
                    self.send(self.format_test_trade(state), reply_markup=self._kb())
            elif action == "teststatus":
                self.send(self.format_test_trade(eng.test_trader.snapshot() if eng else {}), reply_markup=self._kb())
            elif action == "testclose":
                if eng:
                    self.send(self.format_test_trade(eng.test_trader.request_close()), reply_markup=self._kb())
            elif action == "start":
                if eng:
                    eng.start()
                self.send("▶️ start requested", reply_markup=self._kb())
            elif action == "stop":
                if eng:
                    eng.stop()
                self.send("⏹ stop requested", reply_markup=self._kb())
            else:
                self.send(f"unknown {action}")
        except Exception as e:
            log.exception("callback %s", action)
            self.send(f"❌ {_esc(e)}", reply_markup=self._kb())

    def _on_command(self, cmd: str, argument: str = "") -> None:
        if cmd == "/testtrade" or (cmd == "/start" and argument.strip() == "test60"):
            self.test_confirmation()
            return
        if cmd == "/teststatus" or (cmd == "/start" and argument.strip() == "teststatus"):
            self.send(self.format_test_trade(self._engine.test_trader.snapshot() if self._engine else {}), reply_markup=self._kb())
            return
        if cmd in ("/start", "/help", "/panel"):
            self.send_panel()
            return
        eng = self._engine
        if cmd in ("/status", "/report"):
            self.send(self.format_status(eng.snapshot() if eng else {}), reply_markup=self._kb())
        elif cmd == "/sleeves":
            self.send(self.format_sleeves(eng.snapshot() if eng else {}), reply_markup=self._kb())
        elif cmd == "/positions":
            self.send(self.format_positions(eng.snapshot() if eng else {}), reply_markup=self._kb())
        elif cmd == "/balance":
            self.send(self.format_balance(eng.snapshot() if eng else {}), reply_markup=self._kb())
        elif cmd == "/stats":
            self.send(self.format_stats(eng.snapshot() if eng else {}), reply_markup=self._kb())
        elif cmd == "/trades":
            self.send(self.format_trades_brief(), reply_markup=self._kb())
        elif cmd == "/file":
            self.send("⏳ ساخت گزارش جامع ۶ساعته…")
            self.send_full_file_report()
        elif cmd == "/tick":
            if not eng:
                return
            self.send("⏳ tick…")
            res = eng.once()
            trades = [a for a in (res.get("actions") or []) if (a.get("status") or "").lower() in TRADE_STATUSES]
            if trades:
                snap = eng.snapshot()
                self.send(self.format_trade_alert(trades, loop_count=eng.state.loop_count, snapshot=snap), reply_markup=self._kb())
            else:
                self.send("tick done — no trades", reply_markup=self._kb())
        elif cmd == "/health":
            snap = eng.snapshot() if eng else {}
            self.send(f"❤️ {_esc(snap.get('mode'))} loops={_esc(snap.get('loop_count'))}", reply_markup=self._kb())
        elif cmd == "/errors":
            snap = eng.snapshot() if eng else {}
            ev = [e for e in (snap.get("events") or []) if e.get("kind") == "error"][-20:]
            self.send(f"🧯 <code>{_esc(ev)[:3500]}</code>", reply_markup=self._kb())
        else:
            self.send_panel(f"unknown {_esc(cmd)}")

    def _bg_loop(self) -> None:
        time.sleep(10)
        try:
            if self._engine:
                self.on_boot(self._engine.snapshot())
                self._last_heartbeat = time.time()
                self._last_full_report = time.time()
        except Exception:
            log.exception("boot")
        while not self._stop.is_set():
            try:
                now = time.time()
                if now - self._last_full_report >= self.full_report_hours * 3600:
                    self.send_full_file_report()
                    self._last_full_report = now
                if now - self._last_heartbeat >= self.heartbeat_minutes * 60:
                    if self._engine:
                        self.send(self.format_status(self._engine.snapshot()), reply_markup=self._kb())
                    self._last_heartbeat = now
            except Exception:
                log.exception("bg")
            self._stop.wait(20)

    # ── status formatters ─────────────────────────────────────────────────
    def format_status(self, snap: dict) -> str:
        cfg = snap.get("config") or {}
        acct = snap.get("account") or {}
        usdc = acct.get("USDC") or {}
        assets = cfg.get("assets") or []
        pos = snap.get("positions") or []
        st = self.stats
        fin = snap.get("financial") or {}
        lines = [
            f"📊 <b>وضعیت SUPER</b>",
            f"🕐 {utcnow()}",
            f"",
            f"{'🟢 RUNNING' if snap.get('running') else '🔴 STOPPED'} · <b>{_esc(snap.get('mode'))}</b>",
            f"loops: <code>{_esc(snap.get('loop_count'))}</code>",
            f"last: <code>{_esc(snap.get('last_loop_at'))}</code>",
            f"error: <code>{_esc(snap.get('last_error') or 'none')}</code>",
            f"",
            f"<b>سرمایه/ریسک</b>",
            f"تخصیصی ربات: ${_esc(cfg.get('capital_usd'))} · lev≤{_esc(cfg.get('lev_cap'))}",
            f"برآورد موجودی ربات: ${_f(snap.get('risk', {}).get('estimated_bot_equity'))}",
            f"حساب USDC eq=<code>{_esc(usdc.get('equity'))}</code> avail=<code>{_esc(usdc.get('available'))}</code>",
            f"پوزیشن باز: <b>{len(pos)}</b>",
            f"",
            f"<b>آمار سفارش</b> ✅{st['orders_ok']} ⏭{st['orders_skip']} ❌{st['orders_err']}",
            f"alerts: {st['trades_alerted']} · files: {st['files_sent']}",
        ]
        if fin:
            lines.extend([
                f"",
                f"<b>گزارش مالی (۶ساعت اخیر)</b>",
                f"معاملات: {fin.get('execution_count', 0)} · بسته‌شده: {fin.get('closed_event_count', 0)}",
                f"سود/زیان بسته‌شده تأییدشده: {_f(fin.get('verified_closed_net_usdc'))} USDC",
                f"کارمزد دوره: {_f(fin.get('period_fees_usdc'))} USDC",
                f"غیرمحقق: {_f(fin.get('unrealized_gross_usdc'))} USDC",
            ])
        dg = snap.get("diagnostics") or {}
        lines.extend([
            "", "<b>آمادگی واقعی معامله</b>",
            f"state=<code>{_esc(dg.get('trading_state'))}</code> · blocker=<code>{_esc(dg.get('primary_blocker') or 'none')}</code>",
            f"بازار باز {dg.get('market_open_count', 0)}/{len(assets)} · halted={dg.get('market_halted_count', 0)}",
            f"نماد دارای سیگنال: {dg.get('active_signal_assets', 0)} · fill تأییدشده: {dg.get('confirmed_fill_count', 0)}",
            f"آخرین fill: {_esc(snap.get('last_fill_at') or 'none')}",
        ])
        if self.dashboard_url:
            lines.append(f"Web: {_esc(self.dashboard_url)}")
        return "\n".join(lines)

    def test_confirmation(self):
        if not self._engine:
            self.send("موتور در دسترس نیست.")
            return
        if self._engine.test_trader.is_active():
            self.send(self.format_test_trade(self._engine.test_trader.snapshot()), reply_markup=self._kb())
            return
        settings = self._engine.settings
        self.send(
            f"🧪 <b>تأیید معامله تست — فقط پول آزمایشی</b>\n"
            f"نماد: <b>{_esc(settings.test_trade_asset.upper())} USDC perpetual</b>\n"
            f"حجم: کوچک‌ترین لات معتبر صرافی، حداکثر ${_f(settings.test_trade_max_notional_usd)}\n"
            f"خروج: <b>۶۰ ثانیه پس از پرشدن ورود</b>، فقط reduce-only.\n"
            f"در این مدت سفارش‌های پنج استراتژی موقتاً متوقف می‌شوند.\n"
            f"پوزیشن قبلی روی نماد تست مجاز نیست. توقف صرافی یا قطع سرویس می‌تواند خروج را به تأخیر بیندازد.\n\n"
            f"برای ارسال سفارش واقعی تست‌نت، تأیید کنید:",
            reply_markup={"inline_keyboard": [
                [{"text": "✅ تأیید و شروع تست ۶۰ثانیه", "callback_data": "panel:testconfirm"}],
                [{"text": "انصراف / پنل", "callback_data": "panel:home"}],
            ]},
        )

    def format_test_trade(self, state: dict) -> str:
        entry = state.get("entry") or {}
        exits = state.get("exits") or []
        return (
            f"⏱ <b>وضعیت معامله تست</b>\n"
            f"state=<code>{_esc(state.get('status'))}</code> · active={_esc(state.get('active'))}\n"
            f"نماد: {_esc(state.get('instrument'))}\n"
            f"ورود پرشده: {_f(state.get('entry_filled_amount'), 6)}\n"
            f"خروج پرشده: {_f(state.get('exit_filled_amount'), 6)}\n"
            f"زمان ورود: {_esc(state.get('opened_at'))}\n"
            f"زمان برنامه‌ریزی خروج: {_esc(state.get('close_due_at'))}\n"
            f"زمان بسته‌شدن: {_esc(state.get('closed_at'))}\n"
            f"باقی‌مانده تا خروج: {_f(state.get('seconds_remaining'), 0)} ثانیه\n"
            f"order ورود: <code>{_esc(entry.get('order_id'))}</code>\n"
            f"order خروج: <code>{_esc(', '.join(str(x.get('order_id')) for x in exits if x.get('order_id')) or 'none')}</code>\n"
            f"{_esc(state.get('warning') or state.get('reason') or '')}"
        )

    def on_test_trade(self, event: str, state: dict):
        if not self.enabled:
            return
        heads = {
            "opened": "🧪 معامله تست باز شد", "opened_reconciled": "🧪 ورود تست با صرافی تطبیق داده شد",
            "closed": "✅ خروج معامله تست تأیید شد", "closed_external": "ℹ️ حساب بدون long تست است؛ خروج تایمر ثبت نشد",
            "recovered": "↩️ تست نیمه‌تمام از برچسب صرافی بازیابی شد",
            "unfilled": "⏸ ورود تست پر نشد؛ معامله ایجاد نشد", "failed": "❌ سفارش تست رد شد",
            "uncertain": "🚨 نتیجه سفارش تست نامشخص است", "warning": "🚨 خروج/وضعیت تست نیازمند توجه است",
        }
        for phase, receipts in (("entry", [state.get("entry") or {}]), ("exit", state.get("exits") or [])):
            for receipt in receipts:
                order_id = receipt.get("order_id")
                if not order_id or not receipt.get("filled_amount") or order_id in self._test_alerted_ids:
                    continue
                self._test_alerted_ids.add(order_id)
                self.stats["orders_ok"] += 1
                self.journal.append({"ts": utcnow(), "asset": state.get("asset"), "status": "test_"+phase,
                                     "amount": receipt.get("filled_amount"), "price": receipt.get("average_price"),
                                     "notional_usd": float(receipt.get("filled_amount") or 0)*float(receipt.get("average_price") or 0),
                                     "order_id": order_id, "pnl": None})
                self.journal = self.journal[-500:]
        self.send(f"<b>{_esc(heads.get(event, event))}</b>\n{self.format_test_trade(state)}", reply_markup=self._kb())


_reporter: Optional[TelegramReporter] = None


def get_reporter() -> Optional[TelegramReporter]:
    return _reporter


def init_reporter_from_settings(settings) -> Optional[TelegramReporter]:
    global _reporter
    _reporter = TelegramReporter(
        token=getattr(settings, "telegram_bot_token", "") or "",
        chat_id=getattr(settings, "telegram_chat_id", "") or "",
        enabled=bool(getattr(settings, "telegram_enabled", True)),
        dashboard_url=getattr(settings, "public_dashboard_url", "") or "",
        send_every_n_loops=int(getattr(settings, "telegram_every_n_loops", 1) or 1),
        heartbeat_minutes=int(getattr(settings, "telegram_heartbeat_minutes", 30) or 30),
        full_report_hours=float(getattr(settings, "telegram_full_report_hours", 6.0) or 6.0),
        trade_only=bool(getattr(settings, "telegram_trade_only", True)),
        poll_commands=bool(getattr(settings, "telegram_commands", True)),
    )
    return _reporter
