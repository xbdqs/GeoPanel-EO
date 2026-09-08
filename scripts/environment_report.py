"""Capture installed versions and actual distribution license evidence."""

import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import platform

from geopanel_eo.config import Config


ROOT = Path(__file__).resolve().parents[1]
artifacts = ROOT / "artifacts"
artifacts.mkdir(exist_ok=True)
records = []
for dist in sorted(metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
    if dist.metadata["Name"].lower() in {"geopanel-eo", "pip"}:
        continue
    licenses = []
    for file in dist.files or []:
        if "dist-info" in str(file) and any(word in file.name.lower() for word in ("license", "copying", "notice")):
            path = Path(dist.locate_file(file))
            licenses.append(dict(path=str(file), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                text=path.read_text(encoding="utf-8", errors="replace")))
    records.append(dict(name=dist.metadata["Name"], version=dist.version,
        license_expression=dist.metadata.get("License-Expression"), declared_license=dist.metadata.get("License"),
        classifiers=dist.metadata.get_all("Classifier", []), license_files=licenses))
(artifacts / "dependency_license_inventory.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
(artifacts / "environment.json").write_text(json.dumps(dict(python=platform.python_version(), platform=platform.platform(),
    packages={r["name"]: r["version"] for r in records}), indent=2), encoding="utf-8")
(ROOT / "requirements-lock.txt").write_text("# Validated Windows x64 / CPython 3.12 environment, including test/build tools.\n" +
    "\n".join(f"{r['name']}=={r['version']}" for r in records) + "\n", encoding="utf-8")
(ROOT / "schemas").mkdir(exist_ok=True)
(ROOT / "schemas" / "config.schema.json").write_text(json.dumps(Config.model_json_schema(), indent=2, ensure_ascii=False), encoding="utf-8")
print(f"Recorded {len(records)} dependencies and structural schema")
