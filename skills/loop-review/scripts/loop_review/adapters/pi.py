from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import ReviewerAdapter
from ..execution import stream_review
from ..util import LoopReviewError, atomic_json, run_cmd, write_text


class PiAdapter(ReviewerAdapter):
    def _base(self, *, tools: bool = True) -> List[str]:
        argv = [
            self.config["executable"],
            "--provider", "openrouter",
            "--model", self.config["model"],
            "--thinking", self.config["reasoning"],
            "--mode", "json",
            "--print",
            "--no-session",
            "--no-extensions",
            "--no-skills",
            "--no-prompt-templates",
            "--no-context-files",
            "--no-approve",
        ]
        if tools:
            argv += ["--tools", "read,grep,find,ls"]
        else:
            argv += ["--no-tools"]
        return argv

    def version(self) -> str:
        cp = run_cmd([self.config["executable"], "--version"], timeout=30)
        if cp.returncode != 0:
            raise LoopReviewError(
                "CLI_NOT_FOUND",
                "Pi CLI version check failed",
                {"stderr": cp.stderr[-2000:]},
            )
        return cp.stdout.strip() or cp.stderr.strip()

    def _events(self, stdout: str) -> Tuple[Optional[str], Dict[str, Any]]:
        final_text: Optional[str] = None
        turns = 0
        tool_calls: List[Tuple[str, str]] = []
        totals = {
            "input": 0,
            "output": 0,
            "cacheRead": 0,
            "cacheWrite": 0,
            "reasoning": 0,
            "totalTokens": 0,
            "cost": 0.0,
        }
        provider: Optional[str] = None
        model: Optional[str] = None
        last_assistant_stop_reason: Optional[str] = None
        last_assistant_error: Optional[str] = None
        retry_exhausted = False
        retry_final_error: Optional[str] = None
        provider_errors: List[str] = []

        for raw in stdout.splitlines():
            if not raw.strip():
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue

            event_type = event.get("type")
            if event_type == "turn_start":
                turns += 1
            elif event_type == "tool_execution_start":
                tool = str(event.get("toolName", ""))
                args = json.dumps(
                    event.get("args"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                tool_calls.append((tool, args))
            elif event_type == "message_end":
                message = event.get("message")
                if not isinstance(message, dict) or message.get("role") != "assistant":
                    continue
                provider = str(message.get("provider") or provider or "")
                model = str(message.get("model") or model or "")
                stop_reason = message.get("stopReason")
                error_message = message.get("errorMessage")
                last_assistant_stop_reason = str(stop_reason) if stop_reason is not None else None
                last_assistant_error = str(error_message) if error_message is not None else None
                if last_assistant_stop_reason == "error" and last_assistant_error:
                    provider_errors.append(last_assistant_error)
                usage = message.get("usage")
                if isinstance(usage, dict):
                    for key in ("input", "output", "cacheRead", "cacheWrite", "reasoning", "totalTokens"):
                        value = usage.get(key)
                        if isinstance(value, (int, float)):
                            totals[key] += value
                    cost = usage.get("cost")
                    if isinstance(cost, dict) and isinstance(cost.get("total"), (int, float)):
                        totals["cost"] += float(cost["total"])
                content = message.get("content")
                if isinstance(content, list):
                    texts = [
                        item.get("text", "")
                        for item in content
                        if isinstance(item, dict)
                        and item.get("type") == "text"
                        and isinstance(item.get("text"), str)
                    ]
                    if texts:
                        final_text = "".join(texts)
            elif event_type == "auto_retry_end":
                if event.get("success") is False:
                    retry_exhausted = True
                    retry_final_error = str(event.get("finalError") or "") or None
                elif event.get("success") is True:
                    retry_exhausted = False
                    retry_final_error = None

        repeats = Counter(tool_calls)
        stats = {
            "turns": turns,
            "tool_calls": len(tool_calls),
            "max_identical_tool_calls": max(repeats.values(), default=0),
            "usage": totals,
            "provider": provider,
            "model": model,
            "last_assistant_stop_reason": last_assistant_stop_reason,
            "last_assistant_error": last_assistant_error,
            "retry_exhausted": retry_exhausted,
            "retry_final_error": retry_final_error,
            "provider_errors": provider_errors,
        }
        return final_text, stats

    def _extract(self, stdout: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        final_text, stats = self._events(stdout)
        if stats["retry_exhausted"] or stats["last_assistant_stop_reason"] == "error":
            message = (
                stats["retry_final_error"]
                or stats["last_assistant_error"]
                or "provider request failed"
            )
            raise LoopReviewError(
                "PROVIDER_REQUEST_FAILED",
                f"Pi provider request failed: {message}",
                {"stats": stats},
            )
        if not final_text:
            raise LoopReviewError(
                "INVALID_JSON",
                "Pi response did not contain a final assistant text result",
                {"stats": stats},
            )
        if stats["last_assistant_stop_reason"] not in (None, "stop", "end_turn"):
            raise LoopReviewError("MODEL_CALL_FAILED", "Pi did not finish with a normal terminal response", {"stats": stats})

        try:
            result = json.loads(final_text)
        except json.JSONDecodeError as first_error:
            decoder = json.JSONDecoder()
            result = None
            for index, char in enumerate(final_text):
                if char != "{":
                    continue
                try:
                    candidate, consumed = decoder.raw_decode(final_text[index:])
                except json.JSONDecodeError:
                    continue
                if not isinstance(candidate, dict):
                    continue
                if final_text[index + consumed :].strip():
                    continue
                result = candidate
                break
            if result is None:
                raise LoopReviewError(
                    "INVALID_JSON",
                    f"Pi final assistant text was not JSON: {first_error}",
                    {"final_text": final_text[-4000:], "stats": stats},
                )

        if not isinstance(result, dict):
            raise LoopReviewError(
                "INVALID_JSON",
                "Pi final assistant result was not a JSON object",
                {"stats": stats},
            )
        return result, stats

    def parse_saved_output(self, stdout: str) -> Dict[str, Any]:
        result, _ = self._extract(stdout)
        return result

    def healthcheck(self, cwd: Path) -> Dict[str, Any]:
        cp = run_cmd(
            self._base(tools=False) + ['Return exactly {"ok":true} and nothing else.'],
            cwd=cwd,
            timeout=min(int(self.config["timeout_seconds"]), 120),
        )
        if cp.returncode != 0:
            raise LoopReviewError(
                "MODEL_HEALTHCHECK_FAILED",
                "Pi healthcheck failed",
                {"stderr": cp.stderr[-4000:], "stdout": cp.stdout[-4000:]},
            )
        obj, stats = self._extract(cp.stdout)
        if obj != {"ok": True}:
            raise LoopReviewError(
                "MODEL_HEALTHCHECK_FAILED",
                "Pi healthcheck returned unexpected data",
                {"result": obj, "stats": stats},
            )
        return {
            "ok": True,
            "version": self.version(),
            "provider": "openrouter",
            "model": self.config["model"],
            "reasoning": self.config["reasoning"],
        }

    def review(
        self,
        prompt: str,
        cwd: Path,
        run_dir: Path,
        out_dir: Path,
        read_dirs: Optional[List[Path]] = None,
    ) -> Dict[str, Any]:
        out_dir.mkdir(parents=True, exist_ok=True)
        argv = self._base(tools=True) + [prompt]
        timeout_seconds = int(self.config["timeout_seconds"])
        started = time.time()

        try:
            cp = stream_review(argv, cwd=cwd, out_dir=out_dir, timeout=timeout_seconds,
                               limits=self.limits, progress=self.progress, cancel=self.cancel)
        except LoopReviewError as e:
            duration_ms = int((time.time() - started) * 1000)
            if e.code in ("TIMEOUT", "TASK_LIMIT_EXCEEDED", "RUN_BUDGET_EXCEEDED", "CANCELED"):
                stdout = str(e.details.get("stdout", ""))
                stderr = str(e.details.get("stderr", ""))
                _, stats = self._events(stdout)
                if not (out_dir / "raw.stdout").exists():
                    write_text(out_dir / "raw.stdout", stdout)
                if not (out_dir / "raw.stderr").exists():
                    write_text(out_dir / "raw.stderr", stderr)
                meta = {
                    "status": e.code,
                    "duration_ms": duration_ms,
                    "exit_code": None,
                    "provider": "openrouter",
                    "model": self.config["model"],
                    "reasoning": self.config["reasoning"],
                    "timeout_seconds": timeout_seconds,
                    **stats,
                }
                atomic_json(out_dir / "meta.json", meta)
                atomic_json(
                    out_dir / "error.json",
                    {
                        "code": e.code,
                        "message": e.message,
                        "timeout_seconds": timeout_seconds,
                    },
                )
                raise LoopReviewError(
                    e.code,
                    e.message,
                    {
                        "timeout_seconds": timeout_seconds,
                        "partial_stdout_bytes": len(stdout.encode("utf-8")),
                        "partial_stderr_bytes": len(stderr.encode("utf-8")),
                        "stats": stats,
                        "artifacts": {
                            "stdout": str((out_dir / "raw.stdout").resolve()),
                            "stderr": str((out_dir / "raw.stderr").resolve()),
                            "meta": str((out_dir / "meta.json").resolve()),
                            "error": str((out_dir / "error.json").resolve()),
                        },
                    },
                )
            raise

        duration_ms = int((time.time() - started) * 1000)
        if not (out_dir / "raw.stdout").exists():
            write_text(out_dir / "raw.stdout", cp.stdout)
        if not (out_dir / "raw.stderr").exists():
            write_text(out_dir / "raw.stderr", cp.stderr)

        final_text, stats = self._events(cp.stdout)
        meta = {
            "status": "OK" if cp.returncode == 0 else "FAILED",
            "duration_ms": duration_ms,
            "exit_code": cp.returncode,
            "provider": "openrouter",
            "model": self.config["model"],
            "reasoning": self.config["reasoning"],
            "timeout_seconds": timeout_seconds,
            **stats,
        }
        if cp.returncode != 0:
            atomic_json(out_dir / "meta.json", meta)
            atomic_json(
                out_dir / "error.json",
                {
                    "code": "NON_ZERO_EXIT",
                    "message": f"Pi reviewer exited {cp.returncode}",
                },
            )
            raise LoopReviewError(
                "NON_ZERO_EXIT",
                f"Pi reviewer exited {cp.returncode}",
                {"stderr": cp.stderr[-4000:], "stdout": cp.stdout[-4000:]},
            )

        try:
            result, parsed_stats = self._extract(cp.stdout)
            meta.update(parsed_stats)
        except LoopReviewError as e:
            meta["status"] = "FAILED"
            atomic_json(out_dir / "meta.json", meta)
            atomic_json(out_dir / "error.json", {"code": e.code, "message": e.message})
            raise

        return {"result": result, "meta": meta}
