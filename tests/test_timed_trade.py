import time

import httpx
import pytest
from fastapi.testclient import TestClient

import app.engine as engine_module
import app.main as main_module
from app.config import Settings
from app.deribit import DeribitAPIError
from app.engine import TradingEngine
from app.telegram_bot import TelegramReporter
from app.test_trade import TimedTestTrade, TestTradeRejected


class Clock:
    def __init__(self): self.now=time.time()
    def __call__(self): return self.now
    def advance(self,seconds):self.now+=seconds


class DemoVenue:
    last_testnet=True
    def __init__(self,clock):
        self.clock=clock;self.quantities={};self.orders=[];self.calls=[]
        self.state='open';self.age=0;self.ask=2701.;self.bid=2699.;self.equity=1000.;self.available=1000.
        self.fill_fraction=1.;self.failure=None;self.raise_after_fill=False
        self.positions_failure=False;self.hidden_labels=False;self.extra_orders=[];self.private_reads=0
    def instrument(self,inst):return {'instrument_type':'linear','settlement_currency':'USDC','kind':'future',
                                      'contract_size':.0001,'min_trade_amount':.0001,'tick_size':.01}
    def order_book(self,inst,depth=5):return {'state':self.state,'timestamp':int((self.clock()-self.age)*1000),
                                           'bids':[[self.bid,10]] if self.bid else [],'asks':[[self.ask,10]] if self.ask else [],'mark_price':2700.}
    def positions(self,currency):
        self.private_reads+=1
        if self.positions_failure:raise RuntimeError('position read failed')
        return [{'instrument_name':a+'_USDC-PERPETUAL','size_currency':abs(q),'direction':'buy' if q>0 else 'sell',
                 'mark_price':{'ETH':2700.,'BTC':80000.,'TRX':.334}.get(a,100.)} for a,q in self.quantities.items() if q]
    def account_summary(self,currency):
        self.private_reads+=1
        return {'equity':self.equity,'balance':self.equity,'available_funds':self.available}
    def open_orders(self):return self.extra_orders+[o for o in self.orders if o['order_state']=='open']
    def recent_orders(self,inst,historical=False):return list(self.orders)
    def orders_by_label(self,label):return [] if self.hidden_labels else [o for o in self.orders if o['label']==label]
    def cancel_order(self,order_id):
        for o in self.orders:
            if o['order_id']==order_id:o['order_state']='cancelled'
    def limit_ioc(self,instrument,direction,amount,price,label,reduce_only=False):
        self.calls.append({'instrument':instrument,'direction':direction,'amount':amount,'price':price,
                           'label':label,'reduce_only':reduce_only,'at':self.clock()})
        if self.failure and not self.raise_after_fill:raise self.failure
        q=amount*self.fill_fraction;a=instrument.split('_')[0]
        if reduce_only:q=min(q,max(0,self.quantities.get(a,0)))
        self.quantities[a]=self.quantities.get(a,0)+(q if direction=='buy' else -q)
        order={'order_id':'DEMO-'+str(len(self.calls)),'order_state':'filled' if q==amount else 'cancelled',
               'amount':amount,'filled_amount':q,'average_price':price,'label':label,'direction':direction,
               'instrument_name':instrument,'reduce_only':reduce_only,'creation_timestamp':int(self.clock()*1000),
               'last_update_timestamp':int(self.clock()*1000)}
        self.orders.append(order)
        if self.failure:raise self.failure
        return {'order':order,'trades':[{'amount':q,'price':price,'trade_id':'DEMO-T-'+str(len(self.calls)),
                                         'timestamp':int(self.clock()*1000),'fee':.0001,'fee_currency':'USDC'}] if q else []}
    def close(self):pass


@pytest.fixture
def demo(tmp_path,monkeypatch):
    monkeypatch.setattr(engine_module,'get_reporter',lambda:None)
    clock=Clock()
    settings=Settings(_env_file=None,deribit_client_id='MOCK_ID',deribit_client_secret='MOCK_SECRET',
                      assets='BTC,ETH',test_trade_asset='ETH',test_trade_max_notional_usd=5,state_path=str(tmp_path/'state.json'))
    engine=TradingEngine(settings);venue=DemoVenue(clock);engine.client=venue
    engine.test_trader=TimedTestTrade(engine,clock)
    return engine,venue,clock


def test_real_receipt_arms_60_seconds_and_reduce_only_exit(demo):
    e,v,c=demo;state=e.test_trader.start(background=False)
    assert state['active'] and state['status']=='holding'
    assert state['entry_filled_amount']==.0001 and state['seconds_remaining']==pytest.approx(60,abs=.01)
    assert state['entry']['order_id']=='DEMO-1' and state['entry']['trade_ids']==['DEMO-T-1']
    c.advance(59);e.test_trader.step();assert len(v.calls)==1
    c.advance(1);e.test_trader.step()
    assert len(v.calls)==2 and v.calls[1]['direction']=='sell' and v.calls[1]['reduce_only']
    assert v.calls[1]['amount']==.0001 and v.calls[1]['at']-v.calls[0]['at']==pytest.approx(60)
    assert e.test_trader.snapshot()['status']=='closed' and not v.quantities['ETH']
    assert len(e.state.orders_log)==2


def test_other_position_is_untouched(demo):
    e,v,c=demo;v.quantities={'BTC':.0002};e.test_trader.start(background=False);c.advance(60);e.test_trader.step()
    assert v.quantities['BTC']==.0002
    assert all(call['instrument']=='ETH_USDC-PERPETUAL' for call in v.calls)


def test_preexisting_eth_is_never_net_closed(demo):
    e,v,c=demo;v.quantities={'ETH':.0001}
    with pytest.raises(TestTradeRejected) as err:e.test_trader.start(background=False)
    assert err.value.code=='existing_position' and not v.calls


def test_duplicate_button_does_not_open_second_position(demo):
    e,v,c=demo;e.test_trader.start(background=False)
    with pytest.raises(TestTradeRejected) as err:e.test_trader.start(background=False)
    assert err.value.code=='already_active' and len(v.calls)==1


def test_normal_strategy_cycle_is_paused_while_test_holds(demo):
    e,v,c=demo;e.test_trader.start(background=False);out=e.once()
    assert len(v.calls)==1 and all(a['status']=='test_trade_paused' for a in out['actions'])


@pytest.mark.parametrize('change,code',[
    (lambda v:setattr(v,'state','halted'),'market_not_open'),
    (lambda v:setattr(v,'age',500),'stale_quote'),
    (lambda v:setattr(v,'bid',0),'no_exit_liquidity'),
    (lambda v:setattr(v,'ask',0),'no_entry_liquidity'),
    (lambda v:setattr(v,'ask',4000),'wide_spread'),
    (lambda v:setattr(v,'last_testnet',False),'testnet_not_confirmed'),
    (lambda v:setattr(v,'available',0),'insufficient_funds'),
])
def test_entry_checks_are_not_bypassed(demo,change,code):
    e,v,c=demo;change(v)
    with pytest.raises(TestTradeRejected) as err:e.test_trader.start(background=False)
    assert err.value.code==code and not v.calls and not e.test_trader.is_active()


def test_testnet_only_even_if_mainnet_allowed_for_other_features(demo):
    e,v,c=demo;e.settings.deribit_base_url='https://www.deribit.com/api/v2';e.settings.allow_mainnet_trading=True
    with pytest.raises(TestTradeRejected) as err:e.test_trader.start(background=False)
    assert err.value.code=='testnet_only' and not v.private_reads and not v.calls


def test_unfilled_ack_never_gets_a_fake_timer_or_fill(demo):
    e,v,c=demo;v.fill_fraction=0;state=e.test_trader.start(background=False)
    assert state['status']=='unfilled' and not state['active']
    assert not state.get('close_due_timestamp') and not e.state.orders_log
    c.advance(60);e.test_trader.step();assert len(v.calls)==1


def test_explicit_entry_rejection_does_not_arm_test(demo):
    e,v,c=demo;v.failure=DeribitAPIError('private/buy',10031,'invalid_args_for_instrument')
    state=e.test_trader.start(background=False)
    assert state['status']=='failed' and not state['active'] and not e.state.orders_log


def test_entry_timeout_is_reconciled_not_retried(demo):
    e,v,c=demo;v.failure=DeribitAPIError('private/buy','transport','ReadTimeout');v.raise_after_fill=True
    state=e.test_trader.start(background=False);assert state['status']=='entry_uncertain'
    v.failure=None;c.advance(3);e.test_trader.step();assert len(v.calls)==1
    assert e.test_trader.snapshot()['seconds_remaining']==pytest.approx(57,abs=.01)
    c.advance(57);e.test_trader.step();assert len(v.calls)==2 and not e.test_trader.is_active()


def test_unconfirmed_entry_prohibits_blind_retry(demo):
    e,v,c=demo;v.failure=DeribitAPIError('private/buy','transport','ReadTimeout');v.hidden_labels=True
    e.test_trader.start(background=False);c.advance(60);e.test_trader.step()
    assert len(v.calls)==1 and e.test_trader.is_active() and e.test_trader.state['pending']


def test_halt_at_deadline_is_not_reported_closed(demo):
    e,v,c=demo;e.test_trader.start(background=False);v.state='halted';c.advance(60);e.test_trader.step()
    assert e.test_trader.state['status']=='exit_blocked' and e.test_trader.is_active() and len(v.calls)==1
    v.state='open';c.advance(5);e.test_trader.step()
    assert e.test_trader.state['status']=='closed' and len(v.calls)==2


def test_zero_exit_fill_keeps_test_open_then_retries_reduce_only(demo):
    e,v,c=demo;e.test_trader.start(background=False);v.fill_fraction=0;c.advance(60);e.test_trader.step()
    assert e.test_trader.is_active() and e.test_trader.state['exit_filled_amount']==0
    v.fill_fraction=1;c.advance(5);e.test_trader.step()
    assert e.test_trader.state['status']=='closed' and all(x['reduce_only'] for x in v.calls[1:])


def test_ambiguous_exit_waits_for_unique_label_before_retry(demo):
    e,v,c=demo;e.test_trader.start(background=False);v.failure=DeribitAPIError('private/sell','transport','ReadTimeout');v.raise_after_fill=True
    c.advance(60);e.test_trader.step();assert e.test_trader.state['status']=='exit_uncertain'
    v.failure=None;c.advance(5);e.test_trader.step()
    assert len(v.calls)==2 and e.test_trader.state['status']=='closed'


def test_late_external_addition_is_not_closed_with_test(demo):
    e,v,c=demo;e.test_trader.start(background=False);v.quantities['ETH']+=.0003;c.advance(60);e.test_trader.step()
    assert v.quantities['ETH']==pytest.approx(.0003) and v.calls[1]['amount']==.0001
    assert e.test_trader.state['residual_position']==pytest.approx(.0003)


def test_external_manual_close_is_not_fabricated_as_timer_fill(demo):
    e,v,c=demo;e.test_trader.start(background=False);v.quantities['ETH']=0;c.advance(60);e.test_trader.step()
    assert e.test_trader.is_active();c.advance(3);e.test_trader.step()
    assert e.test_trader.state['status']=='closed_external' and len(v.calls)==1 and len(e.state.orders_log)==1


def test_lost_local_state_is_recovered_from_exchange_labels(demo,tmp_path):
    e,v,c=demo;e.test_trader.start(background=False)
    e2=TradingEngine(Settings(_env_file=None,deribit_client_id='MOCK_ID',deribit_client_secret='MOCK_SECRET',
                             assets='BTC,ETH',test_trade_asset='ETH',test_trade_max_notional_usd=5,state_path=str(tmp_path/'new-instance.json')))
    e2.client=v;e2.test_trader=TimedTestTrade(e2,c);e2.test_trader.recovery_in_progress=True
    c.advance(60);e2.test_trader.recover_on_boot()
    assert e2.test_trader.is_active() and e2.test_trader.state['recovered'] and len(v.calls)==1
    e2.test_trader.step();assert e2.test_trader.state['status']=='closed' and len(v.calls)==2


def test_ordinary_eth_position_is_not_adopted_as_test(demo):
    e,v,c=demo;v.quantities={'ETH':.0003};e.test_trader.recovery_in_progress=True;e.test_trader.recover_on_boot()
    assert not e.test_trader.is_active() and not e.test_trader.recovery_in_progress and not v.calls


def test_retained_state_keeps_original_deadline_after_restart(demo):
    e,v,c=demo;e.test_trader.start(background=False);e2=TradingEngine(e.settings);e2.client=v
    e2.test_trader=TimedTestTrade(e2,c);e2.test_trader.recovery_in_progress=True
    c.advance(61);e2.test_trader.recover_on_boot();e2.test_trader.step()
    assert e2.test_trader.state['status']=='closed' and len(v.calls)==2


def test_web_test_endpoints_require_owner_token(demo,monkeypatch):
    e,v,c=demo;monkeypatch.setattr(main_module,'get_engine',lambda:e);monkeypatch.setattr(main_module,'get_settings',lambda:e.settings)
    api=TestClient(main_module.app)
    assert api.post('/api/test-trade/start').status_code==403
    assert api.post('/api/test-trade/close').status_code==403
    assert api.get('/api/test-trade').json()['hold_seconds']==60
    e.settings.dashboard_token='MOCK_ADMIN_TOKEN'
    assert api.post('/api/test-trade/start').status_code==401
    assert not v.calls


def test_telegram_requires_confirmation_and_rejects_other_chat(demo,monkeypatch):
    e,v,c=demo;r=TelegramReporter('MOCK_TOKEN','123');r.bind_engine(e);sent=[]
    monkeypatch.setattr(r,'send',lambda text,**kw:sent.append((text,kw)))
    monkeypatch.setattr(r,'answer_callback',lambda *args,**kw:None)
    r._on_callback({'id':'c','data':'panel:testconfirm','message':{'chat':{'id':999}}})
    assert not v.calls
    r._on_callback({'id':'c','data':'panel:testtrade','message':{'chat':{'id':123}}})
    assert sent and 'تأیید معامله تست' in sent[-1][0] and not v.calls
    assert any(b['callback_data']=='panel:testconfirm' for row in sent[-1][1]['reply_markup']['inline_keyboard'] for b in row)
    r._http.close()


def test_deep_link_shows_confirmation_not_an_immediate_trade(demo,monkeypatch):
    e,v,c=demo;r=TelegramReporter('MOCK_TOKEN','123');r.bind_engine(e);sent=[]
    monkeypatch.setattr(r,'send',lambda text,**kw:sent.append(text))
    r._on_command('/start','test60')
    assert sent and 'تأیید معامله تست' in sent[0] and not v.calls
    r._http.close()


def test_keyboard_exposes_test_start_status_and_close(demo):
    r=TelegramReporter('MOCK_TOKEN','123')
    callbacks={b.get('callback_data') for row in r.main_keyboard()['inline_keyboard'] for b in row}
    assert {'panel:testtrade','panel:teststatus','panel:testclose'}.issubset(callbacks)
    r._http.close()


def test_actual_60_second_worker_not_90_second_strategy_loop(tmp_path,monkeypatch):
    monkeypatch.setattr(engine_module,'get_reporter',lambda:None)
    e=TradingEngine(Settings(_env_file=None,deribit_client_id='MOCK_ID',deribit_client_secret='MOCK_SECRET',
                             assets='BTC,ETH',test_trade_asset='ETH',test_trade_max_notional_usd=5,loop_seconds=90,state_path=str(tmp_path/'live-timer.json')))
    v=DemoVenue(time.time);e.client=v
    start=time.monotonic();e.test_trader.start(background=True)
    assert e.test_trader.closed_event.wait(70),'60-second close did not complete'
    elapsed=time.monotonic()-start
    e.test_trader.shutdown()
    assert 59<=elapsed<=65 and e.test_trader.state['status']=='closed'
    assert len(v.calls)==2 and v.calls[1]['reduce_only']


def test_btc_minimum_lot_preserves_existing_eth_and_trx(demo):
    e,v,c=demo;e.settings.test_trade_asset='BTC';e.settings.test_trade_max_notional_usd=10
    v.quantities={'ETH':.0015,'TRX':19}
    v.order_book=lambda inst,depth=5:{'state':'open','timestamp':int(c()*1000),'bids':[[83500.,10]],'asks':[[83700.,10]],'mark_price':83600.}
    e.test_trader.start(background=False);c.advance(60);e.test_trader.step()
    assert e.test_trader.state['status']=='closed'
    assert v.quantities['ETH']==.0015 and v.quantities['TRX']==19
    assert all(call['instrument']=='BTC_USDC-PERPETUAL' for call in v.calls)
    assert v.calls[0]['amount']*v.calls[0]['price']<10


@pytest.mark.parametrize('shape', [[{'order_id':'o-1','label':'ordinary'}], {'orders':[{'order_id':'o-1','label':'ordinary'}],'continuation':None}])
def test_order_history_normalizes_gateway_shapes(shape):
    from app.deribit import DeribitClient
    client=DeribitClient('https://test.deribit.com/api/v2','MOCK_ID','MOCK_SECRET')
    client._private=lambda method,params:shape
    assert client.recent_orders('BTC_USDC-PERPETUAL')==[{'order_id':'o-1','label':'ordinary'}]
    client.close()


def test_history_failure_is_backed_off_and_does_not_globally_pause_strategy_cycle(demo,monkeypatch):
    import numpy as np
    import pandas as pd
    e,v,c=demo;v.quantities={'ETH':.0003};calls=[]
    def fail_history(*args,**kwargs):
        calls.append(1);raise DeribitAPIError('private/get_order_history_by_instrument','protocol','unavailable')
    v.recent_orders=fail_history
    def candles(inst,**kwargs):
        n=800;d=pd.date_range(end=pd.Timestamp(c(),unit='s',tz='UTC').floor('h')-pd.Timedelta(hours=1),periods=n,freq='h')
        return pd.DataFrame({'dt':d,'open':100.,'high':101.,'low':99.,'close':100.,'volume':1.})
    v.candles=candles;e.test_trader.recovery_in_progress=True
    e.test_trader.recover_on_boot();e.test_trader.recover_on_boot()
    assert len(calls)==1 and e.test_trader.state['recovery_error']
    output=e.once()
    assert e.state.mode=='running' and len(e.state.sleeves)==5
    assert next(a for a in output['actions'] if a['asset']=='ETH')['status']=='test_recovery_pending'
    assert next(a for a in output['actions'] if a['asset']=='BTC')['status']!='test_trade_paused'
