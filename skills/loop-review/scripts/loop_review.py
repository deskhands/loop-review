#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from loop_review.config import DEFAULT_CONFIG, load_config
from loop_review.controller import LoopReviewController
from loop_review.renderer import LABELS, render_final
from loop_review.store import RunStore, timestamp
from loop_review.util import LoopReviewError, atomic_json, expand_path, load_json, write_text

ACTIVE = {"INIT", "PREPARED", "RESUMING", "DISCOVERY", "VERIFICATION"}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Bounded two-model review")
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--json", action="store_true", help="Machine-readable output")
    commands = p.add_subparsers(dest="command", required=True)
    for name in ("run", "list", "status", "resume", "cancel", "doctor", "_execute"):
        sub = commands.add_parser(name)
        sub.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        if name == "run":
            sub.add_argument("--invocation", required=True)
            sub.add_argument("--dry-run", action="store_true")
            group = sub.add_mutually_exclusive_group()
            group.add_argument("--foreground", action="store_true")
            group.add_argument("--detach", action="store_true", help="Compatibility alias for default detached execution")
        elif name in ("status", "resume", "cancel", "_execute"):
            sub.add_argument("--run", "--job", dest="run_id", required=name != "status")
            if name in ("resume", "_execute"):
                sub.add_argument("--foreground", action="store_true")
            if name == "_execute":
                sub.add_argument("--resuming", action="store_true")
        elif name == "list":
            sub.add_argument("--repo", help="Filter by repository path, including its worktrees")
        elif name == "doctor":
            sub.add_argument("--cwd", default=str(Path.home()))
    return p


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return pid > 0
    except (ProcessLookupError, PermissionError):
        return False


def run_status(store: RunStore, run_id: Optional[str]) -> Dict[str, Any]:
    directory = store.resolve(run_id)
    path = directory / "status.json"
    if not path.exists():
        result = load_json(directory / "final" / "status.json")
        result["legacy"] = True
        return result
    state = load_json(path)
    if state["status"] in ACTIVE:
        with (directory / ".run.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return state
            job_path = directory / "audit" / "job.json"
            if job_path.exists() and not _pid_alive(int(load_json(job_path)["pid"])):
                state.update(status="ORPHANED", updated_at=timestamp(), failure_code="ORPHANED",
                             message="Controller exited without a terminal result")
                result = load_json(directory / "result.json")
                result["status"] = "ORPHANED"
                atomic_json(path, state)
                atomic_json(directory / "result.json", result)
                write_text(directory / "report.md", render_final(state, result))
                store.indexes()
    return state


def start_detached(directory: Path, config_path: Path, *, resume: bool = False) -> Dict[str, Any]:
    audit = directory / "audit"
    audit.mkdir(exist_ok=True)
    with (directory / ".run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LoopReviewError("RUN_ALREADY_ACTIVE", "Another controller owns this run")
        state = load_json(directory / "status.json")
        if resume:
            if state["status"] not in ("FAILED", "CANCELED", "ORPHANED"):
                raise LoopReviewError("RESUME_NOT_SUPPORTED", "Run is no longer eligible for resume")
            archive = audit / "resumes" / secrets.token_hex(4)
            atomic_json(archive / "status.json", state)
            result = load_json(directory / "result.json")
            atomic_json(archive / "result.json", result)
            state.update(status="RESUMING", updated_at=timestamp())
            state.pop("failure_code", None)
            state.pop("message", None)
            result["status"] = "RESUMING"
            atomic_json(directory / "status.json", state)
            atomic_json(directory / "result.json", result)
            write_text(directory / "report.md", render_final(state, result))
        argv = [sys.executable, str(Path(__file__).resolve()), "--config", str(config_path),
                "_execute", "--run", state["run_id"], "--json"]
        if resume:
            argv.append("--resuming")
        with (audit / "controller.stdout").open("ab", buffering=0) as stdout, (audit / "controller.stderr").open("ab", buffering=0) as stderr:
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                       start_new_session=True, close_fds=True)
        atomic_json(audit / "job.json", {"pid": process.pid, "started_at": timestamp()})
    return {**state, "launch_status": "RESUME_STARTED" if resume else "STARTED", "pid": process.pid}


def display(result: Dict[str, Any]) -> str:
    if "runs" in result:
        lines = ["Repository / task / review | ID | Status"]
        for item in result["runs"]:
            lines.append(f"{item.get('repo_name', '')} / {item.get('task_title', '')} / {item.get('title', '')} | "
                         f"{item['run_id']} | {LABELS.get(item['status'], item['status'])}")
        return "\n".join(lines) if result["runs"] else "No review runs."
    if result.get("ok"):
        return "Both reviewer health checks passed."
    lines = [result.get("title", "Review"), LABELS.get(result.get("status", ""), result.get("status", ""))]
    if result.get("launch_status"):
        lines.append(result["launch_status"])
    for key in ("run_id", "task_id", "scope", "updated_at", "review_calls", "elapsed_seconds", "report_path", "run_dir"):
        if key in result:
            lines.append(f"{key}: {result[key]}")
    for key, progress in result.get("progress", {}).items():
        lines.append(f"{key}: {progress.get('status', 'RUNNING')}; turns={progress.get('turns', 0)}, "
                     f"tools={progress.get('tool_calls', 0)}, tokens={progress.get('total_tokens', 'unknown')}, "
                     f"last_event={progress.get('last_event_at', 'unknown')}")
    if result.get("failure_code"):
        lines.append(f"{result['failure_code']}: {result.get('message', '')}")
    return "\n".join(lines)


def main() -> int:
    args = parser().parse_args()
    try:
        cfg = load_config(expand_path(args.config))
        store = RunStore(Path(cfg["paths"]["run_root"]))
        controller = LoopReviewController(cfg, SCRIPT_DIR.parent)
        if args.command == "status":
            result = run_status(store, args.run_id)
        elif args.command == "list":
            runs = store.runs()
            if args.repo:
                from loop_review.git_state import _git, ensure_repo
                from loop_review.util import sha256_bytes
                repo = ensure_repo(expand_path(args.repo))
                common = Path(_git(repo, "rev-parse", "--git-common-dir").strip())
                identity = sha256_bytes(str((repo / common).resolve()).encode())
                runs = [item for item in runs if item.get("repo_id") == identity]
            result = {"runs": [run_status(store, item["run_id"]) for item in runs]}
        elif args.command == "doctor":
            result = controller.doctor(expand_path(args.cwd))
        elif args.command == "run":
            result = controller.prepare(expand_path(args.invocation), args.dry_run)
            if not args.dry_run:
                directory = Path(result["run_dir"])
                result = controller.execute(directory) if args.foreground else start_detached(directory, expand_path(args.config))
        else:
            directory = store.resolve(args.run_id)
            if not (directory / "status.json").exists():
                raise LoopReviewError("RESUME_NOT_SUPPORTED", "Legacy runs are inspection-only")
            state = run_status(store, args.run_id)
            if args.command == "cancel":
                if state["status"] not in ACTIVE:
                    raise LoopReviewError("INVALID_ARGUMENT", "Run is not active")
                atomic_json(directory / "audit" / "cancel.json", {"requested_at": timestamp()})
                result = {**state, "message": "Cancellation requested; workers stop at the next supervision check"}
            elif args.command == "resume":
                if state["status"] not in ("FAILED", "CANCELED", "ORPHANED"):
                    raise LoopReviewError("RESUME_NOT_SUPPORTED", "Only incomplete runs can resume")
                result = controller.resume(directory) if args.foreground else start_detached(directory, expand_path(args.config), resume=True)
            else:
                result = controller.execute(directory, resume=args.resuming, wait_for_launcher=True)
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else display(result))
        return 0
    except LoopReviewError as error:
        result = {"status": "FAILED", "failure_code": error.code, "message": error.message, **error.details}
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else display(result), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
