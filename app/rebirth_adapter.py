"""Read-only adapter for strategy 1: original Zenith Rebirth Apex.

This computes research signals only. It is not an order or portfolio engine.
A position may depend on history before a truncated window; never treat this
replay as a verified exchange position or authorize an order from this result.
"""
from pathlib import Path
import sys
import pandas as pd
from app.kronos_shadow import closed_hourly_context

# The preserved research pack uses absolute sibling imports.
RESEARCH_DIR = str(Path(__file__).resolve().parent.parent / 'strategies')
if RESEARCH_DIR not in sys.path:
    sys.path.insert(0, RESEARCH_DIR)
from alt_turtle_ns import atr, adx, kaufman_er
from zenith_apex_rebirth import sig_compress, sig_impulse

ASSETS = ('BTC', 'ETH', 'SOL')
WEIGHTS = {'compress': 0.8, 'impulse': 0.2}

def research_legs(asset, candles, now, lookback=1000):
    if asset not in ASSETS:
        raise ValueError('strategy 1 was researched only on BTC/ETH/SOL')
    if lookback < 400:
        raise ValueError('at least 400 closed candles are required')
    data=closed_hourly_context(candles, now, lookback=lookback)
    data['atr']=atr(data['high'],data['low'],data['close'],14)
    data['adx']=adx(data['high'],data['low'],data['close'],14)
    data['er']=kaufman_er(data['close'],48)
    frames={asset:data}
    # lag=0 expresses the signal calculated at the last completed candle;
    # the backtester uses lag=1 to fill on the following candle.
    compress=sig_compress(frames,atr_n=24,pct_n=336,pct_lo=.25,break_n=96,
                          exit_n=48,confirm=2,adx_min=12.0,lag=0)[asset]
    impulse=sig_impulse(frames,impulse_atr=2.8,ema_n=48,hold_atr_trail=2.2,
                        max_hold=72,confirm=1,adx_min=16.0,er_min=.05,lag=0)[asset]
    return {'strategy_id':1,'asset':asset,'candle_start':data['dt'].iloc[-1].isoformat(),
            'legs':{'compress':int(compress.iloc[-1]),'impulse':int(impulse.iloc[-1])},
            'weights':dict(WEIGHTS), 'trade_authorized':False,
            'caveat':'bounded replay may not reproduce prior position state; not an order signal'}
