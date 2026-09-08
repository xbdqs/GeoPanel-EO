# Scientific and output contract

GeoPanel-EO v0.1.0 produces a canonical long panel keyed by `entity_id`, `time`, and `variable_id`. The optional wide table is derived from this result. Structural balance and numerical completeness are reported separately so that a retained null observation is not described as complete data.

For each polygon, exact partial-cell intersection fractions define valid support. The complete polygon area remains the coverage denominator, including portions outside the raster. Intensive states and accumulated depths use normalized exact-area weights in space; pixel totals are allocated by intersection fraction; and categorical values generate declared class areas and valid-classified-area fractions. Effective sample size describes weight concentration and is not a correction for spatial dependence.

Input temporal support is declared as monthly mean, monthly total, or monthly snapshot. Target periods may be monthly, quarterly, annual, or a declared subset of calendar months. Missing required months invalidate a complete-window mean or total and are retained with an explicit quality flag. Snapshot reducers select the last required month and never substitute an earlier observation.

Compatible cell-level standard uncertainties may be propagated under explicit independence assumptions. Coverage, cell count, weight sum, and effective sample size remain support diagnostics and are never relabeled as measurement uncertainty. Spatial and temporal dependence, registration error, product bias, and model uncertainty remain outside this conditional calculation.

GeoPanel-EO accepts valid Polygon or MultiPolygon units and rectilinear rasters in metre-based equal-area projections. It rejects geographic-area calculations, Web Mercator, implicit raster reprojection, curvilinear grids, non-Gregorian calendars, invalid geometry, duplicate keys, incompatible units, and unsupported semantic-reducer combinations.

The resolved configuration records all defaults. Input and output files are hashed, supported exports are read back and checked, and the scientific-result hash is derived from the canonical long CSV. Existing output directories are not overwritten; a completed result becomes visible only after validation succeeds.
