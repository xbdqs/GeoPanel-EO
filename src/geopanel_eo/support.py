"""Optional R3 spatial-support overlap diagnostics for a COMMON aligned raster grid.

This diagnostic is not a spatial-covariance model and does not enforce a common
support or reinterpret the scientific values. It reports overlap only if the
original pixel footprints are aligned; otherwise it refuses direct comparison.
"""

import numpy as np
import pandas as pd


def spatial_support_diagnostics(slice_support, variable, target_rows):
    """Return one diagnostic row per entity/target period for a source variable.

    slice_support are per-raster polygon/cell records collected using the same
    validity checks as spatial_records. Common/union areas are exact for the
    *same grid* since each covered pixel has fixed polygon intersection area.
    """
    freq = variable.temporal_window.frequency
    pandas_freq = {"monthly": "M", "quarterly": "Q-DEC", "annual": "Y-DEC"}[freq]
    indexed = {(str(r["entity_id"]), str(r["source_month"])): r for r in slice_support}
    if len(indexed) != len(slice_support):
        raise ValueError("Duplicate entity-month in support diagnostics")
    periods = sorted(set((str(r["entity_id"]), str(r["time"])) for r in target_rows))
    result = []
    for entity, period_label in periods:
        period = pd.Period(period_label, freq=pandas_freq)
        months = [str(m) for m in pd.period_range(period.start_time, period.end_time, freq="M")
                  if m.month in variable.temporal_window.months]
        if variable.temporal_reducer == "snapshot":
            months = months[-1:]
        records = [indexed.get((entity, m)) for m in months]
        present = [r for r in records if r is not None]
        record = dict(entity_id=entity, time=period_label, parent_variable_id=variable.variable_id,
                      expected_support_months=len(months), observed_support_months=len(present),
                      minimum_monthly_coverage_fraction=np.nan,
                      common_valid_area_m2=np.nan, union_valid_area_m2=np.nan,
                      common_valid_fraction=np.nan, union_valid_fraction=np.nan,
                      common_union_jaccard=np.nan, support_status="INCOMPLETE_WINDOW")
        if not present:
            result.append(record)
            continue
        total = present[0]["total_unit_area"]
        record["minimum_monthly_coverage_fraction"] = min(
            sum(r["valid_cells"].values())/r["total_unit_area"] for r in present)
        if len(present) != len(months):
            result.append(record)
            continue
        if len({r["grid_signature"] for r in present}) > 1 or any(
                not np.isclose(r["total_unit_area"], total, rtol=1e-10, atol=1e-6) for r in present):
            record["support_status"] = "GRID_MISMATCH"
            result.append(record)
            continue
        sets = [set(r["valid_cells"]) for r in present]
        common_ids = set.intersection(*sets)
        union_ids = set.union(*sets)
        # On a fixed raster grid every pixel's intersection area must be fixed.
        area = {}
        for r in present:
            for index, value in r["valid_cells"].items():
                if index in area and not np.isclose(area[index], value, rtol=1e-9, atol=1e-7):
                    raise ValueError("Grid support areas inconsistent for identical cell index")
                area[index] = value
        common = float(sum(area[i] for i in common_ids))
        union = float(sum(area[i] for i in union_ids))
        record.update(common_valid_area_m2=common, union_valid_area_m2=union,
                      common_valid_fraction=common/total, union_valid_fraction=union/total,
                      common_union_jaccard=common/union if union > 0 else np.nan)
        if len(present) == 1:
            record["support_status"] = "SINGLE_OBSERVATION"
        elif any(not r["spatial_value_usable"] for r in present):
            record["support_status"] = "INVALID_MONTHLY_VALUE"
        elif np.isclose(common, union, atol=1e-8, rtol=1e-10):
            record["support_status"] = "CONSISTENT_SUPPORT"
        else:
            record["support_status"] = "CHANGING_SUPPORT"
        result.append(record)
    return result
