"""R4 full extraction to final manifest link (Windows acceptance)."""
import json
from pathlib import Path
import pytest
import yaml
pytest.importorskip('exactextract')
pytest.importorskip('pyarrow')
from geopanel_eo.preprocess_audit import sha256_file
from geopanel_eo.pipeline import run
from geopanel_eo.synthetic import create_case
from geopanel_eo.errors import GeoPanelError


def make_case(tmp_path, corrupt=False):
    cfg_path = create_case(tmp_path, 'T01')
    config = yaml.safe_load(cfg_path.read_text())
    config['project']['preprocessing_manifest'] = 'preprocessing_manifest.json'
    cfg_path.write_text(yaml.safe_dump(config), encoding='utf8')
    raw = tmp_path/'original_raw.bin'
    raw.write_bytes(b'raw-example')
    m = {'schema_version': 'geopanel-preprocess-1',
         'raw_source_sha256': {'original_raw.bin': sha256_file(raw)},
         'prepared_sha256': {'data.tif': sha256_file(tmp_path/'data.tif')},
         'operations': {'resampling_kernel': 'synthetic-no-resampling',
                        'note': 'Test data; not a real Daymet operation'}}
    (tmp_path/'preprocessing_manifest.json').write_text(json.dumps(m), encoding='utf8')
    if corrupt:
        (tmp_path/'data.tif').write_bytes(b'bad')
    return cfg_path


def test_preproc_manifest_to_core_manifest_roundtrip(tmp_path):
    cfg_path = make_case(tmp_path)
    result = run(cfg_path)
    audit = result['preprocessing_audit']
    assert audit['status'] == 'PREPARED_INPUTS_VERIFIED'
    assert audit['prepared_verified_count'] == 1
    assert audit['raw_verified_count'] == 1
    assert result['row_count'] == 1
    assert str(tmp_path/'preprocessing_manifest.json') in result['input_sha256']
    assert json.loads((tmp_path/'outputs'/'run_manifest.json').read_text())['preprocessing_audit'] == audit


def test_core_rejects_tampered_preprocessed_raster(tmp_path):
    cfg_path = make_case(tmp_path, corrupt=True)
    with pytest.raises(GeoPanelError, match='PREPROCESS_HASH_MISMATCH'):
        run(cfg_path)
    assert not (tmp_path/'outputs').exists()
