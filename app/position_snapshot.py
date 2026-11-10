"""Validate exchange position snapshots; never equate an error with flat."""
import math

class InvalidPositionSnapshot(ValueError):
    pass

def normalize_positions(payload):
    if not isinstance(payload, dict) or payload.get('ok') is not True or not isinstance(payload.get('data'), list):
        raise InvalidPositionSnapshot('position response must contain ok=true and a data list')
    out=[]
    for p in payload['data']:
        if not isinstance(p, dict) or not isinstance(p.get('symbol'), str) or not p['symbol'].strip() or 'size' not in p:
            raise InvalidPositionSnapshot('invalid position record')
        try:
            size=float(p['size'])
            entry=float(p.get('entry') or 0)
            mark=float(p.get('mark') or 0)
            upnl=float(p.get('upnl') or 0)
        except (TypeError, ValueError, OverflowError):
            raise InvalidPositionSnapshot('invalid numeric position record') from None
        if not all(math.isfinite(v) for v in (size,entry,mark,upnl)):
            raise InvalidPositionSnapshot('non-finite position data')
        if abs(size) < 1e-8:
            continue
        if entry <= 0 or mark <= 0:
            raise InvalidPositionSnapshot('missing entry or mark for open position')
        out.append({'instrument_name': p['symbol'], 'size':size,
                    'direction':'buy' if size>0 else 'sell',
                    'average_price':entry, 'mark_price':mark, 'floating_profit_loss':upnl})
    return out
