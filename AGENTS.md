# کارگروه Zenith Live (Multi-Agent)

| Agent | مسئولیت | خروجی |
|---|---|---|
| **A1 Strategy** | DNA بیت‌دقیق zenith REBIRTH (compress/impulse) | `strategies/*` |
| **A2 Execution** | سایزینگ lev≤1 long-only min-notional | `app/engine.py` |
| **A3 Venue** | Deribit testnet REST USDC linear perps | `app/deribit.py` |
| **A4 Ops/Deploy** | GitHub + Render free + health | `render.yaml` scripts |
| **A5 Risk** | capital sleeve $100 · لاگ orders · dry_run switch | env + dashboard |

## قوانین مشترک
1. Secret فقط در env (Render) — هرگز در git
2. long_only · lev_cap=1 · بدون RSI4h/Recovered
3. Testnet اول؛ mainnet فقط با تعویض BASE_URL + کلید جدید
4. SOL روی Deribit = `SOL_USDC-PERPETUAL`

## Coordinated repair policy
- Separate service liveness from trading readiness. A halted book is not healthy execution.
- Never bypass the venue gate, fabricate fills, inflate targets to arbitrary minimum notional, or switch to real money.
- Preserve strategy thresholds; Apex's two capital-funded legs are not a cross-strategy vote.
- The live expanded universe/sizing is an adaptation; historical performance does not transfer unchanged.
- Ambiguous orders must be reconciled before retry; HTTP controls require DASHBOARD_TOKEN.
- Risk stop threshold is not a maximum-loss guarantee; free-tier state is ephemeral.
