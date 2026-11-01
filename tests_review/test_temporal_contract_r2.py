"""R2: checks for scientific temporal semantics independent of raster I/O."""
import calendar
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
import yaml

from geopanel_eo.config import Config, Temporal
from geopanel_eo.temporal import aggregate_time
from geopanel_eo.errors import GeoPanelError

ROOT = Path(__file__).resolve().parents[1]


def variable():
    data = yaml.safe_load((ROOT/'examples/T06/config.yml').read_text(encoding='utf-8'))
    return Config.model_validate(data).variables[0]


def records(year=2020, keep=None, val=lambda m: float(m), sigma=2.0):
    keep = set(range(1, 13)) if keep is None else set(keep)
    return [dict(entity_id='A', entity_name='Area A', variable_id=variable().variable_id,
                 source_time=f'{year}-{m:02d}-01', value=val(m), uncertainty=sigma,
                 qc_flag='OK', total_unit_area=100, valid_coverage_fraction=1,
                 valid_area=100, contributing_cell_count=10, effective_sample_size=10,
                 weight_sum=100) for m in sorted(keep)]


def daymet_counts(year):
    out = {f'{year}-{m:02d}': calendar.monthrange(year, m)[1] for m in range(1, 13)}
    if calendar.isleap(year):
        out[f'{year}-12'] = 30  # Daymet leap year convention; explicitly declared, not inferred
    return out


def test_legacy_equal_month_is_preserved_and_explicitly_labeled():
    row = pd.DataFrame(aggregate_time(records(), variable(), Temporal())).iloc[0]
    assert row.value == pytest.approx(6.5)
    assert row.period_support == 'annual_mean'  # legacy compatibility
    assert row.temporal_mean_weighting == 'equal_month'  # explicit scientific estimand
    assert pd.isna(row.represented_day_count)


def test_represented_days_2020_daymet_uses_365_not_366():
    counts = daymet_counts(2020)
    obj = Temporal(mean_weighting='represented_days', represented_day_counts=counts)
    row = pd.DataFrame(aggregate_time(records(), variable(), obj)).iloc[0]
    assert row.represented_day_count == 365
    assert row.value == pytest.approx(sum(m*counts[f'2020-{m:02d}'] for m in range(1,13))/365)
    assert row.value != pytest.approx(6.5)
    assert row.temporal_mean_weighting == 'represented_days'


def test_weighted_standard_uncertainty_uses_month_specific_coefficients():
    counts = daymet_counts(2020)
    obj = Temporal(mean_weighting='represented_days', represented_day_counts=counts)
    row = pd.DataFrame(aggregate_time(records(sigma=2), variable(), obj)).iloc[0]
    expected_sigma = np.sqrt(sum((counts[f'2020-{m:02d}']*2)**2 for m in range(1,13)))/365
    assert row.uncertainty == pytest.approx(expected_sigma)


def test_missing_day_count_is_rejected_not_silently_imputed():
    counts = daymet_counts(2020)
    del counts['2020-12']
    obj = Temporal(mean_weighting='represented_days', represented_day_counts=counts)
    with pytest.raises(GeoPanelError, match='DAY_COUNTS_MISSING'):
        aggregate_time(records(), variable(), obj)


@pytest.mark.parametrize('bad', [
    dict(expected_start_month='2020-01'),
    dict(expected_start_month='2021-01', expected_end_month='2020-12'),
    dict(expected_start_month='2020-13', expected_end_month='2021-01'),
    dict(mean_weighting='equal_month', represented_day_counts={'2020-01':31}),
    dict(mean_weighting='represented_days', represented_day_counts={'2020-12':32}),
    dict(mean_weighting='represented_days', represented_day_counts={'2020-02':30}),
])
def test_inadmissible_declared_time_contract_rejected(bad):
    with pytest.raises(ValueError):
        Temporal(**bad)


def test_explicit_outer_years_are_retained_when_source_has_only_internal_year():
    obj = Temporal(expected_start_month='2019-01', expected_end_month='2021-12')
    table = pd.DataFrame(aggregate_time(records(2020), variable(), obj))
    assert table.time.tolist() == ['2019','2020','2021']
    assert pd.isna(table.iloc[0].value) and pd.isna(table.iloc[2].value)
    assert table.iloc[1].value == pytest.approx(6.5)
    assert all('PARTIAL_TIME_WINDOW' in table.iloc[i].qc_flag for i in [0,2])
    assert table.iloc[0].expected_time_count == 12


def test_explicit_bounds_exclude_source_years_outside_target_range():
    obj = Temporal(expected_start_month='2020-01', expected_end_month='2020-12')
    data = records(2019) + records(2020) + records(2021)
    table = pd.DataFrame(aggregate_time(data, variable(), obj))
    assert table.time.tolist() == ['2020']


def test_missing_month_still_null_under_explicit_bounds_and_weights():
    obj = Temporal(mean_weighting='represented_days', represented_day_counts=daymet_counts(2020),
                   expected_start_month='2020-01', expected_end_month='2020-12')
    row = pd.DataFrame(aggregate_time(records(keep=range(1,12)), variable(), obj)).iloc[0]
    assert pd.isna(row.value)
    assert 'PARTIAL_TIME_WINDOW' in row.qc_flag
    assert pd.isna(row.represented_day_count)


def test_no_source_within_explicit_bounds_raises():
    obj = Temporal(expected_start_month='2018-01', expected_end_month='2018-12')
    with pytest.raises(GeoPanelError, match='TIME_BOUNDS_EMPTY'):
        aggregate_time(records(2020), variable(), obj)


def test_snapshot_unchanged_by_weighting_preference():
    var = variable().model_copy(update={'temporal_reducer':'snapshot'})
    obj = Temporal(mean_weighting='represented_days')
    row = pd.DataFrame(aggregate_time(records(), var, obj)).iloc[0]
    assert row.value == 12
    assert row.temporal_mean_weighting == 'not_applicable'
