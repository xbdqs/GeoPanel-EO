"""Deterministic tests of independent historical provenance verifier."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd
import numpy as np
import rasterio
from rasterio.transform import from_origin
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from review_tools.chicago_nlcd_provenance_audit import audit


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fixture(tmp_path, *, annual_label=True, tamper=False, missing_file=False):
    root=tmp_path/'review_data'/'chicago'
    run=root/'stage5_results'/'run_20260906T225615102107Z'
    stage3=root/'stage3_results'/'run_20260906T211325137105Z'
    run.mkdir(parents=True)
    (stage3/'prepared').mkdir(parents=True)
    (stage3/'stage3_report.json').write_text(json.dumps({'status':'PASS'}))
    sources=[]
    transfers=[]
    rawdir=root/'data'/'raw'/'nlcd_citywide'
    rawdir.mkdir(parents=True)
    for kind in ('landcover','impervious'):
        paths=[]
        for year in (2019,2020):
            name=f'annual_nlcd_c1_2_{kind}_{year}_chicago.tif'
            path=rawdir/name
            # Real GeoTIFF fixture with native EPSG:5070 and 30 m cells.
            with rasterio.open(path,'w',driver='GTiff',height=2,width=2,count=1,
                    dtype='uint8',crs='EPSG:5070',transform=from_origin(500000,2100000,30,30),nodata=250) as ds:
                grid=np.full((2,2),11+(year-2019)*10 if kind=='landcover' else 30+(year-2019)*10,dtype=np.uint8)
                ds.write(grid,1)
            hashval=_hash(path.read_bytes())
            if missing_file and kind=='landcover' and year==2019:path.unlink()
            paths.append(str(path))
            transfers.append({'transfer':{'product':kind,'year':year,'sha256':hashval}})
        sources.append({'source_id':f'annual_nlcd_{kind}','type':'geotiff','paths':paths,
                        'timestamps':['2019-01-01','2020-01-01']})
    v={"temporal_support":"annual_product" if annual_label else "monthly_snapshot", "temporal_reducer":"snapshot",
       "temporal_window":{"frequency":"annual","months":[1]}}
    config={'sources':sources,'variables':[{'variable_id':'landcover',**v},{'variable_id':'impervious_percent',**v}]}
    (run/'config_nlcd.yml').write_text(yaml.safe_dump(config))
    columns=['entity_id','time','variable_id','period_support','value']
    rows=[(str(i).zfill(2),str(year),name,'annual_product',float(i))
          for i in range(1,78) for year in (2019,2020) for name in ['impervious_percent']+
          [f'landcover_class_{j}_{suffix}' for j in range(16) for suffix in ['area','fraction']]]
    # Exactly 33: 32 categories (16 x 2), one impervious.
    assert len(rows)==77*2*33
    nlcd=run/'panel_nlcd';nlcd.mkdir()
    pd.DataFrame(rows,columns=columns).to_csv(nlcd/'panel_long.csv',index=False)
    pd.DataFrame(rows,columns=columns).to_csv(run/'combined_panel_long.csv',index=False)
    combined_hash=_hash((run/'combined_panel_long.csv').read_bytes())
    (run/'stage5_report.json').write_text(json.dumps({'status':'PASS','software_version':'0.1.0a2',
               'source_stage3':stage3.name,'nlcd_files':transfers,'combined_long_sha256':combined_hash}))
    if tamper: (rawdir/'annual_nlcd_c1_2_landcover_2019_chicago.tif').write_bytes(b'changed')
    return root,run


def test_valid_historical_has_no_r4_link(tmp_path):
    root,_=fixture(tmp_path)
    result=audit(root)
    assert result['status']=='PASS_WITH_LIMITATIONS'
    assert result['r4_runtime_link_proven'] is False
    assert result['check_counts']['fail']==0
    assert len([c for c in result['checks'] if c['check'].startswith('nlcd_tiff_') and c['status']=='PASS'])==4


def test_tampered_tiff_is_rejected(tmp_path):
    root,_=fixture(tmp_path,tamper=True)
    result=audit(root)
    assert result['status']=='FAIL'
    assert next(c for c in result['checks'] if c['check']=='nlcd_tiff_landcover_2019')['status']=='FAIL'


def test_missing_tiff_is_not_claimed_verified(tmp_path):
    root,_=fixture(tmp_path,missing_file=True)
    result=audit(root)
    assert result['status']=='PASS_WITH_LIMITATIONS'
    assert next(c for c in result['checks'] if c['check']=='nlcd_tiff_landcover_2019')['status']=='WARN'


def test_wrong_annual_semantics_rejected(tmp_path):
    root,_=fixture(tmp_path, annual_label=False)
    result=audit(root)
    assert result['status']=='FAIL'
    assert next(c for c in result['checks'] if c['check']=='landcover_semantics')['status']=='FAIL'


def test_year_anchor_mismatch_rejected(tmp_path):
    root,run=fixture(tmp_path)
    cfg=run/'config_nlcd.yml'
    data=yaml.safe_load(cfg.read_text())
    data['sources'][0]['timestamps']=['2019-12-31','2020-12-31']
    cfg.write_text(yaml.safe_dump(data))
    assert audit(root)['status']=='FAIL'


def test_combined_panel_tampering_rejected(tmp_path):
    root,run=fixture(tmp_path)
    with (run/'combined_panel_long.csv').open('ab') as stream: stream.write(b'\n')
    result=audit(root)
    assert result['status']=='FAIL'
    assert next(c for c in result['checks'] if c['check']=='stage5_combined_panel_hash')['status']=='FAIL'
