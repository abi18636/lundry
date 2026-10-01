import time

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.config import Settings
from app.engine import BotState, EngineBusyError, TradingEngine, utcnow


@pytest.fixture
def api(tmp_path,monkeypatch):
    s=Settings(_env_file=None,state_path=str(tmp_path/'state.json'),telegram_enabled=False)
    e=TradingEngine(s)
    e.state.running=True
    e.state.last_loop_at=utcnow()
    e.state.diagnostics={'trading_ready':False,'trading_state':'blocked','primary_blocker':'venue_halted',
                         'market_open_count':0,'market_halted_count':25,'active_signal_assets':15}
    monkeypatch.setattr(main,'get_settings',lambda:s)
    monkeypatch.setattr(main,'get_engine',lambda:e)
    monkeypatch.setattr(main,'get_reporter',lambda:None)
    return TestClient(main.app),e,s


def test_liveness_is_not_trading_readiness(api):
    c,e,s=api;r=c.get('/health');d=r.json()
    assert r.status_code==200 and d['liveness_ok']
    assert not d['trading_ready'] and d['primary_blocker']=='venue_halted'


def test_health_does_not_wait_for_execution_mutex(api):
    c,e,s=api;e._cycle_lock.acquire()
    try:
        before=time.monotonic();r=c.get('/health')
        assert r.status_code==200 and time.monotonic()-before<.5
    finally:e._cycle_lock.release()


def test_public_mutations_forbidden_without_control_token(api):
    c,e,s=api
    for endpoint in ['start','stop','tick','telegram/test','telegram/report']:
        assert c.post('/api/'+endpoint).status_code==403
    assert e.state.running


def test_control_and_private_status_enforce_token(api):
    c,e,s=api;s.dashboard_token='MOCK_ADMIN_TOKEN'
    assert c.get('/api/status').status_code==401
    assert c.post('/api/stop',headers={'X-Token':'wrong'}).status_code==401
    assert c.post('/api/stop',headers={'X-Token':'MOCK_ADMIN_TOKEN'}).status_code==200
    assert not e.state.running
    assert c.get('/api/status',headers={'X-Token':'MOCK_ADMIN_TOKEN'}).status_code==200


def test_overlapping_tick_returns_409(api):
    c,e,s=api;s.dashboard_token='MOCK_ADMIN_TOKEN';e._cycle_lock.acquire()
    try:assert c.post('/api/tick',headers={'X-Token':'MOCK_ADMIN_TOKEN'}).status_code==409
    finally:e._cycle_lock.release()


def test_stale_engine_cannot_report_ready(api):
    c,e,s=api;e.state.diagnostics['trading_ready']=True
    e.state.last_loop_at='2026-01-01T00:00:00+00:00'
    assert not c.get('/health').json()['trading_ready']


def test_diagnostics_expose_operational_evidence(api):
    c,e,s=api;r=c.get('/api/diagnostics')
    assert r.status_code==200
    assert set(['diagnostics','market_data','risk','actions','pending_order','ops_reviews']).issubset(r.json())


def test_dashboard_has_full_matrix_and_no_external_assets(api):
    c,e,s=api;html=c.get('/').text
    assert 'ماتریس پنج استراتژی' in html and 'filled_amount' in html
    assert '<script src=' not in html and '<link ' not in html
    assert 'توکن کنترل' in html and 'رسیدهای واقعی سفارش' in html
