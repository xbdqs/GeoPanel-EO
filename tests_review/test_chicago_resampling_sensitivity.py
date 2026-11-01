"""Independent tests for the Chicago resampling review script.

Run from project root: python -m pytest tests_review/test_chicago_resampling_sensitivity.py -q
"""
from __future__ import annotations
import importlib.util
from pathlib import Path
import numpy as np
import pandas as pd
from pyproj import CRS
import rasterio

MODULE = Path(__file__).resolve().parents[1] / 'review_tools' / 'chicago_real_resampling_sensitivity.py'
spec=importlib.util.spec_from_file_location('chicago_real_resampling_sensitivity', MODULE)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_identity_reprojection_all_three_kernels():
    source = np.array([[[1.,2.],[3.,4.]]],dtype='float32')
    a = rasterio.Affine(1000,0,0,0,-1000,2000)
    for k in mod.METHODS.values():
        got=mod.reproject_stack(source,a,CRS.from_epsg(6933),a,(2,2),k)
        np.testing.assert_array_equal(source,got)


def test_monthly_exact_weighted_mean_and_missing_coverage():
    unit={'entity_id':'01','indices':np.asarray([0,1],dtype=int),
          'weight_m2':np.asarray([50,50],dtype=float),'polygon_area_m2':100.0}
    stack=np.asarray([[[2.,4.]],[[3.,np.nan]]],dtype='float32')
    timestamps=pd.DatetimeIndex(['2019-01-01','2019-02-01'])
    got=mod.polygon_monthly(stack,[unit],'tmax',timestamps,'test',min_coverage=.95)
    assert got.loc[0,'value']==3.0
    assert np.isnan(got.loc[1,'value'])
    assert got.loc[1,'qc_flag']=='LOW_COVERAGE'


def test_changing_support_detects_relocation_of_valid_pixels():
    # Over 10 spatial cells, monthly 90% cover with omitted cell changing.
    arr=np.tile(np.arange(10,dtype='float32'),(24,1,1))
    arr[:12,0,0]=np.nan
    arr[1,0,0]=0.0
    arr[1,0,9]=np.nan
    # 2019 intersection and union: 8/10 and 10/10; min 90%.
    units=[{'entity_id':'01','indices':np.arange(10,dtype=int),
            'weight_m2':np.full(10,10,dtype=float),'polygon_area_m2':100.0}]
    times=pd.DatetimeIndex([f'{y}-{m:02d}-01' for y in (2019,2020) for m in range(1,13)])
    data=mod.spatial_overlap(arr,units,'tmax',times)
    row=data.loc[data.year==2019].iloc[0]
    assert abs(row.common_valid_fraction-.8)<1e-12
    assert abs(row.union_valid_fraction-1.0)<1e-12
    assert row.support_status=='CHANGING_SUPPORT'


def test_equal_month_annual_and_precipitation_total():
    times=pd.DatetimeIndex([f'2019-{m:02d}-01' for m in range(1,13)])
    rows=[]
    for variable in ['tmax','prcp']:
        for m,t in enumerate(times,1):
            rows.append({'entity_id':'01','time':str(t.date()),'variable_id':variable,
                         'method':'bilinear','value':float(m)})
    result=mod.combine_annual(pd.DataFrame(rows))
    assert result.loc[result.variable_id=='tmax','annual_value'].iloc[0]==6.5
    assert result.loc[result.variable_id=='prcp','annual_value'].iloc[0]==78.0


def test_daymet_2020_calendar_accepts_nonmidnight_time_of_day():
    # The original raw time axis has calendar dates; matching full timestamps
    # directly fails if the observation time is 12:00 instead of 00:00.
    dates = pd.date_range("2020-01-01", periods=365, freq="D") + pd.Timedelta(hours=12)
    got = mod.validate_daymet_daily_dates(dates.to_numpy(), 2020, "noon_2020.nc")
    assert len(got) == 365
    assert pd.Timestamp("2020-02-29") in got
    assert pd.Timestamp("2020-12-31") not in got
    assert got[-1] == pd.Timestamp("2020-12-30")


def test_daymet_2019_calendar_ends_dec31():
    dates = pd.date_range("2019-01-01", periods=365, freq="D")
    got = mod.validate_daymet_daily_dates(dates.to_numpy(), 2019, "2019.nc")
    assert got[-1] == pd.Timestamp("2019-12-31")


def test_daymet_calendar_rejects_missing_date_even_when_count_365():
    import pytest
    dates = pd.date_range("2020-01-01", periods=365, freq="D").to_list()
    dates[58] = dates[58] + pd.Timedelta(days=1)  # duplicate Feb 29; omit Feb 28
    with pytest.raises(ValueError, match="Unexpected Daymet calendar"):
        mod.validate_daymet_daily_dates(dates, 2020, "bad.nc")
