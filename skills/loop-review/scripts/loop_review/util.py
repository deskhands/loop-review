from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


class LoopReviewError(RuntimeError):
    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def expand_path(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(value))).resolve()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def atomic_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise LoopReviewError("TARGET_NOT_FOUND", f"JSON file not found: {path}")
    except json.JSONDecodeError as e:
        raise LoopReviewError("INVALID_JSON", f"Invalid JSON in {path}: {e}")


def run_cmd(
    argv: List[str],
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    timeout: int = 60,
    stdin_text: Optional[str] = None,
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            env=env,
            input=stdin_text,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise LoopReviewError("TIMEOUT", f"Command timed out after {timeout}s: {argv[0]}", {"argv": argv})
    except FileNotFoundError:
        raise LoopReviewError("CLI_NOT_FOUND", f"Executable not found: {argv[0]}", {"argv": argv})


def require_ok(cp: subprocess.CompletedProcess, code: str, context: str) -> str:
    if cp.returncode != 0:
        raise LoopReviewError(
            code,
            f"{context} failed with exit code {cp.returncode}",
            {"stdout": cp.stdout[-4000:], "stderr": cp.stderr[-4000:]},
        )
    return cp.stdout


def relpath_if_within(path: Path, root: Path) -> Optional[str]:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return None


def unique_preserve(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out
