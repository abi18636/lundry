import math
import pytest
from app.position_snapshot import normalize_positions, InvalidPositionSnapshot
from app.engine import _position_size


def test_only_explicit_valid_empty_is_flat():
    assert normalize_positions({'ok':True,'data':[]}) == []
    for data in [None, {}, {'ok':False,'data':[]}, {'ok':True}, {'ok':True,'data':None}, {'ok':True,'data':[{}]}]:
        with pytest.raises(InvalidPositionSnapshot): normalize_positions(data)


def test_normalizes_short_and_rejects_malformed_positions():
    x={'symbol':'BTCUSD','size':-0.5,'entry':100,'mark':90,'upnl':5}
    p=normalize_positions({'ok':True,'data':[x]})[0]
    assert p['instrument_name']=='BTCUSD' and p['size']==-0.5 and p['direction']=='sell'
    for bad in [dict(x,size='nan'),dict(x,entry=0),dict(x,mark=None),dict(x,symbol='')]:
        with pytest.raises(InvalidPositionSnapshot): normalize_positions({'ok':True,'data':[bad]})


def test_sizing_never_exceeds_risk_or_notional_caps():
    qty=_position_size(100,95,400)
    assert qty > 0 and qty*5 <= 2 and qty*100 <= 40
    # Wide stop would make $10 minimum exceed risk budget: reject.
    assert _position_size(100,10,400)==0
    for entry,stop in [(0,1),(100,100),(float('nan'),99),(100,float('inf'))]:
        assert _position_size(entry,stop,400)==0
