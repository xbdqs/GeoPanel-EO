"""R4 standalone byte-identity demonstration. No Daymet download required."""
import json
from pathlib import Path
import tempfile
from geopanel_eo.preprocess_audit import sha256_file, validate_preprocessing_manifest
from geopanel_eo.errors import GeoPanelError


def main():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        raw = root/'raw.bin'
        prepared = root/'prepared.bin'
        manifest = root/'preprocessing_manifest.json'
        raw.write_bytes(b'original source bytes')
        prepared.write_bytes(b'prepared raster bytes')
        manifest.write_text(json.dumps({
            'schema_version': 'geopanel-preprocess-1',
            'raw_source_sha256': {'raw.bin': sha256_file(raw)},
            'prepared_sha256': {'prepared.bin': sha256_file(prepared)},
            'operations': {'kernel': 'synthetic_identity', 'description': 'illustrative only'},
        }))
        ok = validate_preprocessing_manifest(manifest, [prepared])
        prepared.write_bytes(prepared.read_bytes() + b'changed')
        try:
            validate_preprocessing_manifest(manifest, [prepared])
            rejection = 'FAILED_TO_REJECT'
        except GeoPanelError as exc:
            rejection = str(exc).split(':', 1)[0]
        print(json.dumps({'baseline': ok['status'], 'tampering': rejection,
                          'scientific_resampling_validated': ok['scientific_resampling_validated']}, indent=2))
        if rejection == 'FAILED_TO_REJECT':
            raise SystemExit(1)


if __name__ == '__main__':
    main()
