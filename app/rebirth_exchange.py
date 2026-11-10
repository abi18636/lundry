"""Explicit, disabled-by-default exchange gateway for Rebirth integration.

An HTTP acknowledgement is NOT a fill. No automatic retries. Order status,
positions, persistence and exit semantics must be independently reconciled before
this gateway may be connected to the automatic trading loop.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Callable
import httpx
from app.position_snapshot import normalize_positions


class OrderOutcomeUnknown(RuntimeError):
    """Order might have reached the venue; reconcile before any retry."""


@dataclass(frozen=True)
class OrderAcknowledgement:
    order_id: str
    status: str = 'accepted_not_fill_confirmed'


class RebirthExchangeGateway:
    def __init__(self, base_url: str, api_key: str, api_secret: str, *,
                 enable_send: bool = False, transport: Callable = httpx.post):
        if not base_url.startswith('https://'):
            raise ValueError('HTTPS required')
        if not api_key or not api_secret:
            raise ValueError('credentials required')
        self.base_url=base_url.rstrip('/')
        self._headers={'X-API-Key':api_key, 'X-API-Secret':api_secret}
        self.enable_send=enable_send
        self._post=transport

    def positions(self, get: Callable = httpx.get):
        r=get(self.base_url+'/api/positions',headers=self._headers,timeout=10)
        r.raise_for_status()
        return normalize_positions(r.json())

    def submit_market(self, symbol: str, side: str, quantity, *, reduce_only: bool):
        if not self.enable_send:
            raise PermissionError('order sending disabled')
        if symbol not in {'BTCUSD','ETHUSD','SOLUSD'} or side not in {'buy','sell'}:
            raise ValueError('instrument or side outside Rebirth universe')
        if not isinstance(reduce_only, bool):
            raise ValueError('reduce_only must be explicit')
        try:
            qty=Decimal(str(quantity))
        except (InvalidOperation,ValueError):
            raise ValueError('invalid quantity') from None
        if not qty.is_finite() or qty <= 0 or qty > Decimal('1000000000'):
            raise ValueError('invalid quantity')
        body={'symbol':symbol,'side':side,'qty':float(qty),'type':'market','reduce_only':reduce_only}
        try:
            # One request only. Never assume the venue implements idempotency.
            r=self._post(self.base_url+'/api/order',headers=self._headers,json=body,timeout=10)
            r.raise_for_status()
            data=r.json()
        except Exception as e:
            raise OrderOutcomeUnknown('order outcome unknown; reconcile venue before retry') from e
        if not isinstance(data,dict) or data.get('ok') is not True or data.get('id') is None:
            raise OrderOutcomeUnknown('unconfirmed order response; reconcile venue before retry')
        return OrderAcknowledgement(str(data['id']))
