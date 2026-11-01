"""Compare equal-month and observed-day-weighted annual temperature summaries.

Requires a real MONTHLY climate panel with entity_id, time/source_time,
variable_id, value. This is a sensitivity analysis, not a reproduction of
source-day raster processing. Product-specific Daymet daily support is used:
2020 has Feb 29 and lacks Dec 31 (365 source days).
"""
from __future__ import annotations
import argparse
from pathlib import Path
import json
import numpy as np
import pandas as pd


def days_daymet(year,month):
    days=pd.Period(f'{year}-{month:02d}',freq='M').days_in_month
    if year%4==0 and (year%100!=0 or year%400==0):
        # Daymet leap-year convention: December 31 omitted (verify against original source times).
        if month==12:days-=1
    return days


def analyze(csv_path):
    df=pd.read_csv(csv_path,dtype={'entity_id':str})
    needed={'entity_id','variable_id','value'}
    if not needed.issubset(df):raise ValueError(f'Missing columns {sorted(needed-set(df))}')
    time='time' if 'time' in df else 'source_time' if 'source_time' in df else None
    if not time:raise ValueError('Provide monthly time or source_time column')
    date=pd.to_datetime(df[time],errors='raise')
    df['year']=date.dt.year;df['month']=date.dt.month
    df=df[df.variable_id.isin(['tmin','tmax'])].copy()
    if df.empty:raise ValueError('No tmin or tmax rows')
    if df.duplicated(['entity_id','variable_id','year','month']).any():raise ValueError('Duplicate entity/variable/year/month keys')
    rows=[]
    for (entity,variable,year),grp in df.groupby(['entity_id','variable_id','year']):
        grp=grp.sort_values('month')
        if grp.month.tolist()!=list(range(1,13)) or grp.value.isna().any():
            raise ValueError(f'Incomplete or null monthly climate: {entity}, {variable}, {year}; cannot silently compare')
        if 'qc_flag' in grp and grp.qc_flag.astype(str).str.contains('PARTIAL_TIME_WINDOW|LOW_COVERAGE|NO_VALID_DATA').any():
            raise ValueError(f'Monthly QC invalid: {entity}, {variable}, {year}')
        x=grp.value.to_numpy(dtype=float)
        counts=np.array([days_daymet(int(year),int(m)) for m in grp.month],dtype=float)
        equal=float(x.mean());weighted=float(np.average(x,weights=counts))
        rows.append({'entity_id':entity,'variable_id':variable,'year':int(year),
          'months':12,'represented_days_assumed':int(counts.sum()),
          'equal_month_C':equal,'day_count_weighted_C':weighted,
          'weighted_minus_equal_C':weighted-equal,
          'absolute_difference_C':abs(weighted-equal),
          'calendar_assumption':'Daymet supplied-day convention; verify input dates'})
    return pd.DataFrame(rows)

if __name__=='__main__':
    a=argparse.ArgumentParser()
    a.add_argument('--monthly-csv',type=Path,required=True)
    a.add_argument('--out',type=Path,default=Path(__file__).resolve().parents[1]/'review_results'/'daymet_weights.csv')
    args=a.parse_args()
    table=analyze(args.monthly_csv);args.out.parent.mkdir(parents=True,exist_ok=True)
    table.to_csv(args.out,index=False)
    print(table.groupby(['variable_id','year']).absolute_difference_C.agg(['count','mean','max']).to_string())
    print(f'Output: {args.out}')
