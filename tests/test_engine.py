import threading
import time
from dataclasses import dataclass

import numpy as np
import pandas as pd
import pytest

import app.engine as engine_module
from app.config import Settings
from app.deribit import DeribitAPIError
from app.engine import EngineBusyError, TradingEngine
from app.sleeves import SleeveResult


class FakeVenue:
    last_testnet=True
    def __init__(self):
        self.quantities={}
        self.prices={'BTC':100.,'ETH':100.,'SOL':100.}
        self.state='open'
        self.book_age=0
        self.equity=1000.
        self.available=1000.
        self.position_failure=False
        self.account_failure=False
        self.book_failure=False
        self.order_failure=None
        self.fill_fraction=1.
        self.calls=[]
        self.reconciled=[]
        self.existing_orders=[]
        self.wait_event=None
        self.spread=0.1
    def account_summary(self,cur):
        if self.account_failure:raise RuntimeError('account read failed')
        return {'equity':self.equity,'balance':self.equity,'available_funds':self.available}
    def positions(self,cur):
        if self.position_failure:raise RuntimeError('position read failed')
        return [{'instrument_name':a+'_USDC-PERPETUAL','size_currency':abs(q),'size':abs(q)*self.prices[a],
                 'direction':'buy' if q>0 else 'sell','mark_price':self.prices[a]} for a,q in self.quantities.items() if q]
    def instrument(self,inst):
        return {'instrument_type':'linear','settlement_currency':'USDC','kind':'future',
                'contract_size':.1,'min_trade_amount':.1,'tick_size':.01}
    def order_book(self,inst,depth=5):
        if self.wait_event:self.wait_event.wait(5)
        if self.book_failure:raise DeribitAPIError('public/get_order_book',10028,'too_many_requests')
        price=self.prices[inst.split('_')[0]]
        return {'state':self.state,'timestamp':int((time.time()-self.book_age)*1000),
                'mark_price':price,'bids':[[price*(1-self.spread/200),100]],
                'asks':[[price*(1+self.spread/200),100]]}
    def candles(self,inst,hours=2500,resolution='60'):
        n=800;price=self.prices[inst.split('_')[0]]
        return pd.DataFrame({'dt':pd.date_range(end=pd.Timestamp.now(tz='UTC').floor('h')-pd.Timedelta(hours=1),periods=n,freq='h'),
                             'open':np.full(n,price),'high':np.full(n,price+1),'low':np.full(n,price-1),
                             'close':np.full(n,price),'volume':np.ones(n)})
    def limit_ioc(self,instrument,direction,amount,price,label,reduce_only=False):
        self.calls.append({'instrument':instrument,'direction':direction,'amount':amount,'price':price,
                           'label':label,'reduce_only':reduce_only})
        if self.order_failure:raise self.order_failure
        a=instrument.split('_')[0];filled=amount*self.fill_fraction
        self.quantities[a]=self.quantities.get(a,0)+(filled if direction=='buy' else -filled)
        number=len(self.calls)
        return {'order':{'order_id':f'order-{number}','order_state':'filled' if self.fill_fraction==1 else 'cancelled',
                          'amount':amount,'filled_amount':filled,'average_price':price},
                'trades':[{'trade_id':f'trade-{number}','amount':filled,'price':price,'fee':0.01,'fee_currency':'USDC'}] if filled else []}
    def open_orders(self):return self.existing_orders
    def orders_by_label(self,label):return self.reconciled
    def cancel_order(self,order_id):return {'order_id':order_id,'order_state':'cancelled'}
    def close(self):pass


@pytest.fixture
def make_engine(tmp_path,monkeypatch):
    monkeypatch.setattr(engine_module,'get_reporter',lambda:None)
    def make(desired=None,assets='BTC',**kw):
        desired=dict(desired or {'BTC':.2})
        settings=Settings(_env_file=None,assets=assets,capital_usd=100,lev_cap=1,deribit_client_id='TEST_ID',
                          deribit_client_secret='TEST_ONLY_NOT_A_REAL_SECRET',state_path=str(tmp_path/(str(time.time_ns())+'.json')),
                          sleeves_enabled='zenith_endurance',sleeve_weights='zenith_endurance:1',**kw)
        venue=FakeVenue();engine=TradingEngine(settings);engine.client=venue
        def fake_sleeves(frames,total_capital,weights,lev_cap,enabled):
            r=SleeveResult('zenith_endurance','test fixture',100,1)
            net={}
            for a,frame in frames.items():
                q=desired.get(a,0);px=float(frame.close.iloc[-1]);r.per_asset[a]={'side':float(np.sign(q)),
                    'price':px,'target_coin':q,'notional_usd':abs(q)*px}
                net[a]={'price':px,'target_coin':q,'notional_usd':abs(q)*px,'contributors':{'zenith_endurance':{'coin':q}}}
            return [r],net
        monkeypatch.setattr(engine_module,'run_all_sleeves',fake_sleeves)
        return engine,venue,desired
    return make


def test_open_market_places_and_proves_fill(make_engine):
    e,v,_=make_engine();out=e.once()
    assert len(v.calls)==1 and v.calls[0]['direction']=='buy'
    assert out['actions'][0]['status']=='bought'
    assert e.state.orders_log[0]['order_id']=='order-1'
    assert e.state.orders_log[0]['trade_ids']==['trade-1']
    assert out['diagnostics']['confirmed_fill_count']==1


def test_no_duplicate_when_target_reached(make_engine):
    e,v,_=make_engine();e.once();out=e.once()
    assert len(v.calls)==1 and out['actions'][0]['status']=='target_reached'


def test_halt_gate_keeps_true_target_and_inventory(make_engine):
    e,v,_=make_engine();v.state='halted';v.quantities={'BTC':.1};out=e.once();a=out['actions'][0]
    assert v.calls==[] and a['status']=='market_halted'
    assert a['target_amt']==.2 and a['current_size']==.1
    assert out['diagnostics']['primary_blocker']=='venue_halted'
    assert not out['diagnostics']['trading_ready']


def test_market_reopen_recovers_without_forced_tick(make_engine):
    e,v,_=make_engine();v.state='halted';e.once();v.state='open';out=e.once()
    assert len(v.calls)==1 and out['actions'][0]['filled_amount']>0
    assert any(x['kind']=='recovery' for x in e.state.events)


def test_stale_open_quote_is_blocked(make_engine):
    e,v,_=make_engine();v.book_age=1000;out=e.once()
    assert v.calls==[] and out['actions'][0]['status']=='stale_market_data'


def test_book_api_error_is_not_halted(make_engine):
    e,v,_=make_engine();v.book_failure=True;out=e.once()
    assert v.calls==[] and out['diagnostics']['market_halted_count']==0
    assert out['actions'][0]['status']!='market_halted'
    assert out['actions'][0]['status']=='market_api_error'


def test_failed_position_read_never_assumes_flat(make_engine):
    e,v,_=make_engine();v.position_failure=True;out=e.once()
    assert v.calls==[] and out['actions'][0]['status']=='safety_blocked'
    assert out['diagnostics']['primary_blocker']=='positions_unavailable'


def test_failed_account_read_blocks_orders(make_engine):
    e,v,_=make_engine();v.account_failure=True;out=e.once()
    assert v.calls==[] and out['diagnostics']['primary_blocker']=='account_unavailable'


def test_small_target_not_rounded_up_to_legacy_minimum(make_engine):
    e,v,_=make_engine({'BTC':.02},min_notional_usd=10);out=e.once()
    assert v.calls==[] and out['actions'][0]['status']=='below_exchange_minimum'


def test_reductions_use_reduce_only_and_are_not_dust_ignored(make_engine):
    e,v,_=make_engine({'BTC':0});v.quantities={'BTC':.1};out=e.once()
    assert v.calls[0]['reduce_only'] and v.calls[0]['direction']=='sell'
    assert out['actions'][0]['status']=='closed'


def test_short_is_covered_not_ignored(make_engine):
    e,v,_=make_engine({'BTC':0});v.quantities={'BTC':-.1};e.once()
    assert v.calls[0]['reduce_only'] and v.calls[0]['direction']=='buy'
    assert not v.quantities['BTC']


def test_flip_needs_close_then_separate_entry(make_engine):
    e,v,_=make_engine({'BTC':-.2},long_only=False);v.quantities={'BTC':.1}
    e.once();assert v.calls[0]['reduce_only'] and v.quantities['BTC']==0
    e.once();assert len(v.calls)==2 and not v.calls[1]['reduce_only'] and v.calls[1]['direction']=='sell'


def test_global_cap_after_quantization(make_engine):
    e,v,_=make_engine({'BTC':2,'ETH':2,'SOL':2},assets='BTC,ETH,SOL',max_notional_usd=500)
    e.once()
    assert sum(abs(q)*100 for q in v.quantities.values())<=100
    assert e.state.risk['notional_cap_usd']==100


def test_existing_outside_universe_position_consumes_risk_budget(make_engine):
    e,v,_=make_engine({'BTC':.2});v.quantities={'ETH':1};out=e.once()
    assert v.calls==[] and out['actions'][0]['status']=='risk_cap_blocked'


def test_guard_latches_and_closes_reduce_only(make_engine):
    e,v,_=make_engine({'BTC':0});e.once();v.quantities={'BTC':.2};v.equity=984
    out=e.once()
    assert e.state.risk['drawdown_latched'] and v.calls[0]['reduce_only']
    assert out['diagnostics']['primary_blocker']=='drawdown_guard'
    v.equity=1000;e.once();assert e.state.risk['drawdown_latched']


def test_partial_fill_only_records_actual_quantity(make_engine):
    e,v,_=make_engine({'BTC':.4});v.fill_fraction=.5;out=e.once()
    assert out['actions'][0]['status']=='partially_filled'
    assert e.state.orders_log[0]['filled_amount']==.2
    assert e.state.orders_log[0]['amount']==.2


def test_unfilled_ack_is_not_success(make_engine):
    e,v,_=make_engine();v.fill_fraction=0;out=e.once()
    assert out['actions'][0]['status']=='unfilled'
    assert e.state.orders_log==[] and not e.state.last_fill_at


def test_dry_run_not_in_real_fill_journal(make_engine):
    e,v,_=make_engine(dry_run=True);out=e.once()
    assert v.calls==[] and not e.state.orders_log
    assert out['diagnostics']['execution_environment']=='dry-run'


def test_explicit_rejection_backoff_not_blind_fallback_close(make_engine):
    e,v,_=make_engine();v.order_failure=DeribitAPIError('private/buy',10031,'invalid_args_for_instrument')
    first=e.once();second=e.once()
    assert len(v.calls)==1 and first['actions'][0]['status']=='error'
    assert second['actions'][0]['status']=='order_error_backoff'
    assert e.state.pending_order is None


def test_ambiguous_timeout_no_duplicate_and_is_persisted(make_engine):
    e,v,_=make_engine();v.order_failure=DeribitAPIError('private/buy','transport','ReadTimeout')
    e.once();e.once()
    assert len(v.calls)==1 and e.state.pending_order
    restored=TradingEngine(e.settings)
    assert restored.state.pending_order['label']==e.state.pending_order['label']


def test_uncertain_order_reconciled_before_next_submission(make_engine):
    e,v,_=make_engine();v.order_failure=DeribitAPIError('private/buy','transport','ReadTimeout');e.once()
    v.order_failure=None;v.quantities={'BTC':.2};v.reconciled=[{'order_id':'recovered-1','order_state':'filled','filled_amount':.2,'average_price':100}]
    e.once()
    assert e.state.pending_order is None and len(v.calls)==1
    assert e.state.orders_log[0]['status']=='recovered_fill'
    e.once();assert len(v.calls)==1


def test_wide_spread_blocks_entry_but_not_reduce_only_exit(make_engine):
    e,v,desired=make_engine();v.spread=3
    assert e.once()['actions'][0]['status']=='spread_too_wide'
    desired['BTC']=0;e._signal_cache=None;v.quantities={'BTC':.1};e.once()
    assert len(v.calls)==1 and v.calls[0]['reduce_only']


def test_health_snapshot_not_blocked_and_overlapping_tick_rejected(make_engine):
    e,v,_=make_engine();v.wait_event=threading.Event();errors=[]
    def run():
        try:e.once()
        except Exception as ex:errors.append(ex)
    t=threading.Thread(target=run);t.start()
    deadline=time.time()+2
    while not e.state.cycle_in_progress and time.time()<deadline:time.sleep(.001)
    before=time.monotonic();e.snapshot();assert time.monotonic()-before<.2
    with pytest.raises(EngineBusyError):e.once()
    v.wait_event.set();t.join(5);assert not t.is_alive() and not errors


def test_public_mainnet_cannot_become_execution_by_accident(make_engine):
    e,v,_=make_engine(deribit_base_url='https://www.deribit.com/api/v2');out=e.once()
    assert not v.calls and out['diagnostics']['primary_blocker']=='mainnet_not_authorized'


def test_ops_hourly_review_is_recorded(make_engine):
    e,v,_=make_engine();e.once();first=e.state.ops_reviews[-1];e.once()
    assert len(e.state.ops_reviews)==1 and first['at']
    e._last_review-=3601;e.once();assert len(e.state.ops_reviews)==2


def test_collateral_is_reserved_across_multiple_entries(make_engine):
    e,v,_=make_engine({'BTC':.2,'ETH':.2},assets='BTC,ETH');v.available=30
    out=e.once()
    assert len(v.calls)==1
    assert any(a['status']=='insufficient_funds' for a in out['actions'])


def test_flat_open_symbol_cannot_hide_unaffordable_active_signal(make_engine):
    e,v,_=make_engine({'BTC':.02,'ETH':0},assets='BTC,ETH')
    out=e.once()
    assert not out['diagnostics']['trading_ready']
    assert out['diagnostics']['primary_blocker']=='below_exchange_minimum'


def test_existing_order_is_not_ignored_or_cancelled(make_engine):
    e,v,_=make_engine();v.existing_orders=[{'instrument_name':'BTC_USDC-PERPETUAL','amount':.2,'filled_amount':0,'price':100,'reduce_only':False}]
    out=e.once()
    assert not v.calls and out['actions'][0]['status']=='existing_open_order'
    assert e.state.risk['reserved_open_order_notional_usd']==20


def test_orders_outside_universe_reserve_gross_budget(make_engine):
    e,v,_=make_engine();v.existing_orders=[{'instrument_name':'ETH_USDC-PERPETUAL','amount':1,'filled_amount':0,'price':100,'reduce_only':False}]
    out=e.once()
    assert not v.calls and out['actions'][0]['status']=='risk_cap_blocked'
