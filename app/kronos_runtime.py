"""Optional research-only Kronos loader. No order access.

Requires the official Kronos source on PYTHONPATH and optional dependencies.
A worker environment, rather than Render free, is recommended for real use.
"""
import asyncio
from functools import lru_cache
import pandas as pd
from app.kronos_shadow import shadow_forecast

MODEL_REVISION = 'f4e68697d9d5aed55cef5c96aabc3376bcad9f81'
TOKENIZER_REVISION = '26966d0035065a0cae0ebad7af8ece35bc1fb51c'

@lru_cache(maxsize=1)
def _predictor():
    import torch
    from model import Kronos, KronosTokenizer, KronosPredictor
    torch.set_num_threads(2)
    tokenizer = KronosTokenizer.from_pretrained('NeoQuasar/Kronos-Tokenizer-2k', revision=TOKENIZER_REVISION)
    model = Kronos.from_pretrained('NeoQuasar/Kronos-mini', revision=MODEL_REVISION)
    return KronosPredictor(model, tokenizer, device='cpu', max_context=512)

def _sync_predict(data, future):
    predictor = _predictor()
    frame = data[['open','high','low','close','volume']].reset_index(drop=True)
    return predictor.predict(df=frame, x_timestamp=data['dt'].reset_index(drop=True),
                             y_timestamp=future.reset_index(drop=True), pred_len=len(future),
                             T=1.0, top_p=0.9, sample_count=1, verbose=False)

async def research_forecast(asset, candles, now):
    async def forecast_fn(data, future):
        return await asyncio.to_thread(_sync_predict, data, future)
    return await shadow_forecast(asset, candles, now, forecast_fn, timeout=30.0)
