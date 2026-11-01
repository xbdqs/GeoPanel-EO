"""Integration acceptance of R2 output fields; requires full geospatial dependencies."""
import calendar
import json
import pandas as pd
import pytest
import yaml

pytest.importorskip('exactextract')
pytest.importorskip('h5netcdf')
pytest.importorskip('pyarrow')

from geopanel_eo.pipeline import run
from geopanel_eo.synthetic import create_case


def test_r2_day_count_weights_visible_in_panels_codebook_and_manifest(tmp_path):
    cfg_path = create_case(tmp_path, 'T06')
    cfg = yaml.safe_load(cfg_path.read_text(encoding='utf-8'))
    # The synthetic 2020 dataset has all 12 monthly values. Use explicitly
    # supplied Daymet-like day counts to test metadata, not assert Daymet provenance.
    counts = {f'2020-{m:02d}': calendar.monthrange(2020, m)[1] for m in range(1,13)}
    counts['2020-12'] = 30
    cfg['temporal'] = dict(mean_weighting='represented_days', represented_day_counts=counts,
                           expected_start_month='2020-01', expected_end_month='2020-12')
    cfg_path.write_text(yaml.safe_dump(cfg), encoding='utf-8')
    manifest = run(cfg_path)
    output_dir = tmp_path/'outputs'
    panel = pd.read_csv(output_dir/'panel_long.csv')
    book = pd.read_csv(output_dir/'variable_codebook.csv')
    saved = json.loads((output_dir/'run_manifest.json').read_text(encoding='utf-8'))
    assert len(panel) == 1
    assert panel.iloc[0].temporal_mean_weighting == 'represented_days'
    assert panel.iloc[0].represented_day_count == 365
    assert book.iloc[0].temporal_mean_weighting == 'represented_days'
    assert book.iloc[0].represented_day_count_source == 'explicit_upstream_counts'
    assert book.iloc[0].expected_start_month == '2020-01'
    assert saved['temporal_mean_weighting'] == 'represented_days'
    assert saved['schema_version'] == 's1-3-explicit-time-contract'
    assert manifest['roundtrip_verified'] is True
