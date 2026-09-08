# GeoPanel-EO

GeoPanel-EO converts local NetCDF and GeoTIFF Earth-observation products into auditable entity-time panels for polygon analysis units. Version **0.1.0** is the first public research release corresponding to the SoftwareX manuscript. The Chicago example is a software validation case, not a causal or substantive urban study.

## Scientific scope

The package validates a declarative YAML contract, reads supported raster and polygon inputs, calculates exact partial-cell intersections, applies variable-specific spatial and temporal reducers, and writes analysis-ready panels together with coverage, effective sample size, quality flags, provenance and file hashes. Supported variable semantics are intensive state, accumulated depth, pixel total and categorical class.

The validated core accepts rectilinear rasters in metre-based equal-area projected coordinate systems. It does not silently reproject rasters, convert physical units, repair invalid geometries or manufacture uncertainty when compatible uncertainty inputs are unavailable. These restrictions are intentional and are documented in `docs/SCIENTIFIC_CONTRACT.md`.

## Installation

Python 3.12 is the validated runtime. A clean environment is recommended.

```bash
python -m venv .venv
python -m pip install --upgrade pip
python -m pip install -r requirements-lock.txt
python -m pip install --no-build-isolation --no-deps .
geopanel-eo --help
```

On Windows, replace `python` with `py -3.12` when using the Python launcher and activate `.venv\\Scripts\\activate` before installation.

## Command-line workflow

```bash
geopanel-eo validate examples/T01/config.yml
geopanel-eo audit examples/T01/config.yml
geopanel-eo run path/to/config.yml
geopanel-eo schema > config.schema.json
```

`validate` checks the contract; `audit` reads inputs and computes diagnostics without writing a result directory; `run` creates the canonical long panel, optional wide panel, codebook, diagnostics, manifest, resolved configuration and quality report. Existing output directories are not overwritten.

## Reproducibility evidence

- `tests/`: automated scientific-core and failure-boundary tests.
- `examples/`: synthetic GeoTIFF and NetCDF inputs with manually specified expectations.
- `case_studies/chicago/`: real-data configuration and citywide validation driver.
- `publication/`: manuscript figures, tables and compact figure-source data.
- `docs/SCIENTIFIC_CONTRACT.md`: scientific and output behavior.
- `docs/DEPENDENCIES.md`: dependency and licensing notes.

The Chicago case uses all 77 community areas and combines Daymet V4 R1 climate fields with Annual NLCD Collection 1.2 products for 2019–2020. Large upstream rasters are not redistributed. Their authoritative identifiers and acquisition instructions must be used to reproduce the case.

## Version and citation

Version 0.1.0 follows semantic versioning and is the normalized public release of the scientifically validated 0.1.0a2 candidate. On 8 September 2026, the source tree and installed wheel each passed all 42 tests on Windows 10 with Python 3.12.14, and the installed-wheel command-line NetCDF example reproduced the locked scientific result hash. GitHub Actions repeats the test suite after every push and pull request. The tagged GitHub Release `v0.1.0` is the public record of this version.

## Licence

GeoPanel-EO source code and synthetic examples are released under the MIT License. Third-party dependencies and upstream datasets retain their respective licences and terms.
