import json
import time

import httpx
import numpy as np
import pandas as pd
import pytest

import app.sleeves as sleeves
from app.deribit import DeribitAPIError, DeribitClient
from app.telegram_bot import TelegramReporter


def frame(n=800):
    rng=np.random.default_rng(177)
    close=100*np.exp(np.cumsum(rng.normal(.001,.002,n)))
    d=pd.DataFrame({'dt':pd.date_range('2026-01-01',periods=n,freq='h',tz='UTC'),
                    'open':close*.999,'high':close*1.005,'low':close*.995,'close':close,'volume':1.})
    return sleeves.enrich(d)


def test_apex_impulse_leg_not_silently_discarded(monkeypatch):
    def fake_make(cfg,frames):return {a:pd.Series(1. if cfg['kind']=='impulse' else 0.,index=d.index) for a,d in frames.items()}
    monkeypatch.setattr(sleeves,'zenith_make',fake_make)
    r=sleeves.sleeve_zenith_apex({'BTC':frame()},25)
    assert r.per_asset['BTC']['target_coin']>0
    assert r.per_asset['BTC']['detail']['compress_notional']==0
    assert 0<r.per_asset['BTC']['detail']['impulse_notional']<=5


def test_tsmom_complete_horizons_required():
    d=frame(400)
    for builder in [sleeves.sleeve_inst_v3_stable,sleeves.sleeve_inst_v3_primary]:
        r=builder({'BTC':d},30)
        assert r.per_asset['BTC']['side']==0
        assert 'insufficient_history' in r.per_asset['BTC']['reason']


def test_confirmed_bar_tsmom_matches_original_next_bar():
    from inst_v3_stable_full import sig_tsmom_discrete as original
    d=frame(850)
    params=dict(horizons=(24,168,720),vote_min=.67,confirm=5,adx_min=22,er_min=.10,long_only=True,exit_vote=.20)
    base=original({'BTC':d},lag=1,**params)['BTC']
    lagged=sleeves.sig_tsmom_discrete({'BTC':d},lag=1,**params)['BTC']
    raw=sleeves.sig_tsmom_discrete({'BTC':d},lag=0,**params)['BTC']
    np.testing.assert_array_equal(base.values,lagged.values)
    np.testing.assert_array_equal(raw.values[:-1],base.values[1:])


def test_total_sleeve_cap_and_independence():
    fs={'BTC':frame(),'ETH':frame()}
    results,book=sleeves.run_all_sleeves(fs,100,lev_cap=.5)
    assert len(results)==5 and sum(r.capital for r in results)==100
    assert sum(abs(b['target_coin'])*b['price'] for b in book.values())<=50+1e-8
    alone,_=sleeves.run_all_sleeves(fs,25,weights={'almasi_primary':1},enabled=['almasi_primary'],lev_cap=.5)
    paired=next(r for r in results if r.sleeve_id=='almasi_primary')
    assert alone[0].per_asset==paired.per_asset


def test_order_json_rpc_and_no_secret_in_url():
    calls=[]
    def handler(request):
        body=json.loads(request.content);calls.append((str(request.url),request.method,body,dict(request.headers)))
        if body['method']=='public/auth':result={'access_token':'MOCK_ACCESS','expires_in':900}
        else:result={'order':{'order_id':'o-1','order_state':'cancelled','filled_amount':0}}
        return httpx.Response(200,json={'jsonrpc':'2.0','result':result,'testnet':True})
    c=DeribitClient('https://test.deribit.com/api/v2','MOCK_ID','MOCK_SECRET')
    c._http.close();c._http=httpx.Client(transport=httpx.MockTransport(handler))
    c.limit_ioc('ETH_USDC-PERPETUAL','buy',.0003,2700,'unique_label',reduce_only=True)
    assert all(url=='https://test.deribit.com/api/v2' and method=='POST' for url,method,body,h in calls)
    p=calls[-1][2]['params']
    assert isinstance(p['amount'],float) and p['amount']==.0003
    assert p['reduce_only'] is True and p['post_only'] is False
    assert p['time_in_force']=='immediate_or_cancel' and p['valid_until']>time.time()*1000
    c.close()


def test_rpc_errors_preserve_code_without_credentials():
    def handler(request):return httpx.Response(400,json={'error':{'code':10001,'message':'MOCK_SECRET rejected','data':'MOCK_ID'}})
    c=DeribitClient('https://test.deribit.com/api/v2','MOCK_ID','MOCK_SECRET')
    c._http.close();c._http=httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(DeribitAPIError) as info:c._auth()
    assert info.value.code==10001
    assert 'MOCK_SECRET' not in str(info.value) and 'MOCK_ID' not in str(info.value)
    c.close()


def test_candles_exclude_current_unclosed_bar():
    hour=int(time.time()//3600)*3600000
    ticks=[hour-2*3600000,hour-3600000,hour]
    def handler(request):return httpx.Response(200,json={'result':{'status':'ok','ticks':ticks,'open':[100]*3,'high':[101]*3,'low':[99]*3,'close':[100]*3,'volume':[1]*3}})
    c=DeribitClient('https://test.deribit.com/api/v2','','')
    c._http.close();c._http=httpx.Client(transport=httpx.MockTransport(handler))
    d=c.candles('BTC_USDC-PERPETUAL')
    assert len(d)==2 and d.dt.iloc[-1].value//1000000==hour-3600000
    c.close()


def test_api_error_not_silently_converted_to_unknown():
    c=DeribitClient('https://test.deribit.com/api/v2','','')
    c._http.close();c._http=httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(400,json={'error':{'code':10020,'message':'invalid_or_unsupported_instrument'}})))
    with pytest.raises(DeribitAPIError):c.market_state('NOT_AN_INSTRUMENT')
    c.close()


def reporter(monkeypatch):
    r=TelegramReporter('MOCK_NOT_A_REAL_TOKEN','MOCK_CHAT')
    sent=[];monkeypatch.setattr(r,'send',lambda text,**kw:sent.append(text))
    return r,sent


def test_telegram_dry_run_not_real_trade(monkeypatch):
    r,sent=reporter(monkeypatch)
    r.on_cycle({'actions':[{'status':'dry_run_buy','amount':1}]})
    assert not sent and r.stats['orders_ok']==0
    r._http.close()


def test_halt_notice_not_spammed_and_reopen_notice_once(monkeypatch):
    r,sent=reporter(monkeypatch)
    halted={'actions':[{'market_state':'halted','status':'market_halted'}],
            'diagnostics':{'market_open_count':0,'primary_blocker':'venue_halted'}}
    r.on_cycle(halted);r.on_cycle(halted);assert len(sent)==1
    opened={'actions':[],'diagnostics':{'market_open_count':1,'trading_state':'ready'}}
    r.on_cycle(opened);r.on_cycle(opened);assert len(sent)==2
    r._http.close()


def test_duplicate_cycle_error_not_spammed(monkeypatch):
    r,sent=reporter(monkeypatch)
    r.on_cycle({},error='same failure');r.on_cycle({},error='same failure')
    assert len(sent)==1
    r._http.close()


@pytest.mark.parametrize('url',['https://example.invalid/api/v2','http://test.deribit.com/api/v2','https://test.deribit.com/api/v2?secret=bad'])
def test_settings_refuse_untrusted_credential_destinations(url):
    from app.config import Settings
    with pytest.raises(ValueError):Settings(_env_file=None,deribit_base_url=url)
