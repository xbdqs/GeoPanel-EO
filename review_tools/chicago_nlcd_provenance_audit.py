"""Read-only Annual NLCD native-year semantics and Chicago provenance audit.

No external downloads, no project edits, no network calls. The emitted evidence
is a post-hoc verification; it does not assert that the historic Stage5 runtime
used the later R4 preprocessing-manifest enforcement feature.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any

import pandas as pd
import numpy as np
import rasterio
import yaml

PRODUCTS = ('landcover', 'impervious')
YEARS = (2019, 2020)
VARS = ('tmin', 'tmax', 'prcp')
NLCD_CLASSES = {11,12,21,22,23,24,31,41,42,43,52,71,81,82,90,95}
NLCD_RE = re.compile(r'annual_nlcd_c1_2_(landcover|impervious)_(2019|2020)_chicago\.tif$', re.I)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding='utf-8'))


def check_record(checks: list[dict], key: str, status: str, message: str, **details) -> None:
    checks.append({'check': key, 'status': status, 'message': message, **details})


def find_stage5(root: Path, explicit: str | None) -> Path:
    if explicit:
        path = root / 'stage5_results' / explicit
        if not path.is_dir():
            raise FileNotFoundError(f'Stage 5 run missing: {path}')
        return path
    runs = []
    for report in (root / 'stage5_results').glob('run_*/stage5_report.json'):
        try:
            d = read_json(report)
            if d.get('status') == 'PASS' and (report.parent/'panel_nlcd'/'panel_long.csv').exists():
                runs.append(report.parent)
        except (OSError, ValueError):
            continue
    if not runs:
        raise FileNotFoundError('No Stage 5 PASS run containing NLCD panel_long.csv')
    return sorted(runs)[-1]



def validate_raster(path: Path, product: str) -> dict:
    """Inspect native NLCD values, grid and nodata without altering the source."""
    with rasterio.open(path) as ds:
        if ds.count != 1:
            raise ValueError(f'NLCD band count must be one: {ds.count}')
        if ds.crs is None or ds.crs.to_epsg()!=5070:
            raise ValueError(f'NLCD source CRS must be EPSG:5070: {ds.crs}')
        if not all(abs(v-30.0)<=1e-3 for v in ds.res):
            raise ValueError(f'NLCD source native pixel size must be 30 m: {ds.res}')
        data=ds.read(1,masked=True)
        values=np.asarray(data.compressed())
        if values.size==0:raise ValueError('NLCD source contains no valid pixels')
        minimum, maximum = float(np.min(values)), float(np.max(values))
        if product=='landcover':
            actual=set(map(int,np.unique(values)))
            if not actual.issubset(NLCD_CLASSES):
                raise ValueError(f'Unexpected NLCD land-cover classes: {sorted(actual-NLCD_CLASSES)}')
        elif minimum<0 or maximum>100:
            raise ValueError(f'NLCD impervious percent outside 0..100: {minimum}..{maximum}')
        return {'crs':str(ds.crs),'width':ds.width,'height':ds.height,
                'resolution_m':list(ds.res),'nodata':ds.nodata,
                'minimum':minimum,'maximum':maximum,'valid_pixel_count':int(values.size)}

def inspect_nlcd_semantics(config: dict, checks: list[dict]) -> dict:
    sources = {s.get('source_id'): s for s in config.get('sources', [])}
    results: dict[str, Any] = {}
    for product, source_id in [('landcover','annual_nlcd_landcover'), ('impervious','annual_nlcd_impervious')]:
        src = sources.get(source_id)
        if src is None:
            check_record(checks, f'{product}_source', 'FAIL', 'Missing expected Annual NLCD source')
            continue
        timestamps = src.get('timestamps', [])
        expected = [f'{year}-01-01' for year in YEARS]
        ok = timestamps == expected and src.get('type') == 'geotiff'
        check_record(checks, f'{product}_source_year_anchors', 'PASS' if ok else 'FAIL',
                     'Jan-01 means source-year indexing anchor, not observation date',
                     configured_timestamps=timestamps, expected_timestamps=expected)
        paths = src.get('paths', [])
        names = [Path(str(p).replace('\\','/')).name for p in paths]
        expected_names = [f'annual_nlcd_c1_2_{product}_{year}_chicago.tif' for year in YEARS]
        check_record(checks, f'{product}_filename_year_mapping', 'PASS' if names == expected_names else 'FAIL',
                     'Filename order must match explicit 2019/2020 source-year anchors',
                     source_filenames=names)
        results[product] = {'source_ids': source_id, 'anchors': timestamps, 'filenames': names}
    variables = {v['variable_id']:v for v in config.get('variables', []) if isinstance(v,dict) and 'variable_id' in v}
    for variable_id in ('landcover','impervious_percent'):
        var = variables.get(variable_id)
        if var is None:
            check_record(checks, f'{variable_id}_semantics', 'FAIL', 'Expected annual variable absent')
            continue
        ok = (var.get('temporal_support') == 'annual_product' and
              var.get('temporal_reducer') == 'snapshot' and
              var.get('temporal_window',{}).get('frequency') == 'annual' and
              var.get('temporal_window',{}).get('months') == [1])
        check_record(checks, f'{variable_id}_semantics', 'PASS' if ok else 'FAIL',
                     'annual_product native annual time support; snapshot is indexing, not an instant observation',
                     observed={k:var.get(k) for k in ('temporal_support','temporal_reducer','temporal_window')})
    return results


def check_nlcd_files(root: Path, report: dict, checks: list[dict]) -> list[dict]:
    records=[]
    source = report.get('nlcd_files', report.get('files', []))
    expected = {}
    for entry in source:
        tr = entry.get('transfer', {})
        key = (tr.get('product'),tr.get('year'))
        if key[0] in PRODUCTS and key[1] in YEARS:
            expected[key] = tr.get('sha256')
    for product in PRODUCTS:
        for year in YEARS:
            basename = f'annual_nlcd_c1_2_{product}_{year}_chicago.tif'
            matches = sorted((root/'data'/'raw').rglob(basename)) if (root/'data'/'raw').exists() else []
            rec={'product':product,'year':year,'basename':basename,'archive_sha256':expected.get((product,year)),
                 'files_found':len(matches)}
            if not matches:
                rec['status']='MISSING_SOURCE_TIFF'
                check_record(checks, f'nlcd_tiff_{product}_{year}', 'WARN',
                             'Original NLCD TIFF unavailable; archived checksum is not independently verified')
            else:
                hashes = {str(p): digest(p) for p in matches}
                rec['file_hashes']=hashes
                if not rec['archive_sha256']:
                    rec['status']='NO_ARCHIVED_HASH'
                    check_record(checks, f'nlcd_tiff_{product}_{year}', 'WARN', 'No archived source hash to compare')
                elif all(v == rec['archive_sha256'] for v in hashes.values()):
                    try:
                        rec['raster_inspection']=validate_raster(matches[0],product)
                    except (OSError,ValueError,rasterio.errors.RasterioError) as exc:
                        rec['status']='INVALID_RASTER'
                        check_record(checks, f'nlcd_tiff_{product}_{year}', 'FAIL',
                                     f'Source hash matches archive but native raster invalid: {exc}')
                    else:
                        rec['status']='VERIFIED'
                        check_record(checks, f'nlcd_tiff_{product}_{year}', 'PASS',
                                     'Local TIFF SHA-256 and native 30 m EPSG:5070 raster values verified')
                else:
                    rec['status']='HASH_MISMATCH'
                    check_record(checks, f'nlcd_tiff_{product}_{year}', 'FAIL',
                                 'NLCD bytes differ from Stage5 acquisition record', observed_hashes=hashes)
            records.append(rec)
    return records


def check_panel(run: Path, report: dict, checks: list[dict]) -> dict:
    path = run/'panel_nlcd'/'panel_long.csv'
    df = pd.read_csv(path, dtype={'entity_id':str,'time':str,'variable_id':str})
    df['entity_id'] = df.entity_id.str.zfill(2)
    years = sorted(set(df.time.astype(str)))
    vars_ = sorted(df.variable_id.unique())
    keys = ['entity_id','time','variable_id']
    n_entities = df.entity_id.nunique()
    full = (len(df)==77*2*33 and n_entities==77 and years==['2019','2020'] and len(vars_)==33
            and not df.duplicated(keys).any() and df.value.notna().all()
            and (df.groupby(['entity_id','time']).size()==33).all())
    check_record(checks,'nlcd_panel_structure','PASS' if full else 'FAIL',
                 '77 communities x 2 mapped product-years x 33 variables',
                 rows=len(df), unique_entities=n_entities, years=years, n_variables=len(vars_))
    if 'period_support' in df:
        annual = set(df.period_support.dropna().unique()) == {'annual_product'}
        check_record(checks,'nlcd_panel_support_metadata','PASS' if annual else 'WARN',
                     'Annual product semantic labels in NLCD output (legacy stage may lack newer R3 semantics)', values=sorted(map(str,df.period_support.dropna().unique())))
    else:
        check_record(checks,'nlcd_panel_support_metadata','WARN',
                     'Historic NLCD panel does not contain period_support; later R3 contract cannot be inferred from old output')
    combined = run/'combined_panel_long.csv'
    combined_sha = digest(combined) if combined.is_file() else None
    expected_sha = report.get('combined_long_sha256')
    check_record(checks,'stage5_combined_panel_hash',
                 'PASS' if combined_sha and combined_sha==expected_sha else 'FAIL',
                 'Stage5 archived output identity', observed_sha256=combined_sha, archived_sha256=expected_sha)
    if combined.is_file():
        combined_df = pd.read_csv(combined,dtype={'entity_id':str,'time':str,'variable_id':str})
        nlcd_keys = df[keys].astype(str).apply(tuple,axis=1)
        joint_keys = set(combined_df[keys].astype(str).apply(tuple,axis=1))
        ok = set(nlcd_keys).issubset(joint_keys)
        check_record(checks,'nlcd_keys_preserved_in_combined_panel','PASS' if ok else 'FAIL',
                     'NLCD row identity retained after climate/NLCD merge')
    return {'rows':len(df),'entities':n_entities,'periods':years,'variables':len(vars_),
            'sha256':digest(path),'original_stage5_version':report.get('software_version')}


def resolve_stage3(root: Path, run: Path, report: dict, checks: list[dict]) -> Path | None:
    source = report.get('source_stage3')
    if not source:
        check_record(checks,'stage3_source_run','WARN','Historical Stage5 report has no source_stage3')
        return None
    path = root/'stage3_results'/str(source)
    good = path/'stage3_report.json'
    if good.is_file():
        data=read_json(good)
        status = 'PASS' if data.get('status')=='PASS' else 'WARN'
        check_record(checks,'stage3_source_run',status,'Historical Stage5 referenced Stage3 report found',
                     source_run=str(source), stage3_status=data.get('status'))
        return path
    check_record(checks,'stage3_source_run','WARN', 'Historical Stage5 referenced Stage3 run unavailable in relocated directory',source_run=str(source))
    return None


def check_preprocess_link(run: Path, stage3: Path|None, checks: list[dict]) -> dict:
    result={}
    for freq in ('monthly','annual'):
        cfgpath=run/f'config_daymet_{freq}.yml'
        manifest_path=run/f'panel_daymet_{freq}'/'run_manifest.json'
        if not (cfgpath.is_file() and manifest_path.is_file()):
            check_record(checks,f'daymet_{freq}_metadata','WARN','Config or runtime manifest unavailable')
            continue
        config=yaml.safe_load(cfgpath.read_text(encoding='utf8'))
        manifest=read_json(manifest_path)
        pp=manifest.get('preprocessing_audit')
        declared=config.get('project',{}).get('preprocessing_manifest')
        if not (isinstance(pp,dict) and pp.get('status')=='PREPARED_INPUTS_VERIFIED' and declared):
            check_record(checks,f'daymet_{freq}_r4_link','WARN',
                         'Historic Stage5 run does not demonstrate R4 preprocessing enforcement; external post-hoc crosswalk only',
                         configured_manifest=declared, runtime_record=pp)
        else:
            check_record(checks,f'daymet_{freq}_r4_link','PASS',
                         'A recorded R4 upstream preprocessing audit is present (verify source files independently)',
                         manifest_sha256=pp.get('manifest_sha256'))
        if stage3:
            observed={}
            for variable in VARS:
                prepared=stage3/'prepared'/f'daymet_{variable}_monthly_epsg6933.nc'
                if prepared.is_file():observed[variable]=digest(prepared)
            input_hashes=manifest.get('input_sha256',{})
            values=set(input_hashes.values()) if isinstance(input_hashes,dict) else set()
            matches={v: h in values for v,h in observed.items()}
            check_record(checks,f'daymet_{freq}_stage3_input_hashes',
                         'PASS' if len(matches)==3 and all(matches.values()) else 'WARN',
                         'Cross-check Stage3 prepared files against historic Stage5 runtime input hashes',
                         prepared_count=len(matches), matched_hashes=matches)
            result[freq]={'input_sha256_matches':matches,'has_runtime_preprocess_link':bool(pp and declared)}
    return result


def audit(root: Path, run_name: str|None=None) -> dict:
    root = root.resolve()
    checks=[]
    stage5 = find_stage5(root, run_name)
    report = read_json(stage5/'stage5_report.json')
    config_path=stage5/'config_nlcd.yml'
    if not config_path.is_file():raise FileNotFoundError(f'NLCD config not found: {config_path}')
    config=yaml.safe_load(config_path.read_text(encoding='utf8'))
    semantics=inspect_nlcd_semantics(config,checks)
    nlcd_files=check_nlcd_files(root,report,checks)
    panel=check_panel(stage5, report,checks)
    stage3=resolve_stage3(root,stage5,report,checks)
    lineage=check_preprocess_link(stage5,stage3,checks)
    n_failed=sum(c['status']=='FAIL' for c in checks)
    n_warn=sum(c['status']=='WARN' for c in checks)
    status='FAIL' if n_failed else ('PASS_WITH_LIMITATIONS' if n_warn else 'PASS')
    return {'schema_version':'chicago-nlcd-temporal-provenance-audit-1','status':status,
            'created_utc':datetime.now(timezone.utc).isoformat(),'root':str(root),'stage5_run':stage5.name,
            'scope':'Read-only historical year-anchor, data identity, panel semantics and posthoc upstream lineage; not proof of independent NLCD source accuracy',
            'annual_nlcd_time_interpretation':'Product-year label; January 1 is a WCS/configuration index anchor, not a measured January 1 state or year-end snapshot.',
            'nlcd_sources':semantics,'nlcd_tiff_files':nlcd_files,'nlcd_panel':panel,
            'upstream_daymet_lineage':lineage,'r4_runtime_link_proven':all(v.get('has_runtime_preprocess_link') for v in lineage.values()) if len(lineage)==2 else False,
            'checks':checks,'check_counts':{'pass':len(checks)-n_failed-n_warn,'warn':n_warn,'fail':n_failed},
            'scientific_limits':['NLCD product-year mapping is validated from configuration, file hashes and published source semantics, not independent classification ground truth.',
                                 'Historic Stage5 runs predate R4 and do not automatically prove an enforced preprocessing manifest.',
                                 'Neither Daymet physical resampling accuracy nor NLCD external validation is inferred from file hashes.']}


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root',type=Path,default=Path(r'E:\GeoPanel-EO\review_data\chicago'))
    parser.add_argument('--run',default=None,help='Stage5 run folder name; default latest successful run with NLCD panel')
    parser.add_argument('--output',type=Path,default=Path(r'E:\GeoPanel-EO\review_results\real_data_validation\chicago_nlcd_provenance_audit.json'))
    args=parser.parse_args()
    try:
        data=audit(args.data_root,args.run)
        args.output.parent.mkdir(exist_ok=True,parents=True)
        args.output.write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding='utf8')
        print(json.dumps({'status':data['status'],'stage5_run':data['stage5_run'],
                          'check_counts':data['check_counts'],'r4_runtime_link_proven':data['r4_runtime_link_proven'],
                          'output':str(args.output)},indent=2,ensure_ascii=False))
        for c in data['checks']:
            if c['status'] != 'PASS':print(f"[{c['status']}] {c['check']}: {c['message']}")
        return 1 if data['status']=='FAIL' else 0
    except (OSError,ValueError,KeyError,TypeError) as exc:
        print(f'ERROR: {type(exc).__name__}: {exc}')
        return 2


if __name__=='__main__':
    raise SystemExit(main())
