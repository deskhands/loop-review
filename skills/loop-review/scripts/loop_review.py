#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from loop_review.config import DEFAULT_CONFIG, load_config
from loop_review.controller import LoopReviewController
from loop_review.util import LoopReviewError, atomic_json, expand_path


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="loop_review.py", description="Auditable multi-model read-only review loop")
    p.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to config.toml")
    sub = p.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="Verify reviewer CLIs/models")
    doctor.add_argument("--cwd", default=str(Path.home()), help="Trusted directory for health checks")

    run = sub.add_parser("run", help="Run loop review")
    run.add_argument("--invocation", required=True, help="Invocation JSON path")
    run.add_argument("--dry-run", action="store_true", help="Prepare inputs/prompts synchronously without model calls")
    execution = run.add_mutually_exclusive_group()
    execution.add_argument(
        "--foreground",
        action="store_true",
        help="Run a real review synchronously in the calling shell (debug/manual use)",
    )
    execution.add_argument(
        "--detach",
        action="store_true",
        help="Compatibility alias for the default real-review execution mode",
    )

    status = sub.add_parser("status", help="Read a detached review job status")
    status.add_argument("--job", help="Detached job id; omit to use the most recent job")
    return p


def _jobs_dir(cfg: Dict[str, Any]) -> Path:
    path = Path(cfg["paths"]["run_root"]).resolve() / "_jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _job_path(cfg: Dict[str, Any], job_id: str) -> Path:
    if len(job_id) != 8 or any(ch not in "0123456789abcdef" for ch in job_id):
        raise LoopReviewError("INVALID_ARGUMENT", "job id must be an 8-character lowercase hex value")
    return _jobs_dir(cfg) / f"{job_id}.json"


def _read_json_if_complete(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _start_detached(args: argparse.Namespace, cfg: Dict[str, Any]) -> Dict[str, Any]:
    invocation = expand_path(args.invocation)
    config_path = expand_path(args.config)
    if not invocation.is_file():
        raise LoopReviewError("INVALID_INVOCATION", f"Invocation file not found: {invocation}")

    jobs_dir = _jobs_dir(cfg)
    job_id = secrets.token_hex(4)
    job_path = jobs_dir / f"{job_id}.json"
    stdout_path = jobs_dir / f"{job_id}.stdout"
    stderr_path = jobs_dir / f"{job_id}.stderr"

    argv = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--config",
        str(config_path),
        "run",
        "--invocation",
        str(invocation),
        "--foreground",
    ]
    if args.dry_run:
        argv.append("--dry-run")

    stdout_f = stdout_path.open("ab", buffering=0)
    stderr_f = stderr_path.open("ab", buffering=0)
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=stdout_f,
            stderr=stderr_f,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        stdout_f.close()
        stderr_f.close()

    record = {
        "job_id": job_id,
        "pid": proc.pid,
        "started_at": datetime.now().astimezone().isoformat(),
        "invocation": str(invocation),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
    }
    atomic_json(job_path, record)
    return {
        "status": "STARTED",
        "job_id": job_id,
        "pid": proc.pid,
        "job_path": str(job_path),
        "status_command": (
            f'python3 "{Path(__file__).resolve()}" --config "{config_path}" '
            f'status --job {job_id}'
        ),
    }


def _latest_job_path(cfg: Dict[str, Any]) -> Path:
    jobs = sorted(_jobs_dir(cfg).glob("????????.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not jobs:
        raise LoopReviewError("JOB_NOT_FOUND", "No detached loop-review job exists")
    return jobs[0]


def _detached_status(cfg: Dict[str, Any], job_id: Optional[str]) -> Dict[str, Any]:
    path = _job_path(cfg, job_id) if job_id else _latest_job_path(cfg)
    if not path.is_file():
        raise LoopReviewError("JOB_NOT_FOUND", f"Detached job not found: {path.stem}")

    record = json.loads(path.read_text(encoding="utf-8"))
    stdout_path = Path(record["stdout_path"])
    stderr_path = Path(record["stderr_path"])

    result = _read_json_if_complete(stdout_path)
    if result is None:
        result = _read_json_if_complete(stderr_path)
    if result is not None:
        out = dict(result)
        out["job_id"] = record["job_id"]
        out["pid"] = record["pid"]
        out["job_path"] = str(path)
        return out

    if _pid_alive(int(record["pid"])):
        return {
            "status": "RUNNING",
            "job_id": record["job_id"],
            "pid": record["pid"],
            "started_at": record["started_at"],
            "invocation": record["invocation"],
            "job_path": str(path),
        }

    return {
        "status": "ORPHANED",
        "job_id": record["job_id"],
        "pid": record["pid"],
        "started_at": record["started_at"],
        "invocation": record["invocation"],
        "job_path": str(path),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
    }


def main() -> int:
    args = parser().parse_args()
    try:
        cfg = load_config(expand_path(args.config))
        if args.command == "status":
            result = _detached_status(cfg, args.job)
        elif args.command == "run" and not args.dry_run and not args.foreground:
            result = _start_detached(args, cfg)
        else:
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
