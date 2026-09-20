"""Comparación reproducible en procesos separados; datos sintéticos, sin red."""
import argparse
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine',choices=['python','cpp','cpp-column'])
    parser.add_argument('--output')
    parser.add_argument('--inputs')
    parser.add_argument('--pressure-grid', choices=['25hpa', 'irregular'], default='25hpa',
                        help='25hpa: niveles múltiplos de 25; irregular: antiguo caso sin reutilización')
    parser.add_argument('--shared-targets', action='store_true',
                        help='Alias compatible de --pressure-grid 25hpa')
    parser.add_argument('--dtype', choices=['float32', 'float64'], default='float64')
    parser.add_argument('--rows',type=int,default=64)
    parser.add_argument('--cols',type=int,default=256)
    args=parser.parse_args()
    if args.shared_targets:
        args.pressure_grid = '25hpa'
    import numpy as np
    if args.engine is None:
        reports=[]
        with tempfile.TemporaryDirectory() as folder:
            rng=np.random.default_rng(772)
            # Representative 25-hPa spacing; atmospheric fields remain synthetic.
            levels=(np.arange(1000, 399, -25, dtype=float)
                    if args.pressure_grid == '25hpa' else np.linspace(1000,300,25))
            p=np.broadcast_to(levels[:,None,None],(25,args.rows,args.cols)).copy()
            p[0]-=rng.uniform(0,70,(args.rows,args.cols))
            t=303-(1000-p)*.065+rng.normal(0,.5,p.shape)
            d=t-rng.uniform(1,18,p.shape);h=(1000-p)*11
            for name,a in zip(('p','t','d','h'),(p,t,d,h)):
                np.save(Path(folder)/(name+'.npy'),a.astype(args.dtype))
            for engine in ('python','cpp'):
                output=str(Path(folder)/(engine+'.npy'))
                result=subprocess.run([sys.executable,__file__,'--engine',engine,
                    '--inputs',folder,'--rows',str(args.rows),'--cols',str(args.cols),'--dtype',args.dtype,'--output',output],
                    capture_output=True,text=True,check=True)
                reports.append(json.loads(result.stdout))
            a=np.load(Path(folder)/'python.npy');b=np.load(Path(folder)/'cpp.npy')
            np.testing.assert_allclose(a,b,rtol=1e-10,atol=1e-8,equal_nan=True)
            print(json.dumps({'pressure_grid':args.pressure_grid, 'isobaric_levels_hpa':levels.tolist(),
                              'surface_pressure':'first level perturbed by uniform 0..70 hPa',
                              'runs':reports,'max_abs_difference':float(np.nanmax(np.abs(a-b))),
                              'speedup':reports[0]['seconds']/reports[1]['seconds']},indent=2))
        return
    os.environ['METEOLABX_DCAPE_ENGINE']=args.engine
    from server.services.convective_diagnostics import downdraft_cape
    if not args.inputs:
        parser.error('--engine requires --inputs: use the parent comparison to generate fixtures')
    p,t,d,h=(np.load(Path(args.inputs)/(name+'.npy')) for name in ('p','t','d','h'))
    started=time.perf_counter();result=downdraft_cape(p,t,d,h);elapsed=time.perf_counter()-started
    rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024)
    if args.output: np.save(args.output,result)
    print(json.dumps({'engine':args.engine,'dtype':args.dtype,'shape':list(p.shape),'seconds':elapsed,'peak_rss_bytes':rss}))


if __name__=='__main__':main()
