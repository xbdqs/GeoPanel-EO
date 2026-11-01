"""Reproducible manual truth for Reviewer 7's changing-spatial-support example.

This is a controlled mathematical demonstration, not an observation of actual
Chicago data and not a proof that v0.1.0 detects support shifts.
"""
import argparse
from pathlib import Path
import csv
import json

V=[0.0]*5+[10.0]*5
M1=[False]+[True]*9
M2=[True]*9+[False]

def measure(mask):
    values=[v for v,yes in zip(V,mask) if yes]
    return sum(values)/len(values),sum(mask)/len(mask)

def run(outfile):
    first,c1=measure(M1);last,c2=measure(M2)
    overlap=sum(a and b for a,b in zip(M1,M2))/len(V)
    ans=[{'month':'month_1','coverage':c1,'valid_mean':first,'common_valid_fraction':overlap},
         {'month':'month_2','coverage':c2,'valid_mean':last,'common_valid_fraction':overlap}]
    assert abs(c1-.9)<1e-12 and abs(c2-.9)<1e-12
    assert abs(overlap-.8)<1e-12
    assert abs(first-50/9)<1e-12 and abs(last-40/9)<1e-12
    outfile.parent.mkdir(parents=True,exist_ok=True)
    with outfile.open('w',newline='',encoding='utf8') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(ans[0]));writer.writeheader();writer.writerows(ans)
    print(json.dumps({'monthly_results':ans,'mean_difference':first-last,
        'interpretation':'Identical spatial field, changing observed support; minimum coverage alone cannot detect it.'},indent=2))

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--out',type=Path,default=Path(__file__).resolve().parents[1]/'review_results'/'changing_support_manual_truth.csv')
    run(a.parse_args().out)
