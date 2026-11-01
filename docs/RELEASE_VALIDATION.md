# Release validation and public-data boundary

## Tests

Run `python -m pytest tests tests_review -q` from the repository root after installing `pip install -e ".[dev]"`. The v0.1.1 release candidate also configures default pytest discovery for both suites.

## Build

Run `python -m build --wheel --sdist` and `python -m pip check`. Check the built wheel in a separate target directory or clean environment before tagging. Validate the installed CLI on a synthetic fixture and check its scientific-result hash against `tests/reference_scientific_hash.json`.

## Data provenance

The repository includes synthetic examples, a Chicago compact panel and figure-source data. Raw Daymet/NLCD images are **not** included. They must be separately acquired using `case_studies/chicago/DATA_SOURCES.md`. The historical Chicago outputs are from 0.1.0a2, whereas the 2026-11 runtime audit used 0.1.1.dev2 with a retrospective manifest. The two runs are separately identified.

## Release steps

After tests and source review, commit **only reviewed files** on a separate release branch. Update the official software version table, tag and GitHub Release only after approval. Never `git add .` from a working folder containing raw research data or review results. Do not force-push or retag v0.1.0.
