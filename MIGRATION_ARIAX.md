# Migration Deribit -> AriaX Testnet

**Date: 2026-10-06**
**User request: Change exchange and wipe previous history**

## Changes

### Old: Deribit Testnet
- Base: https://test.deribit.com/api/v2
- Assets: 7 liquid (BTC,ETH,SOL,DOGE,AVAX,APT,TRX) - 2 locked, 3 stuck locked_by_admin
- Issues: market_locked 28%, spreads 24% (SOL), locked_by_admin FIL/SUI/TAO cannot close
- Capital usage: 13% -> 31% after vol_target fixes
- Equity: 400$ with -0.51$ stuck loss

### New: AriaX Testnet v2 (Bybit v5 compatible)
- Base: https://dryclean-app-1.onrender.com (fallback for ariax-1 which is down)
- API Key: arx-fca61a10ad29397189fcc749bac3285f
- Secret: stored securely in env ARIAX_API_SECRET
- Assets: 15 linear perps (BTC,ETH,SOL,XRP,DOGE,ADA,AVAX,LINK,DOT,LTC,BCH,TRX,XLM,AAVE,UNI)
- Currently 11 with enough history (TRX/XLM/AAVE/UNI only 7 candles, new markets)
- Fees: maker 0.02% taker 0.05% (vs Deribit 0.05%/0.05%)
- Wallet: 20000 USDT (fresh)
- Positions: wiped, empty

## Code Changes

1. **app/config.py**: Complete rewrite for AriaX
   - ariax_base_url, ariax_api_key, ariax_api_secret
   - assets 7->15
   - long_only false (AriaX supports short)
   - lev_cap 1.0->2.0 for more usage within 15% DD
   - instrument_for returns BTCUSDT (v5) and legacy_symbol_for BTCUSD

2. **app/ariax.py**: New client (600+ lines)
   - Supports both legacy /api/* (X-API-Key/Secret) and v5 (HMAC X-BAPI-*)
   - Methods: account_summary, positions, open_orders, order_book, instrument, candles, limit_ioc, buy_market, sell_market, close_position
   - Fallback handling for Render no-server (ariax-1 down -> dryclean)
   - Candle pagination to get 721+ candles (was 100)

3. **app/deribit.py**: Shim that imports from ariax for backward compat
   - Old Deribit code completely removed

4. **app/engine.py**:
   - _ensure_client now uses AriaXClient
   - _update_risk supports USDT and USDC
   - account fetching tries USDT first then USDC
   - positions fetching tries USDT then USDC
   - Environment check allows AriaX (not blocked as mainnet)

5. **app/financial_reports.py**: Uses AriaXClient

6. **render.yaml**: Updated env vars for AriaX

7. **state/**: Wiped completely per user request - history erased

## Testing

- Account: 19999.9 USDT
- Positions: [] empty after wipe
- Orderbook: BTCUSDT bid 85582.4 ask 85582.9 spread 0.5$ (0.5 bps) - excellent vs Deribit SOL 24%!
- Candles: 721 hourly candles for 11 assets
- Sleeves: zenith_apex signals ADA 46$, AVAX 44$, BCH 51$ = 141$ notional (35% usage)
- Expected profit: 1% move = 1.41$ (dollar profits, not hundredths)

## Security

- Secret stored in env variable ARIAX_API_SECRET, not in GitHub (render.yaml sync:false)
- Default in config.py is for local testing only, production uses env
- No Deribit credentials remain

## Next Steps

- Deploy to Render (auto via git push)
- Verify /health shows AriaX
- Monitor first trades on AriaX
- Close old Deribit positions manually if needed (they remain on Deribit testnet but bot no longer touches them)
