from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .util import LoopReviewError, require_ok, run_cmd, sha256_file, write_text


def _git(repo: Path, *args: str, timeout: int = 60) -> str:
    cp = run_cmd(["git", "-C", str(repo), *args], timeout=timeout)
    return require_ok(cp, "GIT_ERROR", "git " + " ".join(args))


def ensure_repo(repo: Path) -> Path:
    root = _git(repo, "rev-parse", "--show-toplevel").strip()
    return Path(root).resolve()


def repo_meta(repo: Path) -> Dict[str, str]:
    head = _git(repo, "rev-parse", "HEAD").strip()
    branch_cp = run_cmd(["git", "-C", str(repo), "branch", "--show-current"])
    branch = branch_cp.stdout.strip() if branch_cp.returncode == 0 else ""
    return {"root": str(repo), "head": head, "branch": branch}


def _untracked(repo: Path) -> List[Dict[str, str]]:
    raw = _git(repo, "ls-files", "--others", "--exclude-standard", "-z")
    items: List[Dict[str, str]] = []
    for rel in [x for x in raw.split("\0") if x]:
        p = repo / rel
        if p.is_file():
            items.append({"path": rel, "sha256": sha256_file(p)})
        else:
            items.append({"path": rel, "sha256": "<non-file>"})
    return items


def working_tree_fingerprint(repo: Path, agents_files: List[Dict[str, str]]) -> Dict[str, Any]:
    tracked = _git(repo, "diff", "--no-ext-diff", "--binary", "HEAD")
    staged = _git(repo, "diff", "--no-ext-diff", "--binary", "--cached")
    meta = repo_meta(repo)
    return {
        **meta,
        "tracked_diff_sha256": hashlib.sha256(tracked.encode("utf-8", errors="surrogateescape")).hexdigest(),
        "staged_diff_sha256": hashlib.sha256(staged.encode("utf-8", errors="surrogateescape")).hexdigest(),
        "untracked": _untracked(repo),
        "agents_files": agents_files,
    }


def file_fingerprint(path: Path, agents_files: List[Dict[str, str]], repo: Path) -> Dict[str, Any]:
    return {
        **repo_meta(repo),
        "target": {"path": str(path.resolve()), "sha256": sha256_file(path), "size": path.stat().st_size},
        "agents_files": agents_files,
    }


def git_range_fingerprint(repo: Path, base: str, head: str, agents_files: List[Dict[str, str]]) -> Dict[str, Any]:
    merge_base = _git(repo, "merge-base", base, head).strip()
    head_sha = _git(repo, "rev-parse", head).strip()
    diff = _git(repo, "diff", "--no-ext-diff", "--binary", f"{merge_base}..{head_sha}")
    return {
        **repo_meta(repo),
        "base": base,
        "range_head": head,
        "merge_base": merge_base,
        "range_head_sha": head_sha,
        "range_diff_sha256": hashlib.sha256(diff.encode("utf-8", errors="surrogateescape")).hexdigest(),
        "agents_files": agents_files,
    }


def changed_files_working_tree(repo: Path) -> List[str]:
    tracked = _git(repo, "diff", "--name-only", "HEAD").splitlines()
    untracked = [x["path"] for x in _untracked(repo)]
    return sorted(set([x for x in tracked if x] + untracked))


def changed_files_range(repo: Path, base: str, head: str) -> Tuple[str, List[str]]:
    merge_base = _git(repo, "merge-base", base, head).strip()
    files = _git(repo, "diff", "--name-only", f"{merge_base}..{head}").splitlines()
    return merge_base, sorted(set(x for x in files if x))


def write_working_tree_diff(repo: Path, out: Path) -> List[str]:
    tracked = _git(repo, "diff", "--no-ext-diff", "--binary", "HEAD")
    files = changed_files_working_tree(repo)
    chunks = [tracked]
    for rel in [x["path"] for x in _untracked(repo)]:
        p = repo / rel
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            chunks.append(f"\n# UNTRACKED BINARY FILE: {rel} sha256={sha256_file(p)}\n")
            continue
        chunks.append(
            f"\n--- /dev/null\n+++ b/{rel}\n@@ -0,0 +1,{len(text.splitlines())} @@\n"
            + "".join("+" + line + "\n" for line in text.splitlines())
        )
    write_text(out, "\n".join(chunks))
    return files


def write_range_diff(repo: Path, base: str, head: str, out: Path) -> List[str]:
    merge_base, files = changed_files_range(repo, base, head)
    diff = _git(repo, "diff", "--no-ext-diff", "--binary", f"{merge_base}..{head}")
    write_text(out, diff)
    return files
