"""Acquire official Annual NLCD subsets and extend the Chicago panel."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import sys
from urllib.parse import urlencode
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from pyproj import CRS, Transformer
import rasterio
from shapely.geometry import shape
from shapely.ops import transform as transform_geometry, unary_union
import yaml

from network_jobs import JobError, run_job
from geopanel_eo.pipeline import run as run_core

YEARS = (2019, 2020)
CLASSES = (11, 12, 21, 22, 23, 24, 31, 41, 42, 43, 52, 71, 81, 82, 90, 95)
PRODUCTS = {
    "landcover": {
        "workspace": "mrlc_Land-Cover-Native_conus_year_data",
        "layer": "Land-Cover-Native_conus_year_data",
    },
    "impervious": {
        "workspace": "mrlc_Fractional-Impervious-Surface-Native_conus_year_data",
        "layer": "Fractional-Impervious-Surface-Native_conus_year_data",
    },
}


def digest(path):
    h = sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def latest_stage3():
    candidates = []
    for report in (ROOT / "stage3_results").glob("run_*/stage3_report.json"):
        try:
            if json.loads(report.read_text(encoding="utf-8")).get("status") == "PASS":
                candidates.append(report.parent)
        except (OSError, ValueError):
            pass
    if not candidates:
        raise RuntimeError("No successful Stage 3 result directory was found.")
    return sorted(candidates)[-1]


def analysis_units(stage3):
    path = stage3 / "chicago_areas_08_25_32.geojson"
    obj = json.loads(path.read_text(encoding="utf-8"))
    if len(obj.get("features", [])) != 3:
        raise RuntimeError("Stage 3 selected-community file is invalid.")
    return path, obj


def request_bounds(units):
    transformer = Transformer.from_crs(4326, 5070, always_xy=True)
    geometries = [transform_geometry(transformer.transform, shape(f["geometry"])) for f in units["features"]]
    left, bottom, right, top = unary_union(geometries).bounds
    # 300 m context, then exact 30 m multiples so the WCS response retains 30 m cells.
    return (math.floor((left-300)/30)*30, math.floor((bottom-300)/30)*30,
            math.ceil((right+300)/30)*30, math.ceil((top+300)/30)*30)


def wcs_url(product, year, bounds):
    item = PRODUCTS[product]
    base = f"https://dmsdata.cr.usgs.gov/geoserver/{item['workspace']}/wcs"
    params = {"service": "WCS", "version": "1.0.0", "request": "GetCoverage",
              "coverage": f"{item['workspace']}:{item['layer']}", "format": "GeoTIFF",
              "crs": "EPSG:5070", "bbox": ",".join(f"{v:.0f}" for v in bounds),
              "resx": "30", "resy": "30", "time": f"{year}-01-01T00:00:00.000Z"}
    return base + "?" + urlencode(params)


def inspect_tiff(path, product, bounds):
    with Path(path).open("rb") as stream:
        if stream.read(4) not in (b"II*\x00", b"MM\x00*"):
            raise ValueError("The WCS response is not a TIFF; it may be an XML service error.")
    with rasterio.open(path) as ds:
        if ds.count != 1 or CRS.from_user_input(ds.crs) != CRS.from_epsg(5070):
            raise ValueError("Unexpected Annual NLCD band count or CRS.")
        if not np.allclose(ds.res, (30, 30), atol=1e-5, rtol=0):
            raise ValueError(f"Annual NLCD response is not 30 m: {ds.res}")
        tolerance = 0.1
        if any(abs(a-b) > tolerance for a, b in zip(ds.bounds, bounds)):
            raise ValueError("Annual NLCD returned bounds differ from the requested aligned extent.")
        values = ds.read(1, masked=True)
        valid = np.asarray(values.compressed())
        if valid.size == 0:
            raise ValueError("Annual NLCD subset has no valid pixels.")
        unique = sorted(map(int, np.unique(valid)))
        if product == "landcover" and not set(unique).issubset(CLASSES):
            raise ValueError(f"Unknown Annual NLCD land-cover codes: {sorted(set(unique)-set(CLASSES))}")
        if product == "impervious" and (valid.min() < 0 or valid.max() > 100):
            raise ValueError("Annual NLCD impervious values fall outside 0..100 percent.")
        return {"driver": ds.driver, "dtype": ds.dtypes[0], "crs": ds.crs.to_string(),
                "width": ds.width, "height": ds.height, "resolution": list(ds.res),
                "bounds": list(ds.bounds), "nodata": ds.nodata, "valid_pixels": int(valid.size),
                "masked_pixels": int(values.mask.sum()) if np.ndim(values.mask) else int(values.mask)*values.size,
                "minimum": int(valid.min()), "maximum": int(valid.max()), "unique_values": unique,
                "sha256": digest(path), "size_bytes": Path(path).stat().st_size}


def acquire(product, year, bounds, raw, stop_file, emit):
    target = raw / f"annual_nlcd_c1_2_{product}_{year}_chicago.tif"
    sidecar = target.with_suffix(".tif.audit.json")
    url = wcs_url(product, year, bounds)
    request_key = sha256(url.encode()).hexdigest()
    if target.is_file() and sidecar.is_file():
        previous = json.loads(sidecar.read_text(encoding="utf-8"))
        if previous.get("request_key") == request_key and previous.get("sha256") == digest(target):
            inspection = inspect_tiff(target, product, bounds)
            emit(f"[CACHE] {target.name}: verified.")
            return target, {**previous, "cache_reused": True}, inspection
    partial = target.with_suffix(".tif.part")
    job = {"kind": "fetch", "url": url, "partial": str(partial),
           "max_bytes": 256*1024*1024, "read_timeout": 120}
    try:
        transfer = run_job(job, seconds=420, label=f"[NLCD {product} {year}]",
                           stop_file=stop_file, emit=emit)
        inspection = inspect_tiff(partial, product, bounds)
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    record = {"request_key": request_key, "source_service": "USGS MRLC Annual NLCD WCS",
              "collection": "Annual NLCD Collection 1.2", "product": product, "year": year,
              "sha256": digest(target), "cache_reused": False, **transfer}
    write_json(sidecar, record)
    return target, record, inspection


def nlcd_config(units, files, output):
    return {"project": {"project_id": "chicago_annual_nlcd_2019_2020",
                         "title": "Chicago Annual NLCD panel", "output_directory": str(output),
                         "overwrite": False},
            "units": {"source": str(units), "entity_id_field": "entity_id",
                      "entity_name_field": "entity_name", "crs": "EPSG:4326",
                      "target_crs": "EPSG:5070", "geometry_action": "fail"},
            "sources": [
                {"source_id": "annual_nlcd_landcover", "type": "geotiff",
                 "paths": [str(files[("landcover", y)]) for y in YEARS],
                 "timestamps": [f"{y}-01-01" for y in YEARS]},
                {"source_id": "annual_nlcd_impervious", "type": "geotiff",
                 "paths": [str(files[("impervious", y)]) for y in YEARS],
                 "timestamps": [f"{y}-01-01" for y in YEARS]},
            ],
            "variables": [
                {"variable_id": "landcover", "source_id": "annual_nlcd_landcover",
                 "source_variable": "band_1", "semantic_type": "categorical_class",
                 "unit": "class_code", "temporal_support": "monthly_snapshot",
                 "spatial_reducer": "area_by_class_and_fraction", "temporal_reducer": "snapshot",
                 "temporal_window": {"frequency": "annual", "months": [1]},
                 "minimum_coverage": 0.99, "classes": list(CLASSES)},
                {"variable_id": "impervious_percent", "source_id": "annual_nlcd_impervious",
                 "source_variable": "band_1", "semantic_type": "intensive_state",
                 "unit": "percent", "temporal_support": "monthly_snapshot",
                 "spatial_reducer": "coverage_weighted_mean", "temporal_reducer": "snapshot",
                 "temporal_window": {"frequency": "annual", "months": [1]},
                 "minimum_coverage": 0.99, "valid_range": [0, 100]},
            ],
            "spatial": {"crs_action": "reproject_units", "area_method": "projected_equal_area"},
            "temporal": {"calendar": "proleptic_gregorian", "timezone": "naive",
                         "input_frequency": "monthly", "mean_weighting": "equal_month",
                         "incomplete_window": "flag_and_null"},
            "panel": {"require_unique_entity_time": True, "require_balanced": True,
                      "retain_flagged_rows": True},
            "uncertainty": {"assumption": "independent_cells_and_times"},
            "outputs": {"csv": True, "parquet": True, "stata": True, "wide_csv": True,
                        "quality_report_html": True, "hash_algorithm": "sha256"}}


def merge_panels(stage3, nlcd_output, run_dir):
    climate = pd.read_csv(stage3 / "panel_annual" / "panel_long.csv", dtype={"entity_id": str, "time": str})
    nlcd = pd.read_csv(nlcd_output / "panel_long.csv", dtype={"entity_id": str, "time": str})
    combined = pd.concat([climate, nlcd], ignore_index=True).sort_values(
        ["entity_id", "time", "variable_id"]).reset_index(drop=True)
    key = ["entity_id", "time", "variable_id"]
    if combined.duplicated(key).any() or combined[key].isna().any().any():
        raise RuntimeError("Combined climate/NLCD panel key is not unique and complete.")
    combined.to_csv(run_dir / "combined_panel_long.csv", index=False, float_format="%.17g")
    wide = combined.pivot(index=["entity_id", "entity_name", "time"], columns="variable_id", values="value").reset_index()
    wide.to_csv(run_dir / "combined_panel_wide.csv", index=False, float_format="%.17g")
    return combined, wide


def make_bundle(run_dir, result_root):
    output = result_root / f"GeoPanel-EO_CHICAGO_STAGE4_{run_dir.name}.zip"
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for path in sorted(run_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(run_dir.parent))
    return output


def main():
    stamp = datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%S%fZ")
    result_root = ROOT / "stage4_results"; run_dir = result_root / stamp
    run_dir.mkdir(parents=True)
    raw = ROOT / "data" / "raw" / "nlcd"; raw.mkdir(parents=True, exist_ok=True)
    stop_file = ROOT / "STOP_REQUESTED.flag"
    report = {"schema_version": "geopanel-chicago-stage4-1", "status": "RUNNING",
              "created_utc": stamp[4:], "software_version": "0.1.0",
              "official_source": "USGS MRLC Annual NLCD Collection 1.2 WCS",
              "doi": "10.5066/P94UXNTS", "files": []}
    log = run_dir / "stage4_log.txt"
    def emit(message):
        print(message, flush=True)
        with log.open("a", encoding="utf-8") as stream: stream.write(message+"\n")
    try:
        stop_file.unlink(missing_ok=True)
        stage3 = latest_stage3(); units_path, units = analysis_units(stage3)
        bounds = request_bounds(units); report["request_bounds_epsg5070"] = list(bounds)
        files = {}
        for product in PRODUCTS:
            for year in YEARS:
                path, transfer, inspection = acquire(product, year, bounds, raw, stop_file, emit)
                files[(product, year)] = path
                report["files"].append({"transfer": transfer, "inspection": inspection})
        config = run_dir / "config_nlcd.yml"; output = run_dir / "panel_nlcd"
        config.write_text(yaml.safe_dump(nlcd_config(units_path, files, output), sort_keys=False), encoding="utf-8")
        emit("[GEOPANEL-EO] building Annual NLCD categorical/impervious panel")
        manifest = run_core(config)
        combined, wide = merge_panels(stage3, output, run_dir)
        report.update({"status": "PASS", "nlcd_panel_rows": manifest["row_count"],
                       "nlcd_variable_count": manifest["variable_count"],
                       "nlcd_panel_sha256": manifest["scientific_result_sha256"],
                       "combined_long_rows": len(combined), "combined_wide_rows": len(wide),
                       "combined_long_sha256": digest(run_dir / "combined_panel_long.csv"),
                       "combined_wide_sha256": digest(run_dir / "combined_panel_wide.csv"),
                       "source_stage3": stage3.name})
        emit("RESULT: PASS")
    except Exception as exc:
        report.update({"status": "FAIL", "error_type": type(exc).__name__, "error": str(exc)})
        emit(f"RESULT: FAIL ({type(exc).__name__}: {exc})")
    write_json(run_dir / "stage4_report.json", report)
    bundle = make_bundle(run_dir, result_root)
    print(f"Upload this result ZIP: {bundle}")
    print(f"SHA256: {digest(bundle)}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
