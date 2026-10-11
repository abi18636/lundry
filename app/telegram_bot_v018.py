"""
v018 Telegram Bot - 3 New Features:
1. Close button for each individual position
2. Version in report
3. Test trade active
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


def _f(x, d=2) -> str:
    try:
        v = float(x)
        if abs(v) >= 1000:
            return f"{v:,.{d}f}"
        return f"{v:.{d}f}"
    except Exception:
        return "—"


class TelegramReporter:
    def __init__(self, bot_token: str, chat_id: str, dashboard_url: str = "", full_report_hours: float = 6.0):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.dashboard_url = dashboard_url
        self.full_report_hours = full_report_hours
        self._engine = None
        self._stop = threading.Event()
        self._thread = None
        self._panel_msg_id = None
        self.stats = {"cycles_ok": 0, "cycles_err": 0, "orders_ok": 0, "orders_skip": 0, "orders_err": 0, "trades_alerted": 0, "sent": 0, "failed": 0, "files_sent": 0}
        self.journal = []
        self._last_heartbeat = 0
        self._last_full_report = 0
        self.enabled = bool(bot_token and chat_id)
        self.poll_commands = False
        # v018: Version tracking
        self.version_history = []
        self.current_version = "v018-10pairs-testtrade-telegram-close"

    def bind_engine(self, engine):
        self._engine = engine

    def main_keyboard(self, positions: List[dict] = None) -> dict:
        """v018: Add close buttons for each individual position"""
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
        ]
        
        # v018 NEW: Close buttons for each individual position
        if positions:
            # Filter non-zero positions
            active_pos = []
            for p in positions:
                try:
                    sz = float(p.get("size_currency") or p.get("size") or 0)
                    if abs(sz) > 1e-12:
                        active_pos.append(p)
                except:
                    active_pos.append(p)
            
            if active_pos:
                # Add close buttons - 2 per row
                close_row = []
                for p in active_pos[:6]:  # Max 6 close buttons to avoid too many
                    inst = p.get("instrument_name", "")
                    asset = inst.replace("USDT", "").replace("USD", "") if inst else "UNKNOWN"
                    direction = p.get("direction", "")
                    icon = "🟢" if direction in ("buy", "long") else "🔴"
                    # Callback data: close:BTC, close:ETH, etc.
                    close_row.append({"text": f"{icon} بستن {asset}", "callback_data": f"close:{asset}"})
                    if len(close_row) == 2:
                        keyboard.append(close_row)
                        close_row = []
                if close_row:
                    keyboard.append(close_row)
                # Add close all button
                keyboard.append([{"text": "🛑 بستن همه پوزیشن‌ها", "callback_data": "panel:closeall"}])
        
        keyboard.append([
            {"text": "▶️ Start", "callback_data": "panel:start"},
            {"text": "⏹ Stop", "callback_data": "panel:stop"},
            {"text": "🔄 پنل", "callback_data": "panel:home"},
        ])
        
        if self.dashboard_url:
            keyboard.append([{"text": "🌐 داشبورد وب", "url": self.dashboard_url}])
        return {"inline_keyboard": keyboard}

    def _kb(self, positions: List[dict] = None) -> dict:
        return self.main_keyboard(positions)

    def send(self, text: str, reply_markup: dict = None, parse_mode: str = "HTML") -> bool:
        if not self.enabled:
            return False
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {"chat_id": self.chat_id, "text": text, "parse_mode": parse_mode}
            if reply_markup:
                payload["reply_markup"] = json.dumps(reply_markup)
            resp = httpx.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                self.stats["sent"] += 1
                return True
            else:
                self.stats["failed"] += 1
                log.warning(f"Telegram send failed {resp.status_code}: {resp.text[:200]}")
                return False
        except Exception as exc:
            self.stats["failed"] += 1
            log.warning(f"Telegram send error: {exc}")
            return False

    def send_panel(self, text: Optional[str] = None) -> None:
        if not self.enabled:
            return
        if text is None:
            # v018: Include version
            version_info = f" | {self.current_version}" if self.current_version else ""
            text = (
                f"🎛️ <b>ZENITH SUPER — پنل کنترل {self.current_version}</b>\n"
                f"🕐 {utcnow()}\n\n"
                f"از دکمه‌های شیشه‌ای زیر استفاده کنید.\n"
                f"• گزارش <b>معامله</b> فقط وقتی سفارش واقعی/خطا باشد\n"
                f"• گزارش <b>کلی فایل</b> هر {self.full_report_hours:g} ساعت یا از دکمه\n"
                f"• <b>جدید v018:</b> دکمه بستن برای هر معامله مجزا + 10 جفت ارز + تست فعال\n"
            )
            if self._engine:
                snap = self._engine.snapshot()
                cfg = snap.get("config") or {}
                text += (
                    f"\nوضعیت: <b>{_esc(snap.get('mode'))}</b> · "
                    f"loops={snap.get('loop_count')} · "
                    f"assets={len(cfg.get('assets') or [])} · "
                    f"version={_esc(cfg.get('version') or self.current_version)}\n"
                    f"سفارشات: {len(snap.get('actions') or [])} · "
                    f"پوزیشن: {len(snap.get('positions') or [])}\n"
                )
        # Get positions for close buttons
        positions = []
        if self._engine:
            try:
                positions = self._engine.state.positions or []
            except:
                pass
        self.send(text, reply_markup=self._kb(positions))

    def format_status(self, snap: dict) -> str:
        cfg = snap.get("config") or {}
        risk = snap.get("risk") or {}
        diag = snap.get("diagnostics") or {}
        # v018: Include version
        version = cfg.get("version") or snap.get("bot") or self.current_version
        lines = [
            f"📊 <b>وضعیت — {version}</b>",
            f"🕐 {utcnow()}",
            f"🔖 ورژن: <b>{_esc(version)}</b>",
            f"",
            f"bot={_esc(snap.get('bot'))} mode={_esc(snap.get('mode'))} running={snap.get('running')}",
            f"loops={snap.get('loop_count')} last={_esc(snap.get('last_loop_at'))}",
            f"assets={len(cfg.get('assets') or [])} ({', '.join((cfg.get('assets') or [])[:5])}...)",
            f"sleeves={len(snap.get('sleeves') or [])} weights={_esc(cfg.get('sleeve_weights'))}",
            f"",
            f"trading_ready={diag.get('trading_ready')} state={_esc(diag.get('trading_state'))}",
            f"blocker={_esc(diag.get('primary_blocker') or 'none')}",
            f"market_open={diag.get('market_open_count')}/{len(cfg.get('assets') or [])}",
            f"active_signals={diag.get('active_signal_assets')}",
            f"",
            f"equity≈${_f(risk.get('estimated_bot_equity'))} peak=${_f(risk.get('peak_bot_equity'))} dd={_f(risk.get('drawdown_pct',0)*100,1)}%",
            f"cap=${_f(risk.get('notional_cap_usd'))} available=${_f(risk.get('available_funds'))}",
            f"",
            f"philosophy: {diag.get('philosophy') or 'Signal-Quality-On'}",
        ]
        if snap.get("last_error"):
            lines.append(f"\n❌ last_error: {_esc(snap.get('last_error')[:200])}")
        # v018: Show version history
        if self.version_history:
            lines.append(f"\n📜 تاریخچه ورژن‌ها:")
            for v in self.version_history[-5:]:
                lines.append(f"  • {v}")
        return "\n".join(lines)

    def format_positions(self, snap: dict) -> str:
        raw_pos = snap.get("positions") or []
        cfg = snap.get("config") or {}
        version = cfg.get("version") or self.current_version
        pos = []
        for p in raw_pos:
            try:
                sz = float(p.get("size_currency") or p.get("size") or 0)
                if abs(sz) > 1e-12:
                    pos.append(p)
            except:
                pos.append(p)
        fin = snap.get("financial") or {}
        lines = [f"💼 <b>پوزیشن‌ها ({len(pos)}) — {version}</b>", f"🕐 {utcnow()}", f"🔖 ورژن: {_esc(version)}", ""]
        if not pos:
            lines.append("flat — بدون پوزیشن (همه صفر)")
            if raw_pos:
                lines.append(f"خام از صرافی {len(raw_pos)} مورد با سایز صفر بود که فیلتر شد")
            # v018: Show available assets
            assets = cfg.get("assets") or []
            lines.append(f"\n📊 جفت ارزهای فعال ({len(assets)}): {', '.join(assets)}")
            return "\n".join(lines)
        total_pnl = 0.0
        for p in pos:
            try:
                total_pnl += float(p.get("total_profit_loss") or 0)
            except:
                pass
            inst = p.get("instrument_name", "")
            asset = inst.replace("USDT", "") if inst else "UNKNOWN"
            # v018: Add close hint
            lines.append(
                f"• <b>{_esc(inst)}</b> {_esc(asset)}\n"
                f"  dir={_esc(p.get('direction'))} size={_esc(p.get('size_currency') or p.get('size'))}\n"
                f"  avg={_esc(p.get('average_price'))} mark={_esc(p.get('mark_price'))} lev={_esc(p.get('leverage'))}\n"
                f"  pnl={_esc(p.get('total_profit_loss'))} uPnL={_esc(p.get('floating_profit_loss'))}\n"
                f"  👉 برای بستن: دکمه 🟢/🔴 بستن {asset} در پنل"
            )
        lines.append(f"\nΣ pnl ≈ <b>{total_pnl:.4f}</b>")
        if fin.get("unrealized_gross_usdc") is not None:
            lines.append(f"غیرمحقق: ${_f(fin.get('unrealized_gross_usdc'))}")
        # v018: Show all assets
        assets = cfg.get("assets") or []
        lines.append(f"\n📊 جفت ارزهای فعال ({len(assets)}): {', '.join(assets)}")
        lines.append(f"\n💡 <b>جدید v018:</b> برای بستن هر پوزیشن مجزا، از دکمه‌های بستن در پنل استفاده کنید")
        return "\n".join(lines)

    def format_balance(self, snap: dict) -> str:
        acct = snap.get("account") or {}
        cfg = snap.get("config") or {}
        version = cfg.get("version") or self.current_version
        lines = [f"💰 <b>موجودی — {version}</b>", f"🕐 {utcnow()}", ""]
        for curr, data in acct.items():
            lines.append(f"<b>{_esc(curr)}</b> equity=${_f(data.get('equity'))} available=${_f(data.get('available'))} balance=${_f(data.get('balance'))}")
        risk = snap.get("risk") or {}
        lines.append(f"\nestimated_bot_equity=${_f(risk.get('estimated_bot_equity'))}")
        lines.append(f"peak=${_f(risk.get('peak_bot_equity'))} dd={_f(risk.get('drawdown_pct',0)*100,1)}%")
        return "\n".join(lines)

    def format_sleeves(self, snap: dict) -> str:
        cfg = snap.get("config") or {}
        version = cfg.get("version") or self.current_version
        lines = [f"🧩 <b>آستین‌ها — {version}</b>", f"🕐 {utcnow()}", ""]
        for sl in snap.get("sleeves") or []:
            pa = sl.get("per_asset") or {}
            n_long = sum(1 for p in pa.values() if float((p or {}).get("side") or 0) > 0)
            n_short = sum(1 for p in pa.values() if float((p or {}).get("side") or 0) < 0)
            n_flat = len(pa) - n_long - n_short
            ntl = sum(float((p or {}).get("notional_usd") or 0) for p in pa.values())
            lines.append(
                f"<b>{_esc(sl.get('id'))}</b> {float(sl.get('weight') or 0)*100:.0f}% · "
                f"${float(sl.get('capital') or 0):.0f}\n"
                f"  long={n_long} short={n_short} flat={n_flat} · ntl=${ntl:.1f}"
            )
        nb = snap.get("net_book") or {}
        top = sorted(nb.items(), key=lambda kv: float((kv[1] or {}).get("notional_usd") or 0), reverse=True)[:15]
        if top:
            lines.append("\n<b>Net top</b>")
            for a, b in top:
                n = float((b or {}).get("notional_usd") or 0)
                if n <= 0:
                    continue
                conf_info = ""
                if b.get("confluence_strength"):
                    conf_info = f" conf{b.get('confluence_strength')}/{b.get('confluence_avg_conf',0):.2f}"
                lines.append(f"• {_esc(a)} ${n:.2f}{conf_info}")
        # Show confluence info
        diag = snap.get("diagnostics") or {}
        if diag.get("confluence_info"):
            lines.append("\n<b>Confluence Filter (v017):</b>")
            for asset, info in list(diag.get("confluence_info").items())[:10]:
                lines.append(f"• {asset}: {info[:80]}")
        return "\n".join(lines)

    def format_test_trade(self, state: dict) -> str:
        cfg = {}
        version = self.current_version
        if self._engine:
            try:
                cfg = self._engine.snapshot().get("config") or {}
                version = cfg.get("version") or version
            except:
                pass
        lines = [f"🧪 <b>تست ۶۰ثانیه — {version}</b>", f"🕐 {utcnow()}", ""]
        if not state or not state.get("active"):
            lines.append("تست فعال نیست")
            lines.append(f"دارایی تست: {state.get('asset') or 'BTC'} (v018 فعال)")
            lines.append(f"ورژن: {version}")
            lines.append(f"جفت ارزها: {', '.join(cfg.get('assets') or ['BTC,ETH,SOL,AVAX,LINK,XRP,DOGE,ADA,DOT,LTC'])}")
            return "\n".join(lines)
        lines.append(f"فعال: {state.get('asset')} dir={state.get('direction')} size={state.get('size')}")
        lines.append(f"ورود: {state.get('entry_price')} خروج مقرر: {state.get('close_due_at')}")
        lines.append(f"باقیمانده: {state.get('seconds_remaining')}s")
        lines.append(f"ورژن: {version}")
        return "\n".join(lines)

    def format_full_report(self, snap: dict) -> str:
        cfg = snap.get("config") or {}
        version = cfg.get("version") or self.current_version
        # v018: Include version in report
        lines = [
            f"📄 <b>گزارش جامع — {version}</b>",
            f"🕐 {utcnow()}",
            f"🔖 ورژن: <b>{_esc(version)}</b>",
            f"📜 تاریخچه: {', '.join(self.version_history[-3:]) if self.version_history else 'اولین ورژن'}",
            f"",
            f"این گزارش هر {self.full_report_hours:g} ساعت ارسال می‌شود",
            f"جفت ارزها: {len(cfg.get('assets') or [])} - {', '.join((cfg.get('assets') or [])[:10])}",
            f"استراتژی‌ها: {len(snap.get('sleeves') or [])}",
            f"پوزیشن‌ها: {len(snap.get('positions') or [])}",
            f"وضعیت: {snap.get('mode')} trading_ready={snap.get('diagnostics',{}).get('trading_ready')}",
        ]
        return "\n".join(lines)

    def build_full_report_text(self, snap: dict) -> str:
        cfg = snap.get("config") or {}
        version = cfg.get("version") or self.current_version
        lines = []
        lines.append("=" * 80)
        lines.append(f"ZENITH SUPER BOT — COMPREHENSIVE REPORT — {version}")
        lines.append(f"generated: {utcnow()} version={version}")
        lines.append(f"version_history: {', '.join(self.version_history[-5:])}")
        lines.append("=" * 80)
        lines.append("")
        lines.append(f"[VERSION] {version}")
        lines.append(f"assets: {cfg.get('assets')} ({len(cfg.get('assets') or [])} pairs)")
        lines.append(f"sleeves: {cfg.get('sleeves_enabled')}")
        lines.append(f"test_trade: {cfg.get('test_trade_enabled')} asset={cfg.get('test_trade_asset')}")
        lines.append("")
        lines.append("[CAPITAL & EQUITY]")
        risk = snap.get("risk") or {}
        lines.append(f"allocated={cfg.get('capital_usd')} estimated={risk.get('estimated_bot_equity')} peak={risk.get('peak_bot_equity')} dd={risk.get('drawdown_pct')}")
        lines.append("")
        lines.append(f"[POSITIONS] n={len(snap.get('positions') or [])}")
        for p in snap.get("positions") or []:
            lines.append(f"  {p.get('instrument_name')} {p.get('direction')} {p.get('size_currency')} pnl={p.get('total_profit_loss')}")
        lines.append("")
        lines.append("[SLEEVES]")
        for sl in snap.get("sleeves") or []:
            lines.append(f"  {sl.get('id')} {sl.get('weight')} {sl.get('title')}")
        lines.append("")
        lines.append(f"[CONFLUENCE] {snap.get('diagnostics',{}).get('confluence_info')}")
        lines.append(f"[CORRELATION] {snap.get('diagnostics',{}).get('correlation_info')}")
        return "\n".join(lines)

    def send_full_file_report(self):
        if not self.enabled or not self._engine:
            return
        try:
            snap = self._engine.snapshot()
            text = self.build_full_report_text(snap)
            # Send as file
            url = f"https://api.telegram.org/bot{self.bot_token}/sendDocument"
            import io
            file_data = io.BytesIO(text.encode('utf-8'))
            file_data.name = f"zenith_report_{utc_stamp()}_{self.current_version}.txt"
            resp = httpx.post(url, data={"chat_id": self.chat_id, "caption": f"📄 گزارش جامع {self.current_version} — {utcnow()}"}, files={"document": file_data}, timeout=20)
            if resp.status_code == 200:
                self.stats["files_sent"] += 1
                self._last_full_report = time.time()
        except Exception as exc:
            log.warning(f"Full report failed: {exc}")

    def on_version_change(self, old_version: str, new_version: str):
        """v018: Report version change in telegram"""
        self.version_history.append(f"{old_version}→{new_version} at {utcnow()}")
        self.version_history = self.version_history[-20:]  # Keep last 20
        self.current_version = new_version
        if self.enabled:
            text = (
                f"🔄 <b>آپدیت ورژن ربات</b>\n"
                f"🕐 {utcnow()}\n\n"
                f"از: <b>{_esc(old_version)}</b>\n"
                f"به: <b>{_esc(new_version)}</b>\n\n"
                f"📊 تغییرات:\n"
            )
            if "v018" in new_version:
                text += (
                    f"• افزایش جفت ارزها از 5 به 10\n"
                    f"• فعال‌سازی معامله تست روی BTC\n"
                    f"• دکمه بستن برای هر معامله مجزا در پنل تلگرام\n"
                    f"• نمایش ورژن در گزارش‌ها\n"
                )
            elif "v017" in new_version:
                text += (
                    f"• 7 لایه ارتقای استراتژی\n"
                    f"• فیلتر Confluence + Vol Targeting + Correlation\n"
                    f"• Smart Exit + Meta-Allocator + Cost Filter\n"
                )
            text += f"\n🔖 ورژن فعلی: {new_version}\n"
            text += f"📜 تاریخچه: {', '.join(self.version_history[-3:])}"
            self.send(text, reply_markup=self._kb())

    def answer_callback(self, callback_id: str, text: str, show_alert: bool = False):
        if not self.enabled:
            return
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/answerCallbackQuery"
            httpx.post(url, json={"callback_query_id": callback_id, "text": text[:200], "show_alert": show_alert}, timeout=5)
        except:
            pass

    def handle_callback(self, cq: dict):
        data = cq.get("data") or ""
        cid = cq.get("id")
        msg = cq.get("message") or {}
        chat = msg.get("chat") or {}
        if str(chat.get("id")) != str(self.chat_id):
            self.answer_callback(cid, "unauthorized", True)
            return
        
        # v018 NEW: Handle close:ASSET callbacks
        if data.startswith("close:"):
            asset = data.split(":", 1)[1]
            self.answer_callback(cid, f"بستن {asset}...")
            eng = self._engine
            if eng:
                try:
                    # Close position for this asset
                    inst = f"{asset}USDT"
                    # Use engine's close logic
                    result = eng.close_position(asset)  # Need to implement in engine
                    self.send(f"🛑 درخواست بستن <b>{_esc(asset)}</b> ارسال شد\nنتیجه: {_esc(str(result)[:500])}", reply_markup=self._kb(eng.state.positions))
                except Exception as e:
                    log.exception(f"close {asset}")
                    self.send(f"❌ خطا در بستن {asset}: {_esc(str(e)[:500])}", reply_markup=self._kb())
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
                self.send(self.format_status(eng.snapshot() if eng else {}), reply_markup=self._kb(eng.state.positions if eng else []))
            elif action == "positions":
                self.send(self.format_positions(eng.snapshot() if eng else {}), reply_markup=self._kb(eng.state.positions if eng else []))
            elif action == "balance":
                self.send(self.format_balance(eng.snapshot() if eng else {}), reply_markup=self._kb(eng.state.positions if eng else []))
            elif action == "sleeves":
                self.send(self.format_sleeves(eng.snapshot() if eng else {}), reply_markup=self._kb(eng.state.positions if eng else []))
            elif action == "closeall":
                if eng:
                    self.send("🛑 بستن همه پوزیشن‌ها...")
                    try:
                        # Close all positions
                        for asset in eng.settings.asset_list:
                            try:
                                eng.close_position(asset)
                            except:
                                pass
                        self.send("✅ درخواست بستن همه ارسال شد", reply_markup=self._kb())
                    except Exception as e:
                        self.send(f"❌ خطا: {e}", reply_markup=self._kb())
            elif action == "testtrade":
                if not eng:
                    self.send("موتور در دسترس نیست")
                    return
                if not eng.settings.effective_test_trade_enabled:
                    self.send("❌ معامله تست غیرفعال است - در config فعال کنید")
                    return
                # Start test trade
                try:
                    if hasattr(eng, 'test_trader'):
                        state = eng.test_trader.start()
                        self.send(self.format_test_trade(state), reply_markup=self._kb(eng.state.positions))
                    else:
                        self.send("❌ test_trader در دسترس نیست - نیاز به پیاده‌سازی", reply_markup=self._kb())
                except Exception as e:
                    self.send(f"❌ خطا در تست: {e}", reply_markup=self._kb())
            elif action == "teststatus":
                if eng and hasattr(eng, 'test_trader'):
                    self.send(self.format_test_trade(eng.test_trader.snapshot()), reply_markup=self._kb(eng.state.positions))
                else:
                    self.send("تست غیرفعال", reply_markup=self._kb())
            elif action == "testclose":
                if eng and hasattr(eng, 'test_trader'):
                    self.send(self.format_test_trade(eng.test_trader.request_close()), reply_markup=self._kb(eng.state.positions))
                else:
                    self.send("تست غیرفعال", reply_markup=self._kb())
            elif action == "start":
                if eng:
                    eng.start()
                self.send("▶️ start requested", reply_markup=self._kb())
            elif action == "stop":
                if eng:
                    eng.stop()
                self.send("⏹ stop requested", reply_markup=self._kb())
            elif action == "tick":
                if not eng:
                    self.send("engine offline")
                    return
                self.send("⏳ tick...")
                res = eng.once()
                self.send(f"tick done {len(res.get('actions',[]))} actions", reply_markup=self._kb(eng.state.positions))
            elif action == "file":
                self.send("⏳ ساخت گزارش...")
                self.send_full_file_report()
            elif action == "health":
                snap = eng.snapshot() if eng else {}
                self.send(f"❤️ {snap.get('mode')} loops={snap.get('loop_count')} version={snap.get('config',{}).get('version')}", reply_markup=self._kb())
            else:
                self.send(f"unknown {action}", reply_markup=self._kb())
        except Exception as e:
            log.exception(f"callback {action}")
            self.send(f"❌ {e}", reply_markup=self._kb())

    def handle_update(self, update: dict):
        try:
            if "callback_query" in update:
                self.handle_callback(update["callback_query"])
            elif "message" in update:
                msg = update["message"]
                text = (msg.get("text") or "").strip()
                if text.startswith("/"):
                    parts = text.split()
                    cmd = parts[0].lower()
                    arg = " ".join(parts[1:]) if len(parts) > 1 else ""
                    self._on_command(cmd, arg)
        except Exception as exc:
            log.exception(f"handle_update failed: {exc}")

    def _on_command(self, cmd: str, argument: str = "") -> None:
        eng = self._engine
        if cmd in ("/start", "/help", "/panel"):
            self.send_panel()
        elif cmd == "/status":
            self.send(self.format_status(eng.snapshot() if eng else {}), reply_markup=self._kb(eng.state.positions if eng else []))
        elif cmd == "/positions":
            self.send(self.format_positions(eng.snapshot() if eng else {}), reply_markup=self._kb(eng.state.positions if eng else []))
        elif cmd == "/close":
            # v018 NEW: /close BTC or /close all
            if not argument:
                self.send("استفاده: /close BTC یا /close all", reply_markup=self._kb())
                return
            asset = argument.strip().upper()
            if asset == "ALL":
                if eng:
                    for a in eng.settings.asset_list:
                        try:
                            eng.close_position(a)
                        except:
                            pass
                    self.send("✅ بستن همه ارسال شد", reply_markup=self._kb())
            else:
                if eng:
                    try:
                        result = eng.close_position(asset)
                        self.send(f"🛑 بستن {asset}: {result}", reply_markup=self._kb())
                    except Exception as e:
                        self.send(f"❌ خطا بستن {asset}: {e}", reply_markup=self._kb())
        else:
            self.send_panel()

    def start_background(self):
        if not self.enabled:
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._bg_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _bg_loop(self):
        time.sleep(10)
        while not self._stop.is_set():
            try:
                time.sleep(60)
            except:
                break


# Singleton
_reporter: Optional[TelegramReporter] = None

def init_reporter_from_settings(settings) -> Optional[TelegramReporter]:
    global _reporter
    if not settings.telegram_enabled or not settings.telegram_bot_token or not settings.telegram_chat_id:
        return None
    _reporter = TelegramReporter(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        dashboard_url="",
        full_report_hours=6.0,
    )
    # v018: Set version
    _reporter.current_version = getattr(settings, 'version', 'v018-10pairs-testtrade-telegram-close')
    return _reporter

def get_reporter() -> Optional[TelegramReporter]:
    return _reporter
