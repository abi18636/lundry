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
