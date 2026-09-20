from types import SimpleNamespace
from server.services import arome_wcs as wcs


def test_http_client_reserves_one_slot_per_attempt_and_none_for_cache(monkeypatch):
    events=[]
    responses=iter([503,200])
    monkeypatch.setattr(wcs,'_wait_for_api_request_slot',lambda:events.append('slot'))
    monkeypatch.setattr(wcs,'_credential_headers',lambda token:{})
    monkeypatch.setattr(wcs.time,'sleep',lambda seconds:None)
    def get(*args,**kwargs):
        events.append('request')
        return SimpleNamespace(status_code=next(responses),headers={},content=b'GRIB',text='')
    monkeypatch.setattr(wcs.requests,'get',get)
    cache={}
    monkeypatch.setattr(wcs,'_cache_get',lambda namespace,key:cache.get((namespace,key)))
    monkeypatch.setattr(wcs,'_cache_put',lambda namespace,key,value,**kwargs:cache.__setitem__((namespace,key),value))
    url='https://example.invalid/GetCoverage'
    assert wcs._api_get(url,(), 'test')==(b'GRIB','')
    assert events==['slot','request','slot','request']
    assert wcs._api_get(url,(), 'test')==(b'GRIB','')
    assert events==['slot','request','slot','request']
