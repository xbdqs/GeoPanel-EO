"""Create an honest input/output checksum log for preprocessing.

The parameters are USER DECLARATIONS, not independently recovered evidence.
Only use on the actual files/run and retain the preprocessing script + logs.
This does NOT independently validate scientifically correct resampling.
"""
from __future__ import annotations
import argparse
from pathlib import Path
from hashlib import sha256
from datetime import datetime,timezone
import json

def checksum(p):
    sha=sha256()
    with p.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):sha.update(block)
    return {'path':str(p.resolve()),'size_bytes':p.stat().st_size,'sha256':sha.hexdigest()}

if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--raw',nargs='+',type=Path,required=True)
    ap.add_argument('--prepared',nargs='+',type=Path,required=True)
    ap.add_argument('--script',type=Path,required=True)
    ap.add_argument('--resampling',required=True,help='Actual kernel used, e.g. bilinear')
    ap.add_argument('--target-crs',required=True)
    ap.add_argument('--grid',required=True,help='Actual transform and shape, as an unambiguous string')
    ap.add_argument('--nodata',required=True,help='Actual source and destination nodata handling')
    ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args()
    all_files=[*args.raw,*args.prepared,args.script]
    for p in all_files:
        if not p.is_file():ap.error(f'Required real file missing: {p}')
    result={'schema_version':'review-manifest-draft-1','created_at_utc':datetime.now(timezone.utc).isoformat(),
      'status':'self-reported-run-file-hashes-not-an-independent-scientific-validation',
      'inputs':[checksum(p) for p in args.raw],
      'outputs':[checksum(p) for p in args.prepared],
      'preprocessing_script':checksum(args.script),
      'declared_parameters':{'resampling':args.resampling,'target_crs':args.target_crs,
                             'actual_grid':args.grid,'nodata_handling':args.nodata}}
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf8')
    print(f'Preprocessing manifest written: {args.out}')
