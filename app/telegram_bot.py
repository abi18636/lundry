
"""
Telegram control panel for Zenith SUPER bot.

- Glass (inline) keyboard panel
- Trade alerts ONLY when real order success/fail (not every flat cycle)
- Full text-file report every N hours or via panel button
- Commands still work
"""
from __future__ import annotations

import html
import io
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
    "bought", "sold", "reduced", "closed",
    "dry_run_buy", "dry_run_sell", "dry_run_close",
    "error", "failed",
}


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
        return f"{float(x):.{d}f}"
    except Exception:
        return "—"


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
        # cumulative trade journal (in-memory + engine snapshot)
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
            data = {"ok": False, "description": str(e)}
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
            # only last chunk gets keyboard
            if reply_markup and i == len(chunks) - 1:
                import json as _json
                params["reply_markup"] = _json.dumps(reply_markup)
            data = self._api("sendMessage", **params)
            if not data.get("ok"):
                plain = _plain(ch)
                params = dict(chat_id=self.chat_id, text=plain[:4000], disable_web_page_preview=True)
                if reply_markup and i == len(chunks) - 1:
                    import json as _json
                    params["reply_markup"] = _json.dumps(reply_markup)
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
            # fallback: chunk as messages
            self.send(f"<b>{_esc(filename)}</b>\n<pre>{_esc(content[:3500])}</pre>")
            return False
        except Exception as e:
            log.exception("sendDocument: %s", e)
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
        """Inline glass-style control panel."""
        return {
            "inline_keyboard": [
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
                    {"text": "▶️ Start", "callback_data": "panel:start"},
                    {"text": "⏹ Stop", "callback_data": "panel:stop"},
                    {"text": "🔄 پنل", "callback_data": "panel:home"},
                ],
                (
                    [[{"text": "🌐 داشبورد وب", "url": self.dashboard_url}]]
                    if self.dashboard_url
                    else []
                )[0]
                if self.dashboard_url
                else [],
            ]
        }

    def _kb(self) -> dict:
        kb = self.main_keyboard()
        # clean empty rows
        kb["inline_keyboard"] = [row for row in kb["inline_keyboard"] if row]
        return kb

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

    # ── formatters ────────────────────────────────────────────────────────
    def format_status(self, snap: dict) -> str:
        cfg = snap.get("config") or {}
        acct = snap.get("account") or {}
        usdc = acct.get("USDC") or {}
        assets = cfg.get("assets") or []
        pos = snap.get("positions") or []
        st = self.stats
        lines = [
            f"📊 <b>وضعیت SUPER</b>",
            f"🕐 {utcnow()}",
            f"",
            f"{'🟢 RUNNING' if snap.get('running') else '🔴 STOPPED'} · "
            f"<b>{_esc(snap.get('mode'))}</b>",
            f"loops: <code>{_esc(snap.get('loop_count'))}</code>",
            f"last: <code>{_esc(snap.get('last_loop_at'))}</code>",
            f"error: <code>{_esc(snap.get('last_error') or 'none')}</code>",
            f"",
            f"<b>سرمایه/ریسک</b>",
            f"capital ${_esc(cfg.get('capital_usd'))} · lev≤{_esc(cfg.get('lev_cap'))}",
            f"dry_run={_esc(cfg.get('dry_run'))} · trading={_esc(cfg.get('trading_enabled'))}",
            f"pairs: <b>{len(assets)}</b>",
            f"",
            f"<b>موجودی USDC</b> eq=<code>{_esc(usdc.get('equity'))}</code> "
            f"avail=<code>{_esc(usdc.get('available'))}</code>",
            f"پوزیشن باز: <b>{len(pos)}</b>",
            f"",
            f"<b>آمار سفارش</b> ✅{st['orders_ok']} ⏭{st['orders_skip']} ❌{st['orders_err']}",
            f"alerts: {st['trades_alerted']} · files: {st['files_sent']}",
        ]
        if self.dashboard_url:
            lines.append(f"Web: {_esc(self.dashboard_url)}")
        return "\n".join(lines)

    def format_balance(self, snap: dict) -> str:
        acct = snap.get("account") or {}
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
        return "\n".join(lines)

    def format_positions(self, snap: dict) -> str:
        pos = snap.get("positions") or []
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
                f"  mark={_esc(p.get('mark_price'))} lev={_esc(p.get('leverage'))}\n"
                f"  pnl={_esc(p.get('total_profit_loss'))} "
                f"uPnL={_esc(p.get('floating_profit_loss'))}"
            )
        lines.append(f"\nΣ pnl ≈ <b>{total_pnl:.4f}</b>")
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
        # top net
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
        pnl = sum(float(x.get("pnl") or 0) for x in j)
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
            f"wins={wins} losses={losses}",
            f"Σ pnl (logged)={pnl:.4f}",
            f"bot loops={_esc(snap.get('loop_count'))}",
            f"open positions={len(snap.get('positions') or [])}",
        ]
        return "\n".join(lines)

    def format_trades_brief(self) -> str:
        j = self.journal[-15:]
        lines = [f"🧾 <b>آخرین معاملات</b> (ژورنال {len(self.journal)})", ""]
        if not j:
            lines.append("هنوز معاملهٔ ثبت‌شده‌ای نیست")
            return "\n".join(lines)
        for x in reversed(j):
            lines.append(
                f"• {_esc(x.get('ts'))} {_esc(x.get('asset'))} "
                f"<code>{_esc(x.get('status'))}</code> "
                f"${_f(x.get('notional_usd'))} {_esc(x.get('error') or '')}"
            )
        return "\n".join(lines)

    def format_trade_alert(self, actions: List[dict], *, loop_count: int = 0) -> str:
        trades = [a for a in actions if (a.get("status") or "").lower() in TRADE_STATUSES]
        if not trades:
            return ""
        errs = [a for a in trades if (a.get("status") or "").lower() in ("error", "failed")]
        oks = [a for a in trades if a not in errs]
        head = "🔴 <b>TRADE ERROR</b>" if errs and not oks else (
            "🟢 <b>TRADE</b>" if oks and not errs else "🟠 <b>TRADE MIXED</b>"
        )
        lines = [head, f"🕐 {utcnow()} · loop {_esc(loop_count)}", ""]
        for a in trades:
            st = (a.get("status") or "").lower()
            icon = "❌" if st in ("error", "failed") else "✅"
            lines.append(
                f"{icon} <b>{_esc(a.get('asset'))}</b> <code>{_esc(a.get('status'))}</code>\n"
                f"  amt={_esc(a.get('amount'))} ${_f(a.get('notional_usd'))} "
                f"px={_f(a.get('price'), 4)}"
            )
            if a.get("error"):
                lines.append(f"  {_esc(a.get('error'))[:240]}")
            # journal
            self.journal.append({
                "ts": utcnow(),
                "asset": a.get("asset"),
                "status": a.get("status"),
                "amount": a.get("amount"),
                "notional_usd": a.get("notional_usd"),
                "price": a.get("price"),
                "error": a.get("error"),
                "pnl": None,
            })
            self.journal = self.journal[-500:]
            if st in ("error", "failed"):
                self.stats["orders_err"] += 1
            else:
                self.stats["orders_ok"] += 1
        self.stats["trades_alerted"] += 1
        return "\n".join(lines)

    def build_full_report_text(self, snap: dict) -> str:
        cfg = snap.get("config") or {}
        acct = snap.get("account") or {}
        pos = snap.get("positions") or []
        sleeves = snap.get("sleeves") or []
        nb = snap.get("net_book") or {}
        actions = snap.get("actions") or []
        events = snap.get("events") or []
        orders = snap.get("orders_log") or []
        lines = []
        lines.append("=" * 72)
        lines.append("ZENITH SUPER BOT — FULL REPORT")
        lines.append(f"generated: {utcnow()}")
        lines.append("=" * 72)
        lines.append("")
        lines.append("[STATUS]")
        lines.append(f"running={snap.get('running')} mode={snap.get('mode')}")
        lines.append(f"loop_count={snap.get('loop_count')} last_loop={snap.get('last_loop_at')}")
        lines.append(f"last_error={snap.get('last_error')}")
        lines.append(f"architecture={cfg.get('architecture')}")
        lines.append(f"capital_usd={cfg.get('capital_usd')} lev_cap={cfg.get('lev_cap')} long_only={cfg.get('long_only')}")
        lines.append(f"dry_run={cfg.get('dry_run')} trading_enabled={cfg.get('trading_enabled')}")
        assets = cfg.get("assets") or []
        lines.append(f"n_assets={len(assets)}")
        lines.append(f"assets={','.join(assets)}")
        lines.append(f"sleeves_enabled={cfg.get('sleeves_enabled')}")
        lines.append(f"sleeve_weights={cfg.get('sleeve_weights')}")
        lines.append("")
        lines.append("[ACCOUNT]")
        for cur, v in acct.items():
            lines.append(f"  {cur}: {v}")
        lines.append("")
        lines.append(f"[POSITIONS] n={len(pos)}")
        total_pnl = 0.0
        for p in pos:
            try:
                total_pnl += float(p.get("total_profit_loss") or 0)
            except Exception:
                pass
            lines.append(
                f"  {p.get('instrument_name')} dir={p.get('direction')} "
                f"size={p.get('size_currency') or p.get('size')} "
                f"mark={p.get('mark_price')} pnl={p.get('total_profit_loss')} "
                f"upnl={p.get('floating_profit_loss')} lev={p.get('leverage')}"
            )
        lines.append(f"  TOTAL_pnl≈{total_pnl}")
        lines.append("")
        lines.append(f"[SLEEVES] n={len(sleeves)}")
        for sl in sleeves:
            lines.append(
                f"  - {sl.get('id')} w={sl.get('weight')} cap={sl.get('capital')} "
                f"title={sl.get('title')} notes={sl.get('notes')}"
            )
            pa = sl.get("per_asset") or {}
            longs = [a for a, p in pa.items() if float((p or {}).get("side") or 0) > 0]
            lines.append(f"    long_assets={longs} n_long={len(longs)}/{len(pa)}")
            # compact per-asset sides
            for a, p in sorted(pa.items()):
                if float((p or {}).get("side") or 0) == 0 and float((p or {}).get("notional_usd") or 0) == 0:
                    continue
                lines.append(
                    f"    {a}: side={p.get('side')} ntl={p.get('notional_usd')} "
                    f"coin={p.get('target_coin')} detail={p.get('detail')}"
                )
        lines.append("")
        lines.append("[NET_BOOK]")
        for a, b in sorted(nb.items(), key=lambda kv: -float((kv[1] or {}).get("notional_usd") or 0)):
            n = float((b or {}).get("notional_usd") or 0)
            if n <= 0:
                continue
            lines.append(
                f"  {a}: coin={b.get('target_coin')} ntl={n} px={b.get('price')} "
                f"contributors={list((b.get('contributors') or {}).keys())}"
            )
        lines.append("")
        lines.append(f"[RECENT_ACTIONS] n={len(actions)}")
        for a in actions[-40:]:
            lines.append(
                f"  {a.get('asset')} status={a.get('status')} amt={a.get('amount')} "
                f"ntl={a.get('notional_usd')} err={a.get('error')}"
            )
        lines.append("")
        lines.append(f"[ORDERS_LOG] n={len(orders)}")
        for o in orders[-50:]:
            lines.append(f"  {o}")
        lines.append("")
        lines.append(f"[EVENTS] n={len(events)}")
        for e in events[-40:]:
            lines.append(f"  {e.get('ts')} [{e.get('kind')}] {e.get('msg')}")
        lines.append("")
        lines.append(f"[TRADE_JOURNAL] n={len(self.journal)}")
        wins = losses = 0
        for x in self.journal:
            lines.append(f"  {x}")
        lines.append("")
        lines.append(f"[TG_STATS] {self.stats}")
        lines.append("")
        lines.append("END OF REPORT")
        return "\n".join(lines)

    # ── hooks ─────────────────────────────────────────────────────────────
    def on_boot(self, snap: dict) -> None:
        if not self.enabled:
            return
        cfg = snap.get("config") or {}
        assets = cfg.get("assets") or []
        msg = (
            f"🚀 <b>SUPER BOT ONLINE</b>\n"
            f"🕐 {utcnow()}\n"
            f"pairs: <b>{len(assets)}</b> · sleeves: <b>{len(snap.get('sleeves') or [])}</b>\n"
            f"capital ${_esc(cfg.get('capital_usd'))} · dry={_esc(cfg.get('dry_run'))}\n"
            f"trade_alerts=only · full_file_report every {self.full_report_hours:g}h\n"
            f"Web: {_esc(self.dashboard_url)}"
        )
        self.send(msg)
        self.send_panel()

    def on_cycle(self, result: dict, *, loop_count: int = 0, error: Optional[str] = None) -> None:
        if not self.enabled:
            return
        if error:
            self.stats["cycles_err"] += 1
            self.send(
                f"🚨 <b>CYCLE ERROR</b>\n🕐 {utcnow()} · loop {_esc(loop_count)}\n"
                f"<code>{_esc(error)[:1500]}</code>",
                reply_markup=self._kb(),
            )
            return
        self.stats["cycles_ok"] += 1
        actions = result.get("actions") or []
        # count skips
        for a in actions:
            st = (a.get("status") or "").lower()
            if st in TRADE_STATUSES:
                continue
            self.stats["orders_skip"] += 1
        trades = [a for a in actions if (a.get("status") or "").lower() in TRADE_STATUSES]
        if trades:
            msg = self.format_trade_alert(trades, loop_count=loop_count)
            if msg:
                self.send(msg, reply_markup=self._kb())
        # One compact notice if venue markets are halted (testnet maintenance)
        halted = [a for a in actions if str(a.get("status") or "").startswith("market_") and a.get("status") != "market_open"]
        if halted and (time.time() - self._last_halt_notice) > 6 * 3600:
            st = halted[0].get("status") or "market_halted"
            self.send(
                f"⏸️ <b>Venue markets not open</b>\n"
                f"status=<code>{_esc(st)}</code> · pairs_flagged={len(halted)}/{len(actions)}\n"
                f"Orders paused until Deribit state=open.\n"
                f"Signals still computed; no spam each loop.",
                reply_markup=self._kb(),
            )
            self._last_halt_notice = time.time()
        elif not self.trade_only:
            pass

    def send_full_file_report(self) -> None:
        if not self.enabled or not self._engine:
            return
        snap = self._engine.snapshot()
        content = self.build_full_report_text(snap)
        fname = f"zenith_super_report_{utc_stamp()}.txt"
        cap = (
            f"📄 گزارش کامل SUPER · {utcnow()}\n"
            f"pairs={len((snap.get('config') or {}).get('assets') or [])} · "
            f"pos={len(snap.get('positions') or [])} · loops={snap.get('loop_count')}"
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
                        self._on_command(text.split()[0].split("@")[0].lower())
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
                self.send("⏳ ساخت گزارش فایل…")
                self.send_full_file_report()
            elif action == "tick":
                if not eng:
                    self.send("engine offline")
                    return
                self.send("⏳ tick…")
                res = eng.once()
                # always acknowledge tick result briefly
                acts = res.get("actions") or []
                trades = [a for a in acts if (a.get("status") or "").lower() in TRADE_STATUSES]
                if trades:
                    self.send(self.format_trade_alert(trades, loop_count=eng.state.loop_count), reply_markup=self._kb())
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

    def _on_command(self, cmd: str) -> None:
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
            self.send("⏳…")
            self.send_full_file_report()
        elif cmd == "/tick":
            if not eng:
                return
            self.send("⏳ tick…")
            res = eng.once()
            trades = [a for a in (res.get("actions") or []) if (a.get("status") or "").lower() in TRADE_STATUSES]
            if trades:
                self.send(self.format_trade_alert(trades, loop_count=eng.state.loop_count), reply_markup=self._kb())
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
                # light heartbeat panel every heartbeat_minutes (not full spam)
                if now - self._last_heartbeat >= self.heartbeat_minutes * 60:
                    if self._engine:
                        self.send(self.format_status(self._engine.snapshot()), reply_markup=self._kb())
                    self._last_heartbeat = now
            except Exception:
                log.exception("bg")
            self._stop.wait(20)


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
