"""Local input audit and normalized north-up raster slices."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json
import re

import numpy as np
import pandas as pd
import pyogrio.raw
from pyproj import CRS, Transformer
import rasterio
from rasterio.transform import Affine
from shapely import from_wkb
from shapely.geometry import shape
from shapely.ops import transform as transform_geometry
import xarray as xr

from .errors import GeoPanelError


@dataclass
class RasterSlice:
    time: pd.Timestamp
    values: np.ndarray
    transform: Affine
    crs: CRS
    path: str
    calendar: str = "proleptic_gregorian"
    stored_unit: str | None = None


@dataclass
class Unit:
    entity_id: str
    entity_name: str
    geometry: object


def audited_crs(stored, declared=None):
    if not stored and not declared:
        raise GeoPanelError("CRS_MISSING", "Input CRS is missing; declare it explicitly")
    try:
        a = CRS.from_user_input(stored or declared)
        if stored and declared and a != CRS.from_user_input(declared):
            raise GeoPanelError("CRS_DECLARATION_CONFLICT", "Declared CRS conflicts with stored CRS")
        return a
    except GeoPanelError:
        raise
    except Exception as exc:
        raise GeoPanelError("CRS_INVALID", str(exc)) from exc


def validate_area_crs(crs):
    method = (crs.coordinate_operation.method_name if crs.coordinate_operation else "").lower()
    if not crs.is_projected or "equal area" not in method.replace("-", " "):
        raise GeoPanelError("AREA_CRS_UNSUPPORTED", "S1 requires an equal-area projected raster CRS (e.g. EPSG:6933); geographic and other projected grids need explicit external preprocessing")
    if len(crs.axis_info) < 2 or any(a.unit_conversion_factor != 1 for a in crs.axis_info[:2]):
        raise GeoPanelError("AREA_UNIT_UNSUPPORTED", "S1 area coordinates must be metres")


def read_units(config):
    source = Path(config.source)
    try:
        if source.suffix.lower() in {".json", ".geojson"}:
            obj = json.loads(source.read_text(encoding="utf-8"))
            if obj.get("type") != "FeatureCollection":
                raise GeoPanelError("UNITS_INVALID", "GeoJSON must be a FeatureCollection")
            stored = obj.get("crs", {}).get("properties", {}).get("name")
            # RFC 7946 has an implicit CRS, but the project requires an explicit declaration.
            crs = audited_crs(stored, config.crs)
            rows = []
            for feature in obj["features"]:
                properties = feature.get("properties", {})
                entity_name = properties.get(config.entity_name_field) if config.entity_name_field else ""
                rows.append((properties.get(config.entity_id_field), entity_name, shape(feature["geometry"])))
        else:
            columns = [config.entity_id_field] + ([config.entity_name_field] if config.entity_name_field else [])
            metadata, _, geometries, fields = pyogrio.raw.read(source, layer=config.layer, columns=columns)
            crs = audited_crs(metadata.get("crs"), config.crs)
            if config.entity_id_field not in metadata["fields"]:
                raise GeoPanelError("ENTITY_ID_MISSING", config.entity_id_field)
            if config.entity_name_field and config.entity_name_field not in metadata["fields"]:
                raise GeoPanelError("ENTITY_NAME_MISSING", config.entity_name_field)
            names = fields[1] if config.entity_name_field else [""] * len(fields[0])
            rows = list(zip(fields[0], names, from_wkb(geometries)))
    except GeoPanelError:
        raise
    except Exception as exc:
        raise GeoPanelError("UNITS_READ_FAILED", str(exc)) from exc
    units = []
    for entity_id, entity_name, geom in rows:
        if entity_id is None or pd.isna(entity_id) or not str(entity_id).strip():
            raise GeoPanelError("ENTITY_ID_MISSING", "Every entity must have an ID")
        if config.entity_name_field and (entity_name is None or pd.isna(entity_name) or not str(entity_name).strip()):
            raise GeoPanelError("ENTITY_NAME_MISSING", "Every entity must have a name when entity_name_field is configured")
        if geom is None or geom.is_empty or not geom.is_valid or geom.geom_type not in {"Polygon", "MultiPolygon"}:
            raise GeoPanelError("GEOMETRY_INVALID", f"Invalid polygon for {entity_id}; S1 does not repair geometries")
        units.append(Unit(str(entity_id), str(entity_name) if config.entity_name_field else "", geom))
    if not units:
        raise GeoPanelError("UNITS_EMPTY", "No spatial units")
    if len({u.entity_id for u in units}) != len(units):
        raise GeoPanelError("DUPLICATE_ENTITY_ID", "Entity IDs must be unique after string normalization")
    return sorted(units, key=lambda u: u.entity_id), crs


def units_in_crs(units, source_crs, raster_crs, config):
    if config.units.target_crs and CRS.from_user_input(config.units.target_crs) != raster_crs:
        raise GeoPanelError("CRS_TARGET_MISMATCH", "units.target_crs must equal the raster CRS")
    if source_crs == raster_crs:
        return units, False
    if config.spatial.crs_action != "reproject_units":
        raise GeoPanelError("CRS_MISMATCH", "Set spatial.crs_action=reproject_units explicitly")
    converter = Transformer.from_crs(source_crs, raster_crs, always_xy=True)
    transformed = [Unit(u.entity_id, u.entity_name, transform_geometry(converter.transform, u.geometry)) for u in units]
    if any(not u.geometry.is_valid or u.geometry.is_empty or not np.isfinite(u.geometry.area) for u in transformed):
        raise GeoPanelError("GEOMETRY_INVALID", "Reprojection produced invalid geometry")
    return transformed, True


def read_tiff(path, band=1, declared_crs=None):
    try:
        with rasterio.open(path) as ds:
            crs = audited_crs(ds.crs, declared_crs)
            transform = ds.transform
            if transform.b != 0 or transform.d != 0 or transform.a <= 0 or transform.e >= 0:
                raise GeoPanelError("GRID_UNSUPPORTED", "GeoTIFF must have an unrotated north-up grid")
            if band > ds.count:
                raise GeoPanelError("BAND_MISSING", f"Band {band} not present in {path}")
            values = ds.read(band, masked=True).astype("float64").filled(np.nan)
            values = values * ds.scales[band - 1] + ds.offsets[band - 1]
            stored_unit = ds.units[band - 1] if ds.units else None
            return values, transform, crs, stored_unit
    except GeoPanelError:
        raise
    except Exception as exc:
        raise GeoPanelError("RASTER_READ_FAILED", str(exc)) from exc


def parsed_time(value):
    try:
        timestamp = pd.Timestamp(value)
        if pd.isna(timestamp) or timestamp.tzinfo is not None:
            raise ValueError("Expected a valid timezone-naive date")
        return timestamp
    except Exception as exc:
        raise GeoPanelError("TIME_PARSE_FAILED", str(value)) from exc


def read_source(source, variable):
    if source.type == "geotiff":
        slices = []
        for i, path in enumerate(source.paths or [source.path]):
            if source.timestamps is not None:
                timestamp = parsed_time(source.timestamps[i])
            else:
                try:
                    match = re.search(source.time_pattern, Path(path).name)
                    timestamp = parsed_time(datetime.strptime(match.group("time"), source.time_format))
                except Exception as exc:
                    raise GeoPanelError("TIME_PARSE_FAILED", f"Cannot parse timestamp from {path}") from exc
            values, affine, crs, stored_unit = read_tiff(path, source.band, source.crs)
            slices.append(RasterSlice(timestamp, values, affine, crs, path, stored_unit=stored_unit))
    else:
        slices = read_netcdf(source, variable.source_variable)
    if not slices:
        raise GeoPanelError("TIME_EMPTY", source.source_id)
    for raster in slices:
        stored_unit = str(raster.stored_unit).strip() if raster.stored_unit else None
        raster.stored_unit = stored_unit
        if stored_unit is not None and stored_unit != variable.unit:
            raise GeoPanelError("UNIT_METADATA_MISMATCH",
                f"{source.source_id}: stored unit {stored_unit!r} does not match declared unit {variable.unit!r}; implicit conversion is unsupported")
    periods = [s.time.to_period("M") for s in slices]
    if len(periods) != len(set(periods)):
        raise GeoPanelError("DUPLICATE_SOURCE_TIME", "S1 accepts at most one observation per source month")
    return sorted(slices, key=lambda s: s.time)


def read_netcdf(source, variable):
    try:
        with Path(source.path).open("rb") as stream:
            signature = stream.read(8)
        engine = "scipy" if signature[:3] == b"CDF" else "h5netcdf"
        with xr.open_dataset(source.path, decode_times=False, engine=engine) as raw:
            if source.time_coordinate not in raw:
                raise GeoPanelError("TIME_MISSING", source.time_coordinate)
            calendar = raw[source.time_coordinate].attrs.get("calendar", "standard")
            if calendar not in {"standard", "gregorian", "proleptic_gregorian"}:
                raise GeoPanelError("UNSUPPORTED_CALENDAR", calendar)
        with xr.open_dataset(source.path, engine=engine) as ds:
            if variable not in ds:
                raise GeoPanelError("VARIABLE_MISSING", variable)
            data = ds[variable]
            coords = (source.time_coordinate, source.y_coordinate, source.x_coordinate)
            if set(data.dims) != set(coords) or len(data.dims) != 3:
                raise GeoPanelError("GRID_UNSUPPORTED", "NetCDF variable must have exactly time, y and x dimensions")
            x, y = ds[source.x_coordinate], ds[source.y_coordinate]
            if x.ndim != 1 or y.ndim != 1 or x.size < 2 or y.size < 2:
                raise GeoPanelError("GRID_UNSUPPORTED", "NetCDF requires regular one-dimensional coordinate centres with >=2 cells per axis")
            xv, yv = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
            for axis in (xv, yv):
                delta = np.diff(axis)
                if not np.isfinite(axis).all() or delta[0] == 0 or not np.allclose(delta, delta[0], rtol=1e-9, atol=1e-10):
                    raise GeoPanelError("GRID_UNSUPPORTED", "Non-regular or non-monotonic coordinate grid")
            mapping_name = data.attrs.get("grid_mapping")
            stored = None
            if mapping_name and mapping_name in ds:
                attrs = ds[mapping_name].attrs
                stored = attrs.get("spatial_ref") or attrs.get("crs_wkt")
                if stored is None and "grid_mapping_name" in attrs:
                    stored = CRS.from_cf(attrs)
            crs = audited_crs(stored, source.crs)
            if xv[0] > xv[-1]:
                data = data.isel({source.x_coordinate: slice(None, None, -1)})
                xv = xv[::-1]
            if yv[0] < yv[-1]:
                data = data.isel({source.y_coordinate: slice(None, None, -1)})
                yv = yv[::-1]
            dx, dy = float(xv[1] - xv[0]), float(yv[1] - yv[0])
            affine = Affine(dx, 0, xv[0] - dx / 2, 0, dy, yv[0] - dy / 2)
            data = data.transpose(*coords)
            result = []
            for i, t in enumerate(ds[source.time_coordinate].values):
                timestamp = parsed_time(t)
                if calendar in {"standard", "gregorian"} and timestamp.year < 1583:
                    raise GeoPanelError("UNSUPPORTED_CALENDAR", "Mixed Gregorian calendar before 1583 is unsupported")
                stored_unit = data.attrs.get("units")
                result.append(RasterSlice(timestamp, np.asarray(data.isel({source.time_coordinate: i}), dtype=float),
                    affine, crs, source.path, calendar, stored_unit))
            return result
    except GeoPanelError:
        raise
    except Exception as exc:
        raise GeoPanelError("NETCDF_READ_FAILED", str(exc)) from exc


def aligned_auxiliary(path, reference):
    if not path:
        return None
    values, affine, crs, _ = read_tiff(path)
    if values.shape != reference.values.shape or affine != reference.transform or crs != reference.crs:
        raise GeoPanelError("AUXILIARY_GRID_MISMATCH", f"Auxiliary raster must match data grid exactly: {path}")
    return values.ravel()
