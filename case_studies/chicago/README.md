# Chicago validation case

This directory documents the real-data validation used in the SoftwareX manuscript. The analysis covers all 77 Chicago community areas for 2019–2020 and combines Daymet V4 R1 temperature and precipitation with Annual NLCD Collection 1.2 land cover and fractional imperviousness.

The upstream Daymet and NLCD raster files are not redistributed. Run `run_citywide_case.py` only after acquiring those products and adapting the local input paths in the supplied YAML configurations. The compact `results/stage5_report.json` records the completed validation; publication figures and summary tables are retained in `../../publication`.

The release reports the actual candidate run as `0.1.0a2`. Version `0.1.0` has the same scientific core and differs only in normalized release metadata and documentation. The package-level test suite must pass before the GitHub tag is created.
