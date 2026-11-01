"""Controlled comparison of a simple unchecked pandas workflow with GeoPanel-EO v0.1.0.

Reviewer-7 pilot: isolates temporal and semantic safeguards using EXACTLY THE SAME
PRE-AGGREGATED MONTHLY INPUT RECORDS; it does not benchmark exactextract or any
spatial estimator. Unchecked pandas is an illustrative failure-prone workflow,
NOT a claim that pandas cannot implement the same checks and NOT a leading baseline.

Runs without source raster inputs and does not modify any GeoPanel-EO code.
"""
from __future__ import annotations
import argparse
import csv
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from geopanel_eo.config import Config
from geopanel_eo.temporal import aggregate_time


def config_variable(case: str):
    cfg = yaml.safe_load((ROOT / 'examples' / case / 'config.yml').read_text(encoding='utf-8'))
    return cfg, Config.model_validate(cfg).variables[0]


def make_records(variable_id: str, years=(2020,), exclude=frozenset(), invalid=frozenset()):
    """Monthly records are hypothetical outputs of a spatial extraction step."""
    ans = []
    for year in years:
        for m in range(1, 13):
            if (year, m) in exclude:
                continue
            bad = (year, m) in invalid
            ans.append(dict(source_time=f'{year}-{m:02d}-01', entity_id='001',
                entity_name='Synthetic unit', variable_id=variable_id,
                value=np.nan if bad else float(m-1), uncertainty=np.nan,
                qc_flag='LOW_COVERAGE' if bad else 'OK', total_unit_area=100.0,
                valid_coverage_fraction=0.5 if bad else 1.0,
                valid_area=50.0 if bad else 100.0,
                contributing_cell_count=5 if bad else 10, effective_sample_size=5.0 if bad else 10.0,
                weight_sum=50.0 if bad else 100.0))
    return ans


def ordinary_pandas(records, reducer):
    """A deliberately UNCHECKED illustration: skips nulls and has no expected calendar."""
    df = pd.DataFrame(records)
    df['year'] = pd.to_datetime(df.source_time).dt.year
    fn = 'sum' if reducer == 'sum' else 'mean'
    out = df.groupby('year').value.agg(fn)
    return {str(year): float(val) for year, val in out.items()}


def out_row(scenario, comparison, expected, baseline, guarded, flag, conclusion):
    return dict(scenario=scenario, comparison=comparison, scientific_expected=expected,
       unchecked_pandas=baseline, geopanel_v010=guarded, geopanel_qc_flag=flag, interpretation=conclusion)


def evaluate():
    tcfg, temp = config_variable('T06')
    pcfg, precip = config_variable('T05')
    rows=[]
    records=make_records(temp.variable_id)
    baseline=ordinary_pandas(records, 'mean')
    guarded=pd.DataFrame(aggregate_time(records,temp)).set_index('time')
    rows.append(out_row('complete_months_temperature','Both workflows with full 12-month data',
        5.5,baseline['2020'],float(guarded.loc['2020','value']),str(guarded.loc['2020','qc_flag']),
        'Positive control: both pipelines agree on an admissible equal-month mean'))

    # A user can write .sum() in pandas; the SAME wrong reducer is inadmissible in the GeoPanel config.
    rawsum=ordinary_pandas(records,'sum')['2020']
    changed=yaml.safe_load(yaml.safe_dump(tcfg))
    changed['variables'][0]['temporal_reducer']='sum'
    caught=False; code=''
    try: Config.model_validate(changed)
    except ValueError as exc:
        caught = 'SEMANTIC_RULE' in str(exc)
        code = 'SEMANTIC_RULE' if caught else type(exc).__name__
    assert caught, 'Semantic guard unexpectedly allowed temperature sum'
    rows.append(out_row('temperature_sum_configuration','Temperature as an intensive state; incorrect annual sum',
        5.5,rawsum,None,code,'Inadmissible sum is rejected before computing an annual value'))

    # Missing December (a terminal month) must not silently shorten 12-month support.
    records=make_records(temp.variable_id,exclude={(2020,12)})
    baseline=ordinary_pandas(records, 'mean')
    guarded=pd.DataFrame(aggregate_time(records,temp)).set_index('time')
    assert pd.isna(guarded.loc['2020','value'])
    assert 'PARTIAL_TIME_WINDOW' in guarded.loc['2020','qc_flag']
    rows.append(out_row('missing_december','December missing in otherwise complete 2020',
        None,baseline['2020'],None,str(guarded.loc['2020','qc_flag']),
        'Unchecked mean averages 11 months; GeoPanel retains null 2020 value'))

    # An entire absent internal calendar year disappears from ordinary groupby.
    records=make_records(precip.variable_id,years=(2019,2021))
    # Use values 0..11; expected annual sum=66 for actual observed years.
    baseline=ordinary_pandas(records,'sum')
    guarded=pd.DataFrame(aggregate_time(records,precip)).set_index('time')
    assert '2020' not in baseline and '2020' in guarded.index
    assert pd.isna(guarded.loc['2020','value'])
    rows.append(out_row('missing_internal_year','2019 and 2021 present; entire 2020 missing',
        None,None,None,str(guarded.loc['2020','qc_flag']),
        'Unchecked pandas omits 2020 key; GeoPanel retains a null year with diagnostic'))

    # An upstream spatial estimator has already rejected one insufficient-coverage month.
    records=make_records(temp.variable_id,invalid={(2020,4)})
    baseline=ordinary_pandas(records,'mean')
    guarded=pd.DataFrame(aggregate_time(records,temp)).set_index('time')
    assert pd.isna(guarded.loc['2020','value'])
    assert 'PARTIAL_TIME_WINDOW' in guarded.loc['2020','qc_flag']
    rows.append(out_row('upstream_low_coverage_month','April flagged low coverage and value set null upstream',
        None,baseline['2020'],None,str(guarded.loc['2020','qc_flag']),
        'Spatial rejection is ASSUMED, not tested here; temporal contract prevents silent mean'))
    return rows


def export(out:Path):
    out.mkdir(parents=True,exist_ok=True)
    records=evaluate()
    metadata={
      'scope':'Demonstration of semantic and temporal safeguards on identical already spatially aggregated synthetic monthly records',
      'warning':'Not a full baseline against exactextract/xagg or an evaluation of spatial intersection algorithms; the unchecked pandas comparator intentionally omits checks that could be programmed in pandas',
      'software_version':'GeoPanel-EO v0.1.0 unchanged',
      'n_scenarios':len(records),
      'results':records,
      'not_tested':['cross-month spatial-overlap detection','actual Chicago source-to-raster preprocessing','day-weighted aggregation','full end-to-end alternative workflow','runtime performance']}
    (out/'contract_comparison_r1.json').write_text(json.dumps(metadata,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    with (out/'contract_comparison_r1.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=list(records[0]));w.writeheader();w.writerows(records)
    print(json.dumps(metadata,indent=2,ensure_ascii=False,allow_nan=False))
    return metadata

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,default=ROOT/'review_results')
    export(parser.parse_args().out)
