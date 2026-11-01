"""Chicago real-data annual temperature weighting audit (independent review tool).

Run from E:\\GeoPanel-EO after copying this file to review_tools/:
    python review_tools\\chicago_real_temperature_sensitivity.py

Reads archived Stage 5 citywide MONTHLY and ANNUAL panel CSVs and the SIX
unmodified Daymet daily NetCDFs in review_data/chicago. Never changes inputs.
Output: review_results/chicago_real_temperature_{by_community,summary,audit}.(csv/json)

Scientific boundary: Estimates the annual area-weighted time series from the
published MONTHLY polygon estimates. This is not a rerun of raster reprojection,
nor is it a replacement for daily-grid source-product validation.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import xarray as xr


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def find_stage5(root: Path) -> Path:
    candidates = []
    for run in sorted((root / "stage5_results").glob("run_*"), reverse=True):
        a = run / "panel_daymet_monthly" / "panel_long.csv"
        b = run / "panel_daymet_annual" / "panel_long.csv"
        report = run / "stage5_report.json"
        if not (a.is_file() and b.is_file() and report.is_file()):
            continue
        if json.loads(report.read_text(encoding="utf-8")).get("status") == "PASS":
            candidates.append(run)
    if not candidates:
        raise FileNotFoundError(
            "No complete PASS Stage 5 run (panel_daymet_monthly, panel_daymet_annual, stage5_report.json) "
            f"found under {root / 'stage5_results'}"
        )
    return candidates[0]


def source_day_counts(root: Path) -> tuple[dict, dict, dict]:
    """Read actual source time coordinates, not assumed civil-calendar durations."""
    counts = {}
    audits = {}
    hashes = {}
    for variable in ("tmin", "tmax", "prcp"):
        for year in (2019, 2020):
            p = root / "data" / "raw" / f"daymet_v4_daily_na_{variable}_{year}_chicago.nc"
            if not p.is_file():
                raise FileNotFoundError(f"Missing actual Daymet raw file: {p}")
            hashes[str(p)] = sha256_file(p)
            with xr.open_dataset(p, engine="h5netcdf") as ds:
                if "time" not in ds.coords or variable not in ds:
                    raise ValueError(f"Missing source 'time' or '{variable}' in {p}")
                dates = pd.DatetimeIndex(ds["time"].values)
                if dates.hasnans or dates.has_duplicates or not dates.is_monotonic_increasing:
                    raise ValueError(f"Missing, duplicate or unsorted source time values in {p}")
                if len(dates) != 365 or any(d.year != year for d in dates):
                    raise ValueError(f"Unexpected source dates: {p.name}: {len(dates)} for {year}")
                source_days = dates.normalize()
                if source_days.has_duplicates:
                    raise ValueError(f"Multiple observations per calendar date: {p.name}")
                for month in range(1, 13):
                    key = (variable, year, month)
                    n = int((dates.month == month).sum())
                    if n <= 0:
                        raise ValueError(f"Missing source month: {key}")
                    counts[key] = n
                audits[f"{variable}_{year}"] = {
                    "file": str(p), "n_observation_dates": int(len(dates)),
                    "first_date": str(dates[0].date()), "last_date": str(dates[-1].date()),
                    "feb29_present": bool(pd.Timestamp(f"{year}-02-29") in source_days) if year == 2020 else None,
                    "dec31_present": bool(pd.Timestamp(f"{year}-12-31") in source_days),
                    "monthly_source_day_counts": {f"{month:02d}": counts[(variable,year,month)] for month in range(1,13)},
                }
    for year in (2019, 2020):
        ref = [counts[("tmax",year,m)] for m in range(1,13)]
        for variable in ("tmin", "prcp"):
            if ref != [counts[(variable,year,m)] for m in range(1,13)]:
                raise ValueError(f"Daymet source calendars do not match for {variable} {year}")
    return counts, audits, hashes


def load_panel(path: Path, date_level: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"entity_id":str,"time":str})
    necessary = {"entity_id", "time", "variable_id", "value"}
    if not necessary.issubset(df):
        raise ValueError(f"Missing {sorted(necessary - set(df))} in {path}")
    df = df[df.variable_id.isin(["tmax", "tmin"])].copy()
    if df.empty or df.value.isna().any():
        raise ValueError(f"Missing or null temperature values in {path}")
    if "qc_flag" in df:
        bad = df.qc_flag.astype(str).str.contains("LOW_COVERAGE|PARTIAL_TIME_WINDOW|NO_VALID_DATA",regex=True)
        if bad.any():
            raise ValueError(f"Invalid quality flags in {path}: {df.loc[bad,'qc_flag'].unique().tolist()}")
    dt = pd.to_datetime(df.time, errors="raise")
    df["year"] = dt.dt.year
    df["month"] = dt.dt.month
    keys = ["entity_id","variable_id","year","month"] if date_level == "monthly" else ["entity_id","variable_id","year"]
    if df.duplicated(keys).any():
        raise ValueError(f"Duplicate {date_level} panel keys in {path}")
    if not set(df.year) == {2019, 2020}:
        raise ValueError(f"Expected source years 2019 and 2020 in {path}: {set(df.year)}")
    return df


def summarize(monthly:pd.DataFrame, annual:pd.DataFrame, counts:dict) -> tuple[pd.DataFrame,pd.DataFrame]:
    rows = []
    for (entity, variable, year), group in monthly.groupby(["entity_id","variable_id","year"], sort=True):
        group = group.sort_values("month")
        if group.month.tolist() != list(range(1,13)):
            raise ValueError(f"Incomplete months: {entity} {variable} {year}: {group.month.tolist()}")
        values = group.value.to_numpy(dtype=float)
        if not np.isfinite(values).all():
            raise ValueError(f"Nonfinite values in {entity} {variable} {year}")
        weights = np.asarray([counts[(variable, int(year), int(m))] for m in group.month],dtype=float)
        rows.append({"entity_id":entity,"variable_id":variable,"year":int(year),
                     "n_months":12,"source_day_count":int(weights.sum()),
                     "equal_month_C":float(values.mean()),
                     "represented_day_weighted_C":float(np.average(values,weights=weights))})
    result = pd.DataFrame(rows)
    if len(result)!=77*2*2 or result.entity_id.nunique()!=77:
        raise ValueError(f"Expected 308 temperature community-year combinations across 77 units; got {len(result)} / {result.entity_id.nunique()}")
    orig = annual[["entity_id","variable_id","year","value"]].rename(columns={"value":"archived_annual_C"})
    result = result.merge(orig,on=["entity_id","variable_id","year"],validate="one_to_one",how="left")
    if result.archived_annual_C.isna().any() or len(orig)!=len(result):
        raise ValueError("Archived annual panel key mismatch")
    result["weighted_minus_equal_C"] = result.represented_day_weighted_C - result.equal_month_C
    result["absolute_weighting_difference_C"] = result.weighted_minus_equal_C.abs()
    result["archive_minus_recomputed_equal_C"] = result.archived_annual_C - result.equal_month_C
    result["absolute_archive_difference_C"] = result.archive_minus_recomputed_equal_C.abs()
    summary = result.groupby(["variable_id","year"],as_index=False).agg(
         n_communities=("entity_id","nunique"),
         represented_days=("source_day_count","first"),
         mean_absolute_weighting_difference_C=("absolute_weighting_difference_C","mean"),
         median_absolute_weighting_difference_C=("absolute_weighting_difference_C","median"),
         max_absolute_weighting_difference_C=("absolute_weighting_difference_C","max"),
         mean_signed_weighted_minus_equal_C=("weighted_minus_equal_C","mean"),
         max_archive_discrepancy_C=("absolute_archive_difference_C","max"))
    return result, summary


def run(root: Path, out: Path) -> dict:
    root, out = root.resolve(), out.resolve()
    stage5 = find_stage5(root)
    month_path = stage5 / "panel_daymet_monthly" / "panel_long.csv"
    year_path = stage5 / "panel_daymet_annual" / "panel_long.csv"
    counts, calendars, source_hashes = source_day_counts(root)
    monthly = load_panel(month_path,"monthly")
    annual = load_panel(year_path,"annual")
    detail, summary = summarize(monthly,annual,counts)
    if detail.absolute_archive_difference_C.max() > 1e-6:
        status = "ARCHIVED_EQUAL_MONTH_REPRODUCTION_MISMATCH"
    else:
        status = "PASS"
    out.mkdir(parents=True,exist_ok=True)
    path1 = out / "chicago_real_temperature_by_community.csv"
    path2 = out / "chicago_real_temperature_summary.csv"
    path3 = out / "chicago_real_temperature_audit.json"
    detail.to_csv(path1,index=False,float_format="%.12g")
    summary.to_csv(path2,index=False,float_format="%.12g")
    audit = {
      "status":status,"scope":"Real Chicago 77-unit monthly-panel temporal sensitivity; no raster resampling validation",
      "source_stage5_run":str(stage5),
      "source_daymet_date_audit":calendars,
      "sha256_raw_daymet":source_hashes,
      "panel_source_files":{"monthly":str(month_path),"annual":str(year_path)},
      "panel_source_sha256":{"monthly":sha256_file(month_path),"annual":sha256_file(year_path)},
      "community_year_variable_comparisons":int(len(detail)),
      "n_communities":int(detail.entity_id.nunique()),
      "max_original_annual_vs_recomputed_equal_C":float(detail.absolute_archive_difference_C.max()),
      "not_validated":[
        "raster spatial extraction and resampling sensitivity",
        "whether source monthly raster means represent an identical valid spatial support every month",
        "pixel-level missing daily data and error covariance",
        "full-day daily raster aggregation equality at every pixel",
      ],
      "outputs":{"by_community":str(path1),"summary":str(path2)},
      "note":"Never use Celsius percent differences; absolute and signed differences remain in degrees C."
    }
    path3.write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding="utf-8")
    print(summary.to_string(index=False,float_format=lambda x:f"{x:.8f}"))
    print(f"\nStatus: {status}\nSource: {stage5}\nDetails: {path1}\nSummary: {path2}\nAudit: {path3}")
    if status != "PASS":
        raise ValueError("Archived annual means do not match recomputed equal-month results; investigate before citing new results")
    return audit


if __name__=="__main__":
    import argparse
    cli=argparse.ArgumentParser(description=__doc__)
    project=Path(__file__).resolve().parents[1]
    cli.add_argument("--data-root",type=Path,default=project/"review_data"/"chicago")
    cli.add_argument("--out",type=Path,default=project/"review_results"/"real_data_validation")
    args=cli.parse_args()
    try:
        run(args.data_root,args.out)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}",file=sys.stderr)
        sys.exit(1)
