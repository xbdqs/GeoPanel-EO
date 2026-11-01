"""Read-only Chicago Daymet raster preflight for GeoPanel-EO R4.

Run from E:\\GeoPanel-EO:
    python review_tools\\chicago_raster_preflight.py

Reads SIX raw daily Daymet NetCDFs and THREE archived monthly EPSG:6933
NetCDFs. Inspects their dimensions, date support, grid, CRS, SHA-256 identity
and a first/last slice sample. Does NOT change rasters, rerun preprocessing,
validate reprojection quality, or claim identical spatial support.

Outputs, under review_results/real_data_validation:
  chicago_raster_preflight.json
  chicago_raster_inventory.csv
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import xarray as xr
from pyproj import CRS

VARS = ("tmin", "tmax", "prcp")
YEARS = (2019, 2020)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for block in iter(lambda: fp.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def locate_prepared(root: Path) -> dict[str, Path]:
    # Check for a coherent set of three files in one run: do not mix grids
    # or different historical executions by picking files independently.
    runs = sorted(set((root / "stage5_results").glob("run_*")) |
                  set((root / "stage3_results").glob("run_*")),
                  key=lambda p: ("stage5" in str(p), p.name), reverse=True)
    result = {}
    for run in runs:
        prepared = run / "prepared"
        paths = {v: prepared / f"daymet_{v}_monthly_epsg6933.nc" for v in VARS}
        if all(p.is_file() for p in paths.values()):
            result = paths
            break
    if not result:
        raise FileNotFoundError("Could not find all THREE monthly Daymet NetCDF files "
                                "together in a stage3_results/run_*/prepared or "
                                "stage5_results/run_*/prepared directory")
    return result


def grid_metadata(ds: xr.Dataset, variable: str) -> dict:
    if not all(n in ds.coords for n in ("x", "y", "time")):
        raise ValueError(f"{variable}: required x, y, time coordinates missing")
    if variable not in ds:
        raise ValueError(f"{variable}: data variable missing")
    x = np.asarray(ds["x"].values, dtype=np.float64)
    y = np.asarray(ds["y"].values, dtype=np.float64)
    if x.ndim != 1 or y.ndim != 1 or len(x) < 2 or len(y) < 2:
        raise ValueError(f"{variable}: x and y must be rectilinear vectors")
    dx, dy = float(np.median(np.diff(x))), float(np.median(np.diff(y)))
    uniform = bool(np.allclose(np.diff(x), dx, rtol=1e-7, atol=1e-5)
                   and np.allclose(np.diff(y), dy, rtol=1e-7, atol=1e-5))
    mapping_name = ds[variable].attrs.get("grid_mapping", "")
    crs = None
    crs_error = None
    if mapping_name and mapping_name in ds:
        try:
            attrs = dict(ds[mapping_name].attrs)
            if attrs.get("crs_wkt"):
                crs = CRS.from_wkt(attrs["crs_wkt"])
            else:
                crs = CRS.from_cf(attrs)
        except Exception as exc:
            crs_error = f"{type(exc).__name__}: {exc}"
    else:
        crs_error = "Missing CF grid_mapping or coordinate reference variable"
    dates = pd.DatetimeIndex(pd.to_datetime(ds["time"].values))
    if dates.empty or dates.hasnans:
        raise ValueError("Missing or empty date coordinate")
    dims = tuple(str(x) for x in ds[variable].dims)
    if not {"time", "x", "y"}.issubset(dims):
        raise ValueError(f"{variable}: unexpected dimension order/names {dims}")
    def preview(index: int) -> dict:
        # Avoid loading the whole 24-month stack into memory.
        arr = np.asarray(ds[variable].isel(time=index).values)
        valid = np.isfinite(arr)
        return {"finite_fraction": float(np.mean(valid)),
                "nan_or_inf_count": int(arr.size - np.count_nonzero(valid)),
                "finite_min": float(np.min(arr[valid])) if np.any(valid) else None,
                "finite_max": float(np.max(arr[valid])) if np.any(valid) else None}
    return {
        "dims": list(dims), "shape": [int(n) for n in ds[variable].shape],
        "dtype": str(ds[variable].dtype),
        "time_count": int(len(dates)),
        "start_date": str(dates[0].date()), "end_date": str(dates[-1].date()),
        "dates_unique": bool(not dates.has_duplicates),
        "dates_monotonic": bool(dates.is_monotonic_increasing),
        "years": [int(v) for v in sorted(set(dates.year))],
        "monthly_date_counts": {str(y): {f"{m:02d}": int(np.count_nonzero((dates.year == y)&(dates.month == m)))
                                        for m in range(1,13)} for y in sorted(set(dates.year))},
        "x_size": len(x), "y_size": len(y),
        "dx": dx, "dy": dy, "uniform_grid": uniform,
        "affine_top_left_edge": [float(x[0] - dx/2), float(y[0] - dy/2)],
        "x_center_minmax": [float(x.min()), float(x.max())],
        "y_center_minmax": [float(y.min()), float(y.max())],
        "crs": crs.to_string() if crs is not None else None,
        "crs_is_projected": bool(crs.is_projected) if crs is not None else None,
        "crs_error": crs_error,
        "grid_mapping": str(mapping_name),
        "variable_units": ds[variable].attrs.get("units"),
        "variable_stage3_support": ds[variable].attrs.get("stage3_temporal_aggregation"),
        "variable_encoding_fill": str(ds[variable].encoding.get("_FillValue")),
        "first_slice": preview(0), "last_slice": preview(-1),
    }


def run(root: Path, out: Path) -> dict:
    root, out = root.resolve(), out.resolve()
    source_paths = {
        (v, y): root / "data" / "raw" / f"daymet_v4_daily_na_{v}_{y}_chicago.nc"
        for v in VARS for y in YEARS
    }
    prepared_paths = locate_prepared(root)
    all_paths = [("raw_daily", v, y, p) for (v,y),p in source_paths.items()]
    all_paths += [("prepared_monthly", v, None, p) for v,p in prepared_paths.items()]
    files, errors = [], []
    for kind, variable, year, path in all_paths:
        if not path.is_file():
            raise FileNotFoundError(f"Missing {kind} file: {path}")
        with xr.open_dataset(path, engine="h5netcdf", decode_times=True) as ds:
            meta = grid_metadata(ds, variable)
        if kind == "raw_daily":
            # Daymet leap-year convention is independently verified from dates.
            counts = meta["monthly_date_counts"].get(str(year), {})
            if meta["time_count"] != 365 or any(counts.get(f"{m:02d}", 0) < 1 for m in range(1,13)):
                errors.append(f"Incomplete {kind} time: {variable} {year}")
            if meta["years"] != [year]:
                errors.append(f"Unexpected source years for {variable} {year}: {meta['years']}")
        else:
            if meta["time_count"] != 24 or meta["years"] != [2019,2020]:
                errors.append(f"Unexpected prepared time support for {variable}")
            if not meta["uniform_grid"] or not (meta["crs"] and "6933" in meta["crs"]):
                errors.append(f"Prepared grid/CRS needs review for {variable}")
        files.append({"kind": kind, "variable": variable, "year": year,
                      "path": str(path), "size_bytes": path.stat().st_size,
                      "sha256": digest(path), "metadata": meta})

    raw = [f for f in files if f["kind"] == "raw_daily"]
    pre = [f for f in files if f["kind"] == "prepared_monthly"]
    def gridkey(f):
        m=f["metadata"]
        return (m["crs"],m["x_size"],m["y_size"],
                round(m["dx"],6),round(m["dy"],6),
                *(round(x,4) for x in m["affine_top_left_edge"]))
    raw_grid_equal = len(set(map(gridkey,raw))) == 1
    prepared_grid_equal = len(set(map(gridkey,pre))) == 1
    if not raw_grid_equal: errors.append("Raw source grids differ across files")
    if not prepared_grid_equal: errors.append("Three prepared target grids differ")

    stage3_reports = sorted((root / "stage3_results").glob("run_*/stage3_report.json"), reverse=True)
    stage5_reports = sorted((root / "stage5_results").glob("run_*/stage5_report.json"), reverse=True)
    stage5_monthly = sorted((root / "stage5_results").glob("run_*/panel_daymet_monthly/panel_long.csv"), reverse=True)
    archive_date_audit = out / "chicago_real_temperature_audit.json"
    previous_hash_matches = {}
    if archive_date_audit.is_file():
        prev = json.loads(archive_date_audit.read_text(encoding="utf-8"))
        hashes = {Path(k).name: v for k,v in prev.get("sha256_raw_daymet",{}).items()}
        previous_hash_matches = {Path(f["path"]).name: (hashes.get(Path(f["path"]).name) == f["sha256"])
                                 for f in raw}
        if not all(previous_hash_matches.values()):
            errors.append("Raw files differ from previous temporal-audit hashes")

    if not stage5_monthly:
        errors.append("Missing Stage5 archived monthly polygon panel")
    report = {
       "status": "PASS_PREFLIGHT" if not errors else "NEEDS_REVIEW",
       "scope": "File identity, geometry, dates, per-slice finite-value samples. No scientific resampling validation.",
       "created_utc": datetime.now(timezone.utc).isoformat(),
       "data_root": str(root),
       "raw_grid_shared": raw_grid_equal,
       "prepared_grid_shared": prepared_grid_equal,
       "source_sha256_match_previous_temporal_audit": previous_hash_matches,
       "stage3_reports": [str(p) for p in stage3_reports],
       "stage5_reports": [str(p) for p in stage5_reports],
       "stage5_monthly_panels": [str(p) for p in stage5_monthly],
       "files": files,
       "errors_or_blockers": errors,
       "not_validated": [
         "bilinear reprojection reproduction from raw daily files",
         "alternative resampling kernels on a shared target grid",
         "polygon-level differences on 77 community boundaries",
         "cross-month valid pixel overlap or reprojected raster conservation",
       ],
    }
    out.mkdir(parents=True, exist_ok=True)
    dest_json = out / "chicago_raster_preflight.json"
    dest_csv = out / "chicago_raster_inventory.csv"
    dest_json.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    rows=[]
    for f in files:
        m=f["metadata"]
        rows.append({**{k:v for k,v in f.items() if k!="metadata"},
                     "time_count":m["time_count"], "first_date":m["start_date"],
                     "last_date":m["end_date"], "shape":str(m["shape"]),
                     "crs":m["crs"],"x_size":m["x_size"], "y_size":m["y_size"],
                     "dx":m["dx"], "dy":m["dy"],
                     "uniform_grid":m["uniform_grid"],
                     "first_slice_valid_fraction":m["first_slice"]["finite_fraction"],
                     "last_slice_valid_fraction":m["last_slice"]["finite_fraction"]})
    pd.DataFrame(rows).to_csv(dest_csv, index=False)
    print(json.dumps({
       "status": report["status"], "raw_daily_files":len(raw),
       "prepared_monthly_files":len(pre),
       "raw_grid_shared":raw_grid_equal,"prepared_grid_shared":prepared_grid_equal,
       "raw_hash_matches_previous_audit":previous_hash_matches,
       "stage3_reports":len(stage3_reports),
       "stage5_monthly_panels":len(stage5_monthly),
       "issues":errors,"report":str(dest_json),"inventory":str(dest_csv)
       },indent=2,ensure_ascii=False))
    return report

if __name__=="__main__":
    project=Path(__file__).resolve().parents[1]
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root",type=Path,default=project/"review_data"/"chicago")
    ap.add_argument("--out",type=Path,default=project/"review_results"/"real_data_validation")
    args=ap.parse_args()
    try:
        result=run(args.data_root,args.out)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}",file=sys.stderr)
        sys.exit(1)
    sys.exit(0 if result["status"]=="PASS_PREFLIGHT" else 2)
