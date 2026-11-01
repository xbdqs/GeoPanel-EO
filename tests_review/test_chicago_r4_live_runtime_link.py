"""Tests for the new real-input runtime harness. Does not need live Chicago data."""
import importlib.util
from pathlib import Path
import json
import sys
import hashlib

import pandas as pd
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'review_tools' / 'chicago_r4_live_runtime_link.py'
spec=importlib.util.spec_from_file_location('chicago_r4_live_runtime_link',SCRIPT)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_sha256_and_relative_key(tmp_path):
    p=tmp_path/'raw.nc';p.write_bytes(b'good')
    assert mod.digest(p)==hashlib.sha256(b'good').hexdigest()
    assert mod.relative_to_manifest(p,tmp_path)=='raw.nc'


def test_retrospective_manifest_has_explicit_provenance_boundary(tmp_path):
    raw={('tmin',2019):tmp_path/'raw.nc'}
    prep={'tmin':tmp_path/'prep.nc'}
    stage=tmp_path/'stage3_report.json';stage.write_text('{"status":"PASS"}')
    m=mod.retrospective_manifest({'raw':raw,'prepared':prep,'stage3_report':stage},tmp_path)
    assert m['schema_version']=='geopanel-preprocess-1'
    assert not m['historical_stage3_manifest_cogenerated']
    assert 'RETROSPECTIVE' in m['provenance_class']
    assert m['operations']['source'].startswith('historical')


def sample_rows():
    return [{'entity_id':f'{i:02d}','time':f'{year}-{month:02d}-01','variable_id':v,'value':float(i)}
            for i in range(1,78) for year in (2019,2020) for month in range(1,13) for v in mod.VARS]


def test_complete_key_and_numeric_reproduction(tmp_path):
    a=tmp_path/'a.csv'; b=tmp_path/'b.csv'
    pd.DataFrame(sample_rows()).to_csv(a,index=False)
    pd.DataFrame(reversed(sample_rows())).to_csv(b,index=False)
    r=mod.compare_archived_monthly(a,b)
    assert r['n_rows']==5544
    assert r['max_abs_difference']==0
    assert r['numeric_reproduction_pass']


def test_mismatch_detected(tmp_path):
    a=tmp_path/'a.csv'; b=tmp_path/'b.csv'
    rows=sample_rows()
    pd.DataFrame(rows).to_csv(a,index=False)
    rows[0]['value']+=0.1
    pd.DataFrame(rows).to_csv(b,index=False)
    assert not mod.compare_archived_monthly(a,b)['numeric_reproduction_pass']


def test_key_loss_rejected(tmp_path):
    a=tmp_path/'a.csv'; b=tmp_path/'b.csv'
    rows=sample_rows()
    pd.DataFrame(rows).to_csv(a,index=False)
    pd.DataFrame(rows[:-1]).to_csv(b,index=False)
    with pytest.raises(ValueError,match='5544'):
        mod.compare_archived_monthly(a,b)


def test_duplicate_key_rejected(tmp_path):
    a=tmp_path/'a.csv'; b=tmp_path/'b.csv'
    rows=sample_rows()
    pd.DataFrame(rows).to_csv(a,index=False)
    rows[1]=dict(rows[0])
    pd.DataFrame(rows).to_csv(b,index=False)
    with pytest.raises(ValueError,match='duplicate'):
        mod.compare_archived_monthly(a,b)
