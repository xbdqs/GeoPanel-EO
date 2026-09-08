"""Small, redistributable synthetic fixtures with analytically specified truths."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import CRS
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box, mapping
import xarray as xr
import yaml


CRS_ID = "EPSG:6933"
TRANSFORM = from_origin(0, 2, 1, 1)


def write_tiff(path, values, crs=CRS_ID, transform=TRANSFORM):
    values = np.asarray(values, dtype="float64")
    with rasterio.open(path, "w", driver="GTiff", height=values.shape[0], width=values.shape[1],
                       count=1, dtype="float64", crs=crs, transform=transform, nodata=-9999) as ds:
        ds.write(np.where(np.isfinite(values), values, -9999), 1)


def write_units(path, geometries=None, ids=None, crs=CRS_ID):
    geometries = geometries if geometries is not None else [box(0, 0, 2, 2)]
    ids = ids if ids is not None else ["001"]
    obj = dict(type="FeatureCollection", features=[dict(type="Feature", properties={"entity_id": entity}, geometry=mapping(g))
               for entity, g in zip(ids, geometries, strict=True)])
    if crs:
        obj["crs"] = {"type": "name", "properties": {"name": crs}}
    path.write_text(json.dumps(obj), encoding="utf-8")


def basic_config():
    return dict(project=dict(project_id="synthetic", title="GeoPanel-EO synthetic truth", output_directory="outputs"),
        units=dict(source="units.geojson", entity_id_field="entity_id"),
        sources=[dict(source_id="raster", type="geotiff", path="data.tif", timestamps=["2020-01-01"])],
        variables=[dict(variable_id="temperature", source_id="raster", source_variable="band_1",
            semantic_type="intensive_state", unit="degC", temporal_support="monthly_mean",
            spatial_reducer="coverage_weighted_mean", temporal_reducer="mean",
            temporal_window=dict(frequency="monthly"), minimum_coverage=0)],
        outputs=dict(csv=True, parquet=True, stata=True, wide_csv=True, quality_report_html=True))


TRUTHS = {
    "T01": {"description": "Four fully covered cells: (10+20+30+40)/4=25", "expected": {"temperature": {"value": 25, "valid_coverage_fraction": 1, "valid_area": 4, "effective_sample_size": 4}}},
    "T02": {"description": "Half each of cells 10 and 20: (0.5*10+0.5*20)/1=15", "expected": {"temperature": {"value": 15, "valid_area": 1, "total_unit_area": 1, "contributing_cell_count": 2, "effective_sample_size": 2}}},
    "T03": {"description": "One half-cell is NoData: valid area 0.5, full polygon area 1", "expected": {"temperature": {"value": 10, "valid_coverage_fraction": 0.5, "valid_area": 0.5, "total_unit_area": 1}}},
    "T04": {"description": "Cell-total weights 1 and 3: (10*1+20*3)/4=17.5; unweighted mean=15", "expected": {"temperature": {"value": 17.5, "weight_sum": 2, "effective_sample_size": 1.6}}},
    "T05": {"description": "Monthly accumulated depths 1..12: annual sum=78", "expected": {"precipitation": {"value": 78, "source_time_count": 12, "expected_time_count": 12}}},
    "T06": {"description": "Monthly states 0..11: equal-month annual mean=5.5; temperature sum must be rejected", "expected": {"temperature": {"value": 5.5, "source_time_count": 12, "expected_time_count": 12}}},
    "T07": {"description": "Classes 1,1,2,NoData: class areas 2 and 1; classified-area fractions 2/3 and 1/3", "expected": {
        "landcover_class_1_area": {"value": 2, "valid_coverage_fraction": 0.75},
        "landcover_class_1_fraction": {"value": 2/3}, "landcover_class_2_area": {"value": 1}, "landcover_class_2_fraction": {"value": 1/3}}},
}


def create_case(directory, case="T01", netcdf=False):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    config = basic_config()
    config["project"]["project_id"] = case
    config["project"]["title"] = TRUTHS[case]["description"]
    values = np.array([[10, 20], [30, 40]], dtype=float)
    geometry = box(0, 0, 2, 2)
    if case in {"T02", "T03", "T04"}:
        geometry = box(0.5, 1, 1.5, 2)
    if case == "T03":
        values[0, 1] = np.nan
    if case == "T04":
        write_tiff(directory / "weights.tif", [[1, 3], [1, 1]])
        config["variables"][0]["optional_weight"] = "weights.tif"
    variable = config["variables"][0]
    if case in {"T05", "T06"}:
        variable["temporal_window"] = dict(frequency="annual")
        months = range(1, 13)
        times = [f"2020-{m:02d}-01" for m in months]
        arrays = [np.full((2, 2), m if case == "T05" else m-1, dtype=float) for m in months]
        if case == "T05":
            variable.update(variable_id="precipitation", semantic_type="accumulation_depth", unit="mm",
                temporal_support="monthly_total", temporal_reducer="sum")
        if netcdf:
            data = xr.Dataset({"climate": (("time", "y", "x"), arrays)}, coords={"time": pd.to_datetime(times), "x": [0.5, 1.5], "y": [1.5, 0.5]})
            data["crs"] = xr.DataArray(0, attrs={"spatial_ref": CRS.from_user_input(CRS_ID).to_wkt()})
            data["climate"].attrs["grid_mapping"] = "crs"
            data.to_netcdf(directory / "climate.nc", engine="h5netcdf")
            config["sources"] = [dict(source_id="raster", type="netcdf", path="climate.nc", x_coordinate="x", y_coordinate="y")]
            variable["source_variable"] = "climate"
        else:
            paths = [f"climate_{m:02d}.tif" for m in months]
            for name, array in zip(paths, arrays, strict=True):
                write_tiff(directory / name, array)
            config["sources"] = [dict(source_id="raster", type="geotiff", paths=paths, timestamps=times)]
    else:
        if case == "T07":
            values = np.array([[1, 1], [2, np.nan]])
            variable.update(variable_id="landcover", semantic_type="categorical_class", unit="class_code",
                temporal_support="monthly_snapshot",
                spatial_reducer="area_by_class_and_fraction", temporal_reducer="snapshot", classes=[1, 2])
        write_tiff(directory / "data.tif", values)
    write_units(directory / "units.geojson", [geometry])
    path = directory / "config.yml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    (directory / "expected.json").write_text(json.dumps({**TRUTHS[case], "absolute_tolerance": 1e-7, "relative_tolerance": 1e-7}, indent=2), encoding="utf-8")
    return path
