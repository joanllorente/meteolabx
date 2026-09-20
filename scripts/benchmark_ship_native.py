"""Benchmark SHIP en una rejilla completa; --inputs permite usar campos reales NPZ."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server.services.convective_diagnostics import significant_hail_parameter_sharppy

KEYS=('mucape','mu_mixing_ratio_gkg','lapse_rate_700_500_ckm','temperature_500_c',
      'shear_surface_6km_ms','freezing_level_agl_m')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs',type=Path,help='NPZ con las seis entradas de significant_hail_parameter_sharppy')
    parser.add_argument('--rows',type=int,default=384)
    parser.add_argument('--cols',type=int,default=1121)
    args=parser.parse_args()
    if args.inputs:
        with np.load(args.inputs,allow_pickle=False) as data:
            fields=[data[key] for key in KEYS]
    else:
        rng=np.random.default_rng(821)
        fields=[rng.uniform(lo,hi,(args.rows,args.cols)) for lo,hi in
                [(0,6000),(0,25),(3,10),(-35,0),(0,50),(0,6000)]]
    fields=np.broadcast_arrays(*fields)
    reports=[]; outputs=[]
    for engine in ('python','cpp'):
        os.environ['METEOLABX_SHIP_ENGINE']=engine
        significant_hail_parameter_sharppy(*(a[:1,:1] for a in fields))
        start=time.perf_counter()
        outputs.append(significant_hail_parameter_sharppy(*fields))
        reports.append({'engine':engine,'seconds':time.perf_counter()-start})
    np.testing.assert_allclose(outputs[0],outputs[1],rtol=1e-12,atol=1e-12)
    valid=np.logical_and.reduce([np.isfinite(a) for a in fields])
    difference=np.abs(outputs[0]-outputs[1])
    print(json.dumps({'source':str(args.inputs) if args.inputs else 'synthetic',
        'shape':list(fields[0].shape),'valid_cells':int(valid.sum()),'runs':reports,
        'max_abs_difference':float(np.nanmax(difference)) if valid.any() else None,
        'speedup':reports[0]['seconds']/reports[1]['seconds']},indent=2))


if __name__=='__main__':main()
