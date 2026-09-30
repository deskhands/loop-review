from __future__ import annotations

import fcntl
import json
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from .git_state import _git
from .renderer import LABELS
from .util import LoopReviewError, atomic_json, load_json, sha256_bytes, write_text


def timestamp() -> str:
    return datetime.now().astimezone().isoformat()


def slug(text: str) -> str:
    return re.sub(r"[^\w.-]+", "-", text, flags=re.UNICODE).strip(".-_")[:48] or "review"


def contains(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


class RunStore:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def allocate(self, repo: Path, invocation: Dict[str, Any]) -> Path:
        common = Path(_git(repo, "rev-parse", "--git-common-dir").strip())
        common = (repo / common).resolve() if not common.is_absolute() else common.resolve()
        main = common.parent if common.name == ".git" else repo
        if contains(main, self.root) or contains(repo, self.root):
            raise LoopReviewError("CONFIG_ERROR", "run_root must be outside the reviewed repository")
        identity = sha256_bytes(str(common).encode())
        task = invocation.get("task", {})
        if not isinstance(task, dict):
            raise LoopReviewError("INVALID_INVOCATION", "task must be an object")
        title = invocation.get("title") or task.get("title") or f"{invocation['mode']} review"
        if not isinstance(title, str) or not title.strip():
            raise LoopReviewError("INVALID_INVOCATION", "title must be nonempty text")
        task_id = task.get("id") or secrets.token_hex(4)
        if not isinstance(task_id, str) or not task_id.strip():
            raise LoopReviewError("INVALID_INVOCATION", "task.id must be nonempty text")
        task_title = task.get("title") or title
        if not isinstance(task_title, str) or not task_title.strip():
            raise LoopReviewError("INVALID_INVOCATION", "task.title must be nonempty text")
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / ".store.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            repo_dir = self.root / slug(main.name)
            for candidate in self.root.glob("*/repo.json"):
                if load_json(candidate).get("identity") == identity:
                    repo_dir = candidate.parent
                    break
            else:
                if repo_dir.exists():
                    repo_dir = self.root / f"{slug(main.name)}_{identity[:8]}"
            repo_dir.mkdir(parents=True, exist_ok=True)
            atomic_json(repo_dir / "repo.json", {"identity": identity, "name": main.name, "common_dir": str(common)})
            task_dir = repo_dir / slug(task_title)
            for candidate in repo_dir.glob("*/task.json"):
                if load_json(candidate).get("id") == task_id:
                    task_dir = candidate.parent
                    break
            else:
                if task_dir.exists():
                    task_dir = repo_dir / f"{slug(task_title)}_{sha256_bytes(task_id.encode())[:8]}"
            task_dir.mkdir(parents=True, exist_ok=True)
            if not (task_dir / "task.json").exists():
                atomic_json(task_dir / "task.json", {"id": task_id, "title": task_title})
            run_id = secrets.token_hex(4)
            stamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H%M%S")
            run_dir = task_dir / f"{stamp}__{invocation['mode']}__{slug(title)}__{run_id}"
            run_dir.mkdir(exist_ok=False)
            atomic_json(run_dir / "status.json", {
                "schema_version": "2.0", "run_id": run_id, "job_id": run_id,
                "repo_id": identity, "repo_name": main.name, "task_id": task_id,
                "task_title": task_title, "title": title, "mode": invocation["mode"],
                "scope": invocation.get("scope", "full"), "status": "INIT",
                "run_dir": str(run_dir), "repo": str(repo), "started_at": timestamp(),
                "updated_at": timestamp(), "review_calls": 0, "elapsed_seconds": 0.0,
                "progress": {}, "report_path": str(run_dir / "report.md"),
                "previous_run_id": invocation.get("previous_run_id"),
            })
        return run_dir

    def runs(self) -> List[Dict[str, Any]]:
        runs = []
        for path in self.root.glob("*/*/*/status.json"):
            try:
                state = load_json(path)
                if state.get("schema_version") == "2.0":
                    runs.append(state)
            except LoopReviewError:
                continue
        # Historical v1 artifacts are read-only, never resumed as a v2 workflow.
        for path in self.root.glob("*/final/status.json"):
            try:
                state = load_json(path)
                state.setdefault("title", path.parent.parent.name)
                state["legacy"] = True
                state["report_path"] = str(path.parent / "review.md")
                runs.append(state)
            except LoopReviewError:
                continue
        return sorted(runs, key=lambda item: item.get("started_at", item.get("run_dir", "")), reverse=True)

    def resolve(self, run_id: Optional[str]) -> Path:
        runs = self.runs()
        matches = [item for item in runs if run_id is None or item.get("run_id") == run_id]
        if not matches:
            raise LoopReviewError("JOB_NOT_FOUND", f"Review run not found: {run_id or 'latest'}")
        if run_id is not None and len(matches) != 1:
            raise LoopReviewError("INVALID_ARGUMENT", "Ambiguous run ID")
        return Path(matches[0]["run_dir"]).resolve()

    def indexes(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / ".store.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            runs = self.runs()
            directories = {self.root}
            for item in runs:
                if not item.get("legacy"):
                    directory = Path(item["run_dir"])
                    directories.update((directory.parent, directory.parent.parent))
            for directory in directories:
                lines = [f"# {directory.name}", "", "Generated review index. Open a report for details.", "",
                         "| Task / review | ID | Status | Updated |", "| --- | --- | --- | --- |"]
                for item in runs:
                    report = Path(item["report_path"])
                    if not contains(directory, report):
                        continue
                    label = f"{item.get('repo_name', '')} / {item.get('task_title', '')} / {item['title']}"
                    label = label.replace("|", "\\|").replace("\n", " ").replace("[", "\\[").replace("]", "\\]")
                    link = quote(str(report.relative_to(directory)), safe="/:")
                    lines.append(f"| [{label}]({link}) | {item.get('run_id', '')} | {LABELS.get(item['status'], item['status'])} | {item.get('updated_at', '')} |")
                write_text(directory / "README.md", "\n".join(lines) + "\n")
