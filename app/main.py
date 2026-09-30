from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse

from app.config import get_settings
from app.engine import get_engine
from app.telegram_bot import init_reporter_from_settings, get_reporter

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("main")


def _check_token(authorization: Optional[str] = None, x_token: Optional[str] = None):
    s = get_settings()
    if not s.dashboard_token:
        return
    tok = None
    if x_token:
        tok = x_token
    elif authorization and authorization.lower().startswith("bearer "):
        tok = authorization.split(" ", 1)[1].strip()
    if tok != s.dashboard_token:
        raise HTTPException(401, "unauthorized")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    eng = get_engine()
    log.info("SUPER boot capital=%s sleeves=%s dry=%s", s.capital_usd, s.enabled_sleeves, s.dry_run)
    try:
        rep = init_reporter_from_settings(s)
        if rep:
            rep.bind_engine(eng)
            rep.start_background()
            log.info("telegram enabled=%s chat=%s", rep.enabled, bool(s.telegram_chat_id))
    except Exception:
        log.exception("telegram init failed")
    try:
        eng.start()
    except Exception:
        log.exception("engine start failed")
    yield
    try:
        r = get_reporter()
        if r:
            r.stop()
    except Exception:
        pass
    eng.stop()


app = FastAPI(
    title="Zenith SUPER Trader",
    description="Independent multi-sleeve bot: zenith + almasi + inst-v3 (no DNA mix)",
    version="2.0.0",
    lifespan=lifespan,
)


@app.get("/health")
def health():
    eng = get_engine()
    snap = eng.snapshot()
    return {
        "ok": True,
        "bot": "zenith-SUPER",
        "running": snap["running"],
        "mode": snap["mode"],
        "loop_count": snap["loop_count"],
        "last_loop_at": snap["last_loop_at"],
        "last_error": snap["last_error"],
        "n_sleeves": len(snap.get("sleeves") or []),
    }


@app.get("/api/status")
def status(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token)
    return get_engine().snapshot()


@app.get("/api/sleeves")
def sleeves(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token)
    snap = get_engine().snapshot()
    return {"sleeves": snap.get("sleeves"), "net_book": snap.get("net_book"), "config": snap.get("config")}


@app.post("/api/start")
def start(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token)
    get_engine().start()
    return {"started": True}


@app.post("/api/stop")
def stop(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token)
    get_engine().stop()
    return {"stopped": True}


@app.post("/api/tick")
def tick(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token)
    try:
        result = get_engine().once()
        return {"ok": True, "result": result}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/", response_class=HTMLResponse)
def dashboard():
    s = get_settings()
    return f"""<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Zenith SUPER Bot</title>
<style>
:root {{ --bg:#070b14; --card:#101827; --line:#1f2a3d; --tx:#e8eefc; --acc:#67e8f9; --ok:#4ade80; --bad:#f87171; --mut:#94a3b8; }}
*{{box-sizing:border-box}} body{{margin:0;font-family:system-ui,sans-serif;background:var(--bg);color:var(--tx)}}
header{{padding:1rem 1.25rem;border-bottom:1px solid var(--line);display:flex;gap:.75rem;flex-wrap:wrap;align-items:center}}
h1{{margin:0;font-size:1.2rem;color:var(--acc)}}
.badge{{padding:.2rem .55rem;border-radius:999px;background:#1e293b;font-size:.78rem}}
.ok{{color:var(--ok)}} .bad{{color:var(--bad)}}
main{{padding:1rem;display:grid;gap:1rem;grid-template-columns:repeat(auto-fit,minmax(300px,1fr))}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:1rem}}
.card h2{{margin:0 0 .7rem;font-size:.95rem;color:var(--acc)}}
table{{width:100%;border-collapse:collapse;font-size:.82rem}}
th,td{{border-bottom:1px solid var(--line);padding:.35rem;text-align:right;vertical-align:top}}
button{{background:#06b6d4;color:#04101c;border:0;border-radius:10px;padding:.5rem .9rem;font-weight:700;cursor:pointer;margin-left:.35rem}}
button.sec{{background:#334155;color:#fff}}
pre{{white-space:pre-wrap;word-break:break-word;font-size:.72rem;background:#0a101b;padding:.7rem;border-radius:10px;max-height:260px;overflow:auto}}
.muted{{color:var(--mut);font-size:.85rem}}
.pill{{display:inline-block;padding:.1rem .45rem;border-radius:6px;background:#0f172a;margin:0 .15rem;font-size:.75rem}}
.on{{border:1px solid #4ade80}} .off{{opacity:.5}}
</style>
</head>
<body>
<header>
  <h1>⚡ Zenith SUPER Trader</h1>
  <span class="badge" id="run">…</span>
  <span class="badge">capital ${s.capital_usd:g} · lev≤{s.lev_cap}</span>
  <span class="badge">Deribit testnet</span>
  <span class="badge">independent sleeves</span>
  <div style="margin-right:auto">
    <button onclick="api('tick')">Tick</button>
    <button class="sec" onclick="api('start')">Start</button>
    <button class="sec" onclick="api('stop')">Stop</button>
  </div>
</header>
<main>
  <section class="card">
    <h2>وضعیت سوپرربات</h2>
    <div id="status" class="muted">loading…</div>
  </section>
  <section class="card">
    <h2>حساب</h2>
    <pre id="account">—</pre>
  </section>
  <section class="card" style="grid-column:1/-1">
    <h2>آستین‌های مستقل (بدون مخلوط DNA)</h2>
    <p class="muted">هر استراتژی سرمایه و سیگنال خودش را دارد. فقط در لایهٔ سفارش با هم جمع می‌شوند.</p>
    <div id="sleeve_pills"></div>
    <table>
      <thead><tr><th>Sleeve</th><th>W</th><th>Cap $</th><th>BTC</th><th>ETH</th><th>SOL</th><th>Note</th></tr></thead>
      <tbody id="sleeves"></tbody>
    </table>
  </section>
  <section class="card" style="grid-column:1/-1">
    <h2>دفتر خالص (net book) → سفارش صرافی</h2>
    <table>
      <thead><tr><th>Asset</th><th>Target coin</th><th>Notional $</th><th>Contributors</th></tr></thead>
      <tbody id="net"></tbody>
    </table>
  </section>
  <section class="card" style="grid-column:1/-1">
    <h2>پوزیشن / اکشن / رویداد</h2>
    <pre id="tail">—</pre>
  </section>
</main>
<script>
function sideCell(pa){{
  if(!pa) return '—';
  const s = pa.side>0 ? 'LONG' : 'FLAT';
  const n = (pa.notional_usd||0).toFixed(1);
  return `${{s}} $${{n}}`;
}}
async function refresh(){{
  try{{
    const r = await fetch('/api/status');
    const d = await r.json();
    const run = document.getElementById('run');
    run.textContent = d.running ? 'RUNNING' : 'STOPPED';
    run.className = 'badge ' + (d.running ? 'ok' : 'bad');
    document.getElementById('status').innerHTML =
      `bot: <b>${{d.bot||'SUPER'}}</b><br/>mode: <b>${{d.mode}}</b><br/>loops: ${{d.loop_count}}<br/>last: ${{d.last_loop_at||'—'}}<br/>error: <span class="${{d.last_error?'bad':''}}">${{d.last_error||'none'}}</span><br/>sleeves: ${{(d.sleeves||[]).length}}`;
    document.getElementById('account').textContent = JSON.stringify(d.account, null, 2);
    const tb = document.getElementById('sleeves'); tb.innerHTML='';
    const pills = document.getElementById('sleeve_pills'); pills.innerHTML='';
    (d.sleeves||[]).forEach(s => {{
      const sp = document.createElement('span');
      sp.className = 'pill on';
      sp.textContent = s.id + ' ' + Math.round((s.weight||0)*100) + '%';
      pills.appendChild(sp);
      const tr = document.createElement('tr');
      const pa = s.per_asset||{{}};
      tr.innerHTML = `<td><b>${{s.id}}</b><br/><span class="muted">${{s.title||''}}</span></td>
        <td>${{((s.weight||0)*100).toFixed(0)}}%</td>
        <td>${{(s.capital||0).toFixed(1)}}</td>
        <td>${{sideCell(pa.BTC)}}</td>
        <td>${{sideCell(pa.ETH)}}</td>
        <td>${{sideCell(pa.SOL)}}</td>
        <td class="muted">${{s.notes||''}}</td>`;
      tb.appendChild(tr);
    }});
    const nb = document.getElementById('net'); nb.innerHTML='';
    const book = d.net_book||{{}};
    Object.keys(book).forEach(a => {{
      const b = book[a];
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${{a}}</td><td>${{(b.target_coin||0).toFixed(6)}}</td>
        <td>${{(b.notional_usd||0).toFixed(2)}}</td>
        <td><code>${{JSON.stringify(b.contributors||{{}})}}</code></td>`;
      nb.appendChild(tr);
    }});
    document.getElementById('tail').textContent = JSON.stringify({{
      positions: d.positions, actions: d.actions, events: (d.events||[]).slice(-15), orders: d.orders_log
    }}, null, 2);
  }}catch(e){{ document.getElementById('status').textContent = e; }}
}}
async function api(name){{
  await fetch('/api/'+name, {{method:'POST'}});
  setTimeout(refresh, 1000);
}}
refresh(); setInterval(refresh, 10000);
</script>
</body></html>"""


@app.get("/api/healthz")
def healthz():
    return {"ok": True}


@app.post("/api/telegram/test")
def telegram_test(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token)
    rep = get_reporter()
    if not rep or not rep.enabled:
        raise HTTPException(400, "telegram not configured")
    rep.send_dashboard()
    return {"ok": True, "stats": rep.stats}


@app.post("/api/telegram/report")
def telegram_report(authorization: Optional[str] = Header(None), x_token: Optional[str] = Header(None)):
    _check_token(authorization, x_token)
    rep = get_reporter()
    if not rep or not rep.enabled:
        raise HTTPException(400, "telegram not configured")
    eng = get_engine()
    rep.send(rep.format_dashboard(eng.snapshot()))
    return {"ok": True}
