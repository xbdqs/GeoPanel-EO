import argparse
import json
from pathlib import Path
import sys

from .config import Config, load_config
from .errors import GeoPanelError
from .pipeline import build_panel, input_paths, run


def main(argv=None):
    parser = argparse.ArgumentParser(description="GeoPanel-EO S1 scientific core")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "audit", "run"):
        sub.add_parser(command).add_argument("config", type=Path)
    sub.add_parser("schema")
    args = parser.parse_args(argv)
    try:
        if args.command == "schema":
            print(json.dumps(Config.model_json_schema(), indent=2, ensure_ascii=False))
        elif args.command == "validate":
            load_config(args.config)
            print("CONFIG_VALID")
        elif args.command == "audit":
            config = load_config(args.config)
            input_paths(config)
            panel, _, _, audit = build_panel(config)
            print(json.dumps({"row_count": len(panel), "audit": audit}, indent=2, ensure_ascii=False))
        else:
            result = run(args.config)
            print(json.dumps({"status": "RUN_COMPLETED", "row_count": result["row_count"],
                "scientific_result_sha256": result["scientific_result_sha256"]}, indent=2))
        return 0
    except GeoPanelError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"RUN_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
