"""Product structure and grid checks; no panel estimates or causal analysis."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import numpy as np
from pyproj import CRS, Transformer
import xarray as xr

YEARS = (2019, 2020)
VARIABLES = ("tmin", "tmax", "prcp")
KEEP_AREAS = {"08", "25", "32"}
BBOX = {"north": 42.10, "west": -88.05, "east": -87.45, "south": 41.60}
CHICAGO_URL = "https://data.cityofchicago.org/resource/igwz-8jzy.geojson?$limit=5000"
COLLECTION_ID = "C2532426483-ORNL_CLOUD"
PROBE_CONSTRAINT = "/x;/y;/lambert_conformal_conic"
DAYMET_CRS = CRS.from_proj4(
    "+proj=lcc +lat_1=25 +lat_2=60 +lat_0=42.5 +lon_0=-100 "
    "+x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"
)


def digest(path):
    h = sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def daymet_url(variable, year):
    if variable not in VARIABLES or year not in YEARS:
        raise ValueError("This audited microcase is fixed to 2019/2020 tmin/tmax/prcp.")
    return (f"https://opendap.earthdata.nasa.gov/collections/{COLLECTION_ID}/granules/"
            f"Daymet_Daily_V4R1.daymet_v4_daily_na_{variable}_{year}.nc.dap.nc4")


def require_netcdf(path):
    with Path(path).open("rb") as stream:
        signature = stream.read(8)
    # The requested response format is NetCDF4, read by h5netcdf.
    if signature != b"\x89HDF\r\n\x1a\n":
        raise ValueError("Expected NetCDF4/HDF5; received another payload (possibly a login or error page).")


def unit_metres(unit):
    if str(unit).strip().lower() in {"km", "kilometer", "kilometers"}:
        return 1000.0
    if str(unit).strip().lower() in {"m", "meter", "meters", "metre", "metres"}:
        return 1.0
    raise ValueError("Unrecognized coordinate units in the Daymet product.")


def index_bounds(values, lower, upper, padding=2):
    lo, hi = sorted((lower, upper))
    if lo < values.min() or hi > values.max():
        raise ValueError("Chicago extent falls outside the returned Daymet grid.")
    indices = np.flatnonzero((values >= lo) & (values <= hi))
    if not len(indices):
        indices = np.array([np.abs(values-lo).argmin(), np.abs(values-hi).argmin()])
    return max(0, int(indices.min())-padding), min(len(values)-1, int(indices.max())+padding)


def inspect_probe(path):
    require_netcdf(path)
    with xr.open_dataset(path, engine="h5netcdf", decode_times=False) as ds:
        x, y = np.asarray(ds["x"], float), np.asarray(ds["y"], float)
        xu, yu = str(ds["x"].attrs.get("units", "")), str(ds["y"].attrs.get("units", ""))
        xf, yf = unit_metres(xu), unit_metres(yu)
        for coords, factor in ((x, xf), (y, yf)):
            if coords.ndim != 1 or len(coords) < 2 or not np.all(np.isfinite(coords)):
                raise ValueError("Daymet x/y must be finite one-dimensional coordinates.")
            steps = np.diff(coords)
            if not (np.all(steps > 0) or np.all(steps < 0)):
                raise ValueError("Daymet coordinates are not strictly monotonic.")
            if not np.allclose(np.abs(steps)*factor, 1000, atol=0.1, rtol=1e-5):
                raise ValueError("The returned grid does not have the expected 1 km spacing.")
        attrs = dict(ds["lambert_conformal_conic"].attrs)
        if attrs.get("grid_mapping_name") != "lambert_conformal_conic":
            raise ValueError("Unexpected Daymet projection.")
        checks = {"latitude_of_projection_origin": 42.5,
                  "longitude_of_central_meridian": -100.0, "standard_parallel": [25., 60.]}
        for key, expected in checks.items():
            if key not in attrs or not np.allclose(attrs[key], expected):
                raise ValueError(f"Daymet projection parameter differs from the documented grid: {key}")
    west, east, south, north = [BBOX[k] for k in ("west", "east", "south", "north")]
    horizontal, vertical = np.linspace(west, east, 101), np.linspace(south, north, 101)
    lon = np.concatenate([horizontal, horizontal, np.full(101, west), np.full(101, east)])
    lat = np.concatenate([np.full(101, south), np.full(101, north), vertical, vertical])
    px, py = Transformer.from_crs("EPSG:4326", DAYMET_CRS, always_xy=True).transform(lon, lat)
    x0, x1 = index_bounds(x, float(min(px))/xf, float(max(px))/xf)
    y0, y1 = index_bounds(y, float(min(py))/yf, float(max(py))/yf)
    cells = (x1-x0+1)*(y1-y0+1)
    if cells > 25000:
        raise ValueError("The derived subset is unexpectedly large for the Chicago microcase.")
    return {"x": [x0, x1], "y": [y0, y1], "x_unit": xu, "y_unit": yu,
            "x_coordinate_range": [float(x[x0]), float(x[x1])],
            "y_coordinate_range": [float(y[y0]), float(y[y1])],
            "cell_count": cells, "padding_cells": 2, "bbox_wgs84": BBOX,
            "crs_projjson": DAYMET_CRS.to_json_dict(),
            "server_grid_mapping_attrs": {k: str(v) for k, v in attrs.items()}}


def subset_constraint(variable, selection, start=0, end=364):
    if not (0 <= start <= end <= 364):
        raise ValueError("Daymet time indices must fall within the 365-step year.")
    y0, y1 = selection["y"]
    x0, x1 = selection["x"]
    ys, xs = f"[{y0}:1:{y1}]", f"[{x0}:1:{x1}]"
    # Daymet has 365 steps even in leap years (Feb 29 retained, Dec 31 omitted).
    ts = f"[{start}:1:{end}]"
    return (f"/time{ts};/y{ys};/x{xs};/lat{ys}{xs};/lon{ys}{xs};"
            f"/{variable}{ts}{ys}{xs};/lambert_conformal_conic")


def inspect_chunk(path, variable, year, selection, start, end):
    require_netcdf(path)
    with xr.open_dataset(path, engine="h5netcdf") as ds:
        data = ds[variable]
        expected_shape = (end-start+1, selection["y"][1]-selection["y"][0]+1,
                          selection["x"][1]-selection["x"][0]+1)
        if data.dims != ("time", "y", "x") or data.shape != expected_shape:
            raise ValueError("Downloaded chunk dimensions do not match the request.")
        dates = ds["time"].values.astype("datetime64[D]")
        expected = np.datetime64(f"{year}-01-01") + np.arange(start, end+1).astype("timedelta64[D]")
        if not np.array_equal(dates, expected):
            raise ValueError("Downloaded chunk dates do not match the requested indices.")
        for axis in ("x", "y"):
            values = np.asarray(ds[axis], float)
            if not np.allclose([values[0], values[-1]], selection[f"{axis}_coordinate_range"],
                               rtol=1e-7, atol=0.01):
                raise ValueError("Downloaded chunk grid differs from the selected grid.")
        if np.any(np.isfinite(np.asarray(data)).sum(axis=(1, 2)) == 0):
            raise ValueError("A downloaded chunk contains a day with no finite land pixels.")
    return {"start_index": start, "end_index": end, "time_count": end-start+1,
            "sha256": digest(path), "size_bytes": Path(path).stat().st_size}


def merge_chunks(paths, output, variable, year, selection):
    datasets = []
    try:
        for path in paths:
            dataset = xr.open_dataset(path, engine="h5netcdf")
            dataset.load()
            datasets.append(dataset)
        merged = xr.concat(datasets, dim="time", data_vars="minimal", coords="minimal",
                           compat="override", combine_attrs="override")
        temporary = Path(output).with_suffix(Path(output).suffix + ".merge")
        merged.to_netcdf(temporary, engine="h5netcdf")
        merged.close()
        inspection = inspect_subset(temporary, variable, year, selection)
        temporary.replace(output)
        return inspection
    finally:
        for dataset in datasets:
            dataset.close()
        Path(output).with_suffix(Path(output).suffix + ".merge").unlink(missing_ok=True)


def inspect_subset(path, variable, year, selection):
    require_netcdf(path)
    with xr.open_dataset(path, engine="h5netcdf") as ds:
        data = ds[variable]
        if data.dims != ("time", "y", "x"):
            raise ValueError("Unexpected Daymet variable dimension order.")
        expected_shape = (365, selection["y"][1]-selection["y"][0]+1,
                          selection["x"][1]-selection["x"][0]+1)
        if data.shape != expected_shape:
            raise ValueError("Downloaded dimensions do not match the requested subset.")
        dates = ds["time"].values.astype("datetime64[D]")
        expected_dates = np.datetime64(f"{year}-01-01") + np.arange(365).astype("timedelta64[D]")
        if not np.array_equal(dates, expected_dates):
            raise ValueError("Time coordinate does not follow the documented Daymet 365-step year.")
        for axis in ("x", "y"):
            values = np.asarray(ds[axis], float)
            wanted = selection[f"{axis}_coordinate_range"]
            if not np.allclose([values[0], values[-1]], wanted, rtol=1e-7, atol=0.01):
                raise ValueError("Returned coordinate extent differs from the requested grid.")
            if unit_metres(ds[axis].attrs.get("units", "")) != unit_metres(selection[f"{axis}_unit"]):
                raise ValueError("Returned coordinate units changed between files.")
        values = np.asarray(data, dtype=float)
        if np.isinf(values).any():
            raise ValueError("Infinite values were found in the returned Daymet data.")
        counts = np.isfinite(values).sum(axis=(1, 2))
        if np.any(counts == 0):
            raise ValueError("One or more days contain no finite land pixels in the Chicago subset.")
        finite = values[np.isfinite(values)]
        mapping = str(data.attrs.get("grid_mapping", ""))
        if mapping not in ds:
            raise ValueError("The downloaded variable has no usable grid mapping.")
        return {"filename": Path(path).name, "variable": variable, "year": year,
                "sha256": digest(path), "size_bytes": Path(path).stat().st_size,
                "dimensions": {k: int(v) for k, v in ds.sizes.items()}, "data_dims": list(data.dims),
                "data_dtype": str(data.dtype), "data_attrs": {k: str(v) for k, v in data.attrs.items()},
                "global_attrs": {k: str(v) for k, v in ds.attrs.items()},
                "grid_mapping": mapping, "grid_mapping_attrs": {k: str(v) for k, v in ds[mapping].attrs.items()},
                "time_count": len(dates), "time_first": str(dates[0]), "time_last": str(dates[-1]),
                "calendar_note": "365 steps/year; leap-day retained; Dec 31 omitted in leap years",
                "finite_pixel_days": int(len(finite)), "finite_min": float(finite.min()),
                "finite_max": float(finite.max()), "minimum_finite_pixels_per_day": int(counts.min())}


def inspect_boundaries(path):
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    if obj.get("type") != "FeatureCollection" or len(obj.get("features", [])) != 77:
        raise ValueError("Expected the official 77-community-area GeoJSON.")
    chosen, seen = [], set()
    for feature in obj["features"]:
        properties = feature.get("properties", {})
        raw = next((properties[k] for k in ("area_numbe", "area_num_1", "area_num", "community_area")
                    if properties.get(k) not in (None, "")), None)
        area = str(raw).split(".")[0].zfill(2)
        if area in KEEP_AREAS:
            if area in seen:
                raise ValueError("Duplicate selected community ID in source boundary.")
            if feature.get("geometry", {}).get("type") not in ("Polygon", "MultiPolygon"):
                raise ValueError("A selected community does not have polygon geometry.")
            properties["geopanel_id"] = area
            chosen.append(feature)
            seen.add(area)
    if seen != KEEP_AREAS:
        raise ValueError("Expected community IDs 08, 25 and 32 were not all found.")
    subset = {"type": "FeatureCollection", "features": sorted(chosen, key=lambda f: f["properties"]["geopanel_id"])}
    return {"feature_count": 77, "selected_ids": sorted(seen), "selected_feature_count": 3,
            "source_sha256": digest(path)}, subset
