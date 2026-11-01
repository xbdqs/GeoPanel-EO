"""Record observed runtime environment. No exact location, machine ID or user name stored."""
from __future__ import annotations
import argparse
import ctypes
from datetime import datetime,timezone
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def memory_gb():
    if platform.system() != 'Windows':
        return None
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_=[('dwLength',ctypes.c_ulong),('dwMemoryLoad',ctypes.c_ulong),
            ('ullTotalPhys',ctypes.c_ulonglong),('ullAvailPhys',ctypes.c_ulonglong),
            ('ullTotalPageFile',ctypes.c_ulonglong),('ullAvailPageFile',ctypes.c_ulonglong),
            ('ullTotalVirtual',ctypes.c_ulonglong),('ullAvailVirtual',ctypes.c_ulonglong),
            ('ullAvailExtendedVirtual',ctypes.c_ulonglong)]
    st=MEMORYSTATUSEX();st.dwLength=ctypes.sizeof(st)
    try:
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return round(st.ullTotalPhys/1024**3,2)
    except Exception: pass
    return None


def cpu_name():
    if platform.system() == 'Windows':
        try:
            res=subprocess.run(['reg','query',r'HKLM\HARDWARE\DESCRIPTION\System\CentralProcessor\0','/v','ProcessorNameString'],
                capture_output=True,text=True,timeout=5)
            for line in res.stdout.splitlines():
                if 'ProcessorNameString' in line:
                    return line.split('REG_SZ',1)[-1].strip()
        except Exception: pass
    return platform.processor() or None


def run(out, test_log=None):
    packages=['geopanel-eo','numpy','pandas','rasterio','shapely','pyproj','pyogrio',
        'xarray','h5netcdf','h5py','scipy','exactextract','pydantic','PyYAML','pyarrow','pytest']
    versions={}
    for name in packages:
        try: versions[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError: versions[name]=None
    logfile=test_log if test_log is not None else ROOT/'review_results'/'windows_r0_tests.txt'
    test_summary=None
    if logfile.exists():
        for line in logfile.read_text(encoding='utf-8',errors='replace').splitlines():
            if re.search(r'\d+ passed',line): test_summary=line.strip()
    obj={
      'timestamp_utc':datetime.now(timezone.utc).isoformat(),
      'source':'observed local host environment, not archival Linux report',
      'platform_system':platform.system(),
      'platform_release':platform.release(),
      'platform_version':platform.version(),
      'architecture':platform.machine(),
      'python_version':platform.python_version(),
      'cpu_model':cpu_name(),
      'logical_cpu_count':os.cpu_count(),
      'total_physical_ram_gib':memory_gb(),
      'dependency_versions':versions,
      'test_summary_from_log':test_summary,
      'test_summary_log_path':str(logfile),
      'notes': 'Test summary is read from an existing log and is not itself an execution of the test suite. CPU and RAM give context; not a controlled performance benchmark.'}
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(obj,ensure_ascii=False,indent=2))
    return obj

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,default=ROOT/'review_results'/'windows_environment_actual.json')
    parser.add_argument('--test-log',type=Path,default=None,help='Explicit pytest log file; avoid stale counts')
    opts=parser.parse_args()
    run(opts.out, test_log=opts.test_log)
