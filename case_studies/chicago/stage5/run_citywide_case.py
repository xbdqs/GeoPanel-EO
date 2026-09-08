"""Build the 77-community Chicago case from cached Stage 2--4 inputs."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import sys
import time
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
import numpy as np
from pyproj import Transformer
from shapely import contains_xy
from shapely.geometry import shape
from shapely.ops import transform as transform_geometry, unary_union
import xarray as xr
import yaml

from geopanel_eo.pipeline import run as run_core
from stage3.run_chicago_panel import TARGET_CRS, config_document
from stage4.run_nlcd_panel import PRODUCTS, YEARS, acquire, nlcd_config


def digest(path):
    h = sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def latest_stage3():
    good = []
    for report in (ROOT / "stage3_results").glob("run_*/stage3_report.json"):
        try:
            if json.loads(report.read_text(encoding="utf-8")).get("status") == "PASS":
                good.append(report.parent)
        except (OSError, ValueError):
            pass
    if not good:
        raise RuntimeError("No successful Stage 3 run was found.")
    chosen = sorted(good)[-1]
    for variable in ("tmin", "tmax", "prcp"):
        if not (chosen / "prepared" / f"daymet_{variable}_monthly_epsg6933.nc").is_file():
            raise RuntimeError("Latest Stage 3 run has no prepared Daymet cache; rerun RUN_04 once.")
    return chosen


def all_units(run_dir):
    source = ROOT / "data" / "raw" / "chicago_community_areas.geojson"
    obj = json.loads(source.read_text(encoding="utf-8"))
    if obj.get("type") != "FeatureCollection" or len(obj.get("features", [])) != 77:
        raise RuntimeError("Expected the official 77-community Chicago GeoJSON cache.")
    seen = set()
    for feature in obj["features"]:
        props = feature.get("properties", {})
        raw = next((props[k] for k in ("area_numbe", "area_num_1", "area_num", "community_area")
                    if props.get(k) not in (None, "")), None)
        entity = str(raw).split(".")[0].zfill(2)
        if entity in seen or feature.get("geometry", {}).get("type") not in ("Polygon", "MultiPolygon"):
            raise RuntimeError("Duplicate ID or invalid geometry in Chicago boundary source.")
        seen.add(entity)
        props["entity_id"] = entity
        props["entity_name"] = str(props.get("community", props.get("community_area", entity)))
    if len(seen) != 77:
        raise RuntimeError("Chicago community IDs are not unique and complete.")
    obj["features"].sort(key=lambda f: f["properties"]["entity_id"])
    # The source embeds OGC:CRS84 while coordinates are longitude/latitude.
    # Write a GeoJSON without the deprecated member and declare EPSG:4326 only
    # in the audited config, avoiding two formally different CRS declarations.
    obj = {"type": "FeatureCollection", "features": obj["features"]}
    target = run_dir / "chicago_community_areas_77.geojson"
    write_json(target, obj)
    return target, obj, {"feature_count": 77, "source_sha256": digest(source)}


def center_comparison_citywide(prepared_files, unit_obj, annual_output, output):
    """Compare exact weighting where a polygon contains >=1 grid centre.

    Small polygons without a 1-km centre remain in the exact main panel and are
    explicitly reported as ineligible for this approximation benchmark.
    """
    transformer = Transformer.from_crs("EPSG:4326", TARGET_CRS, always_xy=True)
    units = [(f["properties"]["entity_id"],
              transform_geometry(transformer.transform, shape(f["geometry"])))
             for f in unit_obj["features"]]
    rows, excluded = [], set()
    for variable, path in prepared_files.items():
        with xr.open_dataset(path, engine="h5netcdf") as ds:
            xx, yy = np.meshgrid(np.asarray(ds.x), np.asarray(ds.y))
            times = pd.DatetimeIndex(ds.time.values)
            values = np.asarray(ds[variable], dtype=float)
            for entity, geometry in units:
                inside = contains_xy(geometry, xx, yy)
                if not inside.any():
                    excluded.add(entity)
                    continue
                monthly = np.nanmean(values[:, inside], axis=1)
                for year in YEARS:
                    chosen = monthly[times.year == year]
                    estimate = float(chosen.sum() if variable == "prcp" else chosen.mean())
                    rows.append({"entity_id": entity, "time": str(year), "variable_id": variable,
                                 "center_point_value": estimate,
                                 "center_cell_count": int(inside.sum())})
    approximate = pd.DataFrame(rows)
    exact = pd.read_csv(annual_output / "panel_long.csv", dtype={"entity_id": str, "time": str})
    exact["entity_id"] = exact.entity_id.str.zfill(2)
    comparison = exact[["entity_id", "entity_name", "time", "variable_id", "value",
                        "valid_coverage_fraction", "effective_sample_size"]].merge(
        approximate, on=["entity_id", "time", "variable_id"], validate="one_to_one")
    comparison = comparison.rename(columns={"value": "exact_area_weighted_value"})
    comparison["center_minus_exact"] = comparison.center_point_value - comparison.exact_area_weighted_value
    comparison["absolute_difference"] = comparison.center_minus_exact.abs()
    denominator = comparison.exact_area_weighted_value.abs().replace(0, np.nan)
    comparison["absolute_percent_difference"] = 100 * comparison.absolute_difference / denominator
    comparison.to_csv(output, index=False, float_format="%.17g")
    return comparison, sorted(excluded)


def bounds_5070(obj):
    tr = Transformer.from_crs(4326, 5070, always_xy=True)
    geoms = [transform_geometry(tr.transform, shape(f["geometry"])) for f in obj["features"]]
    left, bottom, right, top = unary_union(geoms).bounds
    return (math.floor((left-300)/30)*30, math.floor((bottom-300)/30)*30,
            math.ceil((right+300)/30)*30, math.ceil((top+300)/30)*30)


def combined(climate_dir, nlcd_dir, run_dir):
    climate = pd.read_csv(climate_dir / "panel_long.csv", dtype={"entity_id": str, "time": str})
    nlcd = pd.read_csv(nlcd_dir / "panel_long.csv", dtype={"entity_id": str, "time": str})
    data = pd.concat([climate, nlcd], ignore_index=True).sort_values(
        ["entity_id", "time", "variable_id"]).reset_index(drop=True)
    key = ["entity_id", "time", "variable_id"]
    if data.duplicated(key).any() or data[key].isna().any().any() or data.value.isna().any():
        raise RuntimeError("The citywide combined panel is not unique and complete.")
    data.to_csv(run_dir / "combined_panel_long.csv", index=False, float_format="%.17g")
    wide = data.pivot(index=["entity_id", "entity_name", "time"],
                      columns="variable_id", values="value").reset_index()
    wide.to_csv(run_dir / "combined_panel_wide.csv", index=False, float_format="%.17g")
    return data, wide


def zip_result(run_dir, result_root):
    target = result_root / f"GeoPanel-EO_CHICAGO_STAGE5_{run_dir.name}.zip"
    with ZipFile(target, "w", ZIP_DEFLATED) as archive:
        for path in sorted(run_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(run_dir.parent))
    return target


def main():
    stamp = datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%S%fZ")
    result_root = ROOT / "stage5_results"; run_dir = result_root / stamp
    run_dir.mkdir(parents=True)
    report = {"schema_version": "geopanel-chicago-stage5-2", "status": "RUNNING",
              "created_utc": stamp[4:], "software_version": "0.1.0", "timings_seconds": {}}
    log = run_dir / "stage5_log.txt"
    def emit(message):
        print(message, flush=True)
        with log.open("a", encoding="utf-8") as stream: stream.write(message + "\n")
    started = time.perf_counter()
    try:
        stage3 = latest_stage3(); units, unit_obj, boundary = all_units(run_dir)
        prepared = {v: stage3 / "prepared" / f"daymet_{v}_monthly_epsg6933.nc"
                    for v in ("tmin", "tmax", "prcp")}
        manifests = {}
        for frequency in ("monthly", "annual"):
            tick = time.perf_counter(); out = run_dir / f"panel_daymet_{frequency}"
            cfg = run_dir / f"config_daymet_{frequency}.yml"
            cfg.write_text(yaml.safe_dump(config_document(units, prepared, out, frequency),
                                          sort_keys=False), encoding="utf-8")
            emit(f"[DAYMET] 77 communities, {frequency}")
            manifests[frequency] = run_core(cfg)
            report["timings_seconds"][f"daymet_{frequency}"] = round(time.perf_counter()-tick, 3)
        comparison, excluded = center_comparison_citywide(
            prepared, unit_obj, run_dir / "panel_daymet_annual", run_dir / "exact_vs_center_77.csv")
        comparison.groupby("variable_id").agg(
            comparisons=("absolute_difference", "size"),
            mean_absolute_difference=("absolute_difference", "mean"),
            maximum_absolute_difference=("absolute_difference", "max"),
            mean_absolute_percent_difference=("absolute_percent_difference", "mean")
        ).reset_index().to_csv(run_dir / "comparison_summary_77.csv", index=False, float_format="%.17g")

        bounds = bounds_5070(unit_obj); report["request_bounds_epsg5070"] = list(bounds)
        raw = ROOT / "data" / "raw" / "nlcd_citywide"; raw.mkdir(parents=True, exist_ok=True)
        stop = ROOT / "STOP_REQUESTED.flag"; stop.unlink(missing_ok=True)
        files = {}; report["nlcd_files"] = []
        for product in PRODUCTS:
            for year in YEARS:
                path, transfer, inspection = acquire(product, year, bounds, raw, stop, emit)
                files[(product, year)] = path
                report["nlcd_files"].append({"transfer": transfer, "inspection": inspection})
        tick = time.perf_counter(); nlcd_out = run_dir / "panel_nlcd"
        nlcd_cfg = run_dir / "config_nlcd.yml"
        nlcd_cfg.write_text(yaml.safe_dump(nlcd_config(units, files, nlcd_out), sort_keys=False), encoding="utf-8")
        emit("[NLCD] 77 communities, categorical land cover and impervious surface")
        nlcd_manifest = run_core(nlcd_cfg)
        report["timings_seconds"]["nlcd_panel"] = round(time.perf_counter()-tick, 3)
        data, wide = combined(run_dir / "panel_daymet_annual", nlcd_out, run_dir)
        expected_long, expected_wide = 77*2*(3+33), 77*2
        if len(data) != expected_long or len(wide) != expected_wide:
            raise RuntimeError(f"Unexpected citywide dimensions: {len(data)} long, {len(wide)} wide.")
        report.update({"status": "PASS", "boundary_audit": boundary,
                       "center_comparison": {"rows": len(comparison),
                                             "eligible_community_count": 77-len(excluded),
                                             "excluded_no_center_ids": excluded},
                       "source_stage3": stage3.name,
                       "daymet_monthly_rows": manifests["monthly"]["row_count"],
                       "daymet_annual_rows": manifests["annual"]["row_count"],
                       "nlcd_rows": nlcd_manifest["row_count"],
                       "combined_long_rows": len(data), "combined_wide_rows": len(wide),
                       "combined_long_sha256": digest(run_dir / "combined_panel_long.csv"),
                       "combined_wide_sha256": digest(run_dir / "combined_panel_wide.csv")})
        emit("RESULT: PASS")
    except Exception as exc:
        report.update({"status": "FAIL", "error_type": type(exc).__name__, "error": str(exc)})
        emit(f"RESULT: FAIL ({type(exc).__name__}: {exc})")
    report["timings_seconds"]["total"] = round(time.perf_counter()-started, 3)
    write_json(run_dir / "stage5_report.json", report)
    result = zip_result(run_dir, result_root)
    print(f"Upload this result ZIP: {result}")
    print(f"SHA256: {digest(result)}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
