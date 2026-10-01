#!/usr/bin/env python3
"""Hourly liveness AND trading-readiness review. Never rewrites code or forces trades.

Runs locally or in GitHub Actions. Optional recovery requires DASHBOARD_TOKEN and
WATCHDOG_RECOVERY=true. The API itself rejects overlapping ticks and public control.
"""
from __future__ import annotations

import html
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
URL = os.getenv("BOT_URL", "https://zenith-trader-bot.onrender.com").rstrip("/")
TOKEN = os.getenv("DASHBOARD_TOKEN", "")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
RECOVERY = os.getenv("WATCHDOG_RECOVERY", "false").lower() == "true"
OUT = Path(os.getenv("WATCHDOG_LOG", str(ROOT / "state" / "watchdog.log")))


def log(message: str):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now(timezone.utc).isoformat()} {message}"
    print(line)
    with OUT.open("a") as file:
        file.write(line + "\n")


def notify(message: str):
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        response = httpx.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                              data={"chat_id": TG_CHAT, "text": html.escape(message[:3400]), "parse_mode": "HTML"}, timeout=20)
        if response.status_code != 200:
            log(f"notification failed HTTP={response.status_code}")
    except Exception as exc:
        log(f"notification unavailable: {type(exc).__name__}")


def main():
    headers = {"X-Token": TOKEN} if TOKEN else {}
    issues, recovery_notes = [], []
    health, status = {}, {}
    with httpx.Client(timeout=45, headers=headers, follow_redirects=True) as client:
        try:
            response = client.get(URL+"/health")
            response.raise_for_status()
            health = response.json()
        except Exception as exc:
            message = f"service unreachable: {type(exc).__name__}"
            log("ERROR "+message);notify("Watchdog: "+message)
            return 2
        if not health.get("ok"):
            issues.append("service liveness not OK")
        if not health.get("running"):
            issues.append("engine not running")
            if TOKEN and RECOVERY:
                try:
                    response = client.post(URL+"/api/start");response.raise_for_status()
                    recovery_notes.append("authorized start requested")
                except Exception as exc:
                    recovery_notes.append("start failed: "+type(exc).__name__)
        age = health.get("loop_age_seconds")
        if age is None and health.get("last_loop_at"):
            age = (datetime.now(timezone.utc)-datetime.fromisoformat(health["last_loop_at"].replace("Z", "+00:00"))).total_seconds()
        if age is not None and age > 900:
            issues.append(f"stale completed cycle age={age:.0f}s")
            if TOKEN and RECOVERY and not health.get("cycle_in_progress"):
                try:
                    response = client.post(URL+"/api/tick", timeout=90);response.raise_for_status()
                    recovery_notes.append("authorized non-overlapping tick requested")
                except Exception as exc:
                    recovery_notes.append("tick unavailable: "+type(exc).__name__)
        try:
            response = client.get(URL+"/api/status")
            if response.status_code == 401 and not TOKEN:
                recovery_notes.append("deep status protected; reviewed public health only")
            else:
                response.raise_for_status();status = response.json()
        except Exception as exc:
            issues.append("status unavailable: "+type(exc).__name__)
    actions = status.get("actions") or []
    diagnostics = status.get("diagnostics") or dict(health)
    if status and not status.get("diagnostics"):
        # Legacy deployment: derive evidence rather than pretending it is idle/ready.
        halted = sum(a.get("status") == "market_halted" for a in actions)
        opened = sum(a.get("market_state") == "open" for a in actions)
        n_assets = len((status.get("config") or {}).get("assets") or [])
        active = sum(abs(float(b.get("target_coin") or 0)) > 0 for b in (status.get("net_book") or {}).values())
        diagnostics.update(trading_ready=False, trading_state="blocked", market_halted_count=halted,
                           market_open_count=opened, active_signal_assets=active,
                           primary_blocker="venue_halted" if halted == n_assets and n_assets else "legacy_readiness_schema")
    primary = diagnostics.get("primary_blocker")
    order_errors = [a for a in actions if a.get("status") in ("error", "failed", "execution_uncertain", "execution_validation_error", "order_error_backoff",
                                                              "market_api_error", "market_unknown", "instrument_unavailable", "candle_api_error", "safety_blocked")]
    # A halted asset must never suppress genuine errors in another open asset.
    if order_errors:
        issues.append(f"execution problems={len(order_errors)}")
    if health.get("last_error"):
        issues.append("cycle error: "+str(health["last_error"]))
    severe = {"cycle_error", "account_unavailable", "positions_unavailable", "execution_uncertain", "strategy_error",
              "error", "order_error_backoff", "execution_validation_error", "mainnet_not_authorized", "market_api_error", "market_unknown", "instrument_unavailable"}
    if primary in severe and not order_errors and not health.get("last_error"):
        issues.append("trading blocker: "+str(primary))
    review = {
        "checked_at": datetime.now(timezone.utc).isoformat(), "build": health.get("build"),
        "service_running": health.get("running"), "loops": health.get("loop_count"),
        "trading_ready": diagnostics.get("trading_ready", False),
        "trading_state": diagnostics.get("trading_state"), "primary_blocker": primary,
        "markets_open": diagnostics.get("market_open_count", 0),
        "markets_halted": diagnostics.get("market_halted_count", 0),
        "active_signal_assets": diagnostics.get("active_signal_assets", 0),
        "confirmed_fills": diagnostics.get("confirmed_fill_count", 0),
        "issues": issues, "recovery_notes": recovery_notes,
    }
    state = "ERROR" if issues else "BLOCKED" if primary else "READY" if review["trading_ready"] else "IDLE"
    log(state+" "+json.dumps(review, ensure_ascii=False))
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path,"a") as file:
            file.write(f"## Hourly bot review: {state}\n\n```json\n{json.dumps(review,ensure_ascii=False,indent=2)}\n```\n")
            file.write("\nLiveness OK does not mean trading ready. Exchange halts cannot be cleared by the bot.\n")
    if issues:
        notify("Watchdog needs attention:\n"+"\n".join(issues)[:3000])
    # Exchange halt is advisory, NOT a successful trading state and NOT a restart cause.
    return 2 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
