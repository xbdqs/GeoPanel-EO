from importlib import metadata
from pathlib import Path
import sys

LOCK = Path(__file__).with_name("requirements-stage3-lock.txt")


def expected():
    return dict(line.strip().split("==", 1) for line in LOCK.read_text().splitlines()
                if line.strip() and not line.startswith("#"))


def problems():
    issues = []
    if Path(sys.prefix).name.lower() != "geopanel_eo" or not (Path(sys.prefix) / "conda-meta").is_dir():
        issues.append("Activate the existing Conda environment named geopanel_eo.")
    for name, version in expected().items():
        try:
            actual = metadata.version(name)
        except metadata.PackageNotFoundError:
            issues.append(f"missing: {name}=={version}")
        else:
            if actual != version:
                issues.append(f"version: {name} expected {version}, found {actual}")
    return issues


if __name__ == "__main__":
    found = problems()
    if found:
        print("STAGE3_ENV_FAIL")
        print("\n".join(found))
        raise SystemExit(1)
    print("STAGE3_ENV_PASS")
