"""Generate S1 inputs, manual truths, actual outputs and machine-readable checks."""

import argparse
import json
import math
from pathlib import Path

import pandas as pd

from geopanel_eo.pipeline import run
from geopanel_eo.synthetic import TRUTHS, create_case


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    results = []
    for case in TRUTHS:
        config = create_case(args.directory / case, case)
        manifest = run(config)
        actual = pd.read_csv(config.parent / "outputs" / "panel_long.csv")
        for variable_id, expectations in TRUTHS[case]["expected"].items():
            row = actual.loc[actual.variable_id == variable_id].iloc[0]
            for field, expected in expectations.items():
                value = float(row[field])
                results.append(dict(case=case, variable_id=variable_id, field=field, expected=expected, actual=value,
                    absolute_tolerance=1e-7, relative_tolerance=1e-7, passed=math.isclose(value, expected, rel_tol=1e-7, abs_tol=1e-7),
                    scientific_result_sha256=manifest["scientific_result_sha256"]))
    config = create_case(args.directory / "netcdf_annual", "T05", netcdf=True)
    run(config)
    pd.DataFrame(results).to_csv(args.directory / "validation_results.csv", index=False)
    (args.directory / "validation_summary.json").write_text(json.dumps(dict(checks=len(results), passed=sum(r["passed"] for r in results)), indent=2), encoding="utf-8")
    if not all(r["passed"] for r in results):
        raise SystemExit("VALIDATION_FAILED")
    print(f"{len(results)} analytical checks passed; examples at {args.directory.resolve()}")


if __name__ == "__main__":
    main()
