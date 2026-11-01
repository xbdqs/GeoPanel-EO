#!/usr/bin/env python3
"""Verify the built GeoPanel-EO wheel in a clean, non-editable virtual environment.

Invoked by GitHub Actions on both Windows and Ubuntu. It never imports GeoPanel-EO
from the source checkout: all child commands run from a temporary directory.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile


def execute(args: list[str], cwd: Path, env: dict[str, str]) -> str:
    print("RUN:", " ".join(map(str, args)), flush=True)
    result = subprocess.run(
        [str(arg) for arg in args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=600,
    )
    output = (result.stdout or "") + (result.stderr or "")
    print(output[:7000], flush=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed with exit code {result.returncode}: {args}\n"
            + output[-8000:]
        )
    return result.stdout


def main() -> None:
    project = Path(__file__).resolve().parents[1]
    wheels = sorted((project / "dist").glob("geopanel_eo-0.1.1-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"Expected exactly one v0.1.1 wheel; found {wheels}")

    with tempfile.TemporaryDirectory(prefix="geopanel_clean_wheel_") as folder:
        work = Path(folder).resolve()
        virtualenv = work / "venv"
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        execute([sys.executable, "-m", "venv", str(virtualenv)], work, env)
        bindir = virtualenv / ("Scripts" if os.name == "nt" else "bin")
        py = bindir / ("python.exe" if os.name == "nt" else "python")
        command = bindir / ("geopanel-eo.exe" if os.name == "nt" else "geopanel-eo")

        execute([str(py), "-m", "pip", "install", "--no-cache-dir", str(wheels[0])], work, env)
        execute([str(py), "-m", "pip", "check"], work, env)

        code = (
            "import importlib.metadata, pathlib, geopanel_eo, rasterio, pyexpat; "
            "p=pathlib.Path(geopanel_eo.__file__).resolve(); "
            "assert importlib.metadata.version('geopanel-eo')=='0.1.1'; "
            "assert geopanel_eo.__version__=='0.1.1'; "
            "assert 'site-packages' in p.as_posix().lower(), p; "
            "print('CLEAN_WHEEL_IMPORT_PASS', p, 'rasterio', rasterio.__version__)"
        )
        execute([str(py), "-c", code], work, env)
        execute([str(command), "--help"], work, env)
        execute([str(py), "-m", "geopanel_eo", "--help"], work, env)

        schema_output = execute([str(command), "schema"], work, env)
        import json
        schema = json.loads(schema_output)
        assert "properties" in schema, "CLI schema output missing properties"
        print("PASS_CLEAN_INSTALLED_WHEEL_CLI", flush=True)


if __name__ == "__main__":
    main()
