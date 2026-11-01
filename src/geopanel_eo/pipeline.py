"""Shared application API: inputs -> scientific panel -> audited artifacts."""

from datetime import datetime, timezone
import hashlib
import html
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import tempfile

import numpy as np
import pandas as pd
import yaml

from . import __version__
from .config import load_config
from .errors import GeoPanelError
from .inputs import read_source, read_units, units_in_crs
from .spatial import spatial_records
from .support import spatial_support_diagnostics
from .preprocess_audit import validate_preprocessing_manifest
from .temporal import aggregate_time, combine_flags, output_temporal_support


KEY = ["entity_id", "time", "variable_id"]
DEPENDENCIES = ["numpy", "pandas", "rasterio", "shapely", "pyproj", "pyogrio", "xarray", "h5netcdf", "h5py", "scipy", "exactextract", "pydantic", "PyYAML", "pyarrow"]


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def input_paths(config):
    paths = {Path(config.units.source)}
    if config.project.preprocessing_manifest is not None:
        paths.add(Path(config.project.preprocessing_manifest))
    if Path(config.units.source).suffix.lower() == ".shp":
        paths.update(p for p in Path(config.units.source).parent.glob(Path(config.units.source).stem + ".*") if p.suffix.lower() in {".shp", ".shx", ".dbf", ".prj", ".cpg", ".qix"})
    for source in config.sources:
        paths.update(Path(p) for p in (source.paths or [source.path]))
    for variable in config.variables:
        paths.update(Path(p) for p in [variable.optional_weight, variable.quality_mask, variable.uncertainty_source] if p)
    if any(not p.is_file() for p in paths):
        raise GeoPanelError("INPUT_MISSING", ", ".join(str(p) for p in paths if not p.is_file()))
    return sorted(paths, key=str)


def build_panel(config):
    units, unit_crs = read_units(config.units)
    sources = {s.source_id: s for s in config.sources}
    all_records, slices, audit = [], [], []
    support_diagnostics = []
    overlap_pairs = []
    for i, u in enumerate(units):
        for v in units[i + 1:]:
            if u.geometry.intersection(v.geometry).area > 0:
                overlap_pairs.append([u.entity_id, v.entity_id])
    for variable in config.variables:
        records = []
        valid_support = [] if config.spatial.support_overlap_diagnostics else None
        for raster in read_source(sources[variable.source_id], variable):
            projected, reprojected = units_in_crs(units, unit_crs, raster.crs, config)
            values = spatial_records(raster, projected, variable, support_collector=valid_support)
            if reprojected:
                for row in values:
                    row["qc_flag"] = combine_flags([row["qc_flag"], "CRS_ACTION_RECORDED"])
            records.extend(values)
            audit.append(dict(source_id=variable.source_id, variable_id=variable.variable_id, path=raster.path,
                timestamp=raster.time.isoformat(), calendar=raster.calendar, crs=raster.crs.to_wkt(),
                declared_unit=variable.unit, stored_unit=raster.stored_unit,
                unit_metadata_status="matched" if raster.stored_unit is not None else "declared_only",
                units_source_crs=unit_crs.to_wkt(), crs_action="reproject_units" if reprojected else "none",
                shape=list(raster.values.shape), transform=list(raster.transform)[:6],
                missing_cell_count=int(np.count_nonzero(~np.isfinite(raster.values))),
                area_method=config.spatial.area_method))
        slices.extend(records)
        aggregated = aggregate_time(records, variable, config.temporal)
        all_records.extend(aggregated)
        if valid_support is not None:
            support_diagnostics.extend(spatial_support_diagnostics(valid_support, variable, aggregated))
    panel = pd.DataFrame(all_records).sort_values(KEY).reset_index(drop=True)
    if panel[KEY].isna().any().any() or panel.duplicated(KEY).any():
        raise GeoPanelError("DUPLICATE_ENTITY_TIME", "Canonical long key is entity_id + time + variable_id")
    if not np.isfinite(panel.value.dropna()).all():
        raise GeoPanelError("RESULT_NONFINITE", "Aggregation overflow produced nonfinite results")
    balance = []
    for variable_id, group in panel.groupby("variable_id", sort=True):
        n_times = group.time.nunique()
        for entity in sorted(u.entity_id for u in units):
            observed = group.loc[group.entity_id == entity]
            balance.append(dict(variable_id=variable_id, entity_id=entity, expected_time_count=n_times,
                row_count=len(observed), nonmissing_value_count=int(observed.value.notna().sum()),
                missing_value_count=int(observed.value.isna().sum()),
                structurally_balanced=len(observed) == n_times,
                complete_values=len(observed) == n_times and observed.value.notna().all()))
    balance = pd.DataFrame(balance)
    # A shared entity-time grid across variables is required when balance is requested.
    pair_sets = [set(zip(g.entity_id, g.time)) for _, g in panel.groupby("variable_id")]
    shared_grid = all(s == pair_sets[0] for s in pair_sets)
    if config.panel.require_balanced and (not balance.structurally_balanced.all() or not shared_grid):
        raise GeoPanelError("PANEL_UNBALANCED", "Variable panels do not share a complete entity-time grid")
    return panel, pd.DataFrame(slices).sort_values(["entity_id", "source_time", "variable_id"]), balance, dict(
        inputs=audit, overlap_pairs=overlap_pairs, geometry_repair_count=0, shared_entity_time_grid=shared_grid,
        spatial_support_diagnostics=support_diagnostics)


def write_csv(frame, path):
    frame.to_csv(path, index=False, float_format="%.17g", lineterminator="\n", na_rep="")


def verify_roundtrip(expected, actual):
    actual = actual.sort_values(KEY).reset_index(drop=True)
    if len(expected) != len(actual) or expected[KEY].astype(str).to_dict("list") != actual[KEY].astype(str).to_dict("list"):
        raise GeoPanelError("OUTPUT_ROUNDTRIP_FAILED", "Output row count or panel keys changed")
    for column in expected.columns:
        if pd.api.types.is_numeric_dtype(expected[column]):
            if not np.allclose(expected[column].to_numpy(dtype=float), actual[column].to_numpy(dtype=float), rtol=1e-12, atol=1e-12, equal_nan=True):
                raise GeoPanelError("OUTPUT_ROUNDTRIP_FAILED", f"Numeric values changed: {column}")
        elif expected[column].astype(str).tolist() != actual[column].astype(str).tolist():
            raise GeoPanelError("OUTPUT_ROUNDTRIP_FAILED", f"Text values changed: {column}")


def codebook(config):
    rows = []
    for v in config.variables:
        variants = [(v.variable_id, v.unit, "")]
        if v.classes:
            variants = [(f"{v.variable_id}_class_{c}_{k}", "m2" if k == "area" else "1", str(c)) for c in v.classes for k in ("area", "fraction")]
        for variable_id, unit, category in variants:
            rows.append(dict(variable_id=variable_id, parent_variable_id=v.variable_id,
                source_id=v.source_id, source_variable=v.source_variable, semantic_type=v.semantic_type,
                input_unit=v.unit, output_unit=unit, input_temporal_support=v.temporal_support,
                timestamp_interpretation="annual_product_year_anchor" if v.temporal_support == "annual_product" else "source_observation_month",
                output_temporal_support=output_temporal_support(v), spatial_reducer=v.spatial_reducer,
                temporal_reducer=v.temporal_reducer, frequency=v.temporal_window.frequency,
                temporal_mean_weighting=config.temporal.mean_weighting if v.temporal_reducer == "mean" else "not_applicable",
                represented_day_count_source="explicit_upstream_counts" if (v.temporal_reducer == "mean" and config.temporal.mean_weighting == "represented_days") else "not_applicable",
                expected_start_month=config.temporal.expected_start_month or "inferred_from_source",
                expected_end_month=config.temporal.expected_end_month or "inferred_from_source",
                months=",".join(map(str, v.temporal_window.months)), class_code=category,
                minimum_coverage=v.minimum_coverage, nodata_rule=v.nodata_rule,
                weight_semantics=v.weight_semantics if v.optional_weight else "none",
                class_fraction_denominator="valid_classified_area" if v.classes else "",
                uncertainty_assumption=config.uncertainty.assumption if v.uncertainty_source else "none"))
    return pd.DataFrame(rows).sort_values("variable_id")


def run(config_path):
    config = load_config(config_path)
    output = Path(config.project.output_directory)
    if output.exists():
        raise GeoPanelError("OUTPUT_EXISTS", f"Choose a new output directory: {output}")
    paths = input_paths(config)
    hashes = {str(path): sha256(path) for path in paths}
    upstream_audit = None
    if config.project.preprocessing_manifest is not None:
        raster_paths = sorted({str(Path(path).resolve()) for source in config.sources for path in
                               (source.paths if source.paths is not None else [source.path])})
        upstream_audit = validate_preprocessing_manifest(config.project.preprocessing_manifest, raster_paths)
    panel, slices, balance, audit = build_panel(config)
    if any(sha256(path) != hashes[str(path)] for path in paths):
        raise GeoPanelError("INPUT_CHANGED_DURING_RUN", "An input changed while it was being processed")
    if upstream_audit is not None:
        after_audit = validate_preprocessing_manifest(config.project.preprocessing_manifest, raster_paths)
        if after_audit["manifest_sha256"] != upstream_audit["manifest_sha256"]:
            raise GeoPanelError("PREPROCESS_MANIFEST_CHANGED", "Preprocessing manifest changed during run")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-pending-", dir=output.parent))
    try:
        resolved = config.model_dump(mode="json")
        (staging / "resolved_config.yml").write_text(yaml.safe_dump(resolved, sort_keys=True, allow_unicode=True), encoding="utf-8")
        write_csv(panel, staging / "panel_long.csv")
        verify_roundtrip(panel, pd.read_csv(staging / "panel_long.csv", dtype={k: str for k in KEY}, keep_default_na=False,
            na_values={c: [""] for c in panel.columns if pd.api.types.is_numeric_dtype(panel[c])}, float_precision="round_trip"))
        if config.outputs.parquet:
            panel.to_parquet(staging / "panel_long.parquet", index=False)
            verify_roundtrip(panel, pd.read_parquet(staging / "panel_long.parquet"))
        if config.outputs.stata:
            stata = panel.copy()
            for col in stata:
                if not pd.api.types.is_numeric_dtype(stata[col]):
                    stata[col] = stata[col].astype(object)
            stata.to_stata(staging / "panel_long.dta", write_index=False, version=118,
                time_stamp=datetime(2000, 1, 1), data_label="GeoPanel-EO S1; units in variable_codebook.csv",
                variable_labels={"entity_id": "Stable entity identifier", "time": "Readable target period",
                    "entity_name": "Optional entity name retained from units input",
                    "time_code": "Year or pandas period ordinal; see time_frequency", "value": "Estimate; units keyed by variable_id in codebook"})
            verify_roundtrip(panel, pd.read_stata(staging / "panel_long.dta", convert_categoricals=False))
        if config.outputs.wide_csv:
            wide = panel.pivot(index=["entity_id", "entity_name", "time"], columns="variable_id", values="value").reset_index()
            write_csv(wide, staging / "panel_wide.csv")
        write_csv(codebook(config), staging / "variable_codebook.csv")
        write_csv(panel.drop(columns=["value"]), staging / "coverage_diagnostics.csv")
        write_csv(slices, staging / "source_diagnostics.csv")
        write_csv(balance, staging / "panel_balance_report.csv")
        if config.spatial.support_overlap_diagnostics:
            write_csv(pd.DataFrame(audit["spatial_support_diagnostics"]), staging / "spatial_support_overlap.csv")
        warnings = sorted({flag for flags in panel.qc_flag for flag in flags.split(";") if flag != "OK"})
        if audit["overlap_pairs"]:
            warnings.append("UNITS_OVERLAP")
        (staging / "processing_log.txt").write_text("\n".join(["RUN_STARTED: GeoPanel-EO " + __version__] +
            [code + ": see diagnostics and manifest" for code in warnings] + ["OUTPUT_ROUNDTRIP_OK: enabled panel formats verified", "RUN_COMPLETED: all requested outputs written"]) + "\n", encoding="utf-8")
        if config.outputs.quality_report_html:
            (staging / "quality_report.html").write_text("<!doctype html><html lang='en'><meta charset='utf-8'><title>GeoPanel-EO QC</title>"
                "<style>body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:0 20px}table{border-collapse:collapse}td,th{padding:8px;border:1px solid #ddd}</style>"
                f"<h1>{html.escape(config.project.title or config.project.project_id)}</h1>"
                f"<p>Records: {len(panel)}; entities: {panel.entity_id.nunique()}; time labels: {panel.time.nunique()}; derived variables: {panel.variable_id.nunique()}; missing values: {panel.value.isna().sum()}.</p>"
                f"<p>QC: {html.escape(', '.join(warnings) or 'OK')}. Area unit: m²; the coverage denominator is the complete analysis-unit area.</p>"
                f"<p>Target-period support diagnostics use the minimum monthly support; missing months contribute zero support. Mean weighting: {html.escape(config.temporal.mean_weighting)}; incomplete windows are set to null. Class fractions use the valid classified area as denominator.</p>"
                "<p>Input uncertainty is propagated only under independence of pixel and monthly errors and must not be interpreted as total measurement uncertainty.</p>" + balance.to_html(index=False) + "</html>", encoding="utf-8")
        # Re-read all auxiliary tabular/config/report artifacts before recording their hashes.
        for path in staging.glob("*.csv"):
            pd.read_csv(path)
        yaml.safe_load((staging / "resolved_config.yml").read_text(encoding="utf-8"))
        output_hashes = {p.name: sha256(p) for p in sorted(staging.iterdir())}
        manifest = dict(schema_version="s1-3-explicit-time-contract", software_version=__version__,
            preprocessing_audit=upstream_audit,
            temporal_mean_weighting=config.temporal.mean_weighting,
            expected_start_month=config.temporal.expected_start_month,
            expected_end_month=config.temporal.expected_end_month,
            support_overlap_diagnostics_enabled=config.spatial.support_overlap_diagnostics,
            support_overlap_status_counts=(pd.Series([r["support_status"] for r in audit["spatial_support_diagnostics"]]).value_counts().to_dict()
                                           if config.spatial.support_overlap_diagnostics else {}),
            started_at_utc=datetime.now(timezone.utc).isoformat(), python=platform.python_version(), platform=platform.platform(),
            dependencies={name: importlib.metadata.version(name) for name in DEPENDENCIES},
            input_sha256=hashes, config_sha256=sha256(config_path), resolved_config_sha256=output_hashes["resolved_config.yml"],
            output_sha256=output_hashes, scientific_result_sha256=output_hashes["panel_long.csv"],
            row_count=len(panel), entity_count=panel.entity_id.nunique(), time_count=panel.time.nunique(),
            variable_count=panel.variable_id.nunique(), null_value_count=int(panel.value.isna().sum()),
            dropped_row_count=0, warnings=warnings,
            audit={k: v for k, v in audit.items() if k != "spatial_support_diagnostics"},
            uncertainty_assumption=config.uncertainty.assumption,
            long_key=KEY, wide_key=["entity_id", "time"], roundtrip_verified=True)
        (staging / "run_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        json.loads((staging / "run_manifest.json").read_text(encoding="utf-8"))
        if output.exists():
            raise GeoPanelError("OUTPUT_EXISTS", "Output appeared during processing")
        os.rename(staging, output)
    except Exception as exc:
        (staging / "RUN_FAILED.txt").write_text(str(exc), encoding="utf-8")
        raise
    return manifest
