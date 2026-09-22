import fcntl
import json
from datetime import datetime,timezone
from pathlib import Path
import pytest
from server.services import arome_packages as p, arome_forecast as f

RUN=datetime(2026,9,21,tzinfo=timezone.utc)


def test_lock_timeout_does_not_start_duplicate_download(tmp_path,monkeypatch):
    monkeypatch.setattr(p,'_cache_dir',lambda:tmp_path)
    monkeypatch.setattr(p,'_download_package',lambda *args:pytest.fail('duplicate download'))
    path=p._package_path('IP1',RUN,'00H06H')
    with path.with_suffix('.lock').open('a+') as owner:
        fcntl.flock(owner,fcntl.LOCK_EX)
        assert p._active_download_count()==0
        with pytest.raises(p.AromePackageError,match='descarga en curso'):
            p.ensure_package('IP1',RUN,RUN,lock_timeout_s=0)
    assert p._active_download_count()==0


def test_publication_wait_is_bounded_and_retries(monkeypatch):
    now=[0.];calls=[]
    monkeypatch.setenv('METEOLABX_AROME_PACKAGE_WAIT_S','30')
    monkeypatch.setattr(f.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(f.time,'sleep',lambda seconds:now.__setitem__(0,now[0]+seconds))
    def request(*args,**kwargs):
        calls.append(kwargs.get('lock_timeout_s'))
        if len(calls)<2:raise p.AromePackageNotReady('not yet',15)
        return Path('ready')
    monkeypatch.setattr(f,'ensure_package',request)
    assert f._ensure_profile_package('IP1',RUN,RUN)==Path('ready')
    assert calls==[None,None]
    def unavailable(*args,**kwargs):raise p.AromePackageNotReady('not yet',100)
    monkeypatch.setattr(f,'ensure_package',unavailable)
    with pytest.raises(p.AromePackageNotReady):f._ensure_profile_package('IP1',RUN,RUN)
    assert now[0]==45.


def test_permanent_failure_does_not_wait(monkeypatch):
    monkeypatch.setattr(f.time,'sleep',lambda seconds:pytest.fail('must not retry'))
    def forbidden(*args,**kwargs):raise p.AromePackageError('HTTP 403')
    monkeypatch.setattr(f,'ensure_package',forbidden)
    with pytest.raises(p.AromePackageError):f._ensure_profile_package('IP1',RUN,RUN)


def test_download_records_http_phases_and_cleans_partial(tmp_path,monkeypatch):
    monkeypatch.setattr(p,'_cache_dir',lambda:tmp_path)
    monkeypatch.setattr(p,'authorization_headers',lambda:{})
    now=[0.]
    monkeypatch.setattr(p.time,'monotonic',lambda:now[0])
    class Response:
        status_code=200
        headers={}
        def __enter__(self):now[0]=2.;return self
        def __exit__(self,*args):pass
        def iter_content(self,chunk_size):
            now[0]=3.;yield b'GRIB'
            now[0]=5.;yield b'end'
    monkeypatch.setattr(p.requests,'get',lambda *args,**kwargs:Response())
    result=p.ensure_package('IP1',RUN,RUN)
    assert result.read_bytes()==b'GRIBend'
    record=json.loads(p._downloads_log(RUN).read_text())
    assert record['headers_seconds']==2 and record['first_chunk_seconds']==3
    assert record['body_seconds']==3 and record['transfer_seconds']==2
    assert record['active_downloads_start']==record['active_downloads_end']==1
    assert record['bytes']==7 and record['seconds']==5
    assert p.download_stats(RUN)['max_observed_downloads']==1
    assert not list(tmp_path.glob('*.part'))


def test_failed_transfer_removes_partial(tmp_path,monkeypatch):
    monkeypatch.setattr(p,'_cache_dir',lambda:tmp_path)
    monkeypatch.setattr(p,'authorization_headers',lambda:{})
    class Response:
        status_code=200
        headers={}
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def iter_content(self,chunk_size):
            yield b'partial'
            raise p.requests.ConnectionError('broken')
    monkeypatch.setattr(p.requests,'get',lambda *args,**kwargs:Response())
    with pytest.raises(p.AromePackageError):p.ensure_package('IP1',RUN,RUN)
    assert not list(tmp_path.glob('*.part'))
    assert not p.package_ready('IP1',RUN,RUN)


@pytest.mark.parametrize('scheduled', [False, True])
@pytest.mark.parametrize('growing,completes', [(True,True), (False,False), (True,False)])
def test_wait_follows_progress_beyond_publication_deadline(tmp_path,monkeypatch,growing,completes,scheduled):
    monkeypatch.setattr(p,'_cache_dir',lambda:tmp_path)
    if scheduled:
        monkeypatch.setenv('METEOLABX_AROME_SCHEDULED_PACKAGES','1')
    monkeypatch.setenv('METEOLABX_AROME_PACKAGE_WAIT_S','180')
    monkeypatch.setenv('METEOLABX_AROME_PACKAGE_STALL_S','60')
    path=p._package_path('IP1',RUN,'00H06H')
    partial=path.with_suffix('.123.part')
    partial.write_bytes(b'a')
    now=[0.];last_write=[0.]
    monkeypatch.setattr(p.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(p,'_download_package',lambda *args:pytest.fail('duplicate transfer'))
    with path.with_suffix('.lock').open('a+') as owner:
        fcntl.flock(owner,fcntl.LOCK_EX)
        def sleep(seconds):
            now[0]+=seconds
            if growing and now[0]<=210 and now[0]-last_write[0]>=30:
                with partial.open('ab') as output:output.write(b'a')
                last_write[0]=now[0]
            if completes and now[0]>=240:
                partial.replace(path)
                fcntl.flock(owner,fcntl.LOCK_UN)
        monkeypatch.setattr(p.time,'sleep',sleep)
        if completes:
            assert f._ensure_profile_package('IP1',RUN,RUN)==path
            assert now[0]==240
        else:
            with pytest.raises(p.AromePackageError,match='sin progreso'):
                f._ensure_profile_package('IP1',RUN,RUN)
            assert now[0]==(270 if growing else 60)
            assert partial.exists()  # Abandoning wait must not cancel the owner.


def test_download_counter_never_touches_locks(tmp_path,monkeypatch):
    monkeypatch.setattr(p,'_cache_dir',lambda:tmp_path)
    (tmp_path/'IP1-test.lock').touch()
    (tmp_path/'IP1-test.123.part').touch()
    (tmp_path/'IP3-test.456.part').touch()
    monkeypatch.setattr(p.fcntl,'flock',lambda *args:pytest.fail('counter must not lock'))
    assert p._active_download_count()==2
