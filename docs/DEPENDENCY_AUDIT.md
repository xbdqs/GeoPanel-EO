# P15-S1 依赖记录与许可边界

依据为本地实际安装的 Python distribution metadata、LICENSE 和 NOTICE 文件。原文、路径及 SHA-256 保存于 `artifacts/dependency_license_inventory.json`，版本另见 `requirements-lock.txt`。可用 `python scripts/environment_report.py` 再生成。

| 直接依赖 | 分发元数据中的主许可证 |
|---|---|
| exactextract | Apache-2.0 |
| numpy | BSD-3-Clause；二进制还含其他组件条款 |
| pandas / rasterio / shapely / affine / scipy / h5netcdf / h5py | BSD 类条款；以 inventory 原文为准 |
| pyproj / pyogrio / pydantic / PyYAML | MIT |
| xarray / pyarrow | Apache-2.0 |

S1 新编写核心源码、测试和合成输入采用 MIT。第三方许可证不被本项目许可证取代。此交付不包含第三方二进制文件，Python wheel 只打包 GeoPanel-EO 源码，通过依赖声明使用外部包。

**Windows 安装包的第三方二进制分发审计仍待完成**。已实际发现 exactextract 的 Windows 发行版携带 GEOS LGPLv2.1 和 Microsoft runtime 条款；其他 GIS 和数值 wheel 同样可能包含 GDAL、PROJ、GEOS、HDF5、BLAS 或编译运行库。不能仅按顶层 Python 包许可证认定未来单文件 EXE／安装程序已满足所有要求。源码 MIT 的采用不代表第三方二进制许可已经放行。

本次未引入用于外部验证的 GPL Python 工具；运行依赖中 GEOS 的 LGPL 记录已单独保留。实际打包之前还需核对所有 DLL 的来源、链接方式、通知、再分发和源代码可获得性要求，并生成面向该发行包的 notices。此表是工程清单，不代替最终分发版本的法律审查。

依赖稳定性验证采用 NumPy 2.2.6、affine 2.4.0；NetCDF4/HDF5 文件采用 h5netcdf/h5py，NetCDF3 采用 scipy。最初尝试的 netCDF4 Windows wheel 在测试中出现 NumPy ABI 警告，因此未作为交付依赖；没有屏蔽此警告。完整测试以 RuntimeWarning 视为错误执行，实际结果见测试报告。
