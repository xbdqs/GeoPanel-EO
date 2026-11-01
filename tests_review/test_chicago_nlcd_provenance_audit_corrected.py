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
from review_tools.chicago_nlcd_provenance_audit_corrected import audit


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




def test_historical_legacy_labels_warn_not_fail(tmp_path):
    root, _ = fixture(tmp_path, annual_label=False)
    result = audit(root)
    assert result['status'] == 'PASS_WITH_LIMITATIONS'
    assert result['check_counts']['fail'] == 0
    for key in ('landcover_semantics','impervious_percent_semantics'):
        assert next(c['status'] for c in result['checks'] if c['check']==key) == 'WARN'
    assert result['r4_runtime_link_proven'] is False


def test_alternate_extent_same_name_no_false_failure(tmp_path):
    root, _ = fixture(tmp_path, annual_label=False)
    alt = root/'data'/'raw'/'nlcd'
    alt.mkdir(parents=True)
    p = alt/'annual_nlcd_c1_2_landcover_2019_chicago.tif'
    with rasterio.open(p,'w',driver='GTiff',height=3,width=3,count=1,dtype='uint8',
            crs='EPSG:5070',transform=from_origin(500000,2100000,30,30),nodata=250) as ds:
        ds.write(np.full((3,3),21,dtype=np.uint8),1)
    result = audit(root)
    assert result['status'] == 'PASS_WITH_LIMITATIONS'
    assert result['check_counts']['fail']==0
    record=next(x for x in result['nlcd_tiff_files'] if x['product']=='landcover' and x['year']==2019)
    assert record['status']=='VERIFIED_HISTORICAL_SOURCE'
    assert len(record['exact_historical_paths'])==1
    assert len(record['alternate_same_basename_paths'])==1


def test_no_exact_historical_bytes_fails(tmp_path):
    root,_ = fixture(tmp_path, annual_label=False)
    src = root/'data'/'raw'/'nlcd_citywide'/'annual_nlcd_c1_2_landcover_2019_chicago.tif'
    src.write_bytes(b'tampered')
    result=audit(root)
    assert result['status']=='FAIL'
    record=next(x for x in result['nlcd_tiff_files'] if x['product']=='landcover' and x['year']==2019)
    assert record['status']=='HISTORICAL_SOURCE_NOT_FOUND'


def test_modern_version_cannot_use_legacy_label(tmp_path):
    root, run = fixture(tmp_path, annual_label=False)
    report=run/'stage5_report.json'
    data=json.loads(report.read_text());data['software_version']='0.1.1.dev2'
    report.write_text(json.dumps(data))
    result=audit(root)
    assert result['status']=='FAIL'
    assert next(x['status'] for x in result['checks'] if x['check']=='landcover_semantics')=='FAIL'


def test_wrong_year_anchor_is_still_rejected(tmp_path):
    root,run=fixture(tmp_path, annual_label=False)
    config_path=run/'config_nlcd.yml'
    config=yaml.safe_load(config_path.read_text())
    config['sources'][0]['timestamps']=['2019-12-31','2020-12-31']
    config_path.write_text(yaml.safe_dump(config))
    assert audit(root)['status']=='FAIL'


def test_modern_correct_semantics_still_pass(tmp_path):
    root,_=fixture(tmp_path, annual_label=True)
    result=audit(root)
    assert result['status']=='PASS_WITH_LIMITATIONS'
    assert result['check_counts']['fail']==0
