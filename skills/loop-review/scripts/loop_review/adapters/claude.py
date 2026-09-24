from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import ReviewerAdapter
from ..schemas import RESULT_SCHEMA
from ..util import LoopReviewError, atomic_json, run_cmd, write_text


_HEALTH_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


class ClaudeAdapter(ReviewerAdapter):
    def _base(self) -> list:
        return [
            self.config["executable"],
            "-p",
            "--bare",
            "--disable-slash-commands",
            "--no-session-persistence",
            "--permission-mode", "dontAsk",
            "--permission-prompts", "none",
            "--allowedTools", "Read,Glob,Grep",
            "--model", self.config["model"],
            "--effort", self.config["reasoning"],
            "--output-format", "json",
        ]

    def version(self) -> str:
        cp = run_cmd([self.config["executable"], "--version"], timeout=30)
        if cp.returncode != 0:
            raise LoopReviewError("CLI_NOT_FOUND", "Claude CLI version check failed", {"stderr": cp.stderr[-2000:]})
        return cp.stdout.strip() or cp.stderr.strip()

    def _extract(self, stdout: str) -> Dict[str, Any]:
        try:
            outer = json.loads(stdout)
        except json.JSONDecodeError as e:
            raise LoopReviewError("INVALID_JSON", f"Claude CLI did not return JSON: {e}", {"stdout": stdout[-4000:]})
        if outer.get("is_error"):
            raise LoopReviewError("MODEL_CALL_FAILED", "Claude model returned an error", {"raw": outer})
        structured = outer.get("structured_output")
        if isinstance(structured, dict):
            return structured
        result = outer.get("result")
        if isinstance(result, str):
            try:
                parsed = json.loads(result)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
        raise LoopReviewError("INVALID_JSON", "Claude response did not contain structured JSON", {"raw": outer})

    def healthcheck(self, cwd: Path) -> Dict[str, Any]:
        argv = self._base() + [
            "--json-schema", json.dumps(_HEALTH_SCHEMA, separators=(",", ":")),
            'Return exactly {"ok":true}. Use no tools.',
        ]
        cp = run_cmd(argv, cwd=cwd, timeout=min(int(self.config["timeout_seconds"]), 120))
        if cp.returncode != 0:
            raise LoopReviewError("MODEL_HEALTHCHECK_FAILED", "Claude healthcheck failed", {"stderr": cp.stderr[-4000:]})
        obj = self._extract(cp.stdout)
        if obj != {"ok": True}:
            raise LoopReviewError("MODEL_HEALTHCHECK_FAILED", "Claude healthcheck returned unexpected data", {"result": obj})
        return {"ok": True, "version": self.version(), "model": self.config["model"], "reasoning": self.config["reasoning"]}

    def review(
        self,
        prompt: str,
        cwd: Path,
        run_dir: Path,
        out_dir: Path,
        read_dirs: Optional[List[Path]] = None,
    ) -> Dict[str, Any]:
        out_dir.mkdir(parents=True, exist_ok=True)
        argv = self._base() + ["--add-dir", str(run_dir)]
        for read_dir in read_dirs or []:
            argv += ["--add-dir", str(read_dir)]
        argv += [
            "--json-schema", json.dumps(RESULT_SCHEMA, separators=(",", ":")),
            prompt,
        ]
        timeout_seconds = int(self.config["timeout_seconds"])
        started = time.time()
        try:
            cp = run_cmd(argv, cwd=cwd, timeout=timeout_seconds)
        except LoopReviewError as e:
            duration_ms = int((time.time() - started) * 1000)
            if e.code == "TIMEOUT":
                write_text(out_dir / "raw.stdout", str(e.details.get("stdout", "")))
                write_text(out_dir / "raw.stderr", str(e.details.get("stderr", "")))
                meta = {
                    "status": "TIMEOUT",
                    "duration_ms": duration_ms,
                    "exit_code": None,
                    "model": self.config["model"],
                    "reasoning": self.config["reasoning"],
                    "timeout_seconds": timeout_seconds,
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
                    "TIMEOUT",
                    e.message,
                    {
                        "timeout_seconds": timeout_seconds,
                        "partial_stdout_bytes": len(str(e.details.get("stdout", "")).encode("utf-8")),
                        "partial_stderr_bytes": len(str(e.details.get("stderr", "")).encode("utf-8")),
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
        write_text(out_dir / "raw.stdout", cp.stdout)
        write_text(out_dir / "raw.stderr", cp.stderr)
        meta = {
            "status": "OK" if cp.returncode == 0 else "FAILED",
            "duration_ms": duration_ms,
            "exit_code": cp.returncode,
            "model": self.config["model"],
            "reasoning": self.config["reasoning"],
            "timeout_seconds": timeout_seconds,
        }
        if cp.returncode != 0:
            atomic_json(out_dir / "meta.json", meta)
            atomic_json(
                out_dir / "error.json",
                {
                    "code": "NON_ZERO_EXIT",
                    "message": f"Claude reviewer exited {cp.returncode}",
                },
            )
            raise LoopReviewError(
                "NON_ZERO_EXIT",
                f"Claude reviewer exited {cp.returncode}",
                {"stderr": cp.stderr[-4000:], "stdout": cp.stdout[-4000:]},
            )
        try:
            result = self._extract(cp.stdout)
        except LoopReviewError as e:
            meta["status"] = "FAILED"
            atomic_json(out_dir / "meta.json", meta)
            atomic_json(out_dir / "error.json", {"code": e.code, "message": e.message})
            raise
        return {"result": result, "meta": meta}
