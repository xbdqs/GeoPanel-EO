# Changelog

## 0.1.1 - release candidate (date pending)

- Added explicit `annual_product` year-anchor semantics for annual land-cover inputs.
- Added observed-day weighted temporal means with explicit upstream represented-day counts and time-bound validation.
- Added common-support overlap diagnostics and CSV exports for multi-month spatial support.
- Added checked preprocessing manifests, input identity validation before and after extraction, and run-manifest linkage.
- Added synthetic failure-case reviewer tests and real Chicago climate validation scripts.
- In the November 2026 Windows validation of development build 0.1.1.dev2, 116 core and review tests passed; a separate real-data run matched all 5,544 archived Chicago monthly records with zero numeric difference.
- Historical Chicago panels were produced by 0.1.0a2; their NLCD year anchors and source hashes were verified retrospectively. No retroactive R4 provenance is claimed.

## 0.1.0 - 2026-09-08

- First public research-software release.
- Normalized the validated `0.1.0a2` candidate to a stable semantic version.
- Added the Chicago 77-community real-data validation case and publication assets.
- Retained explicit state, accumulation, pixel-total and categorical semantics.
- Retained exact partial-cell weighting, coverage diagnostics, conditional uncertainty propagation and provenance outputs.
- Added publication, citation and archival-deposit templates.

The scientific-core algorithms are unchanged from candidate version `0.1.0a2`.
