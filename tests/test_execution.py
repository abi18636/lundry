import math
import random

import pytest

from app.execution import fill_summary, floor_amount, ioc_price, plan_rebalance, signed_position


@pytest.mark.parametrize('value,step,expected', [(0.000199999,0.0001,0.0001), (0.03,0.01,0.03), (-0.039,0.01,-0.03), (17.4,1,17), (0,0.001,0)])
def test_lot_floor(value,step,expected):
    assert floor_amount(value,step) == expected


def test_floor_never_inflates_budget():
    rng = random.Random(177)
    for step in [0.0001,0.001,0.01,0.1,1,10]:
        for _ in range(100):
            quantity = rng.uniform(-100,100)
            rounded = floor_amount(quantity,step)
            assert abs(rounded) <= abs(quantity)+1e-12
            assert math.isclose(abs(rounded)/step, round(abs(rounded)/step), abs_tol=1e-8)


@pytest.mark.parametrize('direction,expected',[('buy',0.001),('sell',-0.001)])
def test_linear_position_sign(direction,expected):
    assert signed_position({'size':84,'size_currency':0.001,'direction':direction}) == expected


def test_no_guessing_usd_as_coin():
    with pytest.raises(ValueError):
        signed_position({'size':84,'direction':'buy'})


def test_below_btc_lot_does_not_create_10_dollar_order():
    p = plan_rebalance(0, 4/84272.2,84272.2,0.0001,0.0001)
    assert p.status == 'below_exchange_minimum'
    assert p.amount == 0
    assert p.target == 0


def test_small_valid_entry_is_not_blocked_by_legacy_10_usd():
    p=plan_rebalance(0,0.0004,2718.67,0.0001,0.0001)
    assert p.status=='planned'
    assert p.amount==0.0004


def test_close_existing_one_lot_even_if_under_rebalance_threshold():
    p=plan_rebalance(1,0,0.1,1,1,100)
    assert p.status=='planned'
    assert p.direction=='sell' and p.reduce_only and p.amount==1


def test_cover_short_is_buy_reduce_only():
    p=plan_rebalance(-0.01,0,2700,0.0001,0.0001)
    assert p.direction=='buy' and p.reduce_only and p.intent=='close'


def test_flip_closes_first_not_double_inventory():
    p=plan_rebalance(0.01,-0.02,2700,0.0001,0.0001)
    assert p.direction=='sell' and p.reduce_only and p.amount==0.01


def test_acknowledgement_not_a_fill():
    r=fill_summary({'order':{'order_id':'o-1','order_state':'cancelled','amount':10,'filled_amount':0}})
    assert r['filled_amount']==0


def test_trade_receipt_is_preserved():
    r=fill_summary({'order':{'order_id':'o-1','order_state':'filled','filled_amount':1,'average_price':100},
                    'trades':[{'amount':1,'price':100,'trade_id':'t-1','fee':.02,'fee_currency':'USDC'}]})
    assert r['order_id']=='o-1' and r['trade_ids']==['t-1'] and r['fee']==.02


@pytest.mark.parametrize('direction,level,bound', [('buy',100.01,100.51005),('sell',99.99,99.49005)])
def test_ioc_price_stays_inside_slippage_bound(direction,level,bound):
    book={'asks':[[100.01,10]],'bids':[[99.99,10]]}
    price=ioc_price(book,direction,{'tick_size':.1},50)
    assert price<=bound if direction=='buy' else price>=bound
    assert math.isclose(price/.1,round(price/.1),abs_tol=1e-8)
