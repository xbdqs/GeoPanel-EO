"""Strict S1 configuration; unsupported features must never be ignored."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .errors import GeoPanelError


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Project(StrictModel):
    project_id: str = Field(min_length=1)
    title: str = ""
    output_directory: str = "outputs"
    overwrite: Literal[False] = False


class Units(StrictModel):
    source: str
    layer: str | None = None
    entity_id_field: str = "entity_id"
    entity_name_field: str | None = None
    geometry_action: Literal["fail"] = "fail"
    crs: str | None = None
    target_crs: str | None = None


class Source(StrictModel):
    source_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    type: Literal["geotiff", "netcdf"]
    path: str | None = None
    paths: list[str] | None = None
    timestamps: list[str] | None = None
    time_pattern: str | None = None
    time_format: str = "%Y-%m-%d"
    time_coordinate: str = "time"
    x_coordinate: str = "lon"
    y_coordinate: str = "lat"
    crs: str | None = None
    band: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def check_source(self):
        if (self.path is None) == (self.paths is None):
            raise ValueError("Provide exactly one of path or paths")
        if self.paths == []:
            raise ValueError("paths cannot be empty")
        if self.type == "netcdf" and (self.path is None or self.timestamps or self.time_pattern):
            raise ValueError("NetCDF requires path and its internal time coordinate")
        if self.type == "geotiff" and ((self.timestamps is None) == (self.time_pattern is None)):
            raise ValueError("GeoTIFF requires exactly one of timestamps or time_pattern")
        if self.time_pattern and "(?P<time>" not in self.time_pattern:
            raise ValueError("time_pattern must contain a named (?P<time>...) group")
        if self.timestamps is not None and len(self.timestamps) != len(self.paths or [self.path]):
            raise ValueError("timestamps must match the file count")
        return self


class Window(StrictModel):
    frequency: Literal["monthly", "annual", "quarterly"] = "annual"
    months: list[int] = Field(default_factory=lambda: list(range(1, 13)))

    @model_validator(mode="after")
    def check_months(self):
        if not self.months or self.months != sorted(set(self.months)) or not all(1 <= x <= 12 for x in self.months):
            raise ValueError("months must be nonempty, ascending, unique and in 1..12; cross-year seasons are not supported in S1")
        return self


class Variable(StrictModel):
    variable_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    source_id: str
    source_variable: str
    semantic_type: Literal["intensive_state", "accumulation_depth", "extensive_total", "categorical_class"]
    unit: str
    temporal_support: Literal["monthly_mean", "monthly_total", "monthly_snapshot"]
    spatial_reducer: Literal["coverage_weighted_mean", "coverage_adjusted_sum", "area_by_class_and_fraction"]
    temporal_reducer: Literal["mean", "sum", "snapshot"]
    temporal_window: Window = Field(default_factory=Window)
    valid_range: tuple[float, float] | None = None
    nodata_rule: Literal["exclude"] = "exclude"
    minimum_coverage: float = Field(default=0.8, ge=0, le=1)
    quality_mask: str | None = None
    optional_weight: str | None = None
    weight_semantics: Literal["cell_total"] = "cell_total"
    uncertainty_source: str | None = None
    classes: list[int] | None = None

    @field_validator("unit")
    @classmethod
    def check_unit(cls, value):
        if not value.strip() or value != value.strip():
            raise ValueError("UNIT_INVALID: unit must be a nonempty, trimmed physical-unit label")
        return value

    @model_validator(mode="after")
    def semantics(self):
        rules = {
            "intensive_state": ("coverage_weighted_mean", {"mean", "snapshot"}, {"monthly_mean", "monthly_snapshot"}),
            "accumulation_depth": ("coverage_weighted_mean", {"sum", "snapshot"}, {"monthly_total"}),
            "extensive_total": ("coverage_adjusted_sum", {"sum", "snapshot"}, {"monthly_total"}),
            "categorical_class": ("area_by_class_and_fraction", {"snapshot"}, {"monthly_snapshot"}),
        }
        spatial, temporal, support = rules[self.semantic_type]
        if self.spatial_reducer != spatial or self.temporal_reducer not in temporal:
            raise ValueError(f"SEMANTIC_RULE: invalid reducer for {self.semantic_type}")
        if self.temporal_support not in support:
            raise ValueError(f"TEMPORAL_SUPPORT_RULE: invalid temporal_support for {self.semantic_type}")
        if self.valid_range and self.valid_range[0] > self.valid_range[1]:
            raise ValueError("valid_range lower bound exceeds upper bound")
        if self.semantic_type == "categorical_class":
            if not self.classes or len(self.classes) != len(set(self.classes)):
                raise ValueError("Categorical variables require unique, declared classes")
            if self.optional_weight or self.uncertainty_source:
                raise ValueError("S1 categorical area does not accept weights or uncertainty")
        elif self.classes is not None:
            raise ValueError("classes is only valid for categorical_class")
        if self.optional_weight and self.spatial_reducer != "coverage_weighted_mean":
            raise ValueError("Additional weights only supported for means")
        return self


class Spatial(StrictModel):
    crs_action: Literal["fail", "reproject_units"] = "fail"
    area_method: Literal["projected_equal_area"] = "projected_equal_area"


class Temporal(StrictModel):
    calendar: Literal["proleptic_gregorian", "gregorian", "standard"] = "proleptic_gregorian"
    timezone: Literal["naive"] = "naive"
    input_frequency: Literal["monthly"] = "monthly"
    mean_weighting: Literal["equal_month"] = "equal_month"
    incomplete_window: Literal["flag_and_null"] = "flag_and_null"


class Panel(StrictModel):
    require_unique_entity_time: Literal[True] = True
    require_balanced: bool = False
    retain_flagged_rows: Literal[True] = True


class Uncertainty(StrictModel):
    assumption: Literal["independent_cells_and_times"] = "independent_cells_and_times"


class Outputs(StrictModel):
    csv: Literal[True] = True
    parquet: bool = True
    stata: bool = True
    wide_csv: bool = False
    quality_report_html: bool = True
    hash_algorithm: Literal["sha256"] = "sha256"


class Config(StrictModel):
    project: Project
    units: Units
    sources: list[Source] = Field(min_length=1)
    variables: list[Variable] = Field(min_length=1)
    spatial: Spatial = Field(default_factory=Spatial)
    temporal: Temporal = Field(default_factory=Temporal)
    panel: Panel = Field(default_factory=Panel)
    uncertainty: Uncertainty = Field(default_factory=Uncertainty)
    outputs: Outputs = Field(default_factory=Outputs)

    @model_validator(mode="after")
    def references(self):
        source_ids = [s.source_id for s in self.sources]
        variable_ids = [v.variable_id for v in self.variables]
        if len(source_ids) != len(set(source_ids)) or len(variable_ids) != len(set(variable_ids)):
            raise ValueError("Duplicate source_id or variable_id")
        if any(v.source_id not in source_ids for v in self.variables):
            raise ValueError("Unknown source_id")
        sources = {s.source_id: s for s in self.sources}
        for variable in self.variables:
            source = sources[variable.source_id]
            if source.type == "geotiff" and variable.source_variable != f"band_{source.band}":
                raise ValueError("GeoTIFF source_variable must explicitly match band_<source.band>")
        expanded = []
        for v in self.variables:
            expanded.extend([f"{v.variable_id}_class_{c}_{kind}" for c in v.classes for kind in ("area", "fraction")] if v.classes else [v.variable_id])
        if len(expanded) != len(set(expanded)):
            raise ValueError("Derived class variable IDs collide")
        return self


def load_config(path: str | Path) -> Config:
    path = Path(path).resolve()
    try:
        config = Config.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise GeoPanelError("CONFIG_INVALID", str(exc)) from exc
    def resolve(value):
        return str((path.parent / value).resolve()) if value is not None else None
    config.project.output_directory = resolve(config.project.output_directory)
    config.units.source = resolve(config.units.source)
    for source in config.sources:
        source.path = resolve(source.path)
        if source.paths:
            source.paths = [resolve(p) for p in source.paths]
    for variable in config.variables:
        for field in ("quality_mask", "optional_weight", "uncertainty_source"):
            setattr(variable, field, resolve(getattr(variable, field)))
    return config
