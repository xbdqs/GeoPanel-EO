# GeoPanel-EO v0.1.1 scientific contract additions

This document supplements the preserved `docs/SCIENTIFIC_CONTRACT.md` describing v0.1.0. The scientific estimator still uses exact fractional polygon-cell coverage rather than cell-centre approximations.

## Time semantics

`annual_product` identifies an annual mapping product. Its January 1 timestamp is only an indexing anchor that identifies the product year; it is not a daily measurement or end-of-year snapshot. Only annual single-anchor snapshot reducers of intensive-state/categorical variables are accepted.

For monthly observations, `equal_month` preserves the legacy mean estimand. When `represented_days` is selected, all required month-specific day counts must be declared explicitly (usually from the raw data time axis); the software does not infer these from civil calendar length. Incomplete windows are retained and flagged as null. Daymet 2020 as processed in the Chicago case has 365 source dates, including February 29 and excluding December 31.

## Spatial support overlap

An optional spatial-support diagnostic compares exact valid-cell support on the same grid. Equal percentages of coverage do not guarantee common valid pixels. The output distinguishes intersection, union and spatial-support stability; it is a diagnostic, not an independent data quality estimate.

## Preprocessing provenance and limits

`project.preprocessing_manifest` points to a JSON record with raw/prepared SHA-256 identities. At runtime the core verifies prepared inputs, available raw source identities and consistency before and after processing; its own `run_manifest.json` records the linked audit. File identity and an executed preprocessing record do not by themselves demonstrate resampling accuracy, error independence, bias correction, or physical conservation.

**Historical boundary:** the September 2026 Chicago Stage 3/Stage 5 runs used 0.1.0a2 and did not run the new v0.1.1 provenance mechanism. The November 2026 reproduction used a clearly marked retrospective manifest and a new v0.1.1.dev2 core runtime; it reproduced 5,544 archived monthly Daymet panel values. This cannot establish contemporaneous logging by the older release.

## Reproducibility status

Windows Python 3.12: 42 core + 74 reviewer synthetic tests passed for v0.1.1.dev2. Source version `v0.1.1` is a release candidate until CI, clean build and installed-wheel checks pass. Original large Daymet and NLCD inputs are not redistributed.
