"""Chicago real Daymet reprojection + polygon sensitivity (read-only inputs).

Run after R4 and the earlier Chicago data copy:
    python review_tools\\chicago_real_resampling_sensitivity.py

Rebuilds 24 monthly grids per variable from the six *daily* Daymet NetCDFs,
reproducing the historical Stage 3 numpy aggregation and rasterio bilinear
reprojection onto the *exact archived target grid*. Compares nearest and
GDAL average on that same grid; extracts polygon means using the SAME
exactextract cell_id/coverage primitives as GeoPanel-EO v0.1.0.

Does not edit the R4 scientific core or source datasets. Results are scientific
sensitivity analyses, not independent ground-truth validation.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import warnings

import numpy as np
import pandas as pd
from pyproj import CRS, Transformer
import rasterio
from rasterio.warp import reproject, Resampling
from shapely.geometry import shape, mapping
from shapely.ops import transform as transform_geometry
import xarray as xr
from exactextract import exact_extract
from exactextract.feature import JSONFeatureSource
from exactextract.raster import NumPyRasterSource

VARIABLES = ("tmin", "tmax", "prcp")
YEARS = (2019, 2020)
METHODS = {"bilinear": Resampling.bilinear,
           "nearest": Resampling.nearest,
           "average": Resampling.average}
TARGET_CRS = CRS.from_epsg(6933)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def dump_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                               allow_nan=False, default=str), encoding="utf-8")


def grid_geometry(ds: xr.Dataset, variable: str):
    x = np.asarray(ds.x, dtype=float)
    y = np.asarray(ds.y, dtype=float)
    if x.ndim != 1 or y.ndim != 1 or x.size < 2 or y.size < 2:
        raise ValueError("Grid must have 1D x/y coordinates of length >= 2")
    dx, dy = float(x[1]-x[0]), float(y[1]-y[0])
    if dx <= 0 or dy >= 0 or not np.allclose(np.diff(x), dx, atol=1e-6) or not np.allclose(np.diff(y), dy, atol=1e-6):
        raise ValueError("Only unrotated uniform north-up grids are supported")
    a = rasterio.Affine(dx, 0, float(x[0]-dx/2), 0, dy, float(y[0]-dy/2))
    var_attrs = ds[variable].attrs
    grid_mapping = var_attrs.get("grid_mapping")
    if not grid_mapping or grid_mapping not in ds:
        raise ValueError(f"Missing CF grid_mapping for {variable}")
    gm = ds[grid_mapping].attrs
    crs = CRS.from_user_input(gm["crs_wkt"]) if gm.get("crs_wkt") else CRS.from_cf(dict(gm))
    return a, crs, (y.size, x.size)


def load_archived(root: Path):
    matches = sorted((root / "stage3_results").glob("run_*/prepared/daymet_tmin_monthly_epsg6933.nc"))
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one archived prepared Stage 3 run; found {len(matches)}")
    folder = matches[0].parent
    grids, hashes, times, transform, shape_ = {}, {}, None, None, None
    for variable in VARIABLES:
        p = folder / f"daymet_{variable}_monthly_epsg6933.nc"
        with xr.open_dataset(p, engine="h5netcdf") as ds:
            a, crs, shp = grid_geometry(ds, variable)
            date = pd.DatetimeIndex(ds.time.values)
            if crs != TARGET_CRS or len(date) != 24 or date.has_duplicates:
                raise ValueError(f"Invalid archived monthly grid/date metadata: {p}")
            expected = pd.DatetimeIndex([f"{y}-{m:02d}-01" for y in YEARS for m in range(1, 13)])
            if not date.equals(expected):
                raise ValueError(f"Unexpected monthly dates in {p}")
            values = np.asarray(ds[variable].values, dtype="float32")
            if values.shape != (24, *shp):
                raise ValueError(f"Unexpected monthly raster shape: {values.shape}")
            if transform is None:
                transform, shape_, times = a, shp, date
            elif a != transform or shp != shape_ or not date.equals(times):
                raise ValueError("Archived three monthly files have different grids/dates")
            grids[variable] = values
            hashes[variable] = sha256_file(p)
    return grids, hashes, times, transform, shape_


def validate_daymet_daily_dates(time_values, year: int, source_name: str = "") -> pd.DatetimeIndex:
    """Require the exact 365 Daymet observation *dates*, ignoring time-of-day.

    Both 2019 and 2020 here have 365 consecutive observation dates starting
    January 1. Thus 2020 includes February 29 and ends December 30, exactly
    as independently established in the archived Daymet source audit.
    """
    timestamps = pd.DatetimeIndex(pd.to_datetime(time_values, errors="raise"))
    if timestamps.hasnans:
        raise ValueError(f"Missing Daymet timestamps: {source_name}")
    days = timestamps.normalize()
    expected = pd.date_range(f"{year}-01-01", periods=365, freq="D")
    if len(days) != 365 or days.has_duplicates or not days.is_monotonic_increasing or not days.equals(expected):
        missing = expected.difference(days).strftime("%Y-%m-%d").tolist()[:5]
        unexpected = days.difference(expected).strftime("%Y-%m-%d").tolist()[:5]
        raise ValueError(
            f"Unexpected Daymet calendar for {source_name or year}: "
            f"n={len(days)}, first={str(days[0]) if len(days) else 'none'}, "
            f"last={str(days[-1]) if len(days) else 'none'}, "
            f"missing_dates={missing}, unexpected_dates={unexpected}, "
            f"duplicate_dates={bool(days.has_duplicates)}"
        )
    return days


def build_native_monthly(root: Path, variable: str, expected_hashes: dict):
    arrays, dates, hashes, counts = [], [], {}, {}
    affine, crs, shape_ = None, None, None
    missing_to_zero = 0
    for year in YEARS:
        p = root / "data" / "raw" / f"daymet_v4_daily_na_{variable}_{year}_chicago.nc"
        h = sha256_file(p)
        hashes[p.name] = h
        if p.name in expected_hashes and expected_hashes[p.name] != h:
            raise ValueError(f"Original daily raster changed since PASS_PREFLIGHT: {p.name}")
        with xr.open_dataset(p, engine="h5netcdf") as ds:
            aa, cc, ss = grid_geometry(ds, variable)
            if affine is None:
                affine, crs, shape_ = aa, cc, ss
            elif aa != affine or cc != crs or ss != shape_:
                raise ValueError(f"Annual native Daymet grids changed: {p.name}")
            # Normalize before date-only checks: source timestamps may have
            # non-midnight times. Verify all 365 Daymet dates, not just the
            # presence of Feb 29 / absence of Dec 31.
            t = validate_daymet_daily_dates(ds.time.values, year, p.name)
            values = np.asarray(ds[variable].values, dtype="float32")
            if values.shape != (365, *shape_):
                raise ValueError(f"Unexpected raw shape: {values.shape}")
            for month in range(1, 13):
                chosen = values[t.month == month]
                if chosen.size == 0:
                    raise ValueError(f"No {year}-{month:02d} dates in {p.name}")
                counts[f"{year}-{month:02d}"] = int(chosen.shape[0])
                if variable == "prcp":
                    missing_to_zero += int(np.count_nonzero(~np.isfinite(chosen).any(axis=0)))
                    aggregated = np.nansum(chosen, axis=0)
                else:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", category=RuntimeWarning)
                        aggregated = np.nanmean(chosen, axis=0)
                arrays.append(np.asarray(aggregated, dtype="float32"))
                dates.append(pd.Timestamp(year=year, month=month, day=1))
    return (np.stack(arrays), pd.DatetimeIndex(dates), affine, crs,
            hashes, counts, missing_to_zero)


def reproject_stack(native, src_transform, src_crs, dst_transform, target_shape, method):
    out = np.full((len(native), *target_shape), np.nan, dtype="float32")
    for j, layer in enumerate(native):
        reproject(source=layer, destination=out[j],
                  src_transform=src_transform, src_crs=src_crs,
                  dst_transform=dst_transform, dst_crs=TARGET_CRS,
                  src_nodata=np.nan, dst_nodata=np.nan,
                  resampling=method, init_dest_nodata=True)
    return out


def polygon_support_weights(boundaries: Path, transform, target_shape):
    raw = json.loads(boundaries.read_text(encoding="utf-8"))
    if raw.get("type") != "FeatureCollection":
        raise ValueError("Chicago units must be a GeoJSON FeatureCollection")
    converter = Transformer.from_crs("EPSG:4326", TARGET_CRS, always_xy=True)
    features, unit_meta = [], []
    for feature in raw["features"]:
        props = feature["properties"]
        entity = str(props["entity_id"]).zfill(2)
        geom = transform_geometry(converter.transform, shape(feature["geometry"]))
        if not geom.is_valid or geom.area <= 0:
            raise ValueError(f"Invalid community polygon: {entity}")
        features.append({"type": "Feature", "properties": {"entity_id": entity},
                         "geometry": mapping(geom)})
        unit_meta.append({"entity_id": entity, "polygon_area_m2": float(geom.area)})
    h, w = target_shape
    dummy = NumPyRasterSource(np.zeros((h, w), dtype="uint8"),
              xmin=transform.c, ymin=transform.f + transform.e*h,
              xmax=transform.c + transform.a*w, ymax=transform.f,
              srs_wkt=TARGET_CRS.to_wkt())
    geoms = JSONFeatureSource(features, srs_wkt=TARGET_CRS.to_wkt())
    extracted = exact_extract(dummy, geoms, ["cell_id", "coverage"], include_cols=["entity_id"])
    if len(extracted) != len(features):
        raise ValueError("exactextract did not return all communities")
    result = []
    for meta, feature in zip(unit_meta, extracted, strict=True):
        p = feature["properties"]
        if str(p["entity_id"]).zfill(2) != meta["entity_id"]:
            raise ValueError("exactextract entity order/ID mismatch")
        idx = np.asarray(p["cell_id"], dtype="int64")
        coverage = np.asarray(p["coverage"], dtype="float64")
        if np.any(idx < 0) or np.any(idx >= h*w) or np.any(coverage < 0) or np.any(coverage > 1+1e-7):
            raise ValueError(f"Invalid exactextract cell mapping for {meta['entity_id']}")
        result.append({**meta, "indices": idx, "weight_m2": coverage * abs(transform.a * transform.e)})
    if len(result) != 77 or len({x["entity_id"] for x in result}) != 77:
        raise ValueError("Expected 77 unique Chicago community polygons")
    return result


def polygon_monthly(stack, units, variable, times, method, min_coverage=0.95):
    rows=[]
    for monthidx, stamp in enumerate(times):
        vals = stack[monthidx].ravel()
        for unit in units:
            idx, weights = unit["indices"], unit["weight_m2"]
            valid = np.isfinite(vals[idx])
            valid_area = float(weights[valid].sum())
            fraction = min(1.0, valid_area / unit["polygon_area_m2"])
            if not valid.any() or fraction + 1e-12 < min_coverage:
                value = np.nan
                qc = "NO_VALID_DATA" if not valid.any() else "LOW_COVERAGE"
            else:
                value = float(np.dot(vals[idx][valid], weights[valid]) / valid_area)
                qc = "OK"
            rows.append({"entity_id":unit["entity_id"], "time":stamp.strftime("%Y-%m-%d"),
                         "variable_id":variable, "method":method,
                         "value":value, "valid_coverage_fraction":fraction, "qc_flag":qc})
    return pd.DataFrame(rows)


def spatial_overlap(stack, units, variable, times):
    rows = []
    for year in YEARS:
        selected = stack[np.asarray(times.year == year)]
        if selected.shape[0] != 12:
            raise ValueError(f"Expected 12 prepared monthly slices for {year}")
        for unit in units:
            idx, weights = unit["indices"], unit["weight_m2"]
            good = np.isfinite(selected.reshape(12, -1)[:, idx])
            common = float(weights[good.all(axis=0)].sum())
            union = float(weights[good.any(axis=0)].sum())
            area = unit["polygon_area_m2"]
            coverages = [float(weights[valid].sum() / area) for valid in good]
            common_fraction = min(1.0, common/area)
            union_fraction = min(1.0, union/area)
            different = union-common > max(area*1e-9, 1e-6)
            rows.append({"entity_id":unit["entity_id"], "year":year,
                         "variable_id":variable, "expected_months":12,
                         "minimum_monthly_coverage_fraction":min(coverages),
                         "common_valid_area_m2":common, "union_valid_area_m2":union,
                         "common_valid_fraction":common_fraction,
                         "union_valid_fraction":union_fraction,
                         "common_union_jaccard":common/union if union else np.nan,
                         "support_status":"CHANGING_SUPPORT" if different else "STABLE_SUPPORT"})
    return pd.DataFrame(rows)


def combine_annual(monthly):
    rows = []
    frame = monthly.copy()
    frame["year"] = pd.to_datetime(frame.time).dt.year
    for (entity, variable, method, year), data in frame.groupby(["entity_id", "variable_id", "method", "year"], sort=True):
        months = pd.to_datetime(data.time).dt.month
        valid = len(data)==12 and set(months)==set(range(1,13)) and np.isfinite(data.value).all()
        value = float(data.value.sum() if variable=="prcp" else data.value.mean()) if valid else np.nan
        rows.append({"entity_id":entity,"variable_id":variable,"method":method,"year":int(year),
                     "annual_value":value,"complete_window":bool(valid)})
    return pd.DataFrame(rows)


def compare_rebuilt_archived(rebuilt, original, times, variable):
    rows=[]
    for i, date in enumerate(times):
        a, b = np.asarray(rebuilt[i]), np.asarray(original[i])
        both = np.isfinite(a) & np.isfinite(b)
        different_mask = int(np.count_nonzero(np.isfinite(a) != np.isfinite(b)))
        err = np.abs(a[both].astype(float) - b[both].astype(float))
        rows.append({"variable_id":variable,"time":date.strftime("%Y-%m-%d"),
                     "common_valid_pixels":int(np.count_nonzero(both)),
                     "validity_mask_disagreement_pixels":different_mask,
                     "mean_absolute_difference":float(err.mean()) if err.size else np.nan,
                     "max_absolute_difference":float(err.max()) if err.size else np.nan})
    return pd.DataFrame(rows)


def compare_historical_monthly(monthly, root):
    runs = sorted((root / "stage5_results").glob("run_*/panel_daymet_monthly/panel_long.csv"))
    if not runs:
        raise ValueError("No Stage 5 citywide monthly panel available")
    candidates=[]
    for file in runs:
        report = file.parent.parent / "stage5_report.json"
        if report.is_file() and json.loads(report.read_text(encoding="utf-8")).get("status")=="PASS":
            candidates.append(file)
    if not candidates:
        raise ValueError("No PASS Stage 5 monthly panel with matching report")
    path = sorted(candidates)[-1]
    historic = pd.read_csv(path,dtype={"entity_id":str,"time":str})
    historic["entity_id"] = historic.entity_id.str.zfill(2)
    historic["time"] = pd.to_datetime(historic.time).dt.strftime("%Y-%m-%d")
    historic = historic[["entity_id","time","variable_id","value"]].rename(columns={"value":"historic_value"})
    actual = monthly[monthly.method=="archived_bilinear"]
    merged = actual.merge(historic, on=["entity_id","time","variable_id"],validate="one_to_one",how="outer",indicator=True)
    if len(merged)!=77*24*3 or (merged._merge!="both").any():
        raise ValueError("Historical monthly panel does not have the same 77 x 24 x 3 keys")
    merged["abs_difference"] = (merged.value-merged.historic_value).abs()
    return path, merged


def main(data_root: Path, boundaries: Path, out: Path, prior_report: Path,
         grid_tol: float, panel_tol: float):
    data_root = data_root.resolve()
    out.mkdir(parents=True,exist_ok=True)
    meta = json.loads(prior_report.read_text(encoding="utf-8"))
    if meta.get("status") != "PASS_PREFLIGHT":
        raise ValueError("Run and pass chicago_raster_preflight.py before this analysis")
    expected_hashes={Path(k).name:v for k,v in meta.get("source_sha256_match_previous_temporal_audit",{}).items() if isinstance(v,str)}
    # The preflight hash map here stores booleans. Use individual inventory records instead.
    expected_hashes.update({Path(f["path"]).name:f["sha256"] for f in meta.get("files",[]) if f.get("kind")=="raw_daily"})
    original, prepared_hashes, time, target_affine, target_shape = load_archived(data_root)
    expected_prepared = {f["variable"]: f["sha256"] for f in meta.get("files",[]) if f.get("kind")=="prepared_monthly"}
    for variable, checksum in expected_prepared.items():
        if prepared_hashes.get(variable) != checksum:
            raise ValueError(f"Prepared {variable} NetCDF changed since PASS_PREFLIGHT")
    if target_shape != (64,83) or not np.isclose(target_affine.a,1000) or not np.isclose(target_affine.e,-1000):
        raise ValueError("Unexpected archived target grid; review before changing analysis assumptions")
    if not boundaries.is_file():
        raise FileNotFoundError(boundaries)
    units = polygon_support_weights(boundaries, target_affine, target_shape)
    source_hashes, day_counts, missing_zero, grids = {}, {}, {}, []
    all_monthly, all_overlap, raster_checks = [], [], []
    for variable in VARIABLES:
        native, dates, native_affine, native_crs, hashes, counts, zero_count = build_native_monthly(data_root,variable,expected_hashes)
        if not dates.equals(time):
            raise ValueError("Raw and archived monthly timestamps differ")
        source_hashes.update(hashes)
        day_counts[variable]=counts
        missing_zero[variable]=zero_count
        grids.append({"variable":variable,"source_affine":list(native_affine)[:6],
                      "source_crs_wkt":native_crs.to_wkt(),"source_shape":list(native.shape[1:])})
        all_monthly.append(polygon_monthly(original[variable],units,variable,time,"archived_bilinear"))
        all_overlap.append(spatial_overlap(original[variable],units,variable,time))
        for name,resampler in METHODS.items():
            rebuilt = reproject_stack(native,native_affine,native_crs,target_affine,target_shape,resampler)
            all_monthly.append(polygon_monthly(rebuilt,units,variable,time,name))
            if name=="bilinear":
                raster_checks.append(compare_rebuilt_archived(rebuilt,original[variable],time,variable))
        print(f"[DONE] {variable}: 24 monthly grids, three resampling kernels, 77 communities",flush=True)
    monthly=pd.concat(all_monthly,ignore_index=True)
    annual=combine_annual(monthly)
    overlap=pd.concat(all_overlap,ignore_index=True)
    checks=pd.concat(raster_checks,ignore_index=True)
    historic_path, comparison=compare_historical_monthly(monthly,data_root)
    grid_max=float(checks.max_absolute_difference.max())
    grid_disagree=int(checks.validity_mask_disagreement_pixels.sum())
    historical_max=float(comparison.abs_difference.max())
    baseline=annual[annual.method=="archived_bilinear"].drop(columns="method").rename(columns={"annual_value":"archived_annual_value","complete_window":"archived_complete"})
    alternates=annual[annual.method!="archived_bilinear"].merge(
        baseline,on=["entity_id","variable_id","year"],validate="many_to_one")
    alternates["method_minus_archived"] = alternates.annual_value-alternates.archived_annual_value
    alternates["absolute_difference"] = alternates.method_minus_archived.abs()
    summary_rows=[]
    for (variable,year,method),g in alternates.groupby(["variable_id","year","method"],sort=True):
        valid=g[np.isfinite(g.absolute_difference)]
        summary_rows.append({"variable_id":variable,"year":int(year),"method":method,
                            "n_total_communities":len(g),"n_compared":len(valid),
                            "mean_absolute_difference":float(valid.absolute_difference.mean()) if len(valid) else np.nan,
                            "maximum_absolute_difference":float(valid.absolute_difference.max()) if len(valid) else np.nan,
                            "mean_signed_difference":float(valid.method_minus_archived.mean()) if len(valid) else np.nan,
                            "unit":"mm" if variable=="prcp" else "degrees C"})
    summary=pd.DataFrame(summary_rows)
    status="PASS_REPRODUCTION" if (grid_max <= grid_tol and grid_disagree==0 and historical_max<=panel_tol) else "REPRODUCTION_MISMATCH"
    outputs={
       "raster_reproduction":"chicago_raster_reproduction.csv",
       "monthly_detail":"chicago_resampling_monthly_detail.csv",
       "annual_comparison":"chicago_resampling_annual_comparison.csv",
       "annual_summary":"chicago_resampling_annual_summary.csv",
       "spatial_support":"chicago_real_spatial_support.csv",
       "historic_monthly_reproduction":"chicago_historic_monthly_reproduction.csv"}
    for key, frame in [("raster_reproduction",checks),("monthly_detail",monthly),("annual_comparison",alternates),
                       ("annual_summary",summary),("spatial_support",overlap),
                       ("historic_monthly_reproduction",comparison.drop(columns="_merge"))]:
        frame.to_csv(out/outputs[key],index=False,float_format="%.12g")
    report={"status":status,"scope":"Reprojection sensitivity versus archived bilinear, exact intersection re-extraction; no external ground truth",
            "created_utc":datetime.now(timezone.utc).isoformat(),
            "source_daily_sha256":source_hashes,"prepared_monthly_sha256":prepared_hashes,
            "boundaries_sha256":sha256_file(boundaries),"historic_monthly_path":str(historic_path),
            "historic_monthly_sha256":sha256_file(historic_path),
            "target_crs":"EPSG:6933","target_shape":list(target_shape),
            "target_affine":list(target_affine)[:6],"source_grids":grids,
            "resampling_kernels":list(METHODS),"monthly_aggregation":{"tmin":"np.nanmean","tmax":"np.nanmean","prcp":"np.nansum"},
            "raw_day_counts_by_month":day_counts,
            "all_missing_daily_precipitation_cells_mapped_to_zero":missing_zero,
            "reproduction":{"max_abs_grid_difference":grid_max,"grid_mask_disagreement_pixels":grid_disagree,
                            "max_abs_historic_monthly_panel_difference":historical_max,
                            "grid_tolerance":grid_tol,"monthly_panel_tolerance":panel_tol},
            "n_communities":len(units),"n_annual_alternative_comparisons":len(alternates),
            "spatial_support_status_counts":{str(k):int(v) for k,v in overlap.support_status.value_counts().items()},
            "outputs":{k:str(out/v) for k,v in outputs.items()},
            "limitations":["No independent ground truth for source Daymet field",
                           "No claim that interpolation conserves area-integrated precipitation",
                           "Precipitation totals represent cell depths in mm and polygon averages of depths, not allocated water volume",
                           "No spatial or temporal covariance/error model", "Nearest/average are sensitivity baselines, not automatically superior estimators"]}
    audit_path=out/"chicago_resampling_audit.json"
    dump_json(audit_path,report)
    print("\nStatus:",status)
    print(f"Historical bilinear raster max abs difference: {grid_max:.8g}; mask disagree: {grid_disagree}")
    print(f"Historical polygon monthly max abs difference: {historical_max:.8g}")
    print("\nAnnual sensitivity by variable/year/method (mean and max abs differences):")
    print(summary[["variable_id","year","method","n_compared","mean_absolute_difference","maximum_absolute_difference","unit"]].to_string(index=False,float_format=lambda x:f"{x:.7g}"))
    print("Spatial support status counts:",report["spatial_support_status_counts"])
    print("Audit:",audit_path)
    if status!="PASS_REPRODUCTION":
        raise ValueError("Historical reprojection/panel reconstruction did not meet tolerances. Inspect audit and reproduction CSV before interpreting sensitivity statistics.")


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    root=Path(__file__).resolve().parents[1]
    p.add_argument("--data-root",type=Path,default=root/"review_data"/"chicago")
    p.add_argument("--boundaries",type=Path,default=root/"case_studies"/"chicago"/"results"/"chicago_community_areas_77.geojson")
    p.add_argument("--preflight",type=Path,default=root/"review_results"/"real_data_validation"/"chicago_raster_preflight.json")
    p.add_argument("--out",type=Path,default=root/"review_results"/"real_data_validation"/"resampling")
    p.add_argument("--grid-tol",type=float,default=1e-4)
    p.add_argument("--panel-tol",type=float,default=1e-4)
    args=p.parse_args()
    try:
        main(args.data_root,args.boundaries,args.out,args.preflight,args.grid_tol,args.panel_tol)
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}",file=sys.stderr)
        sys.exit(1)
