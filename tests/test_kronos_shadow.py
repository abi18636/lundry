import asyncio
import pandas as pd
import pytest
from app.kronos_shadow import closed_hourly_context, shadow_forecast


def sample():
    return pd.DataFrame({'dt': pd.date_range('2026-01-01', periods=401, freq='h', tz='UTC'),
                         'open': [100.] * 401, 'high': [101.] * 401, 'low': [99.] * 401,
                         'close': [100.] * 401, 'volume': [10.] * 401})


def test_excludes_open_candle():
    x=sample(); now=x.dt.iloc[-1]+pd.Timedelta(minutes=30)
    out=closed_hourly_context(x,now)
    assert len(out)==400 and out.dt.iloc[-1] == x.dt.iloc[-2]


def test_rejects_gaps_duplicates_bad_ohlc_and_stale():
    x=sample(); now=x.dt.iloc[-1]+pd.Timedelta(hours=1)
    for bad in [x.drop(index=10), pd.concat([x,x.iloc[[-1]]]), x.assign(high=98), x.assign(close=float('nan'))]:
        with pytest.raises(ValueError): closed_hourly_context(bad,now)
    with pytest.raises(ValueError): closed_hourly_context(x,now+pd.Timedelta(hours=3))


def test_forecast_uses_future_timestamps_and_cannot_place_orders():
    x=sample(); now=x.dt.iloc[-1]+pd.Timedelta(minutes=30)
    async def fake(data,future):
        assert data.dt.iloc[-1]==x.dt.iloc[-2]
        assert future.iloc[0] == x.dt.iloc[-1]
        return pd.DataFrame({'close':[100,101,102,103]})
    result=asyncio.run(shadow_forecast('BTC',x,now,fake))
    assert result.predicted_return == pytest.approx(0.03)


def test_rejects_bad_forecast_and_timeout():
    x=sample(); now=x.dt.iloc[-1]+pd.Timedelta(minutes=30)
    async def bad(data,future): return pd.DataFrame({'close':[float('nan')]*4})
    async def slow(data,future): await asyncio.sleep(1)
    with pytest.raises(ValueError): asyncio.run(shadow_forecast('BTC',x,now,bad))
    with pytest.raises(TimeoutError): asyncio.run(shadow_forecast('BTC',x,now,slow,timeout=.001))
