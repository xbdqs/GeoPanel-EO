"""Read-only integrity audit of the shipped *derived* Chicago outputs.

This does NOT rerun the source-data processing and cannot validate upstream raster error.
"""
from __future__ import annotations
import argparse
from pathlib import Path
from hashlib import sha256
import json
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'case_studies'/'chicago'/'results'

def digest(p):
    h=sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda: f.read(1048576),b''):h.update(chunk)
    return h.hexdigest()

def run(out):
    wide=BASE/'combined_panel_wide.csv'; long=BASE/'combined_panel_long.csv'; report=BASE/'stage5_report.json'
    missing=[str(p) for p in (wide,long,report) if not p.is_file()]
    if missing: raise FileNotFoundError('; '.join(missing))
    frame=pd.read_csv(long,dtype={'entity_id':str,'time':str})
    framewide=pd.read_csv(wide,dtype={'entity_id':str,'time':str})
    metadata=json.loads(report.read_text(encoding='utf8'))
    unique=not frame.duplicated(['entity_id','time','variable_id']).any()
    units=frame.entity_id.nunique(); times=sorted(frame.time.unique().tolist())
    groups=frame.groupby(['entity_id','time']).size()
    qc=frame.qc_flag.value_counts(dropna=False).to_dict() if 'qc_flag' in frame else {}
    summary={
        'scope':'Derived-output-only audit; not source-data or resampling validation',
        'source_files':{p.name:digest(p) for p in (wide,long,report)},
        'n_communities':int(units),'n_times':len(times),'times':times,
        'n_variables':int(frame.variable_id.nunique()),'long_rows':int(len(frame)),
        'wide_rows':int(len(framewide)), 'unique_primary_keys':bool(unique),
        'balanced_variable_count':bool(groups.nunique()==1),
        'nan_values':int(frame.value.isna().sum()),
        'qc_flag_counts':{str(k):int(v) for k,v in qc.items()},
        'reported_software_version':metadata.get('software_version'),
        'warnings':[]}
    if len(frame)!=5544 or len(framewide)!=154 or units!=77:summary['warnings'].append('Published case row counts mismatch')
    if not unique:summary['warnings'].append('Duplicate scientific primary keys')
    if not summary['balanced_variable_count']:summary['warnings'].append('Variable counts not balanced')
    # Closure calculations require 16 class area/fraction columns, numerical only.
    if {'variable_id','value'}.issubset(frame.columns):
        landcover=frame[frame.variable_id.str.match(r'landcover_class_\d+_fraction$')]
        if not landcover.empty:
            sums=landcover.groupby(['entity_id','time']).value.sum()
            summary['landcover_fraction_closure_max_abs']=float((sums-1).abs().max())
            if summary['landcover_fraction_closure_max_abs']>1e-9:
                summary['warnings'].append('Land cover fraction closure exceeds 1e-9')
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    return 0 if not summary['warnings'] else 1

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,default=ROOT/'review_results'/'existing_case_audit.json')
    a=p.parse_args();raise SystemExit(run(a.out))
