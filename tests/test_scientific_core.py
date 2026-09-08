import copy
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Polygon, box
import xarray as xr
import yaml

from geopanel_eo.config import Config, load_config
from geopanel_eo.errors import GeoPanelError
from geopanel_eo.pipeline import build_panel, run
from geopanel_eo.synthetic import TRUTHS, create_case, write_tiff, write_units


def edit_config(path, change):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def compute(path):
    return build_panel(load_config(path))[0]


@pytest.mark.parametrize("case", list(TRUTHS))
def test_T01_T07_manual_truths_and_all_export_roundtrips(tmp_path, case):
    path = create_case(tmp_path, case)
    manifest = run(path)
    actual = pd.read_csv(tmp_path / "outputs" / "panel_long.csv", dtype={"entity_id": str})
    assert set(actual.variable_id) == set(TRUTHS[case]["expected"])
    assert actual.entity_id.tolist() == ["001"] * len(actual)
    for variable_id, fields in TRUTHS[case]["expected"].items():
        row = actual.loc[actual.variable_id == variable_id].iloc[0]
        for field, expected in fields.items():
            assert row[field] == pytest.approx(expected, abs=1e-7, rel=1e-7)
    assert manifest["roundtrip_verified"]
    assert len(manifest["input_sha256"]) >= 2
    assert (tmp_path / "outputs" / "panel_long.dta").is_file()
    assert (tmp_path / "outputs" / "quality_report.html").is_file()


def test_T06_temperature_sum_rejected(tmp_path):
    path = create_case(tmp_path, "T06")
    edit_config(path, lambda d: d["variables"][0].update(temporal_reducer="sum"))
    with pytest.raises(GeoPanelError, match="SEMANTIC_RULE"):
        load_config(path)


def test_netcdf_matches_geotiff_with_permuted_dimensions_and_axes(tmp_path):
    tif = create_case(tmp_path / "tif", "T05")
    nc = create_case(tmp_path / "nc", "T05", netcdf=True)
    file = nc.parent / "climate.nc"
    with xr.open_dataset(file) as src:
        modified = src.load().isel(x=slice(None, None, -1), y=slice(None, None, -1)).transpose("x", "time", "y")
    modified.to_netcdf(file)
    pd.testing.assert_frame_equal(compute(tif), compute(nc))


def test_netcdf_orientation_with_nonuniform_values(tmp_path):
    path = create_case(tmp_path, "T05", netcdf=True)
    file = tmp_path / "climate.nc"
    with xr.open_dataset(file) as src:
        ds = src.load()
    ds["climate"].values[:] = [[1, 3], [9, 27]]
    ds = ds.isel(x=slice(None, None, -1), y=slice(None, None, -1)).transpose("x", "time", "y")
    ds.to_netcdf(file)
    write_units(tmp_path / "units.geojson", [box(0, 1, 1, 2)])
    assert compute(path).iloc[0].value == pytest.approx(12)


def test_netcdf3_matches_netcdf4(tmp_path):
    path = create_case(tmp_path, "T05", netcdf=True)
    expected = compute(path)
    file = tmp_path / "climate.nc"
    with xr.open_dataset(file, engine="h5netcdf") as src:
        ds = src.load()
    ds.to_netcdf(tmp_path / "classic.nc", engine="scipy", format="NETCDF3_64BIT")
    edit_config(path, lambda d: d["sources"][0].update(path="classic.nc"))
    pd.testing.assert_frame_equal(expected, compute(path))


@pytest.mark.parametrize("extension,driver", [("gpkg", "GPKG"), ("shp", "ESRI Shapefile")])
def test_vector_file_formats_preserve_ids(tmp_path, extension, driver):
    import pyogrio.raw
    from shapely import to_wkb
    path = create_case(tmp_path)
    file = tmp_path / f"units.{extension}"
    pyogrio.raw.write(file, to_wkb([box(0, 0, 2, 2)]), [np.array(["001"], dtype=object)],
        ["entity_id"], driver=driver, geometry_type="Polygon", crs="EPSG:6933")
    edit_config(path, lambda d: d["units"].update(source=file.name))
    row = compute(path).iloc[0]
    assert row.entity_id == "001" and row.value == pytest.approx(25)


def test_explicit_unit_reprojection_recorded(tmp_path):
    from pyproj import Transformer
    from shapely.ops import transform
    path = create_case(tmp_path)
    geom = transform(Transformer.from_crs(6933, 4326, always_xy=True).transform, box(0, 0, 2, 2))
    write_units(tmp_path / "units.geojson", [geom], crs="EPSG:4326")
    edit_config(path, lambda d: d.update(spatial={"crs_action": "reproject_units"}))
    row = compute(path).iloc[0]
    assert row.value == pytest.approx(25, abs=1e-5)
    assert "CRS_ACTION_RECORDED" in row.qc_flag


def test_extensive_cell_total_uses_coverage_adjusted_sum(tmp_path):
    path = create_case(tmp_path, "T02")
    edit_config(path, lambda d: d["variables"][0].update(semantic_type="extensive_total",
        spatial_reducer="coverage_adjusted_sum", temporal_reducer="snapshot", unit="persons", temporal_support="monthly_total"))
    assert compute(path).iloc[0].value == pytest.approx(15)


def test_unit_temporal_support_contract_rejects_legacy_conversion_fields(tmp_path):
    path = create_case(tmp_path, "T05")
    def legacy_conversion(d):
        variable = d["variables"][0]
        variable["source_unit"] = "mm"
        variable["output_unit"] = "bananas"
    edit_config(path, legacy_conversion)
    with pytest.raises(GeoPanelError, match="CONFIG_INVALID"):
        load_config(path)


@pytest.mark.parametrize("semantic,support,reducer,code", [
    ("accumulation_depth", "monthly_total", "mean", "SEMANTIC_RULE"),
    ("intensive_state", "monthly_mean", "sum", "SEMANTIC_RULE"),
    ("accumulation_depth", "monthly_mean", "snapshot", "TEMPORAL_SUPPORT_RULE"),
])
def test_invalid_temporal_support_and_reducer_combinations(tmp_path, semantic, support, reducer, code):
    path = create_case(tmp_path, "T05")
    def invalid(d):
        d["variables"][0].update(semantic_type=semantic, temporal_support=support, temporal_reducer=reducer)
    edit_config(path, invalid)
    with pytest.raises(GeoPanelError, match=code):
        load_config(path)


def test_stored_raster_unit_must_match_declared_unit(tmp_path):
    import rasterio
    path = create_case(tmp_path)
    with rasterio.open(tmp_path / "data.tif", "r+") as dataset:
        dataset.set_band_unit(1, "K")
    with pytest.raises(GeoPanelError, match="UNIT_METADATA_MISMATCH"):
        compute(path)
    edit_config(path, lambda d: d["variables"][0].update(unit="K"))
    assert compute(path).iloc[0].value == pytest.approx(25)


def test_period_support_is_explicit(tmp_path):
    precipitation = compute(create_case(tmp_path / "precip", "T05")).iloc[0]
    temperature = compute(create_case(tmp_path / "temp", "T06")).iloc[0]
    assert precipitation.period_support == "annual_total"
    assert temperature.period_support == "annual_mean"


def test_entity_name_field_is_retained_and_time_label_is_rejected(tmp_path):
    path = create_case(tmp_path)
    obj = json.loads((tmp_path / "units.geojson").read_text(encoding="utf-8"))
    obj["features"][0]["properties"]["community"] = "Example Area"
    (tmp_path / "units.geojson").write_text(json.dumps(obj), encoding="utf-8")
    edit_config(path, lambda d: d["units"].update(entity_name_field="community"))
    assert compute(path).iloc[0].entity_name == "Example Area"
    edit_config(path, lambda d: d.update(panel={"time_label": "year"}))
    with pytest.raises(GeoPanelError, match="CONFIG_INVALID"):
        load_config(path)


@pytest.mark.parametrize("weights,code", [([[0, 0], [0, 0]], "NO_VALID_DATA"), ([[np.nan, 3], [1, 1]], "WEIGHT_MISSING")])
def test_zero_and_missing_weights(tmp_path, weights, code):
    path = create_case(tmp_path, "T04")
    write_tiff(tmp_path / "weights.tif", weights)
    row = compute(path).iloc[0]
    assert code in row.qc_flag
    if code == "NO_VALID_DATA":
        assert pd.isna(row.value) and row.contributing_cell_count == 0
    else:
        assert row.value == pytest.approx(20)
        assert row.valid_coverage_fraction == pytest.approx(0.5)


def test_auxiliary_alignment_and_negative_weight_fail(tmp_path):
    from rasterio.transform import from_origin
    path = create_case(tmp_path, "T04")
    write_tiff(tmp_path / "weights.tif", [[1, -3], [1, 1]])
    with pytest.raises(GeoPanelError, match="WEIGHT_INVALID"):
        compute(path)
    write_tiff(tmp_path / "weights.tif", [[1, 3], [1, 1]], transform=from_origin(0.1, 2, 1, 1))
    with pytest.raises(GeoPanelError, match="AUXILIARY_GRID_MISMATCH"):
        compute(path)


def test_quarterly_and_custom_month_aggregation(tmp_path):
    path = create_case(tmp_path, "T05")
    edit_config(path, lambda d: d["variables"][0].update(temporal_window={"frequency": "quarterly"}))
    assert compute(path).value.tolist() == pytest.approx([6, 15, 24, 33])
    edit_config(path, lambda d: d["variables"][0].update(temporal_window={"frequency": "annual", "months": [6, 7, 8]}))
    assert compute(path).iloc[0].value == pytest.approx(21)


def test_complete_missing_year_is_retained_and_flagged(tmp_path):
    path = create_case(tmp_path, "T05")
    def gap(d):
        source = d["sources"][0]
        source["paths"] += source["paths"].copy()
        source["timestamps"] += [t.replace("2020", "2022") for t in source["timestamps"]]
    edit_config(path, gap)
    panel = compute(path)
    assert panel.time.tolist() == ["2020", "2021", "2022"]
    assert pd.isna(panel.iloc[1].value) and panel.iloc[1].source_time_count == 0


def test_snapshot_does_not_substitute_earlier_available_month(tmp_path):
    path = create_case(tmp_path, "T06")
    edit_config(path, lambda d: d["variables"][0].update(temporal_reducer="snapshot"))
    assert compute(path).iloc[0].value == pytest.approx(11)
    write_tiff(tmp_path / "climate_12.tif", [[np.nan] * 2] * 2)
    assert pd.isna(compute(path).iloc[0].value)


def test_unknown_class_is_excluded_from_classified_area(tmp_path):
    path = create_case(tmp_path, "T07")
    write_tiff(tmp_path / "data.tif", [[1, 3], [2, np.nan]])
    panel = compute(path)
    assert "UNKNOWN_CLASS" in panel.iloc[0].qc_flag
    assert panel.loc[panel.variable_id == "landcover_class_1_fraction", "value"].iloc[0] == pytest.approx(0.5)


def test_unicode_and_na_like_entity_ids_roundtrip(tmp_path):
    path = create_case(tmp_path)
    write_units(tmp_path / "units.geojson", [box(0, 0, 1, 2)] * 3, ["NA", "0001", "Région-α"])
    run(path)
    result = pd.read_stata(tmp_path / "outputs" / "panel_long.dta")
    assert set(result.entity_id) == {"NA", "0001", "Région-α"}


def test_T09_missing_mismatched_and_unsupported_crs(tmp_path):
    path = create_case(tmp_path)
    write_tiff(tmp_path / "data.tif", [[1, 2], [3, 4]], crs=None)
    with pytest.raises(GeoPanelError, match="CRS_MISSING"):
        compute(path)
    write_tiff(tmp_path / "data.tif", [[1, 2], [3, 4]], crs="EPSG:4326")
    with pytest.raises(GeoPanelError, match="CRS_MISMATCH"):
        compute(path)
    edit_config(path, lambda d: d.update(spatial={"crs_action": "reproject_units"}))
    with pytest.raises(GeoPanelError, match="AREA_CRS_UNSUPPORTED"):
        compute(path)


def test_T10_invalid_geometry_stops(tmp_path):
    path = create_case(tmp_path)
    write_units(tmp_path / "units.geojson", [Polygon([(0, 0), (2, 2), (2, 0), (0, 2), (0, 0)])])
    with pytest.raises(GeoPanelError, match="GEOMETRY_INVALID"):
        compute(path)


def test_T11_duplicate_entities_and_times(tmp_path):
    path = create_case(tmp_path)
    write_units(tmp_path / "units.geojson", [box(0, 0, 1, 1)] * 2, ["001", "001"])
    with pytest.raises(GeoPanelError, match="DUPLICATE_ENTITY_ID"):
        compute(path)
    write_units(tmp_path / "units.geojson")
    def duplicates(d):
        d["sources"][0].pop("path")
        d["sources"][0].update(paths=["data.tif", "data.tif"], timestamps=["2020-01-01", "2020-01-31"])
    edit_config(path, duplicates)
    with pytest.raises(GeoPanelError, match="DUPLICATE_SOURCE_TIME"):
        compute(path)


def test_T12_missing_month_flags_and_nulls_annual_sum(tmp_path):
    path = create_case(tmp_path, "T05")
    def remove_month(d):
        d["sources"][0]["paths"].pop(5)
        d["sources"][0]["timestamps"].pop(5)
    edit_config(path, remove_month)
    row = compute(path).iloc[0]
    assert pd.isna(row.value)
    assert row.source_time_count == 11
    assert row.expected_time_count == 12
    assert "PARTIAL_TIME_WINDOW" in row.qc_flag
    assert row.valid_coverage_fraction == 0


def test_T14_repeat_scientific_hash_and_resolved_replay(tmp_path):
    path = create_case(tmp_path)
    first = run(path)
    replay = tmp_path / "outputs" / "resolved_config.yml"
    edit_config(replay, lambda d: d["project"].update(output_directory=str(tmp_path / "replayed")))
    second = run(replay)
    assert first["scientific_result_sha256"] == second["scientific_result_sha256"]
    assert first["input_sha256"] == second["input_sha256"]


def test_coverage_denominator_includes_outside_raster(tmp_path):
    path = create_case(tmp_path)
    write_units(tmp_path / "units.geojson", [box(0, 0, 4, 2)])
    row = compute(path).iloc[0]
    assert row.valid_coverage_fraction == pytest.approx(0.5)
    assert row.total_unit_area == 8
    edit_config(path, lambda d: d["variables"][0].update(minimum_coverage=0.8))
    row = compute(path).iloc[0]
    assert pd.isna(row.value)
    assert "LOW_COVERAGE" in row.qc_flag


def test_all_nodata_and_no_intersection_are_not_zero(tmp_path):
    path = create_case(tmp_path)
    write_tiff(tmp_path / "data.tif", [[np.nan] * 2] * 2)
    row = compute(path).iloc[0]
    assert pd.isna(row.value) and "NO_VALID_DATA" in row.qc_flag
    write_units(tmp_path / "units.geojson", [box(10, 10, 12, 12)])
    row = compute(path).iloc[0]
    assert pd.isna(row.value) and row.valid_area == 0


def test_mask_range_and_conditional_uncertainty(tmp_path):
    path = create_case(tmp_path)
    write_tiff(tmp_path / "sigma.tif", [[2, 2], [2, 2]])
    edit_config(path, lambda d: d["variables"][0].update(uncertainty_source="sigma.tif"))
    row = compute(path).iloc[0]
    assert row.uncertainty == pytest.approx(1)
    assert "CONDITIONAL_UNCERTAINTY_ONLY" in row.qc_flag
    write_tiff(tmp_path / "quality.tif", [[1, 0], [1, 1]])
    edit_config(path, lambda d: d["variables"][0].update(quality_mask="quality.tif", valid_range=[0, 30]))
    row = compute(path).iloc[0]
    assert row.value == pytest.approx(20)
    assert row.valid_coverage_fraction == pytest.approx(0.5)
    assert "OUT_OF_VALID_RANGE" in row.qc_flag and "QUALITY_MASKED" in row.qc_flag


def test_multivariable_long_and_wide_keys(tmp_path):
    path = create_case(tmp_path)
    def add_variable(d):
        extra = copy.deepcopy(d["variables"][0])
        extra["variable_id"] = "temperature_b"
        d["variables"].append(extra)
    edit_config(path, add_variable)
    manifest = run(path)
    assert manifest["row_count"] == 2
    assert len(pd.read_csv(tmp_path / "outputs" / "panel_wide.csv")) == 1


def test_overwrite_and_unknown_configuration_rejected(tmp_path):
    path = create_case(tmp_path)
    (tmp_path / "outputs").mkdir()
    with pytest.raises(GeoPanelError, match="OUTPUT_EXISTS"):
        run(path)
    edit_config(path, lambda d: d["variables"][0].update(minimum_coverge=0.9))
    with pytest.raises(GeoPanelError, match="CONFIG_INVALID"):
        load_config(path)


def test_non_gregorian_calendar_rejected(tmp_path):
    path = create_case(tmp_path, "T05", netcdf=True)
    file = tmp_path / "climate.nc"
    with xr.open_dataset(file, decode_times=False) as src:
        ds = src.load()
    ds.time.attrs["calendar"] = "360_day"
    ds.to_netcdf(file)
    with pytest.raises(GeoPanelError, match="UNSUPPORTED_CALENDAR"):
        compute(path)


def test_cli_shared_core_and_stable_failure_code(tmp_path):
    path = create_case(tmp_path)
    result = subprocess.run([sys.executable, "-m", "geopanel_eo", "run", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["row_count"] == 1
    again = subprocess.run([sys.executable, "-m", "geopanel_eo", "run", str(path)], capture_output=True, text=True)
    assert again.returncode == 2 and "OUTPUT_EXISTS" in again.stderr
