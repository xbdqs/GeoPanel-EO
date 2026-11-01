"""Build the audited Chicago Daymet panel from Stage 2 local data."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from pyproj import CRS, Transformer
import rasterio
from rasterio.transform import array_bounds
from rasterio.warp import Resampling, calculate_default_transform, reproject
from shapely import contains_xy
from shapely.geometry import shape, mapping
from shapely.ops import transform as transform_geometry
import xarray as xr
import yaml

from daymet_audit import KEEP_AREAS, VARIABLES, YEARS, digest, inspect_boundaries, inspect_subset
from geopanel_eo.pipeline import run as run_core
from geopanel_eo import __version__

TARGET_CRS = CRS.from_epsg(6933)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def raw_path(variable, year):
    return ROOT / "data" / "raw" / f"daymet_v4_daily_na_{variable}_{year}_chicago.nc"


def selection_from_probe():
    path = ROOT / "data" / "raw" / "daymet_grid_coordinate_probe.nc"
    sidecar = path.with_suffix(path.suffix + ".audit.json")
    if not path.is_file() or not sidecar.is_file():
        raise RuntimeError("Stage 2 coordinate probe or audit sidecar is missing.")
    candidates = sorted((ROOT / "local_results").glob("run_*/grid_selection.json"), reverse=True)
    if not candidates:
        raise RuntimeError("No Stage 2 grid_selection.json was found in local_results.")
    return json.loads(candidates[0].read_text(encoding="utf-8"))


def selected_units(prepared):
    source = ROOT / "data" / "raw" / "chicago_community_areas.geojson"
    inspection, subset = inspect_boundaries(source)
    for feature in subset["features"]:
        props = feature["properties"]
        props["entity_id"] = props["geopanel_id"]
        props["entity_name"] = str(props.get("community", props.get("community_area", props["geopanel_id"])))
    output = prepared / "chicago_areas_08_25_32.geojson"
    write_json(output, subset)
    return output, inspection, subset


def source_geometry(dataset):
    x = np.asarray(dataset.x, dtype=float)
    y = np.asarray(dataset.y, dtype=float)
    dx, dy = float(x[1]-x[0]), float(y[1]-y[0])
    transform = rasterio.Affine(dx, 0, x[0]-dx/2, 0, dy, y[0]-dy/2)
    variable = next(name for name in VARIABLES if name in dataset)
    grid_name = dataset[variable].attrs["grid_mapping"]
    crs = CRS.from_cf(dict(dataset[grid_name].attrs))
    return transform, crs


def monthly_native(variable, selection):
    arrays, times, source_hashes, source_crs, source_transform, day_counts = [], [], {}, None, None, {}
    for year in YEARS:
        path = raw_path(variable, year)
        if not path.is_file():
            raise RuntimeError(f"Missing Stage 2 file: {path.name}")
        inspection = inspect_subset(path, variable, year, selection)
        source_hashes[str(path)] = inspection["sha256"]
        with xr.open_dataset(path, engine="h5netcdf") as ds:
            transform, crs = source_geometry(ds)
            if source_crs is None:
                source_crs, source_transform = crs, transform
            elif crs != source_crs or transform != source_transform:
                raise RuntimeError("Daymet annual files do not share one native grid.")
            dates = pd.DatetimeIndex(ds.time.values)
            values = np.asarray(ds[variable], dtype="float32")
            for month in range(1, 13):
                chosen = values[dates.month == month]
                if chosen.size == 0:
                    raise RuntimeError(f"No observations for {variable} {year}-{month:02d}")
                day_counts[f"{year}-{month:02d}"] = int((dates.month == month).sum())
                aggregate = np.nansum(chosen, axis=0) if variable == "prcp" else np.nanmean(chosen, axis=0)
                arrays.append(aggregate.astype("float32"))
                times.append(np.datetime64(f"{year}-{month:02d}-01"))
    return np.stack(arrays), np.asarray(times), source_transform, source_crs, source_hashes, day_counts


def target_grid(source_transform, source_crs, shape):
    height, width = shape
    bounds = array_bounds(height, width, source_transform)
    transform, out_width, out_height = calculate_default_transform(
        source_crs, TARGET_CRS, width, height, *bounds, resolution=1000)
    return transform, out_width, out_height


def prepare_variable(variable, selection, prepared, grid=None):
    native, times, src_transform, src_crs, hashes, day_counts = monthly_native(variable, selection)
    dst_transform, width, height = grid or target_grid(src_transform, src_crs, native.shape[1:])
    converted = np.full((len(times), height, width), np.nan, dtype="float32")
    for index, source in enumerate(native):
        reproject(source=source, destination=converted[index], src_transform=src_transform,
                  src_crs=src_crs, dst_transform=dst_transform, dst_crs=TARGET_CRS,
                  src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.bilinear,
                  init_dest_nodata=True)
    x = dst_transform.c + dst_transform.a * (np.arange(width) + 0.5)
    y = dst_transform.f + dst_transform.e * (np.arange(height) + 0.5)
    units = "mm" if variable == "prcp" else "degrees C"
    ds = xr.Dataset({variable: (("time", "y", "x"), converted),
                     "crs": ((), np.int16(0))}, coords={"time": times, "y": y, "x": x})
    ds[variable].attrs.update({"units": units, "grid_mapping": "crs",
                               "stage3_temporal_aggregation": "monthly_total" if variable == "prcp" else "monthly_mean"})
    ds["crs"].attrs.update(TARGET_CRS.to_cf())
    ds["crs"].attrs["crs_wkt"] = TARGET_CRS.to_wkt()
    ds.attrs.update({"title": "GeoPanel-EO Chicago prepared Daymet monthly subset",
                     "source_stage": "Stage 2 audited Daymet Daily V4 R1",
                     "reprojection": "bilinear to EPSG:6933 at 1000 m"})
    output = prepared / f"daymet_{variable}_monthly_epsg6933.nc"
    ds.to_netcdf(output, engine="h5netcdf")
    ds.close()
    return output, (dst_transform, width, height), hashes, day_counts, {
        "native_crs_wkt": src_crs.to_wkt(),
        "native_transform": [float(v) for v in src_transform[:6]],
        "native_shape": [int(native.shape[1]), int(native.shape[2])],
        "target_transform": [float(v) for v in dst_transform[:6]],
        "target_shape": [height, width],
    }


def config_document(units, prepared_files, output, frequency, preprocess_manifest):
    sources, variables = [], []
    for variable in VARIABLES:
        sources.append({"source_id": f"daymet_{variable}", "type": "netcdf",
                        "path": str(prepared_files[variable]), "time_coordinate": "time",
                        "x_coordinate": "x", "y_coordinate": "y"})
        variables.append({"variable_id": variable, "source_id": f"daymet_{variable}",
                          "source_variable": variable,
                          "semantic_type": "accumulation_depth" if variable == "prcp" else "intensive_state",
                          "unit": "mm" if variable == "prcp" else "degrees C",
                          "temporal_support": "monthly_total" if variable == "prcp" else "monthly_mean",
                          "spatial_reducer": "coverage_weighted_mean",
                          "temporal_reducer": "sum" if variable == "prcp" and frequency == "annual" else "mean" if variable != "prcp" else "snapshot",
                          "temporal_window": {"frequency": frequency, "months": list(range(1, 13))},
                          "minimum_coverage": 0.95})
    return {"project": {"project_id": f"chicago_daymet_{frequency}",
                         "title": f"Chicago Daymet {frequency} panel", "output_directory": str(output),
                         "overwrite": False, "preprocessing_manifest": str(preprocess_manifest)},
            "units": {"source": str(units), "entity_id_field": "entity_id",
                      "entity_name_field": "entity_name", "crs": "EPSG:4326",
                      "target_crs": "EPSG:6933", "geometry_action": "fail"},
            "sources": sources, "variables": variables,
            "spatial": {"crs_action": "reproject_units", "area_method": "projected_equal_area"},
            "temporal": {"calendar": "proleptic_gregorian", "timezone": "naive",
                         "input_frequency": "monthly", "mean_weighting": "equal_month",
                         "incomplete_window": "flag_and_null"},
            "panel": {"require_unique_entity_time": True, "require_balanced": True,
                      "retain_flagged_rows": True},
            "uncertainty": {"assumption": "independent_cells_and_times"},
            "outputs": {"csv": True, "parquet": True, "stata": True,
                        "wide_csv": True, "quality_report_html": True, "hash_algorithm": "sha256"}}


def center_comparison(prepared_files, subset, annual_output, output):
    transformer = Transformer.from_crs("EPSG:4326", TARGET_CRS, always_xy=True)
    units = [(f["properties"]["entity_id"],
              transform_geometry(transformer.transform, shape(f["geometry"]))) for f in subset["features"]]
    rows = []
    for variable, path in prepared_files.items():
        with xr.open_dataset(path, engine="h5netcdf") as ds:
            xx, yy = np.meshgrid(np.asarray(ds.x), np.asarray(ds.y))
            times = pd.DatetimeIndex(ds.time.values)
            data = np.asarray(ds[variable], dtype=float)
            for entity, geometry in units:
                inside = contains_xy(geometry, xx, yy)
                if not inside.any():
                    raise RuntimeError(f"No cell centres fall inside community {entity}.")
                monthly = np.nanmean(data[:, inside], axis=1)
                for year in YEARS:
                    chosen = monthly[times.year == year]
                    value = float(chosen.sum() if variable == "prcp" else chosen.mean())
                    rows.append({"entity_id": entity, "time": str(year), "variable_id": variable,
                                 "center_point_value": value, "center_cell_count": int(inside.sum())})
    approximate = pd.DataFrame(rows)
    exact = pd.read_csv(annual_output / "panel_long.csv", dtype={"entity_id": str})
    exact["entity_id"] = exact.entity_id.str.zfill(2)
    exact["time"] = exact.time.astype(str)
    comparison = exact[["entity_id", "entity_name", "time", "variable_id", "value",
                        "valid_coverage_fraction", "effective_sample_size"]].merge(
                            approximate, on=["entity_id", "time", "variable_id"], validate="one_to_one")
    comparison = comparison.rename(columns={"value": "exact_area_weighted_value"})
    comparison["center_minus_exact"] = comparison.center_point_value - comparison.exact_area_weighted_value
    comparison["absolute_difference"] = comparison.center_minus_exact.abs()
    denominator = comparison.exact_area_weighted_value.abs().replace(0, np.nan)
    comparison["absolute_percent_difference"] = 100 * comparison.absolute_difference / denominator
    comparison.to_csv(output, index=False, float_format="%.17g")
    return comparison


def bundle(run_dir, result_root):
    output = result_root / f"GeoPanel-EO_CHICAGO_STAGE3_{run_dir.name}.zip"
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for path in sorted(run_dir.rglob("*")):
            if path.is_file() and "prepared" not in path.parts:
                archive.write(path, path.relative_to(run_dir.parent))
    return output


def main():
    stamp = datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%S%fZ")
    result_root = ROOT / "stage3_results"
    run_dir = result_root / stamp
    prepared = run_dir / "prepared"
    prepared.mkdir(parents=True)
    report = {"schema_version": "geopanel-chicago-stage3-1", "status": "RUNNING",
              "created_utc": stamp[4:], "software_version": __version__}
    log_path = run_dir / "stage3_log.txt"
    def emit(message):
        print(message, flush=True)
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(message + "\n")
    try:
        selection = selection_from_probe()
        units, boundary_audit, subset = selected_units(run_dir)
        prepared_files, input_hashes, shared_grid = {}, {}, None
        monthly_counts, variable_grid_metadata = None, {}
        for variable in VARIABLES:
            emit(f"[PREPARE] {variable}: daily to monthly and EPSG:6933")
            path, grid, hashes, counts, grid_metadata = prepare_variable(variable, selection, prepared, shared_grid)
            if monthly_counts is None:
                monthly_counts = counts
            elif monthly_counts != counts:
                raise RuntimeError("Daymet variables contain inconsistent monthly represented-day counts")
            variable_grid_metadata[variable] = grid_metadata
            shared_grid = grid
            prepared_files[variable] = path
            input_hashes.update(hashes)
        # Record *executed* preprocessing, before running GeoPanel-EO.
        # Paths are relative to the manifest location for transferable records.
        preprocess_manifest = run_dir / "preprocessing_manifest.json"
        import os as _os
        preprocess_record = {
            "schema_version": "geopanel-preprocess-1",
            "dataset": "Daymet V4 R1, Chicago, executed Stage 3 preparation",
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "raw_source_sha256": {_os.path.relpath(k, run_dir).replace("\\", "/"): v for k, v in input_hashes.items()},
            "prepared_sha256": {_os.path.relpath(v, run_dir).replace("\\", "/"): digest(v) for v in prepared_files.values()},
            "represented_day_counts": monthly_counts,
            "operations": {
                "input_time_resolution": "Daymet source daily time coordinate",
                "monthly_temperature_reducer": "numpy.nanmean (arithmetic mean)",
                "monthly_precipitation_reducer": "numpy.nansum (sum)",
                "source_nodata": "NaN (array-level aggregation)",
                "target_nodata": "NaN (rasterio reproject)",
                "resampling_kernel": "rasterio.warp.Resampling.bilinear",
                "target_crs": "EPSG:6933",
                "nominal_resolution_m": 1000,
                "target_grid": {"width": shared_grid[1], "height": shared_grid[2],
                                "affine_transform": list(map(float, shared_grid[0][:6]))},
                "source_and_target_grids_by_variable": variable_grid_metadata,
                "day_counts_source": "actual daily time coordinates, not inferred calendar",
                "known_caveat": "NaN-sum may map fully missing precipitation cells to zero; this manifest is not a source-product validation",
            },
            "validation_boundary": "Byte identities and executed parameters only; resampling bias and conservation require separate tests",
        }
        write_json(preprocess_manifest, preprocess_record)
        manifests = {}
        for frequency in ("monthly", "annual"):
            config_path = run_dir / f"config_{frequency}.yml"
            output = run_dir / f"panel_{frequency}"
            config_path.write_text(yaml.safe_dump(config_document(units, prepared_files, output, frequency, preprocess_manifest),
                                                  sort_keys=False), encoding="utf-8")
            emit(f"[GEOPANEL-EO] building {frequency} panel")
            manifests[frequency] = run_core(config_path)
        comparison = center_comparison(prepared_files, subset, run_dir / "panel_annual",
                                       run_dir / "exact_vs_center.csv")
        summary = comparison.groupby("variable_id").agg(
            comparisons=("absolute_difference", "size"),
            mean_absolute_difference=("absolute_difference", "mean"),
            maximum_absolute_difference=("absolute_difference", "max"),
            mean_absolute_percent_difference=("absolute_percent_difference", "mean")).reset_index()
        summary.to_csv(run_dir / "comparison_summary.csv", index=False, float_format="%.17g")
        report.update({"status": "PASS", "input_sha256": input_hashes,
                       "preprocessing_manifest_sha256": digest(preprocess_manifest),
                       "preprocessing_manifest": str(preprocess_manifest),
                       "boundary_audit": boundary_audit,
                       "preprocessing": {"daily_to_monthly": {"tmin": "arithmetic mean",
                                                               "tmax": "arithmetic mean",
                                                               "prcp": "sum"},
                                         "target_crs": "EPSG:6933",
                                         "target_resolution_metres": 1000,
                                         "resampling": "bilinear",
                                         "daymet_calendar": "365 steps; leap-day retained and Dec 31 omitted in leap years",
                                         "target_grid": {"width": shared_grid[1], "height": shared_grid[2]}},
                       "prepared_sha256": {k: digest(v) for k, v in prepared_files.items()},
                       "monthly_panel": {"rows": manifests["monthly"]["row_count"],
                                         "sha256": manifests["monthly"]["scientific_result_sha256"]},
                       "annual_panel": {"rows": manifests["annual"]["row_count"],
                                        "sha256": manifests["annual"]["scientific_result_sha256"]},
                       "comparison_rows": len(comparison), "comparison_summary": summary.to_dict("records")})
        emit("RESULT: PASS")
    except Exception as exc:
        report.update({"status": "FAIL", "error_type": type(exc).__name__, "error": str(exc)})
        emit(f"RESULT: FAIL ({type(exc).__name__}: {exc})")
    write_json(run_dir / "stage3_report.json", report)
    output = bundle(run_dir, result_root)
    print(f"Upload this result ZIP: {output}")
    print(f"SHA256: {digest(output)}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
