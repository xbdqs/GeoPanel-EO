# GeoPanel-EO v0.1.1

This release expands the auditable scientific workflow for transforming Earth observation raster data into longitudinal panel data.

## Highlights

- Explicit temporal semantics for annual raster products and annual aggregation, including month-duration weighting.
- Cross-time spatial-support diagnostics to surface changes in coverage.
- Source/preprocessing manifest verification with SHA-256 provenance checks, while distinguishing historical outputs from newly audited runs.
- Revised configuration schema, validation reports, migration documentation, and reproducibility tooling.
- Reviewer-focused synthetic and contract tests alongside the scientific-core test suite.

## Verification

- 42 scientific-core tests and 74 reviewer-focused tests (116 total) passed on the release candidate.
- GitHub Actions Python 3.12 workflows passed on Windows and Ubuntu, including building a wheel and source distribution, installing the wheel in a fresh environment, checking dependencies, and verifying both CLI entry points.

## Installation

See the [README](https://github.com/xbdqs/GeoPanel-EO#readme) and [release validation documentation](https://github.com/xbdqs/GeoPanel-EO/blob/main/docs/RELEASE_VALIDATION.md).

The CLI can be invoked using `geopanel-eo --help` or `python -m geopanel_eo --help`.

## Scientific reproducibility note

Historical Chicago stage outputs predate the v0.1.1 preprocessing-manifest contract. Retrospective checks and new-runtime audit evidence are distinguished in the accompanying documentation. This release does not retroactively imply that historical runs were produced with v0.1.1.
