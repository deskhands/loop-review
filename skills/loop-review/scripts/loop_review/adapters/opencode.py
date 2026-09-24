from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import ReviewerAdapter
from ..util import LoopReviewError, run_cmd, write_text


_READ_ONLY_CONFIG = {
    "permission": {
        "*": "deny",
        "read": "allow",
        "glob": "allow",
        "grep": "allow",
        "external_directory": "allow",
    }
}


class OpenCodeAdapter(ReviewerAdapter):
    def _env(self) -> Dict[str, str]:
        env = os.environ.copy()
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(_READ_ONLY_CONFIG, separators=(",", ":"))
        env["OPENCODE_DISABLE_CLAUDE_CODE_SKILLS"] = "1"
        env["OPENCODE_DISABLE_EXTERNAL_SKILLS"] = "1"
        env["OPENCODE_DISABLE_PROJECT_CONFIG"] = "1"
        env["OPENCODE_DISABLE_DEFAULT_PLUGINS"] = "1"
        return env

    def version(self) -> str:
        cp = run_cmd([self.config["executable"], "--version"], env=self._env(), timeout=30)
        if cp.returncode != 0:
            raise LoopReviewError("CLI_NOT_FOUND", "OpenCode CLI version check failed", {"stderr": cp.stderr[-2000:]})
        return cp.stdout.strip() or cp.stderr.strip()

    def _extract_text(self, stdout: str) -> str:
        # OpenCode emits intermediate narration and a final answer as separate text events.
        # The final JSON contract is the last text event; concatenation corrupts valid JSON.
        last_text = None
        for line in stdout.splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "text":
                part = event.get("part") or {}
                value = part.get("text")
                if isinstance(value, str):
                    last_text = value
        if last_text is None:
            raise LoopReviewError("INVALID_JSON", "OpenCode emitted no text event", {"stdout": stdout[-4000:]})
        return last_text.strip()

    def _extract_json(self, stdout: str) -> Dict[str, Any]:
        texts: List[str] = []
        for line in stdout.splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "text":
                value = (event.get("part") or {}).get("text")
                if isinstance(value, str):
                    texts.append(value.strip())
        if not texts:
            raise LoopReviewError("INVALID_JSON", "OpenCode emitted no text event", {"stdout": stdout[-4000:]})

        last_error = None
        for text in reversed(texts):
            candidate = text
            if candidate.startswith(("```", "~~~")):
                fence = candidate[:3]
                lines = candidate.splitlines()[1:]
                if lines and lines[-1].strip() == fence:
                    lines = lines[:-1]
                candidate = "\n".join(lines).strip()
            try:
                obj = json.loads(candidate)
            except json.JSONDecodeError as e:
                last_error = e
                continue
            if isinstance(obj, dict):
                return obj
        raise LoopReviewError(
            "INVALID_JSON",
            f"OpenCode emitted no parseable JSON object: {last_error}",
            {"last_text": texts[-1][-4000:]},
        )

    def _argv(self, cwd: Path, prompt: str) -> List[str]:
        return [
            self.config["executable"], "run",
            "--pure",
            "--format", "json",
            "--model", self.config["model"],
            "--variant", self.config["reasoning"],
            "--dir", str(cwd),
            prompt,
        ]

    def healthcheck(self, cwd: Path) -> Dict[str, Any]:
        cp = run_cmd(
            self._argv(cwd, 'Return exactly this JSON object and nothing else: {"ok":true}'),
            cwd=cwd,
            env=self._env(),
            timeout=min(int(self.config["timeout_seconds"]), 120),
        )
        if cp.returncode != 0:
            raise LoopReviewError("MODEL_HEALTHCHECK_FAILED", "OpenCode healthcheck failed", {"stderr": cp.stderr[-4000:]})
        obj = self._extract_json(cp.stdout)
        if obj != {"ok": True}:
            raise LoopReviewError("MODEL_HEALTHCHECK_FAILED", "OpenCode healthcheck returned unexpected data", {"result": obj})
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
        started = time.time()
        cp = run_cmd(
            self._argv(cwd, prompt),
            cwd=cwd,
            env=self._env(),
            timeout=int(self.config["timeout_seconds"]),
        )
        duration_ms = int((time.time() - started) * 1000)
        write_text(out_dir / "raw.stdout", cp.stdout)
        write_text(out_dir / "raw.stderr", cp.stderr)
        if cp.returncode != 0:
            raise LoopReviewError(
                "NON_ZERO_EXIT",
                f"OpenCode reviewer exited {cp.returncode}",
                {"stderr": cp.stderr[-4000:], "stdout": cp.stdout[-4000:]},
            )
        return {
            "result": self._extract_json(cp.stdout),
            "meta": {
                "duration_ms": duration_ms,
                "exit_code": cp.returncode,
                "model": self.config["model"],
                "reasoning": self.config["reasoning"],
            },
        }
