# Chicago data sources and redistribution boundary

The validation case uses three upstream resources:

1. Chicago community-area polygons from the City of Chicago open-data service. The validated boundary file contains 77 uniquely identified Polygon or MultiPolygon features.
2. Daymet V4 R1 daily minimum temperature, maximum temperature and precipitation for 2019–2020 (ORNL DAAC DOI: `10.3334/ORNLDAAC/2129`).
3. Annual NLCD Collection 1.2 land cover and fractional impervious surface for 2019–2020 (USGS DOI: `10.5066/P94UXNTS`).

Large Daymet and NLCD source rasters are not redistributed in this repository. The scripts under `stage2`–`stage5` document acquisition, preprocessing, audit and panel construction. `results/stage5_report.json` is the machine-readable report from the completed `0.1.0a2` candidate run; the scientific core in public release `0.1.0` is unchanged. Derived panel tables and the audited 77-feature analysis boundary are included to support result inspection and figure reproduction.

Users remain responsible for complying with the current terms and citation requirements of each upstream data provider.
