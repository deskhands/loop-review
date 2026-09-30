from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .util import LoopReviewError, atomic_json


DEFAULT_LIMITS = {
    "max_task_attempts": 6, "max_turns": 32, "max_tool_calls": 48,
    "max_identical_tool_calls": 3, "max_provider_retries": 2,
    "max_total_tokens": 2_000_000, "max_run_seconds": 1800,
    "max_prompt_bytes": 65_536, "max_output_bytes": 52_428_800,
}


class EventMonitor:
    def __init__(self, limits: Dict[str, int], progress: Optional[Callable[[Dict[str, Any]], None]] = None):
        self.limits = {**DEFAULT_LIMITS, **limits}
        self.progress = progress
        self.calls: Counter[str] = Counter()
        self.messages: set[str] = set()
        self.tool_ids: set[str] = set()
        self.stats: Dict[str, Any] = {
            "turns": 0, "tool_calls": 0, "provider_retries": 0,
            "max_identical_tool_calls": 0, "total_tokens": None, "reported_cost": None,
        }

    def feed(self, raw: str) -> None:
        try:
            event = json.loads(raw)
        except ValueError:
            return
        if not isinstance(event, dict):
            return
        kind = event.get("type")
        if kind == "turn_start":
            self.stats["turns"] += 1
        elif kind == "auto_retry_start":
            self.stats["provider_retries"] += 1
        elif kind == "tool_execution_start":
            self._tool(event.get("toolName"), event.get("args"))
        elif kind == "message_end" and event.get("message", {}).get("role") == "assistant":
            self._usage(event["message"].get("usage", {}), cumulative=False)
        elif kind == "assistant":
            message = event.get("message", {})
            message_id = message.get("id") or str(event.get("uuid") or len(self.messages))
            if message_id not in self.messages:
                self.messages.add(message_id)
                self.stats["turns"] += 1
                self._usage(message.get("usage", {}), cumulative=False)
            for item in message.get("content", []):
                if item.get("type") == "tool_use" and item.get("id") not in self.tool_ids:
                    if item.get("id"):
                        self.tool_ids.add(item["id"])
                    self._tool(item.get("name"), item.get("input"))
        elif kind == "result":
            self._usage(event.get("usage", {}), cumulative=True)
            if isinstance(event.get("total_cost_usd"), (int, float)):
                self.stats["reported_cost"] = event["total_cost_usd"]
            if isinstance(event.get("num_turns"), int):
                self.stats["turns"] = max(self.stats["turns"], event["num_turns"])
        self.stats["last_event_at"] = time.time()
        for key in ("turns", "tool_calls", "identical_tool_calls", "provider_retries"):
            value = self.stats.get("max_identical_tool_calls" if key == "identical_tool_calls" else key, 0)
            if value > self.limits[f"max_{key}"]:
                raise LoopReviewError("TASK_LIMIT_EXCEEDED", f"Reviewer exceeded {key} limit", {"stats": dict(self.stats)})
        if self.progress:
            self.progress(dict(self.stats))

    def _tool(self, name: Any, args: Any) -> None:
        key = json.dumps([name, args], sort_keys=True, ensure_ascii=False)
        self.calls[key] += 1
        self.stats["tool_calls"] += 1
        self.stats["max_identical_tool_calls"] = max(self.calls.values())

    def _usage(self, usage: Dict[str, Any], *, cumulative: bool) -> None:
        total = usage.get("totalTokens")
        if total is None:
            fields = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
            if any(isinstance(usage.get(key), (int, float)) for key in fields):
                total = sum(usage.get(key, 0) for key in fields)
        if isinstance(total, (int, float)):
            self.stats["total_tokens"] = total if cumulative else (self.stats["total_tokens"] or 0) + total
        cost = usage.get("cost", {}).get("total")
        if isinstance(cost, (int, float)):
            self.stats["reported_cost"] = (self.stats["reported_cost"] or 0) + cost


def stop_group(proc: subprocess.Popen) -> None:
    # Killing the group also closes inherited pipes held by launcher descendants.
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=0.2)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        # macOS can report EPERM for a group whose terminated leader has just
        # become a zombie. Reap it before deciding whether escalation failed.
        if proc.poll() is None:
            raise
    proc.wait()


def stream_review(
    argv: List[str], *, cwd: Path, out_dir: Path, timeout: int,
    limits: Dict[str, int], progress: Optional[Callable[[Dict[str, Any]], None]] = None,
    cancel: Optional[Callable[[], None]] = None,
) -> subprocess.CompletedProcess:
    out_dir.mkdir(parents=True, exist_ok=True)
    monitor = EventMonitor(limits, progress)
    started = time.monotonic()
    try:
        proc = subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    except FileNotFoundError:
        raise LoopReviewError("CLI_NOT_FOUND", f"Executable not found: {argv[0]}")
    selector = None
    files = {}
    pending = b""
    total_bytes = 0
    error: Optional[Exception] = None
    try:
        # Cleanup ownership begins immediately after spawning, including setup I/O.
        atomic_json(out_dir / "process.json", {"pid": proc.pid})
        selector = selectors.DefaultSelector()
        assert proc.stdout is not None and proc.stderr is not None
        for pipe, filename in ((proc.stdout, "raw.stdout"), (proc.stderr, "raw.stderr")):
            files[pipe.fileno()] = (out_dir / filename).open("wb", buffering=0)
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ)
        while selector.get_map():
            if cancel:
                cancel()
            if time.monotonic() - started >= timeout:
                raise LoopReviewError("TIMEOUT", f"Reviewer timed out after {timeout}s")
            for key, _ in selector.select(timeout=0.2):
                pipe = key.fd
                chunk = os.read(pipe, 65_536)
                if not chunk:
                    selector.unregister(pipe)
                    continue
                files[pipe].write(chunk)
                total_bytes += len(chunk)
                if total_bytes > monitor.limits["max_output_bytes"]:
                    raise LoopReviewError("TASK_LIMIT_EXCEEDED", "Reviewer output exceeds byte limit")
                if pipe == proc.stdout.fileno():
                    pending += chunk
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        monitor.feed(line.decode("utf-8", errors="replace"))
        if pending:
            monitor.feed(pending.decode("utf-8", errors="replace"))
        # A worker may close its pipes before exiting. Keep supervision active.
        while proc.poll() is None:
            if cancel:
                cancel()
            if time.monotonic() - started >= timeout:
                raise LoopReviewError("TIMEOUT", f"Reviewer timed out after {timeout}s")
            time.sleep(0.05)
    except Exception as caught:
        error = caught
    finally:
        stop_group(proc)
        if selector is not None:
            selector.close()
        for output in files.values():
            output.close()
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.stderr is not None:
            proc.stderr.close()
        (out_dir / "process.json").unlink(missing_ok=True)
        meta = {"status": "FAILED" if error else "OK", "duration_ms": int((time.monotonic() - started) * 1000),
                "exit_code": None if error else proc.returncode, **monitor.stats}
        atomic_json(out_dir / "stream.json", meta)
    if error and not isinstance(error, LoopReviewError):
        raise error
    stdout = (out_dir / "raw.stdout").read_text(encoding="utf-8", errors="replace")
    stderr = (out_dir / "raw.stderr").read_text(encoding="utf-8", errors="replace")
    if isinstance(error, LoopReviewError):
        error.details.update({"stdout": stdout, "stderr": stderr, "stats": monitor.stats})
        atomic_json(out_dir / "error.json", {"code": error.code, "message": error.message})
        raise error
    return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)
