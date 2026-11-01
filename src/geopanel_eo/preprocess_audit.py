"""Verify upstream preprocessing identities without claiming algorithmic correctness.

This module checks the *bytes* of prepared inputs against a separately
recorded preprocessing manifest. It cannot prove that a resampling kernel is
scientifically appropriate or reconstruct an absent raw dataset.
"""
from __future__ import annotations

from pathlib import Path
import hashlib
import json
import re

from .errors import GeoPanelError

_SHA = re.compile(r"^[0-9a-f]{64}$")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_preprocessing_manifest(manifest_path: str | Path, prepared_paths: list[str | Path]) -> dict:
    """Fail closed when any prepared raster's recorded hash is absent/wrong.

    Raw-source hashes are checked if raw files are available locally. When a
    raw file is missing, its hash remains a *recorded claim*, not a verified
    identity; the result explicitly reports that boundary.
    """
    manifest_path = Path(manifest_path).resolve()
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError) as exc:
        raise GeoPanelError("PREPROCESS_MANIFEST_INVALID", str(exc)) from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != "geopanel-preprocess-1":
        raise GeoPanelError("PREPROCESS_MANIFEST_INVALID", "Unsupported preprocessing manifest schema")
    prepared = raw.get("prepared_sha256")
    inputs = raw.get("raw_source_sha256", {})
    operations = raw.get("operations")
    if not isinstance(prepared, dict) or not prepared or not isinstance(inputs, dict) or not isinstance(operations, dict) or not operations:
        raise GeoPanelError("PREPROCESS_MANIFEST_INVALID", "Expected nonempty prepared_sha256 and operations, and raw_source_sha256 mapping")
    if not all(isinstance(k, str) and isinstance(v, str) and _SHA.fullmatch(v) for k, v in [*prepared.items(), *inputs.items()]):
        raise GeoPanelError("PREPROCESS_MANIFEST_INVALID", "Invalid input path/hash declaration")
    # Prepared-file keys are paths relative to manifest directory (portable).
    files = {str((manifest_path.parent / key).resolve()): value for key, value in prepared.items()}
    if len(files) != len(prepared):
        raise GeoPanelError("PREPROCESS_MANIFEST_INVALID", "Duplicate canonical prepared-file path")
    supplied = {str(Path(path).resolve()) for path in prepared_paths}
    if supplied != set(files):
        raise GeoPanelError("PREPROCESS_INPUT_SET_MISMATCH", "Preprocessing record does not cover exactly the declared source rasters")
    for file_path in sorted(supplied):
        if not Path(file_path).is_file() or sha256_file(file_path) != files[file_path]:
            raise GeoPanelError("PREPROCESS_HASH_MISMATCH", f"Prepared raster missing or hash changed: {file_path}")
    raw_verified, raw_missing = 0, 0
    for key, expected in inputs.items():
        file_path = (manifest_path.parent / key).resolve()
        if not file_path.is_file():
            raw_missing += 1
            continue
        if sha256_file(file_path) != expected:
            raise GeoPanelError("PREPROCESS_RAW_HASH_MISMATCH", f"Raw source changed: {file_path}")
        raw_verified += 1
    return {"status": "PREPARED_INPUTS_VERIFIED", "manifest_path": str(manifest_path),
            "manifest_sha256": sha256_file(manifest_path), "prepared_verified_count": len(files),
            "raw_verified_count": raw_verified, "raw_unavailable_count": raw_missing,
            "scientific_resampling_validated": False,
            "scope": "Input byte identity and recorded processing parameters only; not physical accuracy or conservation"}
