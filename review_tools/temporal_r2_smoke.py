"""R2 smoke demonstration. No Chicago files or source rasters required."""
import calendar
import json
from pathlib import Path
import sys
import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from geopanel_eo.config import Config, Temporal
from geopanel_eo.temporal import aggregate_time
from geopanel_eo import __version__


def main():
    cfg = yaml.safe_load((ROOT/'examples/T06/config.yml').read_text(encoding='utf-8'))
    var = Config.model_validate(cfg).variables[0]
    rec = [dict(source_time=f'2020-{month:02d}-01', entity_id='A', entity_name='Area A',
        variable_id=var.variable_id, value=float(month), uncertainty=np.nan, qc_flag='OK',
        total_unit_area=100.0, valid_coverage_fraction=1.0, valid_area=100.0,
        contributing_cell_count=10, effective_sample_size=10.0, weight_sum=100.0)
        for month in range(1,13)]
    counts = {f'2020-{month:02d}': calendar.monthrange(2020,month)[1] for month in range(1,13)}
    counts['2020-12'] = 30  # Example upstream source counts, consistent with Daymet leap-year convention
    equal = pd.DataFrame(aggregate_time(rec, var, Temporal())).iloc[0]
    weighted = pd.DataFrame(aggregate_time(rec, var, Temporal(
        mean_weighting='represented_days', represented_day_counts=counts,
        expected_start_month='2019-01', expected_end_month='2021-12')))
    example = dict(candidate_version=__version__, scope='synthetic monthly inputs, not real Chicago values',
       equal_month_2020=float(equal.value), represented_days_2020=float(weighted.loc[weighted.time=='2020','value'].iloc[0]),
       represented_day_count=365, expected_years=weighted.time.tolist(),
       null_years=weighted.loc[weighted.value.isna(),'time'].tolist(),
       legacy_period_support=equal.period_support,
       new_explicit_weighting=weighted.loc[weighted.time=='2020','temporal_mean_weighting'].iloc[0])
    out = ROOT/'review_results'/'temporal_r2_smoke.json'
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(example, indent=2), encoding='utf-8')
    print(json.dumps(example, indent=2))
    return example

if __name__=='__main__':
    main()
