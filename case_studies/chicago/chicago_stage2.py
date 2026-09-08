"""GeoPanel-EO Chicago acquisition/audit, resumable Conda workflow r6."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
import multiprocessing as mp
from pathlib import Path
import platform
import sys
from urllib.parse import urlencode
from zipfile import ZipFile, ZIP_DEFLATED

from check_env import environment_problems, package_versions
from daymet_audit import (BBOX, CHICAGO_URL, COLLECTION_ID, KEEP_AREAS, PROBE_CONSTRAINT,
                          VARIABLES, YEARS, daymet_url, digest, inspect_boundaries,
                          inspect_chunk, inspect_probe, inspect_subset, merge_chunks,
                          subset_constraint)
from earthdata_config import credentials_candidates, hidden_input
from network_jobs import Cancelled, JobError, run_job

ROOT = Path(__file__).resolve().parent
ALLOWED_RETURN_FILES = ("acquisition_audit.json", "run_log.txt", "grid_selection.json",
                        "chicago_community_areas_08_25_32.geojson")


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


@contextmanager
def single_run_lock(path):
    with path.open("a+b") as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise JobError("ALREADY_RUNNING", "Another run is active in this folder. Use STOP_DOWNLOAD.bat first.") from None
        try:
            yield
        finally:
            stream.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class AuditRun:
    def __init__(self, root, mode):
        self.root = Path(root)
        self.stop_file = self.root / "STOP_REQUESTED.flag"
        self.data = self.root / "data" / "raw"
        self.data.mkdir(parents=True, exist_ok=True)
        self.results = self.root / "local_results"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        self.directory = self.results / f"run_{stamp}"
        self.directory.mkdir(parents=True)
        self.log = self.directory / "run_log.txt"
        self.report = {
            "schema_version": "geopanel-chicago-acquisition-r6",
            "software_core_version": "0.1.0 (not changed or executed by this acquisition stage)",
            "mode": mode, "status": "RUNNING", "created_utc": stamp,
            "scope": {"years": list(YEARS), "variables": list(VARIABLES),
                      "community_area_ids": sorted(KEEP_AREAS), "bbox_wgs84": BBOX},
            "environment": {"python": sys.version.split()[0], "platform": platform.system(),
                            "machine": platform.machine(), "package_versions": package_versions()},
            "source": {"chicago": CHICAGO_URL, "daymet_collection": COLLECTION_ID,
                       "daymet_doi": "https://doi.org/10.3334/ORNLDAAC/2129"},
            "authentication_attempts": [], "files": [], "file_failures": [],
            "credentials_recorded": False,
            "limits_seconds": {"token_exchange": 60, "coordinate_probe": 90,
                               "boundary": 90, "each_subset": 300, "worker_cleanup_grace_max": 4},
        }
        self.save()

    def emit(self, message):
        print(message, flush=True)
        with self.log.open("a", encoding="utf-8") as stream:
            stream.write(message + "\n")

    def save(self):
        write_json(self.directory / "acquisition_audit.json", self.report)

    def check_cancel(self):
        if self.stop_file.exists():
            raise Cancelled()

    def fetch_file(self, name, base_url, constraint, *, token=None, seconds=90,
                   max_bytes=2*1024*1024, force=False, audit_only=False, validator=None):
        self.check_cancel()
        target = self.data / name
        target.parent.mkdir(parents=True, exist_ok=True)
        sidecar = target.with_suffix(target.suffix + ".audit.json")
        partial = target.with_suffix(target.suffix + ".part")
        request = {"base_url": base_url, "constraint": constraint}
        key = sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        if target.exists() and sidecar.exists() and not force:
            try:
                previous = json.loads(sidecar.read_text(encoding="utf-8"))
                usable = previous.get("request_key") == key and previous.get("sha256") == digest(target)
            except (OSError, ValueError):
                usable = False
            if usable:
                checked = validator(target) if validator else None
                self.emit(f"[CACHE] {name}: hash and structure verified; no download.")
                return target, {**previous, "cache_reused": True}, checked
        if audit_only:
            raise JobError("CACHE_UNAVAILABLE", f"Verified cached data missing or modified: {name}. Run the download step.")
        url = base_url + ("?" + urlencode({"dap4.ce": constraint}) if constraint else "")
        job = {"kind": "fetch", "url": url, "token": token, "partial": str(partial),
               "max_bytes": max_bytes, "read_timeout": min(seconds-5, 90)}
        try:
            transfer = run_job(job, seconds=seconds, label=f"[FETCH {name}]",
                               stop_file=self.stop_file, emit=self.emit)
            checked = validator(partial) if validator else None
            # Promote only after transport and product validation; old good files survive failures.
            self.check_cancel()
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)
        transfer.update(request)
        transfer.update({"request_key": key, "sha256": digest(target), "cache_reused": False})
        write_json(sidecar, transfer)
        return target, transfer, checked

    def probe(self, token, *, force=False, audit_only=False):
        path, transfer, selection = self.fetch_file(
            "daymet_grid_coordinate_probe.nc", daymet_url("tmin", 2019), PROBE_CONSTRAINT,
            token=token, force=force, audit_only=audit_only, validator=inspect_probe)
        self.report["coordinate_probe"] = transfer
        self.report["grid_selection"] = selection
        write_json(self.directory / "grid_selection.json", selection)
        self.save()
        return selection

    def authenticate(self, interactive_token=False):
        candidates, diagnostics = credentials_candidates()
        self.report["credential_availability"] = diagnostics
        if interactive_token:
            self.emit("[INPUT] Paste a user token at the hidden prompt, then press Enter. No network request is active yet.")
            token = hidden_input("Earthdata user token (hidden; not saved): ").strip()
            if not token or any(c.isspace() for c in token):
                raise JobError("EMPTY_TOKEN", "Token input is empty or contains whitespace.")
            self.emit("[INPUT] Token received. Starting the bounded online probe.")
            candidates = [{"source": "runtime_token", "kind": "token", "value": token}]
        if not candidates:
            raise JobError("NO_CREDENTIALS", "No Earthdata credentials found. Run CONFIGURE_EARTHDATA.bat or RUN_WITH_TOKEN.bat.")
        retryable_auth = {"ACCOUNT_REJECTED", "TOKEN_REJECTED", "ACCESS_FORBIDDEN", "AUTH_REDIRECT"}
        last_error = None
        for candidate in candidates:
            self.check_cancel()
            source = candidate["source"]
            attempt = {"source": source, "status": "TESTING"}
            self.report["authentication_attempts"].append(attempt)
            self.save()
            self.emit(f"[AUTH] Checking {source}; credential values are not logged.")
            try:
                if candidate["kind"] == "account":
                    result = run_job({"kind": "token", "credentials": candidate["value"]},
                                     seconds=60, label="[EARTHDATA TOKEN EXCHANGE]",
                                     stop_file=self.stop_file, emit=self.emit)
                    token = result.pop("access_token")
                else:
                    token = candidate["value"]
                # A live tiny NetCDF probe, never a cached success claim.
                selection = self.probe(token, force=True)
            except JobError as exc:
                attempt.update({"status": "FAILED", "code": exc.code})
                self.save()
                if exc.code not in retryable_auth:
                    raise
                last_error = exc
                self.emit(f"[AUTH] {source}: {exc.code}. Checking the next configured source, if any.")
                continue
            attempt["status"] = "ACCEPTED"
            self.report["authentication_source"] = source
            self.emit(f"[AUTH] ACCEPTED: {source}; live Daymet coordinate probe verified.")
            self.save()
            return token, selection
        raise last_error or JobError("AUTH_FAILED", "Configured credential sources did not pass the live probe.")

    def boundaries(self, audit_only=False):
        path, transfer, checked = self.fetch_file(
            "chicago_community_areas.geojson", CHICAGO_URL, None,
            max_bytes=10*1024*1024, audit_only=audit_only, validator=inspect_boundaries)
        inspection, subset = checked
        output = self.directory / "chicago_community_areas_08_25_32.geojson"
        write_json(output, subset)
        self.report["boundaries"] = {"transfer": transfer, "inspection": inspection, "subset_sha256": digest(output)}
        self.save()

    def download_daymet_file(self, token, variable, year, selection, audit_only=False):
        name = f"daymet_v4_daily_na_{variable}_{year}_chicago.nc"
        target = self.data / name
        final_validator = lambda path: inspect_subset(path, variable, year, selection)
        if target.exists() and target.with_suffix(target.suffix + ".audit.json").exists():
            try:
                return self.fetch_file(name, daymet_url(variable, year),
                                       subset_constraint(variable, selection), token=token,
                                       audit_only=True, validator=final_validator)
            except (JobError, ValueError):
                if audit_only:
                    raise
                self.emit(f"[CACHE] {name}: annual cache is invalid; rebuilding from verified chunks.")
        if audit_only:
            raise JobError("CACHE_UNAVAILABLE", f"Verified cached data missing or modified: {name}. Run the download step.")
        chunk_paths, chunk_records = [], []
        for start in range(0, 365, 30):
            end = min(start + 29, 364)
            chunk_name = f"chunks/daymet_{variable}_{year}_{start:03d}_{end:03d}.nc"
            validator = lambda path, a=start, b=end: inspect_chunk(
                path, variable, year, selection, a, b)
            path, transfer, inspection = self.fetch_file(
                chunk_name, daymet_url(variable, year),
                subset_constraint(variable, selection, start, end), token=token,
                seconds=150, max_bytes=24*1024*1024, validator=validator)
            chunk_paths.append(path)
            chunk_records.append({"transfer": transfer, "inspection": inspection})
        inspection = merge_chunks(chunk_paths, target, variable, year, selection)
        inspection["filename"] = target.name
        request = {"base_url": daymet_url(variable, year),
                   "constraint": subset_constraint(variable, selection)}
        key = sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        transfer = {**request, "request_key": key, "sha256": digest(target),
                    "size_bytes": target.stat().st_size, "cache_reused": False,
                    "transport": "30-day OPeNDAP chunks merged locally",
                    "chunk_count": len(chunk_records), "chunks": chunk_records}
        write_json(target.with_suffix(target.suffix + ".audit.json"), transfer)
        return target, transfer, inspection

    def download_daymet(self, token, selection, audit_only=False):
        count = 0
        for year in YEARS:
            for variable in VARIABLES:
                count += 1
                self.emit(f"[DAYMET {count}/6] {year} {variable}")
                try:
                    path, transfer, inspection = self.download_daymet_file(
                        token, variable, year, selection, audit_only)
                    inspection["filename"] = path.name
                    self.report["files"].append({"transfer": transfer, "inspection": inspection})
                except Cancelled:
                    raise
                except JobError as exc:
                    failure = {"variable": variable, "year": year, "code": exc.code,
                               "error": str(exc)}
                    self.report["file_failures"].append(failure)
                    self.emit(f"[DAYMET FAILED] {year} {variable}: {exc.code}. Cached chunks were preserved.")
                except ValueError as exc:
                    failure = {"variable": variable, "year": year,
                               "code": "PRODUCT_VALIDATION", "error": str(exc)}
                    self.report["file_failures"].append(failure)
                    self.emit(f"[DAYMET FAILED] {year} {variable}: PRODUCT_VALIDATION. Cached chunks were preserved.")
                self.save()
        if self.report["file_failures"]:
            completed = len(self.report["files"])
            raise JobError("INCOMPLETE_DOWNLOAD", f"{completed}/6 annual files passed; rerun to resume failed chunks.")

    def bundle(self):
        output = self.results / f"GeoPanel-EO_CHICAGO_STAGE2_r6_{self.directory.name}.zip"
        with ZipFile(output, "w", ZIP_DEFLATED) as archive:
            for name in ALLOWED_RETURN_FILES:
                path = self.directory / name
                if path.exists():
                    archive.write(path, arcname=f"{self.directory.name}/{name}")
        return output


def execute(root, mode="download", interactive_token=False):
    run = AuditRun(root, mode)
    rc = 0
    try:
        with single_run_lock(Path(root) / "RUNNING.lock"):
            run.stop_file.unlink(missing_ok=True)
            run.emit(f"GeoPanel-EO Chicago r6: {mode}")
            if mode == "audit-only":
                selection = run.probe(None, audit_only=True)
                run.boundaries(audit_only=True)
                run.download_daymet(None, selection, audit_only=True)
                run.report["status"] = "AUDIT_PASS"
            else:
                token, selection = run.authenticate(interactive_token)
                if mode == "auth-check":
                    run.report["status"] = "AUTH_PASS"
                else:
                    run.boundaries()
                    run.download_daymet(token, selection)
                    run.report["status"] = "PASS"
                token = None
    except (Cancelled, KeyboardInterrupt, EOFError):
        run.report.update({"status": "CANCELLED", "error_code": "CANCELLED", "error": "Run cancelled; completed files were preserved."})
        rc = 130
    except JobError as exc:
        run.report.update({"status": "FAIL", "error_code": exc.code, "error": str(exc)})
        rc = 1
    except Exception as exc:
        # Do not serialize arbitrary tracebacks, request objects, env or credentials.
        error = str(exc) if isinstance(exc, (ValueError, KeyError)) else "Local validation/I/O failure. Check available disk space and environment."
        run.report.update({"status": "FAIL", "error_code": "LOCAL_VALIDATION", "error_type": type(exc).__name__, "error": error})
        rc = 1
    run.save()
    if "error" in run.report:
        run.emit(f"[{run.report.get('error_code')}] {run.report['error']}")
    run.emit(f"RESULT: {run.report['status']}")
    bundle = run.bundle()
    print(f"Upload this result ZIP: {bundle}", flush=True)
    print(f"SHA256: {digest(bundle)}", flush=True)
    return rc


def main():
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--auth-check", action="store_true")
    modes.add_argument("--audit-only", action="store_true")
    parser.add_argument("--token", action="store_true", help="Explicitly prompt for a runtime-only Bearer token")
    args = parser.parse_args()
    problems = environment_problems()
    if problems:
        print("ENV CHECK FAIL\n" + "\n".join(problems))
        print("Run SETUP_CONDA_ONCE.bat in Anaconda Prompt. This command did not install anything.")
        return 2
    mode = "auth-check" if args.auth_check else "audit-only" if args.audit_only else "download"
    return execute(ROOT, mode, args.token)


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
