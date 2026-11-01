"""R4 byte-identity checks; independent of external Daymet downloads."""
from pathlib import Path
import json
import pytest
import yaml

from geopanel_eo.errors import GeoPanelError
from geopanel_eo.preprocess_audit import sha256_file, validate_preprocessing_manifest
from geopanel_eo.config import load_config
from geopanel_eo.synthetic import create_case


def _setup(tmp_path, raw_available=True):
    p = tmp_path / 'prepared.bin'
    p.write_bytes(b'a known synthetic raster payload')
    raw = tmp_path / 'raw.bin'
    if raw_available:
        raw.write_bytes(b'a known synthetic upstream payload')
    m = tmp_path / 'preprocessing_manifest.json'
    record = {
        'schema_version': 'geopanel-preprocess-1',
        'raw_source_sha256': {'raw.bin': sha256_file(raw) if raw_available else '1'*64},
        'prepared_sha256': {'prepared.bin': sha256_file(p)},
        'operations': {'resampling_kernel': 'synthetic-no-resampling', 'record_status': 'test-only'},
    }
    m.write_text(json.dumps(record), encoding='utf8')
    return m, p, raw


def test_r4_prepared_input_identity_verified(tmp_path):
    m, p, raw = _setup(tmp_path)
    result = validate_preprocessing_manifest(m, [p])
    assert result['status'] == 'PREPARED_INPUTS_VERIFIED'
    assert result['prepared_verified_count'] == 1
    assert result['raw_verified_count'] == 1
    assert result['raw_unavailable_count'] == 0
    assert result['scientific_resampling_validated'] is False


def test_r4_missing_raw_is_explicitly_unverified(tmp_path):
    m, p, raw = _setup(tmp_path, raw_available=False)
    result = validate_preprocessing_manifest(m, [p])
    assert result['raw_unavailable_count'] == 1
    assert result['raw_verified_count'] == 0


def test_r4_tampered_prepared_file_rejected(tmp_path):
    m, p, raw = _setup(tmp_path)
    p.write_bytes(p.read_bytes() + b'\x00')
    with pytest.raises(GeoPanelError, match='PREPROCESS_HASH_MISMATCH'):
        validate_preprocessing_manifest(m, [p])


def test_r4_tampered_raw_file_rejected_if_available(tmp_path):
    m, p, raw = _setup(tmp_path)
    raw.write_bytes(b'wrong upstream payload')
    with pytest.raises(GeoPanelError, match='PREPROCESS_RAW_HASH_MISMATCH'):
        validate_preprocessing_manifest(m, [p])


def test_r4_untracked_extra_raster_rejected(tmp_path):
    m, p, raw = _setup(tmp_path)
    extra = tmp_path/'new.tif'
    extra.write_bytes(b'data')
    with pytest.raises(GeoPanelError, match='PREPROCESS_INPUT_SET_MISMATCH'):
        validate_preprocessing_manifest(m, [p, extra])


def test_r4_invalid_schema_rejected(tmp_path):
    m, p, raw = _setup(tmp_path)
    data = json.loads(m.read_text())
    data['schema_version'] = 'unknown'
    m.write_text(json.dumps(data))
    with pytest.raises(GeoPanelError, match='PREPROCESS_MANIFEST_INVALID'):
        validate_preprocessing_manifest(m, [p])


def test_r4_relative_config_manifest_resolves(tmp_path):
    config_path = create_case(tmp_path, 'T01')
    data = yaml.safe_load(config_path.read_text())
    data['project']['preprocessing_manifest'] = 'preprocessing_manifest.json'
    config_path.write_text(yaml.safe_dump(data), encoding='utf8')
    config = load_config(config_path)
    assert Path(config.project.preprocessing_manifest) == (tmp_path/'preprocessing_manifest.json').resolve()
