"""R3 output-level validation; skipped only where binary extraction is unavailable."""
import json
import pandas as pd
import pytest
import yaml

pytest.importorskip('exactextract')
pytest.importorskip('pyarrow')
from geopanel_eo.pipeline import run
from geopanel_eo.synthetic import create_case


def test_annual_temperature_support_sidecar_and_manifest(tmp_path):
    cfg_path = create_case(tmp_path, 'T06')
    cfg = yaml.safe_load(cfg_path.read_text(encoding='utf8'))
    cfg['spatial'] = {'support_overlap_diagnostics': True}
    cfg_path.write_text(yaml.safe_dump(cfg), encoding='utf8')
    manifest = run(cfg_path)
    out = tmp_path/'outputs'
    diag = pd.read_csv(out/'spatial_support_overlap.csv')
    panel = pd.read_csv(out/'panel_long.csv')
    assert len(diag)==1
    assert diag.loc[0,'support_status']=='CONSISTENT_SUPPORT'
    assert diag.loc[0,'observed_support_months']==12
    assert diag.loc[0,'common_valid_fraction']==pytest.approx(1.0)
    assert panel.loc[0,'value']==pytest.approx(5.5)
    assert manifest['support_overlap_diagnostics_enabled'] is True
    assert manifest['support_overlap_status_counts']['CONSISTENT_SUPPORT']==1
    assert 'spatial_support_overlap.csv' in manifest['output_sha256']


def test_annual_product_output_does_not_claim_year_end_snapshot(tmp_path):
    cfg_path=create_case(tmp_path,'T01')
    cfg=yaml.safe_load(cfg_path.read_text(encoding='utf8'))
    cfg['variables'][0].update(temporal_support='annual_product', temporal_reducer='snapshot',
                               temporal_window={'frequency':'annual','months':[1]})
    cfg['spatial']={'support_overlap_diagnostics':True}
    cfg_path.write_text(yaml.safe_dump(cfg),encoding='utf8')
    run(cfg_path)
    out=tmp_path/'outputs'
    panel=pd.read_csv(out/'panel_long.csv')
    book=pd.read_csv(out/'variable_codebook.csv')
    diag=pd.read_csv(out/'spatial_support_overlap.csv')
    assert panel.loc[0,'period_support']=='annual_product'
    assert book.loc[0,'input_temporal_support']=='annual_product'
    assert book.loc[0,'output_temporal_support']=='annual_product'
    assert book.loc[0,'timestamp_interpretation']=='annual_product_year_anchor'
    assert diag.loc[0,'support_status']=='SINGLE_OBSERVATION'
