#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from loop_review.config import DEFAULT_CONFIG, load_config
from loop_review.controller import LoopReviewController
from loop_review.util import LoopReviewError, expand_path


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="loop_review.py", description="Auditable multi-model read-only review loop")
    p.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to config.toml")
    sub = p.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="Verify reviewer CLIs/models")
    doctor.add_argument("--cwd", default=str(Path.home()), help="Trusted directory for health checks")

    run = sub.add_parser("run", help="Run loop review")
    run.add_argument("--invocation", required=True, help="Invocation JSON path")
    run.add_argument("--dry-run", action="store_true", help="Prepare inputs/prompts without model calls")
    return p


def main() -> int:
    args = parser().parse_args()
    try:
        cfg = load_config(expand_path(args.config))
        skill_root = SCRIPT_DIR.parent
        controller = LoopReviewController(cfg, skill_root)
        if args.command == "doctor":
            result = controller.doctor(expand_path(args.cwd))
        else:
            result = controller.run(expand_path(args.invocation), dry_run=args.dry_run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except LoopReviewError as e:
        print(json.dumps({
            "status": "FAILED",
            "failure_code": e.code,
            "message": e.message,
            "details": e.details,
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    except Exception as e:
        print(json.dumps({
            "status": "FAILED",
            "failure_code": "UNEXPECTED_ERROR",
            "message": str(e),
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
