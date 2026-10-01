# Zenith SUPER Trader Bot

Independent multi-sleeve live bot on Deribit testnet.

## Architecture (no DNA mixing)

```
               ┌─ zenith_apex        (REBIRTH compress/impulse)  w=25%
 total capital ├─ almasi_primary     (TQ70/VQ30)                 w=25%
               ├─ inst_v3_stable     (TSMOM 70/30)               w=30%
               ├─ inst_v3_primary    (TSMOM consensus)           w=10%
               └─ zenith_endurance   (impulse only)              w=10%
                                    │
                                    ▼
                         net_book[asset] = Σ sleeve targets
                                    │
                                    ▼
                         single Deribit order layer (long-only, lev≤1)
```

Each sleeve computes **its own** signal and notional from **its own** capital slice.
Signals are never averaged across strategies. Only coin targets are summed for one account.

## Deploy
Render free + GitHub. Health: `/health` (use UptimeRobot on this URL).

## API
- `GET /health` `GET /api/status` `GET /api/sleeves`
- `POST /api/tick` `POST /api/start` `POST /api/stop`

## Coordinated execution repair (2026-10-01)

See `COORDINATED_REPAIR_FA.md`. The venue must be `open` and quotes fresh.
Service liveness does **not** imply trading readiness: inspect `trading_ready`,
`primary_blocker`, and `/api/diagnostics`. Only exchange-confirmed fills count.

- Completed hourly bars; funded Apex 80/20 legs (no vote discarding impulse).
- Exchange lot flooring never inflates the allocation.
- Sign-correct inventory; reduce-only exits; slippage-bounded limit IOC.
- <=1x allocated notional cap, collateral reservation and a drawdown stop threshold.
- Fail-closed account reads and unique-label reconciliation after ambiguous execution.
- Public control is disabled: set `DASHBOARD_TOKEN` for HTTP Start/Stop/Tick.
- Risk state on Render free is ephemeral; use a dedicated account and durable state before real-money deployment.
- New 25-asset live allocation is NOT the bit-exact three-asset historical backtest portfolio.

Tests: `python -m pip install pytest && python -m pytest -q tests`.
Hourly reviews run inside the service; the GitHub workflow (if installed successfully) also checks external health.
