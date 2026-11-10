import pytest
from app.rebirth_exchange import RebirthExchangeGateway, OrderOutcomeUnknown

class Response:
    def __init__(self, body): self.body=body
    def raise_for_status(self): pass
    def json(self): return self.body

def test_disabled_by_default_and_scope():
    calls=[]
    g=RebirthExchangeGateway('https://example.test','key','secret',transport=lambda *a,**k:calls.append(k))
    with pytest.raises(PermissionError): g.submit_market('BTCUSD','buy',1,reduce_only=False)
    assert calls==[]

def test_acknowledgement_is_not_a_fill_and_reduce_only_transmitted():
    calls=[]
    def post(*a,**kw): calls.append(kw); return Response({'ok':True,'id':123})
    g=RebirthExchangeGateway('https://example.test','key','secret',transport=post,enable_send=True)
    result=g.submit_market('BTCUSD','sell',0.1,reduce_only=True)
    assert result.order_id=='123' and result.status=='accepted_not_fill_confirmed'
    assert calls[0]['json']['reduce_only'] is True and len(calls)==1

def test_unknown_outcome_no_retry():
    calls=[]
    def post(*args,**kw): calls.append(1); raise TimeoutError()
    g=RebirthExchangeGateway('https://example.test','key','secret',transport=post,enable_send=True)
    with pytest.raises(OrderOutcomeUnknown):g.submit_market('BTCUSD','buy',1,reduce_only=False)
    assert len(calls)==1

def test_reject_invalid():
    g=RebirthExchangeGateway('https://example.test','key','secret',enable_send=True)
    for symbol,side,qty,ro in [('DOGEUSD','buy',1,False),('BTCUSD','foo',1,False),('BTCUSD','buy','nan',False),('BTCUSD','buy',0,False),('BTCUSD','buy',1,None)]:
        with pytest.raises(ValueError):g.submit_market(symbol,side,qty,reduce_only=ro)
