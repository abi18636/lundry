import importlib.util
from pathlib import Path

import httpx
import pytest


@pytest.fixture
def watchdog(tmp_path,monkeypatch):
    path=Path(__file__).parents[1]/'scripts'/'hourly_watchdog.py'
    spec=importlib.util.spec_from_file_location('test_hourly_watchdog_module',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.OUT=tmp_path/'watchdog.log';module.TOKEN='';module.RECOVERY=False
    notified=[];monkeypatch.setattr(module,'notify',lambda text:notified.append(text))
    real_client=httpx.Client
    def setup(health,status):
        calls=[]
        def handler(request):
            calls.append((request.method,request.url.path))
            if request.url.path=='/health':return httpx.Response(200,json=health)
            if request.url.path=='/api/status':return httpx.Response(200,json=status)
            if request.url.path in ('/api/start','/api/tick'):return httpx.Response(200,json={'ok':True})
            return httpx.Response(404,json={})
        monkeypatch.setattr(module.httpx,'Client',lambda **kwargs:real_client(transport=httpx.MockTransport(handler),**kwargs))
        return calls
    return module,notified,setup


def health():
    return {'ok':True,'running':True,'loop_count':1,'loop_age_seconds':1,'last_error':None,'build':'mock'}


def test_halted_is_blocked_not_healthy_or_restart(watchdog,capsys):
    w,notified,setup=watchdog
    dg={'primary_blocker':'venue_halted','trading_ready':False,'trading_state':'blocked',
        'market_open_count':0,'market_halted_count':25}
    calls=setup(health(),{'diagnostics':dg,'actions':[{'status':'market_halted'}]})
    assert w.main()==0 and not notified and all(m=='GET' for m,p in calls)
    assert 'BLOCKED' in capsys.readouterr().out


def test_halted_symbol_does_not_hide_another_real_error(watchdog):
    w,notified,setup=watchdog
    setup(health(),{'diagnostics':{'primary_blocker':'error'},'actions':[{'status':'market_halted'},{'status':'error','error':'10031'}]})
    assert w.main()==2 and notified


def test_all_book_api_failures_require_attention(watchdog):
    w,notified,setup=watchdog
    setup(health(),{'diagnostics':{'primary_blocker':'market_api_error'},'actions':[{'status':'market_api_error'}]})
    assert w.main()==2 and notified


def test_no_unauthorized_recovery_when_token_missing(watchdog):
    w,notified,setup=watchdog;h=health();h['running']=False;w.RECOVERY=True
    calls=setup(h,{'diagnostics':{'primary_blocker':'engine_not_running'},'actions':[]})
    assert w.main()==2 and all(m=='GET' for m,p in calls)


def test_authorized_recovery_can_request_start_not_order(watchdog):
    w,notified,setup=watchdog;h=health();h['running']=False;w.TOKEN='MOCK_ADMIN_TOKEN';w.RECOVERY=True
    calls=setup(h,{'diagnostics':{'primary_blocker':'engine_not_running'},'actions':[]})
    w.main()
    assert ('POST','/api/start') in calls
    assert not any('buy' in path or 'sell' in path for method,path in calls)
