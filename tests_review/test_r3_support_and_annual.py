"""R3 deterministic support overlap and annual-product contract cases."""
from pathlib import Path
import pandas as pd
import pytest
import yaml
from geopanel_eo.support import spatial_support_diagnostics
from geopanel_eo.config import Config
from geopanel_eo.temporal import output_temporal_support

ROOT = Path(__file__).resolve().parents[1]

def variable():
    raw = yaml.safe_load((ROOT / 'examples/T06/config.yml').read_text(encoding='utf8'))
    return Config.model_validate(raw).variables[0]

def support(month, invalid_cell, grid='A', status=True):
    # 10 equal area cells, with 9 supported each month.
    cells={i:10.0 for i in range(10) if i != invalid_cell}
    return dict(entity_id='u',source_month=f'2020-{month:02d}',grid_signature=(grid,),
                valid_cells=cells,total_unit_area=100.0,spatial_value_usable=status)

def annual_target():
    return [dict(entity_id='u',time='2020')]

def test_same_90_percent_coverage_can_have_80_percent_common_support():
    var=variable().model_copy(update={'temporal_window':variable().temporal_window.model_copy(update={'months':[1,2]})})
    d=spatial_support_diagnostics([support(1,0),support(2,9)],var,annual_target())[0]
    assert d['minimum_monthly_coverage_fraction']==pytest.approx(.9)
    assert d['common_valid_fraction']==pytest.approx(.8)
    assert d['union_valid_fraction']==pytest.approx(1.0)
    assert d['common_union_jaccard']==pytest.approx(.8)
    assert d['support_status']=='CHANGING_SUPPORT'

def test_identical_support_is_consistent():
    var=variable().model_copy(update={'temporal_window':variable().temporal_window.model_copy(update={'months':[1,2]})})
    d=spatial_support_diagnostics([support(1,0),support(2,0)],var,annual_target())[0]
    assert d['common_valid_fraction']==pytest.approx(.9)
    assert d['support_status']=='CONSISTENT_SUPPORT'

def test_missing_month_not_silently_assessed():
    var=variable().model_copy(update={'temporal_window':variable().temporal_window.model_copy(update={'months':[1,2]})})
    d=spatial_support_diagnostics([support(1,0)],var,annual_target())[0]
    assert d['support_status']=='INCOMPLETE_WINDOW'
    assert pd.isna(d['common_valid_fraction'])

def test_different_grids_not_compared():
    var=variable().model_copy(update={'temporal_window':variable().temporal_window.model_copy(update={'months':[1,2]})})
    d=spatial_support_diagnostics([support(1,0),support(2,0,grid='B')],var,annual_target())[0]
    assert d['support_status']=='GRID_MISMATCH'
    assert pd.isna(d['common_union_jaccard'])

def test_low_coverage_status_is_not_misrepresented():
    var=variable().model_copy(update={'temporal_window':variable().temporal_window.model_copy(update={'months':[1,2]})})
    d=spatial_support_diagnostics([support(1,0,status=False),support(2,0)],var,annual_target())[0]
    assert d['support_status']=='INVALID_MONTHLY_VALUE'

def test_annual_product_label_and_validation():
    data=yaml.safe_load((ROOT/'examples/T03/config.yml').read_text(encoding='utf8'))
    v=data['variables'][0]
    v.update(temporal_support='annual_product',temporal_reducer='snapshot',temporal_window={'frequency':'annual','months':[1]})
    parsed=Config.model_validate(data).variables[0]
    assert output_temporal_support(parsed)=='annual_product'
    v['temporal_window']['months']=[1,2]
    with pytest.raises(ValueError,match='ANNUAL_PRODUCT_RULE'):
        Config.model_validate(data)

def test_annual_product_cannot_be_temperature_mean():
    data=yaml.safe_load((ROOT/'examples/T06/config.yml').read_text(encoding='utf8'))
    data['variables'][0]['temporal_support']='annual_product'
    with pytest.raises(ValueError,match='ANNUAL_PRODUCT_RULE'):
        Config.model_validate(data)
