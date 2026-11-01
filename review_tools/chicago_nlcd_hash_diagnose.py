"""Read-only forensic diagnostic of historical Annual NLCD acquisition identities.

Checks acquisition hash, sidecars, native raster values/geometry, and legacy
annual-product semantics. It NEVER changes historical config, TIFFs, or logs.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

try:
    import yaml
    import numpy as np
    import rasterio
except ImportError as exc:
    raise SystemExit(f"Missing package in geopanel_eo environment: {exc}")

YEARS = (2019, 2020)
PRODUCTS = ("landcover", "impervious")
CLASSES = {11, 12, 21, 22, 23, 24, 31, 41, 42, 43, 52, 71, 81, 82, 90, 95}
STOP_DIRS = {".git", ".venv", "venv", "node_modules", "site-packages", "__pycache__", "chunks", ".pytest_cache", "review_results"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as src:
        for block in iter(lambda: src.read(2 << 20), b""):
            h.update(block)
    return h.hexdigest()


def raster_fingerprint(path: Path, expected: dict[str, Any], product: str) -> dict[str, Any]:
    """Compare raster geometry/content summary; not a pixel comparison to lost historical raster."""
    with rasterio.open(path) as ds:
        arr = ds.read(1, masked=True)
        data = arr.compressed()
        if data.size == 0:
            raise ValueError("No valid cells")
        values = sorted(map(int, np.unique(data)))
        meta = {
            "driver": ds.driver,
            "width": ds.width, "height": ds.height,
            "count": ds.count, "dtype": ds.dtypes[0],
            "crs": str(ds.crs), "resolution": list(ds.res),
            "bounds": list(ds.bounds), "nodata": ds.nodata,
            "valid_pixels": int(data.size),
            "masked_pixels": int(arr.size - data.size),
            "minimum": float(np.min(data)), "maximum": float(np.max(data)),
            "unique_value_count": len(values),
            "unique_values": values if product == "landcover" else None,
            "valid_range": bool(set(values).issubset(CLASSES)) if product == "landcover" else bool(np.min(data) >= 0 and np.max(data) <= 100),
            "cell_array_sha256": hashlib.sha256(np.asarray(ds.read(1)).tobytes()).hexdigest(),
            "valid_mask_sha256": hashlib.sha256(np.ma.getmaskarray(arr).tobytes()).hexdigest(),
        }
        comparisons = {}
        for key in ("driver", "width", "height", "dtype", "crs", "valid_pixels", "masked_pixels", "nodata", "minimum", "maximum", "size_bytes"):
            if key == "size_bytes":
                actual = path.stat().st_size
            else:
                actual = meta.get(key)
            if key in expected:
                comparisons[key] = (actual == expected[key])
        if expected.get("unique_values") is not None:
            comparisons["unique_values"] = values == expected["unique_values"]
        if "bounds" in expected:
            comparisons["bounds_at_0.1m"] = len(expected["bounds"]) == 4 and all(abs(a - b) <= .1 for a, b in zip(meta["bounds"], expected["bounds"]))
        if "resolution" in expected:
            comparisons["resolution_at_0.001m"] = len(expected["resolution"]) == 2 and all(abs(a - b) <= .001 for a, b in zip(meta["resolution"], expected["resolution"]))
        meta["historical_metadata_comparison"] = comparisons
        meta["all_comparable_metadata_match"] = bool(comparisons) and all(comparisons.values())
        return meta


def classify_semantics(config: dict) -> dict[str, str]:
    """Separate legacy labels from true year-anchor errors."""
    out = {}
    variables = {v.get('variable_id'): v for v in config.get('variables', []) if isinstance(v, dict)}
    for varid in ("landcover", "impervious_percent"):
        v = variables.get(varid)
        if v is None:
            out[varid] = "MISSING_VARIABLE"
        elif v.get('temporal_reducer') != 'snapshot' or v.get('temporal_window', {}).get('frequency') != 'annual' or v.get('temporal_window', {}).get('months') != [1]:
            out[varid] = "UNEXPECTED_TEMPORAL_REDUCER_OR_WINDOW"
        elif v.get('temporal_support') == 'annual_product':
            out[varid] = "MODERN_ANNUAL_PRODUCT"
        elif v.get('temporal_support') == 'monthly_snapshot':
            out[varid] = "LEGACY_MONTHLY_SNAPSHOT_LABEL_REQUIRES_DISCLOSURE"
        else:
            out[varid] = "UNRECOGNIZED_TEMPORAL_SUPPORT"
    for p in PRODUCTS:
        sid = 'annual_nlcd_' + p
        src = next((s for s in config.get('sources', []) if s.get('source_id') == sid), None)
        if not src:
            out[sid] = "MISSING_SOURCE"
        else:
            dates = src.get('timestamps', [])
            names = [Path(str(s).replace('\\', '/')).name.lower() for s in src.get('paths', [])]
            expected_names = [f'annual_nlcd_c1_2_{p}_{y}_chicago.tif' for y in YEARS]
            out[sid] = "YEAR_ANCHORS_AND_FILES_VALID" if dates == [f'{y}-01-01' for y in YEARS] and names == expected_names else "YEAR_ANCHOR_OR_FILENAME_MISMATCH"
    return out


def walk_matching_files(roots: list[Path], names: set[str]) -> dict[str, list[Path]]:
    found = {name: [] for name in names}
    visited = set()
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            if root.name.lower() in names:
                found[root.name.lower()].append(root)
            continue
        for folder, dirs, files in os.walk(root, followlinks=False, onerror=lambda err: print(f"[SCAN WARN] {err}")):
            key = os.path.normcase(os.path.abspath(folder))
            if key in visited:
                dirs[:] = []
                continue
            visited.add(key)
            dirs[:] = [d for d in dirs if d.lower() not in STOP_DIRS]
            for file in files:
                if file.lower() in names:
                    found[file.lower()].append(Path(folder)/file)
    return found


def diagnose(data_root: Path, stage5_name: str, extra_roots: list[Path], output_dir: Path) -> dict[str, Any]:
    run = data_root/'stage5_results'/stage5_name
    report = json.loads((run/'stage5_report.json').read_text(encoding='utf-8'))
    config = yaml.safe_load((run/'config_nlcd.yml').read_text(encoding='utf-8'))
    semantics = classify_semantics(config)
    original_sources = [s for s in config.get('sources', []) if isinstance(s, dict)]
    roots = [data_root/'data'/'raw', data_root/'stage4_results']
    # Original source paths from historical config might still exist locally.
    for source in original_sources:
        for path in source.get('paths', []):
            pp = Path(path)
            if pp.parent.exists():
                roots.append(pp.parent)
    roots.extend(extra_roots)
    names = {f'annual_nlcd_c1_2_{p}_{y}_chicago.tif' for p in PRODUCTS for y in YEARS}
    found = walk_matching_files(roots, names)
    records = []
    csv_rows = []
    for p in PRODUCTS:
        for y in YEARS:
            name = f'annual_nlcd_c1_2_{p}_{y}_chicago.tif'
            original = next((r for r in report.get('nlcd_files', [])
                             if r.get('transfer', {}).get('product') == p and r.get('transfer', {}).get('year') == y), None)
            if not original:
                records.append({'product': p, 'year': y, 'status': 'MISSING_HISTORICAL_ACQUISITION_RECORD'})
                continue
            transfer = original['transfer']; expected = transfer.get('sha256')
            item = {'product': p, 'year': y, 'historical_sha256': expected,
                    'historical_size_bytes': transfer.get('size_bytes'),
                    'historical_request_key': transfer.get('request_key'),
                    'historical_source_service': transfer.get('source_service'),
                    'candidates': []}
            for path in found[name.lower()]:
                now = sha256(path)
                candidate = {'path': str(path), 'sha256': now, 'file_size_bytes': path.stat().st_size,
                             'byte_identical_to_historical': bool(now == expected)}
                sidecar = path.with_suffix('.tif.audit.json')
                if sidecar.exists():
                    try:
                        audit_info = json.loads(sidecar.read_text(encoding='utf-8'))
                        candidate['sidecar_sha256'] = audit_info.get('sha256')
                        candidate['sidecar_request_key'] = audit_info.get('request_key')
                        candidate['sidecar_hash_matches_current'] = audit_info.get('sha256') == now
                        candidate['sidecar_request_key_matches_historical'] = audit_info.get('request_key') == transfer.get('request_key')
                    except (OSError, ValueError) as exc:
                        candidate['sidecar_read_error'] = str(exc)
                try:
                    candidate['raster'] = raster_fingerprint(path, original.get('inspection', {}), p)
                except (OSError, ValueError, rasterio.errors.RasterioError) as exc:
                    candidate['raster_read_error'] = f'{type(exc).__name__}: {exc}'
                item['candidates'].append(candidate)
                csv_rows.append({'product': p, 'year': y, 'path': str(path),
                                 'historical_sha256': expected, 'current_sha256': now,
                                 'sha256_match': now == expected,
                                 'historical_bytes': transfer.get('size_bytes'),
                                 'current_bytes': path.stat().st_size,
                                 'metadata_match': candidate.get('raster', {}).get('all_comparable_metadata_match'),
                                 'raster_read_error': candidate.get('raster_read_error', '')})
            if any(c['byte_identical_to_historical'] for c in item['candidates']):
                item['status'] = 'FOUND_EXACT_HISTORICAL_BYTES'
            elif not item['candidates']:
                item['status'] = 'NO_LOCAL_SOURCE_FOUND'
            elif any(c.get('raster', {}).get('all_comparable_metadata_match') for c in item['candidates']):
                item['status'] = 'BYTE_MISMATCH_METADATA_MATCHES_PIXEL_IDENTITY_UNPROVEN'
            else:
                item['status'] = 'BYTE_MISMATCH_OR_METADATA_INCONSISTENT'
            records.append(item)
    exact = sum(r.get('status') == 'FOUND_EXACT_HISTORICAL_BYTES' for r in records)
    state = 'ALL_FOUR_ARCHIVE_IDENTICAL' if exact == 4 else 'IDENTITY_UNRESOLVED'
    result = {'schema_version': 'nlcd-historical-source-diagnostic-1', 'created_utc': datetime.now(timezone.utc).isoformat(),
              'status': state, 'matching_historical_files': exact, 'total_expected': 4,
              'historical_stage5_run': stage5_name, 'historical_stage5_status': report.get('status'),
              'historical_software_version': report.get('software_version'), 'historical_annual_semantics': semantics,
              'search_roots': [str(p) for p in roots], 'sources': records,
              'warnings': ['File hash mismatch cannot distinguish re-encoding from changed pixels without original archived pixel data.',
                           'Do not alter historical TIFFs, stage5_report.json, or config_nlcd.yml.',
                           'An old monthly_snapshot label must not be retroactively interpreted as having enforced annual_product at run time.',
                           'This diagnostic does not prove R4 runtime preprocessing linkage.']}
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir/'chicago_nlcd_source_diagnostic.json').write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    with (output_dir/'chicago_nlcd_source_candidates.csv').open('w', newline='', encoding='utf-8-sig') as file:
        writer = csv.DictWriter(file, fieldnames=['product', 'year', 'path', 'historical_sha256', 'current_sha256', 'sha256_match', 'historical_bytes', 'current_bytes', 'metadata_match', 'raster_read_error'])
        writer.writeheader()
        writer.writerows(csv_rows)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, default=Path(r'E:\GeoPanel-EO\review_data\chicago'))
    parser.add_argument('--stage5-run', default='run_20260906T225615102107Z')
    parser.add_argument('--search-root', type=Path, action='append', default=[], help='Optional extra directory of older TIFFs; repeatable')
    parser.add_argument('--out-dir', type=Path, default=Path(r'E:\GeoPanel-EO\review_results\real_data_validation'))
    args = parser.parse_args()
    result = diagnose(args.data_root, args.stage5_run, args.search_root, args.out_dir)
    print(json.dumps({'status': result['status'], 'historical_annual_semantics': result['historical_annual_semantics'],
                      'source_status': {f"{r['product']}_{r['year']}": r['status'] for r in result['sources']},
                      'source_candidate_counts': {f"{r['product']}_{r['year']}": len(r['candidates']) for r in result['sources']},
                      'report': str(args.out_dir/'chicago_nlcd_source_diagnostic.json'),
                      'csv': str(args.out_dir/'chicago_nlcd_source_candidates.csv')}, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
