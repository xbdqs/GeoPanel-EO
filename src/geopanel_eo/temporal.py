"""Monthly source observations to explicit calendar periods."""

import numpy as np
import pandas as pd

from .errors import GeoPanelError


def output_temporal_support(variable):
    """Return a stable label for the target-period value actually produced."""
    frequency = variable.temporal_window.frequency
    if variable.temporal_support == "annual_product":
        return "annual_product"
    if variable.temporal_reducer == "sum":
        return f"{frequency}_total"
    if variable.temporal_reducer == "mean":
        return f"{frequency}_mean"
    if frequency == "monthly":
        return variable.temporal_support
    return f"{frequency}_end_snapshot"


def combine_flags(values):
    flags = set()
    for value in values:
        flags.update(str(value).split(";"))
    flags.discard("OK")
    return ";".join(sorted(flags)) or "OK"


def aggregate_time(records, variable, temporal=None):
    """Aggregate pre-validated spatial records using an optional explicit temporal contract.

    Legacy two-argument calls retain equal-month behavior; production should
    pass the validated Config.temporal object so expected bounds are honored.
    """
    frame = pd.DataFrame(records)
    dates = pd.to_datetime(frame.source_time)
    frame["month"] = dates.dt.to_period("M")
    if frame.duplicated(["entity_id", "month", "variable_id"]).any():
        raise GeoPanelError("DUPLICATE_ENTITY_TIME", "Repeated entity-month-variable input")
    frame = frame.loc[dates.dt.month.isin(variable.temporal_window.months)].copy()
    if frame.empty:
        raise GeoPanelError("TIME_WINDOW_EMPTY", variable.variable_id)
    frequency = variable.temporal_window.frequency
    pandas_frequency = {"monthly": "M", "quarterly": "Q-DEC", "annual": "Y-DEC"}[frequency]
    frame["period"] = frame.month.map(lambda m: m.asfreq(pandas_frequency))
    if temporal is not None and temporal.expected_start_month is not None:
        start = pd.Period(temporal.expected_start_month, freq="M").asfreq(pandas_frequency)
        end = pd.Period(temporal.expected_end_month, freq="M").asfreq(pandas_frequency)
        periods = pd.period_range(start, end, freq=pandas_frequency)
        frame = frame.loc[frame.period.isin(periods)].copy()
        if frame.empty:
            raise GeoPanelError("TIME_BOUNDS_EMPTY", variable.variable_id)
    else:
        periods = pd.period_range(frame.period.min(), frame.period.max(), freq=pandas_frequency)
    weighting = getattr(temporal, "mean_weighting", "equal_month")
    declared_days = getattr(temporal, "represented_day_counts", {})
    result = []
    for (entity, variable_id), series in frame.groupby(["entity_id", "variable_id"], sort=True):
        for period in periods:
            expected = [m for m in pd.period_range(period.start_time, period.end_time, freq="M")
                        if m.month in variable.temporal_window.months]
            if not expected:
                continue
            observed = series.loc[series.period == period].sort_values("month")
            available_count = len(observed)
            if variable.temporal_reducer == "snapshot":
                # Snapshot means the final selected calendar month, not last nonmissing observation.
                observed = observed.loc[observed.month == expected[-1]]
                expected_count = 1
            else:
                expected_count = len(expected)
            flags = combine_flags(observed.qc_flag)
            partial = len(observed) != expected_count or observed.value.isna().any()
            valid_count = int(observed.value.notna().sum())
            if partial:
                flags = combine_flags([flags, "PARTIAL_TIME_WINDOW"])
            if observed.empty:
                flags = combine_flags([flags, "NO_VALID_DATA"])
            value, uncertainty = np.nan, np.nan
            mean_weighting = weighting if variable.temporal_reducer == "mean" else "not_applicable"
            represented_day_count = np.nan
            if not partial:
                weights = np.ones(len(observed), dtype=float)
                if variable.temporal_reducer == "mean" and weighting == "represented_days":
                    month_labels = [str(month) for month in observed.month]
                    missing = [month for month in month_labels if month not in declared_days]
                    if missing:
                        raise GeoPanelError("DAY_COUNTS_MISSING",
                            f"{variable.variable_id}: no upstream represented-day counts for {', '.join(missing)}")
                    weights = np.array([declared_days[month] for month in month_labels], dtype=float)
                    represented_day_count = int(weights.sum())
                if variable.temporal_reducer == "sum":
                    value = float(observed.value.sum())
                elif variable.temporal_reducer == "mean":
                    value = float(np.average(observed.value.to_numpy(dtype=float), weights=weights))
                else:
                    value = float(observed.value.iloc[-1])
                if observed.uncertainty.notna().all():
                    sigma = observed.uncertainty.to_numpy(dtype=float)
                    if variable.temporal_reducer == "mean":
                        uncertainty = float(np.sqrt(np.square(weights * sigma).sum()) / weights.sum())
                    else:
                        uncertainty = float(np.sqrt(np.square(sigma).sum()))
            # Conservative target-period diagnostics; full per-slice values are also exported.
            complete_support = len(observed) == expected_count
            row = dict(entity_id=entity, time=str(period), time_code=period.year if frequency == "annual" else period.ordinal,
                time_frequency=frequency, period_support=output_temporal_support(variable),
                temporal_mean_weighting=mean_weighting, represented_day_count=represented_day_count,
                entity_name=str(series.entity_name.iloc[0]), variable_id=variable_id, value=value, qc_flag=flags,
                uncertainty=uncertainty, source_time_count=len(observed), available_time_count=available_count,
                valid_time_count=valid_count, expected_time_count=expected_count,
                total_unit_area=float(series.total_unit_area.iloc[0]))
            for field in ("valid_coverage_fraction", "valid_area", "contributing_cell_count", "effective_sample_size", "weight_sum"):
                row[field] = float(observed[field].min()) if complete_support else 0.0
            result.append(row)
    return result
