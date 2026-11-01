"""Revision R0 evidence: explicit read-only behavior checks. NOT the original 42 tests."""
import copy
from pathlib import Path
import sys
import numpy as np
import pandas as pd
import pytest
import yaml

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from geopanel_eo.config import Config
from geopanel_eo.temporal import aggregate_time, output_temporal_support
from review_tools.daymet_month_weight_sensitivity import days_daymet


def test_temperature_sum_rejected_by_contract():
    cfg=yaml.safe_load((ROOT/'examples/T06/config.yml').read_text(encoding='utf8'))
    cfg['variables'][0]['temporal_reducer']='sum'
    with pytest.raises(ValueError,match='SEMANTIC_RULE'):
        Config.model_validate(cfg)


def test_annual_temperature_semantic_label_in_existing_version():
    cfg=yaml.safe_load((ROOT/'examples/T06/config.yml').read_text(encoding='utf8'))
    parsed=Config.model_validate(cfg)
    assert parsed.temporal.mean_weighting=='equal_month'
    assert output_temporal_support(parsed.variables[0])=='annual_mean'
    # Documents an ambiguity to FIX next; this is not a claim that metadata is sufficient.


def test_daymet_2020_supplied_days_assumption():
    assert [days_daymet(2020,m) for m in (2,12)]==[29,30]
    assert sum(days_daymet(2020,m) for m in range(1,13))==365
    assert sum(days_daymet(2019,m) for m in range(1,13))==365


def test_missing_internal_year_is_explicit():
    cfg=yaml.safe_load((ROOT/'examples/T06/config.yml').read_text(encoding='utf8'))
    var=Config.model_validate(cfg).variables[0]
    records=[]
    for year in (2019,2021):
        for month in range(1,13):
            records.append(dict(source_time=f'{year}-{month:02d}-01',entity_id='A',entity_name='A',
                                variable_id=var.variable_id,value=float(month),uncertainty=np.nan,
                                qc_flag='OK',total_unit_area=100.0,valid_coverage_fraction=1.0,
                                valid_area=100.0,contributing_cell_count=10,effective_sample_size=10.0,
                                weight_sum=100.0))
    actual=pd.DataFrame(aggregate_time(records,var))
    assert actual.time.tolist()==['2019','2020','2021']
    assert pd.isna(actual.loc[1,'value'])
    assert 'PARTIAL_TIME_WINDOW' in actual.loc[1,'qc_flag']
    assert actual.loc[0,'value']==pytest.approx(6.5)
    assert actual.loc[2,'value']==pytest.approx(6.5)


def test_same_coverage_can_have_changed_spatial_support():
    v=np.array([0.0]*5+[10.0]*5)
    a=np.array([False]+[True]*9)
    b=np.array([True]*9+[False])
    assert a.mean()==pytest.approx(0.9)
    assert b.mean()==pytest.approx(0.9)
    assert (a&b).mean()==pytest.approx(0.8)
    assert v[a].mean()-v[b].mean()==pytest.approx(10/9)


def test_celsius_relative_difference_is_origin_dependent():
    a,b=10.0,10.5
    apd_c=100*abs(a-b)/abs(a)
    apd_k=100*abs(a-b)/(a+273.15)
    assert apd_c != pytest.approx(apd_k)
    assert abs((a+273.15)-(b+273.15))==pytest.approx(abs(a-b))


def test_archived_case_has_unique_expected_keys():
    path=ROOT/'case_studies/chicago/results/combined_panel_long.csv'
    df=pd.read_csv(path,dtype={'entity_id':str,'time':str})
    assert len(df)==5544
    assert df.entity_id.nunique()==77
    assert df.variable_id.nunique()==36
    assert not df.duplicated(['entity_id','time','variable_id']).any()
