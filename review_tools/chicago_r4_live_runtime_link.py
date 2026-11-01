"""Exercise R4 preprocessing-manifest enforcement with real archived Chicago prepared NetCDFs.

A manifest constructed by this script is RETROSPECTIVE and MUST NOT be represented
as an executed Stage 3-time provenance record. The new R4 run does perform
live pre-/post-extraction identity checks and writes its own run_manifest.json.

Usage (Anaconda Prompt, E:\\GeoPanel-EO):
  python review_tools\\chicago_r4_live_runtime_link.py --check-only
  python review_tools\\chicago_r4_live_runtime_link.py

Inputs are read-only; writes are restricted to review_results/real_data_validation/r4_live_link.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

import numpy as np
import pandas as pd
import yaml

VARS = ('tmin', 'tmax', 'prcp')
YEARS = (2019, 2020)
EXPECTED_RAW = {
    'tmin_2019': '789c40d0050953bb2d3bf74d384e933cd53929c8082aed61446fd8f49eb08e11',
    'tmin_2020': 'dcac67c30d9a8aa578e1c04dcce91ea6a0e73f8cb5e04e2b80f1d07ad862eee5',
    'tmax_2019': 'e3fa254a6adfbc20ebe37cadef7cbf4b82ae8b23ea0fb295e0093eaa33d4785a',
    'tmax_2020': 'a3ce2d91d9fb7475505fb79536d97ae97a0c630a1b6e956eacdcd13605c81c0d',
    'prcp_2019': '8166ae9630a3aa796d86bcb7dd384fb76b620167741a1135fbb4540d876f8d6d',
    'prcp_2020': '7028aa76507d39e99661933a0c98b4e956a9b60c16e60caf9c9df6e128b7f55c',
}
EXPECTED_PREPARED = {
    'tmin': '3125860499858225c3cc4df3fcb44ffbf1e70289615bf22f8d44a97190a7760c',
    'tmax': '65e42d8f8febb022756ba0519b0fd363681f3ce899f52ab2536f74ca7598a4f1',
    'prcp': '5320a2dde48337d76894c65236f8b63eb47ce20776f1da0589914c01439da822',
}
EXPECTED_ARCHIVE_MONTHLY = '6b634987eb4598fdaa5b70248441f1b5856370f42c76b71c44eab8443e08022d'


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(2**20), b''):
            h.update(block)
    return h.hexdigest()


def relative_to_manifest(path: Path, directory: Path) -> str:
    return os.path.relpath(path, directory).replace('\\', '/')


def file_paths(project: Path, data_root: Path) -> dict:
    stage3 = data_root / 'stage3_results' / 'run_20260906T211325137105Z'
    stage5 = data_root / 'stage5_results' / 'run_20260906T225615102107Z'
    return {
        'raw': {(v, y): data_root / 'data' / 'raw' / f'daymet_v4_daily_na_{v}_{y}_chicago.nc'
                for v in VARS for y in YEARS},
        'prepared': {v: stage3 / 'prepared' / f'daymet_{v}_monthly_epsg6933.nc' for v in VARS},
        'stage3_report': stage3 / 'stage3_report.json',
        'archive_monthly': stage5 / 'panel_daymet_monthly' / 'panel_long.csv',
        'units': project / 'case_studies' / 'chicago' / 'results' / 'chicago_community_areas_77.geojson',
    }


def verify_inputs(paths: dict) -> dict:
    missing = [str(p) for p in [*paths['raw'].values(), *paths['prepared'].values(),
                                paths['stage3_report'], paths['archive_monthly'], paths['units']]
               if not p.is_file()]
    if missing:
        raise FileNotFoundError('Required real Chicago input missing: ' + '; '.join(missing))
    hashes = {'raw': {}, 'prepared': {}}
    for (v,y), p in paths['raw'].items():
        actual = digest(p)
        if actual != EXPECTED_RAW[f'{v}_{y}']:
            raise ValueError(f'RAW_HASH_MISMATCH: {p}')
        hashes['raw'][f'{v}_{y}'] = actual
    for v,p in paths['prepared'].items():
        actual = digest(p)
        if actual != EXPECTED_PREPARED[v]:
            raise ValueError(f'PREPARED_HASH_MISMATCH: {p}')
        hashes['prepared'][v] = actual
    if digest(paths['archive_monthly']) != EXPECTED_ARCHIVE_MONTHLY:
        raise ValueError(f'ARCHIVED_MONTHLY_HASH_MISMATCH: {paths["archive_monthly"]}')
    report = json.loads(paths['stage3_report'].read_text(encoding='utf-8-sig'))
    if report.get('status') != 'PASS':
        raise ValueError('Historical Stage 3 report is not PASS')
    with paths['units'].open('r', encoding='utf-8-sig') as f:
        units = json.load(f)['features']
    ids = [f['properties'].get('entity_id') for f in units]
    if len(ids) != 77 or len(set(ids)) != 77:
        raise ValueError(f'Expected 77 distinct Chicago community entities; got {len(ids)}')
    return {'historical_stage3_status': report['status'], 'raw_sha256': hashes['raw'],
            'prepared_sha256': hashes['prepared'], 'unit_count': len(ids),
            'archive_monthly_sha256': EXPECTED_ARCHIVE_MONTHLY}


def retrospective_manifest(paths: dict, directory: Path) -> dict:
    raw_map = {relative_to_manifest(p, directory): EXPECTED_RAW[f'{v}_{y}']
               for (v,y),p in paths['raw'].items()}
    prep_map = {relative_to_manifest(p, directory): EXPECTED_PREPARED[v]
                for v,p in paths['prepared'].items()}
    return {
        'schema_version': 'geopanel-preprocess-1',
        'dataset': 'Chicago Daymet 2019-2020 / retrospective identity crosswalk',
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'provenance_class': 'RETROSPECTIVE_RECONSTRUCTION_NOT_STAGE3_EXECUTION_RECORD',
        'historical_stage3_manifest_cogenerated': False,
        'historic_stage3_report_path': str(paths['stage3_report']),
        'historic_stage3_report_sha256': digest(paths['stage3_report']),
        'raw_source_sha256': raw_map,
        'prepared_sha256': prep_map,
        'operations': {
            'source': 'historical Stage 3 report, plus independently verified raster reproduction',
            'historical_processing_claim': 'daily-to-monthly; temperature mean, precipitation sum; bilinear to EPSG:6933 at 1000 m',
            'verification_boundary': 'New R4 run enforces real current input byte identity; historic Stage 3 was not R4',
            'independent_real_data_validation': 'Prior 72-grid exact reproduction and 77-community sensitivity results',
        },
        'validation_boundary': 'No proof that this manifest was present at original 2026-09-06 Stage 3 runtime',
    }


def config_document(paths: dict, out: Path, manifest: Path) -> dict:
    variables, sources = [], []
    for v in VARS:
        sources.append({'source_id':f'daymet_{v}', 'type':'netcdf', 'path':str(paths['prepared'][v]),
                        'time_coordinate':'time', 'x_coordinate':'x', 'y_coordinate':'y'})
        variables.append({
            'variable_id':v, 'source_id':f'daymet_{v}', 'source_variable':v,
            'semantic_type':'accumulation_depth' if v=='prcp' else 'intensive_state',
            'unit':'mm' if v=='prcp' else 'degrees C',
            'temporal_support':'monthly_total' if v=='prcp' else 'monthly_mean',
            'spatial_reducer':'coverage_weighted_mean',
            'temporal_reducer':'snapshot' if v=='prcp' else 'mean',
            'temporal_window':{'frequency':'monthly', 'months':list(range(1,13))},
            'minimum_coverage':0.95,
        })
    return {
        'project': {'project_id':'chicago_daymet_r4_live_runtime_link',
                    'title':'Chicago Daymet R4 real-input runtime identity test',
                    'output_directory':str(out), 'overwrite':False, 'preprocessing_manifest':str(manifest)},
        'units': {'source':str(paths['units']), 'entity_id_field':'entity_id',
                  'entity_name_field':'entity_name', 'crs':'EPSG:4326',
                  'target_crs':'EPSG:6933','geometry_action':'fail'},
        'sources':sources, 'variables':variables,
        'spatial':{'crs_action':'reproject_units','area_method':'projected_equal_area'},
        'temporal':{'calendar':'proleptic_gregorian','timezone':'naive', 'input_frequency':'monthly',
                    'mean_weighting':'equal_month', 'incomplete_window':'flag_and_null'},
        'panel':{'require_unique_entity_time':True,'require_balanced':True,'retain_flagged_rows':True},
        'uncertainty':{'assumption':'independent_cells_and_times'},
        'outputs':{'csv':True,'parquet':True,'stata':True,'wide_csv':True,
                   'quality_report_html':True,'hash_algorithm':'sha256'}
    }


def compare_archived_monthly(current_csv: Path, archive_csv: Path) -> dict:
    keys = ['entity_id','time','variable_id']
    def get(p):
        frame = pd.read_csv(p, dtype={'entity_id':str,'time':str})
        if not set([*keys,'value']).issubset(frame.columns):
            raise ValueError(f'Panel CSV missing keys/value: {p}')
        if frame.duplicated(keys).any():
            raise ValueError(f'Panel CSV contains duplicate canonical keys: {p}')
        return frame[[*keys,'value']]
    a,b=get(current_csv),get(archive_csv)
    if len(a)!=77*24*3 or len(b)!=77*24*3:
        raise ValueError(f'Expected 5544 monthly rows, got current={len(a)}, archived={len(b)}')
    merged=a.merge(b,on=keys,how='outer',indicator=True,validate='one_to_one',suffixes=('_r4','_archive'))
    if not (merged['_merge']=='both').all():
        raise ValueError(f'Monthly panel key mismatch: {merged._merge.value_counts().to_dict()}')
    current=pd.to_numeric(merged['value_r4'],errors='coerce').to_numpy(float)
    archived=pd.to_numeric(merged['value_archive'],errors='coerce').to_numpy(float)
    finite=np.isfinite(current)&np.isfinite(archived)
    if np.any(np.isfinite(current)!=np.isfinite(archived)):
        raise ValueError('New/archived monthly panel valid-value masks differ')
    diffs=np.abs(current[finite]-archived[finite])
    max_abs=float(diffs.max()) if diffs.size else float('nan')
    by_variable={}
    for variable in VARS:
        group=merged.variable_id.eq(variable).to_numpy() & finite
        by_variable[variable]={'n_valid':int(group.sum()),'max_abs':float(np.max(np.abs(current[group]-archived[group]))) if group.any() else None}
    return {'n_rows':len(merged),'n_matched_keys':len(merged),'n_finite_compared':int(finite.sum()),
            'max_abs_difference':max_abs,'units_by_variable':{'tmin':'degrees C','tmax':'degrees C','prcp':'mm'},
            'by_variable':by_variable,'tolerance':1e-6,'numeric_reproduction_pass':bool(np.isfinite(max_abs) and max_abs<=1e-6)}


def run(project: Path, data_root: Path, root: Path, check_only=False) -> dict:
    from geopanel_eo import __version__
    if __version__ not in {'0.1.1.dev2', '0.1.1'}:
        raise ValueError(f'Need a validated GeoPanel-EO 0.1.1 release candidate, detected {__version__}')
    paths=file_paths(project,data_root)
    preflight=verify_inputs(paths)
    if check_only:
        return {'status':'READY_REAL_INPUTS','core_version':__version__, **preflight}
    from geopanel_eo.pipeline import run as run_core
    # New output directory only: never overwrite a previous run.
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
    run_dir=root / f'run_{stamp}_{uuid.uuid4().hex[:8]}'
    run_dir.mkdir(parents=True,exist_ok=False)
    manifest=run_dir / 'RETROSPECTIVE_preprocessing_manifest.json'
    config=run_dir / 'config_monthly_r4.yml'
    output=run_dir / 'panel_monthly_r4'
    manifest.write_text(json.dumps(retrospective_manifest(paths,run_dir),indent=2,ensure_ascii=False),encoding='utf-8')
    config.write_text(yaml.safe_dump(config_document(paths,output,manifest),sort_keys=False,allow_unicode=True),encoding='utf-8')
    audit_report=run_dir / 'r4_live_link_audit.json'
    details={'core_version':__version__,'historical_run_not_retroactively_modified':True,
             'retrospective_manifest':str(manifest), 'retrospective_manifest_sha256':digest(manifest),
             'current_r4_output_directory':str(output),'file_inputs':preflight,
             'scope':'R4 real-input runtime checks and output reconstruction, NOT proof of Stage3-time cogeneration'}
    try:
        actual=run_core(config)
        saved=json.loads((output/'run_manifest.json').read_text(encoding='utf-8-sig'))
        au=saved.get('preprocessing_audit') or {}
        expected = {
            'audit_ok': au.get('status')=='PREPARED_INPUTS_VERIFIED',
            '3_prepared_verified': au.get('prepared_verified_count')==3,
            '6_raw_verified': au.get('raw_verified_count')==6,
            'manifest_input_hashed': saved.get('input_sha256',{}).get(str(manifest))==digest(manifest),
            'audit_manifest_sha256_matches':au.get('manifest_sha256')==digest(manifest),
            'software_version_r4':saved.get('software_version')==__version__,
            'actual_returned_manifest_matches_saved':actual.get('preprocessing_audit')==au,
        }
        comparison=compare_archived_monthly(output/'panel_long.csv',paths['archive_monthly'])
        details.update(status='PASS_LIVE_R4_RETROSPECTIVE_SOURCE_LINK' if all(expected.values()) and comparison['numeric_reproduction_pass']
                       else 'FAIL_RUNTIME_LINK_OR_REPRODUCTION',
                       run_manifest=str(output/'run_manifest.json'), new_r4_runtime_link_proven=all(expected.values()), old_stage5_runtime_link_proven=False, runtime_checks=expected,
                       preprocessing_audit=au, monthly_comparison=comparison)
    except Exception as exc:
        details.update(status='FAIL_RUNTIME_EXCEPTION',exception_type=type(exc).__name__,exception=str(exc))
    audit_report.write_text(json.dumps(details,indent=2,ensure_ascii=False),encoding='utf-8')
    return {'status':details['status'],'output':str(audit_report),
            'run_manifest':details.get('run_manifest'),
            'runtime_checks':details.get('runtime_checks'),
            'monthly_comparison':details.get('monthly_comparison'),
            'exception':details.get('exception'),
            'historical_stage3_r4_cogeneration_proven':False,
            'new_r4_runtime_link_proven':details.get('new_r4_runtime_link_proven',False),
            'old_stage5_runtime_link_proven':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    project=Path(__file__).resolve().parents[1]
    parser.add_argument('--project',type=Path,default=project)
    parser.add_argument('--data-root',type=Path,default=project/'review_data'/'chicago')
    parser.add_argument('--output-root',type=Path,default=project/'review_results'/'real_data_validation'/'r4_live_link')
    parser.add_argument('--check-only',action='store_true')
    args=parser.parse_args()
    try:
        result=run(args.project.resolve(),args.data_root.resolve(),args.output_root.resolve(),args.check_only)
    except Exception as exc:
        result={'status':'FAIL_PREFLIGHT','exception_type':type(exc).__name__,'exception':str(exc)}
    print(json.dumps(result,indent=2,ensure_ascii=False))
    return 0 if result['status'].startswith(('PASS','READY')) else 1


if __name__=='__main__':
    sys.exit(main())
