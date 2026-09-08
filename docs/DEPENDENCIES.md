# Dependencies and licensing

The validated Python 3.12 environment is recorded in `requirements-lock.txt`. GeoPanel-EO source code and synthetic examples are released under the MIT License. Runtime dependencies retain their own licenses; the project license does not replace the terms of NumPy, pandas, rasterio/GDAL, PROJ, Shapely/GEOS, exactextract, HDF5, SciPy, PyArrow, or other third-party components.

This repository distributes source code rather than a bundled executable containing third-party binary libraries. Users installing wheels from package indexes remain responsible for the terms accompanying those distributions. A future standalone executable would require a separate audit of bundled libraries, notices, redistribution terms, and source-availability obligations.
