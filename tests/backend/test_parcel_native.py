from dataclasses import fields
import numpy as np
import pytest
from server.services import convective_diagnostics as d

pytest.importorskip('server.services._dcape_native')


def profiles():
    rng=np.random.default_rng(192)
    p=np.broadcast_to(np.linspace(1000,100,29)[:,None,None],(29,8,9)).copy()
    p[0]-=rng.uniform(0,60,(8,9))
    h=8000*np.log(1000/p)
    t=303-.0068*h+rng.normal(0,1.5,p.shape)
    td=t-rng.uniform(0,22,p.shape)
    return p,t,td,h


@pytest.mark.parametrize('case',['normal','float32','strided','missing','duplicate','elevated','stable','supersaturated'])
def test_parcel_matches_python(monkeypatch,case):
    arrays=profiles()
    if case=='float32': arrays=tuple(a.astype('float32') for a in arrays)
    if case=='strided': arrays=tuple(a[:,::-1,::2] for a in arrays)
    if case=='missing':
        arrays[1][6,1,2]=np.nan
        arrays[2][:,2,3]=np.nan
        arrays[3][4,3,4]=np.nan
    if case=='duplicate':
        for a in arrays:a[1]=a[0]
    if case=='stable':arrays[1][:]=285.;arrays[2][:]=270.
    if case=='supersaturated':arrays[2][:]=arrays[1]+2
    origin=4 if case=='elevated' else 0
    origins=tuple(a[origin].astype(float) for a in arrays[:3])
    monkeypatch.setenv('METEOLABX_PARCEL_ENGINE','python')
    expected=d.parcel_diagnostics(*arrays,*origins)
    monkeypatch.setenv('METEOLABX_PARCEL_ENGINE','cpp')
    actual=d.parcel_diagnostics(*arrays,*origins)
    for field in fields(expected):
        np.testing.assert_allclose(getattr(actual,field.name),getattr(expected,field.name),
                                   rtol=1e-9,atol=1e-6,err_msg=field.name)


def test_parcel_partition_independent(monkeypatch):
    arrays=profiles();monkeypatch.setenv('METEOLABX_PARCEL_ENGINE','cpp')
    whole=d.parcel_diagnostics(*arrays,*(a[0] for a in arrays[:3]))
    parts=[]
    for start in range(0,8,2):
        band=tuple(a[:,start:start+2] for a in arrays)
        parts.append(d.parcel_diagnostics(*band,*(a[0] for a in band[:3])))
    for field in fields(whole):
        np.testing.assert_array_equal(getattr(whole,field.name),
            np.concatenate([getattr(part,field.name) for part in parts]))


def test_parcel_concurrent_calls_are_independent(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    arrays=tuple(a.astype('float32') for a in profiles())
    monkeypatch.setenv('METEOLABX_PARCEL_ENGINE','cpp')
    def calculate(index):
        return d.parcel_diagnostics(*arrays,*(a[index].astype(float) for a in arrays[:3]))
    expected=[calculate(i) for i in (0,2,4,6)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        actual=list(pool.map(calculate,(0,2,4,6)))
    for a,b in zip(actual,expected):
        for field in fields(a):
            np.testing.assert_array_equal(getattr(a,field.name),getattr(b,field.name))


def test_parcel_native_rejects_malformed_profiles(monkeypatch):
    arrays=profiles();monkeypatch.setenv('METEOLABX_PARCEL_ENGINE','cpp')
    origins=tuple(a[0] for a in arrays[:3])
    with pytest.raises(ValueError):d.parcel_diagnostics(arrays[0],arrays[1][:,:1],*arrays[2:],*origins)
    with pytest.raises(TypeError):d.parcel_diagnostics(*(a.astype('int32') for a in arrays),*origins)
