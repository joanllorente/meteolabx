"""RSS/tiempo en procesos separados; fixture sintético o directorio --inputs con NPY."""
import argparse
from dataclasses import fields
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine',choices=['python','cpp'])
    parser.add_argument('--stage',choices=['parcel','all'],default='parcel')
    parser.add_argument('--inputs',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--rows',type=int,default=128)
    parser.add_argument('--cols',type=int,default=1121)
    args=parser.parse_args()
    if not args.engine:
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            if not args.inputs:
                rng=np.random.default_rng(192)
                p=np.broadcast_to(np.arange(1000,299,-25.)[:,None,None],(29,args.rows,args.cols)).copy()
                p[0]-=rng.uniform(0,60,p.shape[1:])
                h=8000*np.log(1000/p);t=303-.0068*h+rng.normal(0,1.5,p.shape)
                d=t-rng.uniform(0,22,p.shape)
                for name,a in zip(('p','t','d','h'),(p,t,d,h)):
                    np.save(folder/(name+'.npy'),a.astype('float32'))
                inputs=folder
            else:inputs=args.inputs
            reports=[];outputs=[]
            for engine in ('python','cpp'):
                out=folder/(engine+'.npz')
                result=subprocess.run([sys.executable,__file__,'--engine',engine,'--stage',args.stage,
                    '--inputs',str(inputs),'--output',str(out)],capture_output=True,text=True,check=True)
                reports.append(json.loads(result.stdout));outputs.append(out)
            differences={}
            with np.load(outputs[0]) as expected,np.load(outputs[1]) as actual:
                for key in expected:
                    np.testing.assert_allclose(actual[key],expected[key],rtol=1e-8,atol=1e-5,err_msg=key)
                    delta=np.abs(actual[key]-expected[key])
                    differences[key]=float(np.nanmax(delta)) if np.isfinite(delta).any() else None
            print(json.dumps({'source':str(args.inputs) if args.inputs else 'synthetic',
                'stage':args.stage,'runs':reports,'max_abs_differences':differences},indent=2))
        return
    if not args.inputs:parser.error('--engine requires --inputs')
    os.environ['METEOLABX_PARCEL_ENGINE']=args.engine
    # Isolate the parcel change; use identical engines for other diagnostics.
    os.environ['METEOLABX_SATURATION_ENGINE']='python'
    os.environ['METEOLABX_SHIP_ENGINE']='cpp'
    from server.services import convective_diagnostics as d
    if args.stage=='all':from server.services.arome_forecast import _convective_outputs
    p,t,td,h=(np.load(args.inputs/(name+'.npy')) for name in ('p','t','d','h'))
    rss=lambda:resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024)
    loaded=rss();start=time.perf_counter()
    if args.stage=='parcel':
        result=d.parcel_diagnostics(p,t,td,h,*(a[0].astype(float) for a in (p,t,td)))
        values={f.name:getattr(result,f.name) for f in fields(result)}
    else:
        # Constant winds isolate parcel memory; this is not a captured AROME field.
        u=np.full_like(p,10);v=np.full_like(p,5);w=np.zeros_like(p)
        surface=np.zeros(p.shape[1:])
        values=_convective_outputs(p,t,td,u,v,surface,surface,surface,
            list(p[:,0,0]),include_dcape=False,vertical_velocity=w)
    elapsed=time.perf_counter()-start;peak=rss()
    if args.output:np.savez(args.output,**values)
    print(json.dumps({'engine':args.engine,'shape':list(p.shape),'loaded_peak_rss_bytes':loaded,
        'peak_rss_bytes':peak,'seconds':elapsed}))


if __name__=='__main__':main()
