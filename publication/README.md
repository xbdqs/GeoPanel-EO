# Publication assets

Install the plotting environment and run the following commands from the repository root to reproduce the four manuscript figures and three summary tables from the included compact source data:

```bash
python -m pip install -r publication/requirements-plotting.txt
python publication/make_softwarex_assets.py
```

Figures are written to `publication/figures/` as 600-dpi PNG and vector PDF files. Tables are written to `publication/tables/` as CSV files. The script reads the Chicago compact panel and boundary files from `case_studies/chicago/results/` and method-comparison tables from `publication/figure_source_data/`.

Large upstream Daymet and Annual NLCD rasters are not redistributed. Their identifiers and acquisition information are documented in `case_studies/chicago/DATA_SOURCES.md`.
