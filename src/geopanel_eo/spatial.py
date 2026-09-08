"""Exact pixel coverage with explicit support and variable semantics."""

import numpy as np
from exactextract import exact_extract
from exactextract.feature import JSONFeatureSource
from exactextract.raster import NumPyRasterSource
from shapely.geometry import mapping

from .errors import GeoPanelError
from .inputs import aligned_auxiliary, validate_area_crs


def spatial_records(raster, units, variable):
    validate_area_crs(raster.crs)
    a = raster.transform
    height, width = raster.values.shape
    # An unmasked index grid makes coverage independent of data NoData handling.
    index_grid = NumPyRasterSource(np.zeros((height, width), dtype=np.uint8),
        xmin=a.c, ymin=a.f + a.e * height, xmax=a.c + a.a * width, ymax=a.f,
        srs_wkt=raster.crs.to_wkt())
    features = JSONFeatureSource([{"type": "Feature", "properties": {"entity_id": u.entity_id},
        "geometry": mapping(u.geometry)} for u in units], srs_wkt=raster.crs.to_wkt())
    extracted = exact_extract(index_grid, features, ["cell_id", "coverage"], include_cols=["entity_id"])
    weight = aligned_auxiliary(variable.optional_weight, raster)
    quality = aligned_auxiliary(variable.quality_mask, raster)
    sigma = aligned_auxiliary(variable.uncertainty_source, raster)
    if weight is not None and np.any(np.isfinite(weight) & (weight < 0)):
        raise GeoPanelError("WEIGHT_INVALID", "Weights cannot be negative")
    if sigma is not None and np.any(np.isfinite(sigma) & (sigma < 0)):
        raise GeoPanelError("UNCERTAINTY_INVALID", "Standard uncertainties cannot be negative")
    cell_area = abs(a.a * a.e)
    records = []
    for unit, feature in zip(units, extracted, strict=True):
        props = feature["properties"]
        indices = np.asarray(props["cell_id"], dtype=int)
        coverage = np.asarray(props["coverage"], dtype=float)
        values = raster.values.ravel()[indices]
        valid = np.isfinite(values)
        flags = set()
        if variable.valid_range is not None:
            in_range = (values >= variable.valid_range[0]) & (values <= variable.valid_range[1])
            if np.any(valid & ~in_range):
                flags.add("OUT_OF_VALID_RANGE")
            valid &= in_range
        if variable.classes:
            known = np.isin(values, variable.classes)
            if np.any(valid & ~known):
                flags.add("UNKNOWN_CLASS")
            valid &= known
        if quality is not None:
            accepted = np.isfinite(quality[indices]) & (quality[indices] > 0)
            if np.any(valid & ~accepted):
                flags.add("QUALITY_MASKED")
            valid &= accepted
        if weight is not None:
            if np.any(valid & ~np.isfinite(weight[indices])):
                flags.add("WEIGHT_MISSING")
            valid &= np.isfinite(weight[indices])
        areas = coverage * cell_area
        valid_area = float(areas[valid].sum())
        total_area = float(unit.geometry.area)
        fraction = valid_area / total_area
        if fraction > 1 + 1e-6:
            raise GeoPanelError("COVERAGE_INVALID", "Computed coverage exceeds polygon area")
        fraction = min(1.0, fraction)
        if not valid.any():
            flags.add("NO_VALID_DATA")
        if fraction + 1e-12 < variable.minimum_coverage:
            flags.add("LOW_COVERAGE")
        c, x = coverage[valid], values[valid]
        if variable.optional_weight:
            weights = c * weight[indices][valid]  # explicitly cell totals, not density
        elif variable.spatial_reducer == "coverage_adjusted_sum":
            weights = c
        else:
            weights = c * cell_area
        weight_sum = float(weights.sum())
        if weight_sum <= 0:
            flags.add("NO_VALID_DATA")
        n_eff = float(weight_sum ** 2 / np.square(weights).sum()) if weight_sum > 0 else 0.0
        null_result = bool(flags & {"NO_VALID_DATA", "LOW_COVERAGE"})
        uncertainty = np.nan
        if sigma is not None:
            flags.add("CONDITIONAL_UNCERTAINTY_ONLY")
            s = sigma[indices][valid]
            if not np.isfinite(s).all():
                flags.add("UNCERTAINTY_MISSING")
            elif not null_result:
                coefficients = weights if variable.spatial_reducer == "coverage_adjusted_sum" else weights / weight_sum
                uncertainty = float(np.sqrt(np.square(coefficients * s).sum()))
        base = dict(entity_id=unit.entity_id, entity_name=unit.entity_name, source_time=raster.time.isoformat(),
            valid_coverage_fraction=fraction, valid_area=valid_area, total_unit_area=total_area,
            contributing_cell_count=int(np.count_nonzero(weights > 0)),
            effective_sample_size=n_eff, weight_sum=weight_sum,
            uncertainty=uncertainty, qc_flag=";".join(sorted(flags)) or "OK")
        if variable.classes:
            for category in variable.classes:
                area = float(areas[valid][x == category].sum())
                for kind, value in (("area", area), ("fraction", area / valid_area if valid_area else np.nan)):
                    records.append({**base, "variable_id": f"{variable.variable_id}_class_{category}_{kind}",
                        "value": np.nan if null_result else value})
        else:
            value = np.nan
            if not null_result:
                value = float(np.dot(weights, x))
                if variable.spatial_reducer == "coverage_weighted_mean":
                    value /= weight_sum
            records.append({**base, "variable_id": variable.variable_id, "value": value})
    return records
