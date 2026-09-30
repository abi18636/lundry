#!/usr/bin/env python3
"""Hourly health check + optional auto-tick. Run via cron or keep alive with UptimeRobot + this on a worker."""
from __future__ import annotations
import json, os, sys, time
from datetime import datetime, timezone
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
URL = os.getenv("BOT_URL", "https://zenith-trader-bot.onrender.com").rstrip("/")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
OUT = Path(os.getenv("WATCHDOG_LOG", str(ROOT / "state" / "watchdog.log")))
OUT.parent.mkdir(parents=True, exist_ok=True)

def log(msg: str):
    line = f"{datetime.now(timezone.utc).isoformat()} {msg}"
    print(line)
    with OUT.open("a") as f:
        f.write(line + "\n")

def tg(text: str):
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": TG_CHAT, "text": text[:3500], "parse_mode": "HTML"},
            timeout=30,
        )
    except Exception as e:
        log(f"tg fail {e}")

def main():
    issues = []
    try:
        h = httpx.get(f"{URL}/health", timeout=60).json()
    except Exception as e:
        issues.append(f"health unreachable: {e}")
        tg(f"🚨 watchdog: health unreachable\n{e}")
        log(str(issues))
        return 2
    if not h.get("ok"):
        issues.append(f"health not ok: {h}")
    if not h.get("running"):
        issues.append("engine not running")
        try:
            httpx.post(f"{URL}/api/start", timeout=30)
            issues.append("sent /api/start")
        except Exception as e:
            issues.append(f"start failed: {e}")
    # stale loop?
    last = h.get("last_loop_at")
    if last:
        try:
            ts = datetime.fromisoformat(last.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - ts).total_seconds()
            if age > 15 * 60:
                issues.append(f"stale loop age={age:.0f}s — forcing tick")
                httpx.post(f"{URL}/api/tick", timeout=300)
        except Exception as e:
            issues.append(f"parse last_loop: {e}")
    # status deep
    try:
        st = httpx.get(f"{URL}/api/status", timeout=90).json()
        acts = st.get("actions") or []
        n_err = sum(1 for a in acts if (a.get("status") or "").lower() in ("error", "failed"))
        n_halt = sum(1 for a in acts if str(a.get("status") or "").startswith("market_"))
        n_assets = len((st.get("config") or {}).get("assets") or [])
        log(f"ok loops={st.get('loop_count')} assets={n_assets} act_err={n_err} halt={n_halt} last_err={st.get('last_error')}")
        if n_err > 0 and n_halt == 0:
            # real order errors while markets open
            sample = [a for a in acts if (a.get("status") or "").lower() in ("error", "failed")][:5]
            tg(
                "⚠️ watchdog: order errors\n"
                + "\n".join(f"{a.get('asset')}: {str(a.get('error'))[:120]}" for a in sample)
            )
        elif n_halt >= max(1, n_assets // 2):
            log("venue markets mostly halted — expected on testnet maintenance")
        if st.get("last_error"):
            issues.append(f"last_error={st.get('last_error')}")
    except Exception as e:
        issues.append(f"status fail: {e}")
    if issues:
        log("ISSUES " + " | ".join(issues))
        # only TG if not pure halt
        if not any("halt" in x.lower() for x in issues):
            tg("🛠️ watchdog issues:\n" + "\n".join(issues)[:3000])
    else:
        log("healthy")
    return 0

if __name__ == "__main__":
    sys.exit(main())
