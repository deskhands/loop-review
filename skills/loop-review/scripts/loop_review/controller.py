from __future__ import annotations

import concurrent.futures
import copy
import fcntl
import json
import secrets
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .adapters import ClaudeAdapter, PiAdapter
from .agents_rules import discover_rules
from .execution import DEFAULT_LIMITS, EventMonitor
from .git_state import (ensure_repo, git_range_fingerprint, repo_meta, working_tree_fingerprint,
                        write_range_diff, write_working_tree_diff)
from .ledger import discovery_claims, final_status, resolve_claims
from .prompts import build_prompt
from .renderer import render_final
from .schemas import validate_result
from .store import RunStore, timestamp
from .util import LoopReviewError, atomic_json, expand_path, load_json, sha256_bytes, sha256_file, write_text


class LoopReviewController:
    def __init__(self, config: Dict[str, Any], skill_root: Path):
        self.config = copy.deepcopy(config)
        self.config['limits'] = {**DEFAULT_LIMITS, **config.get('limits', {})}
        self.skill_root = skill_root.resolve()
        self.store = RunStore(Path(config['paths']['run_root']))
        self.reviewers = {
            'reviewer-a': PiAdapter('reviewer-a', config['reviewers']['qwen']),
            'reviewer-b': ClaudeAdapter('reviewer-b', config['reviewers']['deepseek']),
        }
        self.lock = threading.RLock()
        self.state: Dict[str, Any] = {}
        self.result: Dict[str, Any] = {}
        self.session_started = 0.0
        self.prior_elapsed = 0.0
        self.run_dir = Path()
        self.last_publish = 0.0

    def doctor(self, cwd: Optional[Path] = None) -> Dict[str, Any]:
        cwd = (cwd or Path.home()).resolve()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = {name: pool.submit(adapter.healthcheck, cwd) for name, adapter in self.reviewers.items()}
            return {'ok': True, 'reviewers': {name: future.result() for name, future in futures.items()}}

    def _persist_source(self, source: Dict[str, Any], input_dir: Path, name: str) -> Tuple[str, Path]:
        kind = source.get("kind")
        if kind == "text":
            content = source.get("content")
            if not isinstance(content, str):
                raise LoopReviewError("INVALID_INVOCATION", f"{name}.content must be text")
            path = input_dir / f"{name}.md"
            write_text(path, content)
            return content, path
        if kind == "file":
            raw = source.get("path")
            if not isinstance(raw, str):
                raise LoopReviewError("INVALID_INVOCATION", f"{name}.path is required")
            path = expand_path(raw)
            if not path.is_file():
                raise LoopReviewError("TARGET_NOT_FOUND", f"{name} file not found: {path}")
            ref = {"path": str(path), "sha256": sha256_file(path), "size": path.stat().st_size}
            atomic_json(input_dir / f"{name}.ref.json", ref)
            return path.read_text(encoding="utf-8"), path
        raise LoopReviewError("INVALID_INVOCATION", f"{name}.kind must be text or file")

    def _prepare(
        self,
        invocation: Dict[str, Any],
        run_dir: Path,
        repo: Path,
    ) -> Dict[str, Any]:
        input_dir = run_dir / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        atomic_json(input_dir / "invocation.json", invocation)

        mode = invocation.get("mode")
        if mode not in ("design", "code", "review"):
            raise LoopReviewError("INVALID_INVOCATION", "mode must be design, code, or review")
        request_source = invocation.get("request")
        if not isinstance(request_source, dict):
            raise LoopReviewError("INVALID_INVOCATION", "request object is required")
        request_text, request_path = self._persist_source(request_source, input_dir, "request")

        target: Any = invocation.get("target")
        if not isinstance(target, dict):
            raise LoopReviewError("INVALID_INVOCATION", "target object is required")

        target_kind = target.get("kind")
        target_paths: List[Path] = []
        target_description = ""
        target_path: Optional[Path] = None
        base: Optional[str] = None
        range_head: Optional[str] = None

        if target_kind in ("text", "file"):
            _, target_path = self._persist_source(target, input_dir, "target")
            try:
                target_path.relative_to(repo)
                target_paths = [target_path]
            except ValueError:
                target_paths = []
            target_description = f"Read and review this complete target file: {target_path}"
        elif target_kind == 'files':
            paths = target.get('paths')
            if not isinstance(paths, list) or not paths or any(not isinstance(p, str) for p in paths):
                raise LoopReviewError('INVALID_INVOCATION', 'files target requires a nonempty paths list')
            target_paths = [expand_path(path) for path in paths]
            if len(target_paths) != len(set(target_paths)):
                raise LoopReviewError('INVALID_INVOCATION', 'files target must not contain duplicate paths')
            for index, path in enumerate(target_paths):
                self._persist_source({'kind': 'file', 'path': str(path)}, input_dir, f'target-{index:03d}')
            target_description = 'Read and review EVERY complete target file: ' + json.dumps([str(p) for p in target_paths])
        elif target_kind == "working-tree":
            if mode == "design":
                raise LoopReviewError("INVALID_INVOCATION", "design mode does not support working-tree target")
            diff_path = input_dir / "target.diff"
            changed: List[str] = write_working_tree_diff(repo, diff_path)
            atomic_json(input_dir / "changed-files.json", changed)
            target_paths = [repo / rel for rel in changed]
            target_path = diff_path
            target_description = (
                f"Review the current Git working tree in repository {repo}. "
                f"Start with the complete generated diff: {diff_path}. "
                f"Then inspect the real source files and surrounding callers/tests using read/search tools."
            )
        elif target_kind == "git-range":
            if mode == "design":
                raise LoopReviewError("INVALID_INVOCATION", "design mode does not support git-range target")
            base = target.get("base")
            range_head = target.get("head", "HEAD")
            if not isinstance(base, str) or not isinstance(range_head, str):
                raise LoopReviewError("INVALID_INVOCATION", "git-range needs string base and head")
            diff_path = input_dir / "target.diff"
            changed = write_range_diff(repo, base, range_head, diff_path)
            atomic_json(input_dir / "changed-files.json", changed)
            target_paths = [repo / rel for rel in changed]
            target_path = diff_path
            target_description = (
                f"Review Git range base={base} head={range_head} in repository {repo}. "
                f"Start with the complete generated diff: {diff_path}. "
                f"Then inspect the real source files and surrounding callers/tests."
            )
        else:
            raise LoopReviewError("INVALID_INVOCATION", "Unsupported target.kind")

        seed_review_path: Optional[Path] = None
        if mode == "review":
            seed = invocation.get("seed_review")
            if not isinstance(seed, dict):
                raise LoopReviewError("INVALID_INVOCATION", "review mode requires seed_review")
            _, seed_review_path = self._persist_source(seed, input_dir, "seed-review")

        rules = discover_rules(repo, target_paths)
        atomic_json(input_dir / "agents-map.json", rules)
        policy_paths = [x["path"] for x in rules["files"]]

        read_dirs: List[Path] = []
        for candidate in (*target_paths, target_path, seed_review_path):
            if candidate is None:
                continue
            try:
                candidate.relative_to(repo)
            except ValueError:
                read_dirs.append(candidate.parent.resolve())

        prepared = {
            "mode": mode,
            "repo": repo,
            "request_text": request_text,
            "request_path": request_path,
            "target_kind": target_kind,
            "target_path": target_path,
            "target_paths": target_paths,
            "target_description": target_description,
            "base": base,
            "range_head": range_head,
            "seed_review_path": seed_review_path,
            "read_dirs": sorted(set(read_dirs)),
            "rules": rules,
            "policy_paths": policy_paths,
        }
        prepared["fingerprint"] = self._fingerprint(prepared)

        # Close the snapshot window between creating a generated diff and
        # fingerprinting repository state: regenerate once and require the
        # bytes to match the artifact reviewers will read.
        if target_kind in ("working-tree", "git-range") and target_path:
            verify_path = input_dir / "target.verify.diff"
            try:
                if target_kind == "working-tree":
                    write_working_tree_diff(repo, verify_path)
                else:
                    assert base is not None and range_head is not None
                    write_range_diff(repo, base, range_head, verify_path)
                if sha256_file(verify_path) != sha256_file(target_path):
                    raise LoopReviewError(
                        "FAILED_INPUT_CHANGED",
                        "Repository changed while the review diff snapshot was being created",
                    )
            finally:
                if verify_path.exists():
                    verify_path.unlink()

        atomic_json(input_dir / "repo-fingerprint.json", prepared["fingerprint"])
        return prepared

    def _fingerprint(self, prepared: Dict[str, Any]) -> Dict[str, Any]:
        repo: Path = prepared["repo"]
        agents_files = [{**item, "sha256": sha256_file(Path(item["path"]))}
                        for item in prepared["rules"]["files"]]
        base_fp = working_tree_fingerprint(repo, agents_files)
        kind = prepared["target_kind"]
        target_path = prepared.get("target_path")
        if kind in ("text", "file") and target_path:
            base_fp["target"] = {
                "path": str(target_path),
                "sha256": sha256_file(target_path),
                "size": target_path.stat().st_size,
            }
        elif kind == 'files':
            base_fp['target_files'] = [{'path': str(p), 'sha256': sha256_file(p), 'size': p.stat().st_size}
                                       for p in prepared['target_paths']]
        elif kind == "working-tree" and target_path:
            base_fp["target_artifact"] = {
                "path": str(target_path),
                "sha256": sha256_file(target_path),
                "size": target_path.stat().st_size,
            }
        elif kind == "git-range":
            range_fp = git_range_fingerprint(repo, prepared["base"], prepared["range_head"], agents_files)
            base_fp["git_range"] = {
                "base": range_fp["base"],
                "range_head": range_fp["range_head"],
                "merge_base": range_fp["merge_base"],
                "range_head_sha": range_fp["range_head_sha"],
                "range_diff_sha256": range_fp["range_diff_sha256"],
            }
            if target_path:
                base_fp["target_artifact"] = {
                    "path": str(target_path),
                    "sha256": sha256_file(target_path),
                    "size": target_path.stat().st_size,
                }
        seed = prepared.get("seed_review_path")
        if seed:
            base_fp["seed_review"] = {
                "path": str(seed),
                "sha256": sha256_file(seed),
                "size": seed.stat().st_size,
            }
        return base_fp

    def _verify_fingerprint(self, prepared: Dict[str, Any]) -> None:
        current = self._fingerprint(prepared)
        if current != prepared["fingerprint"]:
            raise LoopReviewError(
                "FAILED_INPUT_CHANGED",
                "Repository, target, seed review, or AGENTS.md changed during review",
                {"expected": prepared["fingerprint"], "actual": current},
            )

    def _load_persisted_source(
        self,
        source: Dict[str, Any],
        input_dir: Path,
        name: str,
    ) -> Tuple[str, Path]:
        kind = source.get("kind")
        if kind == "text":
            path = input_dir / f"{name}.md"
            if not path.is_file():
                raise LoopReviewError("RESUME_CHECKPOINT_INVALID", f"Missing persisted {name}: {path}")
            return path.read_text(encoding="utf-8"), path
        if kind == "file":
            ref_path = input_dir / f"{name}.ref.json"
            if not ref_path.is_file():
                raise LoopReviewError("RESUME_CHECKPOINT_INVALID", f"Missing persisted {name} reference: {ref_path}")
            ref = load_json(ref_path)
            path = Path(ref["path"]).expanduser().resolve()
            if not path.is_file():
                raise LoopReviewError("FAILED_INPUT_CHANGED", f"{name} file no longer exists: {path}")
            if path.stat().st_size != int(ref["size"]) or sha256_file(path) != ref["sha256"]:
                raise LoopReviewError("FAILED_INPUT_CHANGED", f"{name} file changed since the failed run: {path}")
            return path.read_text(encoding="utf-8"), path
        raise LoopReviewError("RESUME_CHECKPOINT_INVALID", f"Unsupported persisted {name}.kind: {kind}")

    def _load_prepared_for_resume(self, run_dir: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        input_dir = run_dir / "input"
        invocation = load_json(input_dir / "invocation.json")
        manifest = load_json(run_dir / "manifest.json")
        repo = ensure_repo(expand_path(invocation["repo"]))
        if Path(manifest["repo"]["root"]).resolve() != repo:
            raise LoopReviewError("FAILED_INPUT_CHANGED", "Repository path no longer matches the failed run")

        mode = invocation.get("mode")
        if mode not in ("design", "code", "review") or manifest.get("mode") != mode:
            raise LoopReviewError("RESUME_CHECKPOINT_INVALID", "Run mode is missing or inconsistent")

        request_text, request_path = self._load_persisted_source(
            invocation["request"], input_dir, "request"
        )
        target = invocation.get("target")
        if not isinstance(target, dict):
            raise LoopReviewError("RESUME_CHECKPOINT_INVALID", "Persisted invocation has no target")
        target_kind = target.get("kind")
        target_path: Optional[Path] = None
        target_paths: List[Path] = []
        base: Optional[str] = None
        range_head: Optional[str] = None

        if target_kind in ("text", "file"):
            _, target_path = self._load_persisted_source(target, input_dir, "target")
            try:
                target_path.relative_to(repo)
                target_paths = [target_path]
            except ValueError:
                target_paths = []
            target_description = f"Read and review this complete target file: {target_path}"
        elif target_kind == 'files':
            for index, raw_path in enumerate(target['paths']):
                _, path = self._load_persisted_source({'kind': 'file', 'path': raw_path}, input_dir, f'target-{index:03d}')
                target_paths.append(path)
            target_description = 'Read and review EVERY complete target file: ' + json.dumps([str(p) for p in target_paths])
        elif target_kind == "working-tree":
            target_path = input_dir / "target.diff"
            if not target_path.is_file():
                raise LoopReviewError("RESUME_CHECKPOINT_INVALID", "Persisted working-tree diff is missing")
            changed_path = input_dir / "changed-files.json"
            changed = load_json(changed_path) if changed_path.is_file() else []
            target_paths = [repo / rel for rel in changed]
            target_description = (
                f"Review the current Git working tree in repository {repo}. "
                f"Start with the complete generated diff: {target_path}. "
                f"Then inspect the real source files and surrounding callers/tests using read/search tools."
            )
        elif target_kind == "git-range":
            base = target.get("base")
            range_head = target.get("head", "HEAD")
            if not isinstance(base, str) or not isinstance(range_head, str):
                raise LoopReviewError("RESUME_CHECKPOINT_INVALID", "Persisted git-range is invalid")
            target_path = input_dir / "target.diff"
            if not target_path.is_file():
                raise LoopReviewError("RESUME_CHECKPOINT_INVALID", "Persisted git-range diff is missing")
            changed_path = input_dir / "changed-files.json"
            changed = load_json(changed_path) if changed_path.is_file() else []
            target_paths = [repo / rel for rel in changed]
            target_description = (
                f"Review Git range base={base} head={range_head} in repository {repo}. "
                f"Start with the complete generated diff: {target_path}. "
                f"Then inspect the real source files and surrounding callers/tests."
            )
        else:
            raise LoopReviewError("RESUME_CHECKPOINT_INVALID", f"Unsupported persisted target.kind: {target_kind}")

        seed_review_path: Optional[Path] = None
        if mode == "review":
            seed = invocation.get("seed_review")
            if not isinstance(seed, dict):
                raise LoopReviewError("RESUME_CHECKPOINT_INVALID", "Persisted review mode has no seed review")
            _, seed_review_path = self._load_persisted_source(seed, input_dir, "seed-review")

        rules = load_json(input_dir / "agents-map.json")
        policy_paths = [item["path"] for item in rules["files"]]
        read_dirs: List[Path] = []
        for candidate in (*target_paths, target_path, seed_review_path):
            if candidate is None:
                continue
            try:
                candidate.relative_to(repo)
            except ValueError:
                read_dirs.append(candidate.parent.resolve())

        prepared = {
            "mode": mode,
            "repo": repo,
            "request_text": request_text,
            "request_path": request_path,
            "target_kind": target_kind,
            "target_path": target_path,
            "target_paths": target_paths,
            "target_description": target_description,
            "base": base,
            "range_head": range_head,
            "seed_review_path": seed_review_path,
            "read_dirs": sorted(set(read_dirs)),
            "rules": rules,
            "policy_paths": policy_paths,
            "fingerprint": load_json(input_dir / "repo-fingerprint.json"),
        }
        self._verify_fingerprint(prepared)
        return prepared, manifest

    def prepare(self, invocation_path: Path, dry_run: bool = False) -> Dict[str, Any]:
        invocation = load_json(invocation_path)
        if not isinstance(invocation, dict) or invocation.get("mode") not in ("design", "code", "review"):
            raise LoopReviewError("INVALID_INVOCATION", "Invocation requires a supported mode")
        repo = ensure_repo(expand_path(invocation["repo"]))
        previous = invocation.get("previous_run_id")
        if previous:
            prior = load_json(self.store.resolve(previous) / "status.json")
            invocation.setdefault("task", {"id": prior["task_id"], "title": prior["task_title"]})
        self.run_dir = self.store.allocate(repo, invocation)
        self.session_started = self.prior_elapsed = 0.0
        self.state = load_json(self.run_dir / "status.json")
        self.result = {"schema_version": "2.0", "findings": [], "open_questions": []}
        try:
            if self.state["scope"] not in ("full", "fixes") or (self.state["scope"] == "fixes" and not previous):
                raise LoopReviewError("INVALID_INVOCATION", "fixes scope requires previous_run_id")
            prepared = self._prepare(invocation, self.run_dir, repo)
            history = self._history_claims(previous)
            if self.state["scope"] == "fixes" and not history:
                raise LoopReviewError("INVALID_INVOCATION", "fixes scope requires at least one unresolved historical claim")
            atomic_json(self.run_dir / "input" / "history.json", history)
            manifest = {
                "schema_version": "2.0", "workflow": "discovery-verification-v2",
                "mode": prepared["mode"], "repo": repo_meta(repo), "limits": self.config["limits"],
                "reviewers": self.config["reviewers"], "input_fingerprint": prepared["fingerprint"],
                "request_sha256": sha256_file(prepared["request_path"]),
                "invocation_sha256": sha256_file(self.run_dir / "input" / "invocation.json"),
                "history_sha256": sha256_file(self.run_dir / "input" / "history.json"),
                "prompt_sha256": self._prompt_fingerprint(prepared),
            }
            atomic_json(self.run_dir / "manifest.json", manifest)
            self.state["status"] = "PREPARED_DRY_RUN" if dry_run else "PREPARED"
            self._publish()
            if dry_run:
                for name in self.reviewers:
                    phase = "verification" if self.state["scope"] == "fixes" else "discovery"
                    prompt = self._prompt(prepared, name, phase, history if phase == "verification" else [])
                    write_text(self.run_dir / "audit" / phase / name / "prompt.md", prompt)
            self.store.indexes()
            return dict(self.state)
        except Exception as error:
            raise self._failure(error)

    def _history_claims(self, previous: Optional[str]) -> List[Dict[str, Any]]:
        if not previous:
            return []
        directory = self.store.resolve(previous)
        prior = load_json(directory / "status.json")
        if prior.get("schema_version") != "2.0":
            raise LoopReviewError("INVALID_INVOCATION", "Historical verification requires a v2 run")
        if prior["repo_id"] != self.state["repo_id"] or prior["task_id"] != self.state["task_id"]:
            raise LoopReviewError("INVALID_INVOCATION", "Previous run must belong to the same repository and task")
        if prior["status"] in ("INIT", "PREPARED", "RESUMING", "DISCOVERY", "VERIFICATION"):
            raise LoopReviewError("INVALID_INVOCATION", "Previous run has not finished")
        result = load_json(directory / "result.json")
        return [{"id": f"H{index + 1:03d}", "origin": "history", "previous_id": item["id"],
                 "finding": item["finding"]}
                for index, item in enumerate(result["findings"])
                if item["status"] != "RESOLVED"]

    def _prompt(self, prepared: Dict[str, Any], name: str, phase: str, claims: List[Dict[str, Any]]) -> str:
        prompt = build_prompt(
            skill_root=self.skill_root, mode=prepared["mode"], request_text=prepared["request_text"],
            target_description=prepared["target_description"], policy_paths=prepared["policy_paths"],
            claims=claims, phase=phase, reviewer_alias=name,
            budget_path=str(self.run_dir / 'audit' / phase / name / 'live-budget.json'),
            token_limit=self._token_limit(phase), limits=self.config['limits'],
            seed_review_path=str(prepared["seed_review_path"]) if prepared.get("seed_review_path") else None,
        )
        if len(prompt.encode()) > self.config["limits"]["max_prompt_bytes"]:
            raise LoopReviewError("PROMPT_LIMIT_EXCEEDED", "Review prompt exceeds configured byte limit; narrow the review scope")
        return prompt

    def _prompt_fingerprint(self, prepared: Dict[str, Any]) -> Dict[str, str]:
        return {f"{phase}/{name}": sha256_bytes(self._prompt(prepared, name, phase, []).encode())
                for phase in ("discovery", "verification") for name in self.reviewers}

    def _reconcile_usage(self) -> None:
        # Terminal stream accounting can survive a controller crash before status publication.
        for stream in (self.run_dir / "audit").glob("*/*/attempt-*/stream.json"):
            key = str(stream.parent.relative_to(self.run_dir / "audit"))
            stats = self.state["progress"].setdefault(key, {})
            stats.update(load_json(stream))
            corrected = self._terminal_turn_recovery(stream.parent)
            if corrected is not None:
                stats.update(corrected)
            stats['status'] = ('RECOVERED' if (stream.parent / 'recovery.json').exists() else 'OK') if (stream.parent / 'result.json').exists() else 'FAILED'

    def _terminal_turn_recovery(self, attempt: Path) -> Optional[Dict[str, Any]]:
        # Recover only the known false rejection at a successful Claude terminal.
        # Original failure artifacts remain immutable; other failures still retry.
        error_path = attempt / 'error.json'
        if not error_path.exists() or attempt.parent.name != 'reviewer-b':
            return None
        error = load_json(error_path)
        if error.get('code') != 'TASK_LIMIT_EXCEEDED' or error.get('message') != 'Reviewer exceeded turns limit':
            return None
        stream = load_json(attempt / 'stream.json')
        if stream.get('exit_code') is not None or (stream.get('turns') or 0) <= self.config['limits']['max_turns']:
            return None
        raw_path = attempt / 'raw.stdout'
        try:
            raw = raw_path.read_text()
            terminal = json.loads(raw.rstrip().splitlines()[-1])
            if (terminal.get('type') != 'result' or terminal.get('subtype') != 'success'
                    or terminal.get('is_error') is not False
                    or terminal.get('terminal_reason', 'completed') != 'completed'
                    or terminal.get('num_turns') != stream.get('turns')):
                return None
            monitor = EventMonitor(self.config['limits'], budget_path=attempt.parent / 'live-budget.json')
            for line in raw.splitlines():
                monitor.feed(line)
            if not monitor.stats['turns']:
                return None
            return monitor.stats
        except (OSError, ValueError, IndexError, AttributeError, LoopReviewError):
            return None

    def _elapsed(self) -> float:
        return self.prior_elapsed + (time.monotonic() - self.session_started if self.session_started else 0)

    def _token_limit(self, phase: str) -> int:
        share = self.config['limits']['max_total_tokens'] // len(self.reviewers)
        if self.state['scope'] == 'fixes':
            return share if phase == 'verification' else 0
        discovery = share * 3 // 4
        return discovery if phase == 'discovery' else share - discovery

    def _budget(self, phase: str, name: str) -> Dict[str, Any]:
        attempts = [item for key, item in self.state['progress'].items() if key.startswith(f'{phase}/{name}/')]
        known = [item['total_tokens'] for item in attempts if item.get('total_tokens') is not None]
        tokens = sum(known)
        limit = self._token_limit(phase)
        limits = self.config['limits']
        finish = (bool(known) and tokens * 4 >= limit * 3) or any(
            item.get('finish_requested') or item.get('turns', 0) * 4 >= limits['max_turns'] * 3
            or item.get('tool_calls', 0) * 4 >= limits['max_tool_calls'] * 3 for item in attempts)
        return {'token_limit': limit, 'known_tokens': tokens if known else None,
                'usage_unknown': not attempts or len(known) != len(attempts),
                'remaining_tokens': max(0, limit - tokens), 'action': 'finish' if finish else 'inspect',
                'max_turns': limits['max_turns'], 'max_tool_calls': limits['max_tool_calls']}

    def _coverage_question(self, result: Dict[str, Any], phase: str, name: str) -> Dict[str, Any]:
        with self.lock:
            if self._budget(phase, name)['action'] == 'finish':
                question = f'{phase}/{name}: resource warning reached; complete scope coverage requires follow-up.'
                if question not in result['open_questions']:
                    result['open_questions'].append(question)
        return result

    def _publish(self) -> None:
        self.state["updated_at"] = timestamp()
        self.state["elapsed_seconds"] = round(self._elapsed(), 3)
        self.state['budgets'] = {}
        for phase in ('discovery', 'verification'):
            for name in self.reviewers:
                budget = self._budget(phase, name)
                self.state['budgets'][f'{phase}/{name}'] = budget
                atomic_json(self.run_dir / 'audit' / phase / name / 'live-budget.json', budget)
        self.result["status"] = self.state["status"]
        atomic_json(self.run_dir / "status.json", self.state)
        atomic_json(self.run_dir / "result.json", self.result)
        write_text(self.run_dir / "report.md", render_final(self.state, self.result))

    def _check_budget(self) -> None:
        with self.lock:
            if (self.run_dir / "audit" / "cancel.json").exists():
                raise LoopReviewError("CANCELED", "Review canceled")
            limits = self.config["limits"]
            if self._elapsed() >= limits["max_run_seconds"]:
                raise LoopReviewError("RUN_BUDGET_EXCEEDED", "Cumulative active execution time exhausted")
            tokens = sum(item.get("total_tokens") or 0 for item in self.state["progress"].values())
            if tokens >= limits["max_total_tokens"]:
                raise LoopReviewError("RUN_BUDGET_EXCEEDED", "Cumulative known token budget exhausted")
            if self.session_started and time.monotonic() - self.last_publish >= 1:
                self._publish()
                self.last_publish = time.monotonic()

    def _progress(self, key: str, stats: Dict[str, Any]) -> None:
        with self.lock:
            self.state["progress"][key].update(stats)
            phase, name, _ = key.split('/')
            budget = self._budget(phase, name)
            if budget['action'] == 'finish':
                self.state['progress'][key]['finish_requested'] = True
            atomic_json(self.run_dir / 'audit' / phase / name / 'live-budget.json', budget)
            if time.monotonic() - self.last_publish >= 0.5:
                self._publish()
                self.last_publish = time.monotonic()
            self._check_worker_budget(phase, name)

    def _check_worker_budget(self, phase: str, name: str) -> None:
        with self.lock:
            self._check_budget()
            budget = self._budget(phase, name)
            if (budget['known_tokens'] or 0) >= budget['token_limit']:
                raise LoopReviewError('TASK_BUDGET_EXCEEDED',
                                      f'{phase}/{name} exhausted its cumulative token allocation')

    def _checkpoint(
        self, slot: Path, digest: str, prepared: Dict[str, Any], ids: List[str], name: str,
    ) -> Optional[Dict[str, Any]]:
        checkpoint = slot / "checkpoint.json"
        if checkpoint.exists():
            saved = load_json(checkpoint)
            if saved["digest"] == digest:
                attempt_name = saved["attempt"]
                if not isinstance(attempt_name, str) or not attempt_name.startswith("attempt-") or Path(attempt_name).name != attempt_name:
                    raise LoopReviewError("RESUME_CHECKPOINT_INVALID", "Invalid checkpoint attempt path")
                result_path = slot / attempt_name / "result.json"
                if sha256_file(result_path) != saved["result_sha256"]:
                    raise LoopReviewError("RESUME_CHECKPOINT_INVALID", "Saved result checksum changed")
                return validate_result(load_json(result_path), prepared["policy_paths"], ids)
        for attempt in sorted(slot.glob("attempt-*"), reverse=True):
            call_path, stream_path = attempt / "call.json", attempt / "stream.json"
            if not call_path.exists() or not stream_path.exists() or load_json(call_path).get("digest") != digest:
                continue
            corrected = None
            if load_json(stream_path).get("exit_code") != 0:
                corrected = self._terminal_turn_recovery(attempt)
            if load_json(stream_path).get("exit_code") != 0 and corrected is None:
                continue
            try:
                raw = (attempt / "raw.stdout").read_text()
                result = validate_result(self.reviewers[name].parse_saved_output(raw), prepared["policy_paths"], ids)
            except (LoopReviewError, OSError):
                continue
            result = self._coverage_question(result, slot.parent.name, name)
            if corrected is not None:
                with self.lock:
                    key = str(attempt.relative_to(self.run_dir / 'audit'))
                    self.state['progress'][key].update(corrected)
                    self._check_worker_budget(slot.parent.name, name)
                atomic_json(attempt / 'recovery.json', {'reason': 'terminal_turn_accounting',
                            'raw_sha256': sha256_file(attempt / 'raw.stdout'), 'stats': corrected})
            atomic_json(attempt / "result.json", result)
            atomic_json(checkpoint, {"digest": digest, "attempt": attempt.name,
                                     "result_sha256": sha256_file(attempt / "result.json")})
            with self.lock:
                key = str(attempt.relative_to(self.run_dir / 'audit'))
                self.state['progress'][key]['status'] = 'RECOVERED' if corrected is not None else 'OK'
            return result
        return None

    def _worker(
        self, name: str, phase: str, prepared: Dict[str, Any], claims: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        prompt = self._prompt(prepared, name, phase, claims)
        cfg_name = "qwen" if name == "reviewer-a" else "deepseek"
        call_input = {"protocol": "2.0", "prompt": prompt, "fingerprint": prepared["fingerprint"],
                      "reviewer": self.config["reviewers"][cfg_name]}
        digest = sha256_bytes(json.dumps(call_input, sort_keys=True, ensure_ascii=False).encode())
        slot = self.run_dir / "audit" / phase / name
        ids = [claim["id"] for claim in claims]
        cached = self._checkpoint(slot, digest, prepared, ids, name)
        if cached is not None:
            return cached
        with self.lock:
            self._check_worker_budget(phase, name)
            if self.state["review_calls"] >= self.config["limits"]["max_task_attempts"]:
                raise LoopReviewError("RUN_BUDGET_EXCEEDED", "Cumulative reviewer attempt budget exhausted")
            self.state["review_calls"] += 1
            attempt = slot / f"attempt-{self.state['review_calls']:03d}"
            key = f"{phase}/{name}/{attempt.name}"
            self.state["progress"][key] = {"status": "RUNNING", "total_tokens": None, "reported_cost": None}
            self._publish()
        write_text(attempt / "prompt.md", prompt)
        atomic_json(attempt / "call.json", {"digest": digest})
        adapter = self.reviewers[name]
        adapter.limits = self.config["limits"]
        adapter.progress = lambda stats: self._progress(key, stats)
        adapter.cancel = lambda: self._check_worker_budget(phase, name)
        response = None
        try:
            response = adapter.review(prompt, prepared["repo"], self.run_dir, attempt, prepared["read_dirs"])
            result = validate_result(response["result"], prepared["policy_paths"], ids)
            result = self._coverage_question(result, phase, name)
            atomic_json(attempt / "result.json", result)
            atomic_json(attempt / "meta.json", response["meta"])
            atomic_json(slot / "checkpoint.json", {"digest": digest, "attempt": attempt.name,
                                                   "result_sha256": sha256_file(attempt / "result.json")})
            return result
        except Exception as error:
            if isinstance(error, LoopReviewError):
                if response is not None:
                    atomic_json(attempt / "result.invalid.json", response["result"])
                atomic_json(attempt / "error.json", {"code": error.code, "message": error.message})
            else:
                write_text(attempt / "exception.txt", traceback.format_exc())
            raise
        finally:
            with self.lock:
                stream = attempt / "stream.json"
                if stream.exists():
                    self.state["progress"][key].update(load_json(stream))
                self.state["progress"][key]["status"] = "OK" if (attempt / "result.json").exists() else "FAILED"
                self._publish()

    def _phase(
        self, phase: str, prepared: Dict[str, Any], claims: List[Dict[str, Any]],
        results: Dict[str, Dict[str, Any]],
    ) -> None:
        self.state["status"] = phase.upper()
        self._publish()
        self.store.indexes()
        errors = []
        base_questions = [] if phase == "discovery" else list(self.result["open_questions"])
        work = {name: ([] if phase == "discovery" else [claim for claim in claims if claim["origin"] != name])
                for name in self.reviewers}
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = {pool.submit(self._worker, name, phase, prepared, selected): name
                       for name, selected in work.items() if phase == "discovery" or selected}
            for future in concurrent.futures.as_completed(futures):
                name = futures[future]
                try:
                    output = future.result()
                    with self.lock:
                        results[name] = output
                        current = discovery_claims(results) + claims if phase == "discovery" else claims
                        self.result["findings"] = resolve_claims(current, results if phase == "verification" else {})
                        self.result["open_questions"] = base_questions + [q for item in results.values() for q in item["open_questions"]]
                        self._publish()
                except Exception as error:
                    errors.append(error)
        if errors:
            raise errors[0]

    def execute(self, run_dir: Path, *, resume: bool = False, wait_for_launcher: bool = False) -> Dict[str, Any]:
        self.run_dir = run_dir.resolve()
        with (self.run_dir / ".run.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | (0 if wait_for_launcher else fcntl.LOCK_NB))
            except BlockingIOError:
                raise LoopReviewError("RUN_ALREADY_ACTIVE", "Another controller owns this run")
            self.state = load_json(self.run_dir / "status.json")
            allowed = ("FAILED", "CANCELED", "ORPHANED", "RESUMING") if resume else ("PREPARED",)
            if self.state["status"] not in allowed:
                raise LoopReviewError("RESUME_NOT_SUPPORTED", "Run is not eligible for this operation")
            self.result = load_json(self.run_dir / "result.json")
            self.prior_elapsed = float(self.state["elapsed_seconds"])
            self.session_started = time.monotonic()
            discoveries: Dict[str, Dict[str, Any]] = {}
            verifications: Dict[str, Dict[str, Any]] = {}
            claims: List[Dict[str, Any]] = []
            try:
                prepared, manifest = self._load_prepared_for_resume(self.run_dir)
                if manifest.get("workflow") != "discovery-verification-v2":
                    raise LoopReviewError("RESUME_NOT_SUPPORTED", "Legacy workflow is inspection-only")
                if manifest["reviewers"] != self.config["reviewers"] or manifest["limits"] != self.config["limits"]:
                    raise LoopReviewError("RESUME_CONFIG_CHANGED", "Model, harness, reasoning or limits changed; start a new run")
                self._reconcile_usage()
                for label, path in (("request", prepared["request_path"]),
                                    ("invocation", self.run_dir / "input" / "invocation.json"),
                                    ("history", self.run_dir / "input" / "history.json")):
                    if sha256_file(path) != manifest[f"{label}_sha256"]:
                        raise LoopReviewError("RESUME_CHECKPOINT_INVALID", f"Persisted {label} changed")
                if manifest["prompt_sha256"] != self._prompt_fingerprint(prepared):
                    raise LoopReviewError("RESUME_CONFIG_CHANGED", "Prompt or rubric changed; start a new run")
                if resume and self.state["status"] != "RESUMING":
                    archive = self.run_dir / "audit" / "resumes" / secrets.token_hex(4)
                    atomic_json(archive / "status.json", self.state)
                    atomic_json(archive / "result.json", load_json(self.run_dir / "result.json"))
                    # Clear the old request before exposing RESUMING. Detached launchers
                    # already performed this transition; their new requests must survive.
                    (self.run_dir / "audit" / "cancel.json").unlink(missing_ok=True)
                    self.state["status"] = "RESUMING"
                    self._publish()
                self.state.pop("failure_code", None)
                self.state.pop("message", None)
                history = load_json(self.run_dir / "input" / "history.json")
                claims = history
                self._check_budget()
                if self.state["scope"] == "full":
                    self._phase("discovery", prepared, history, discoveries)
                claims = discovery_claims(discoveries) + history
                self.result["findings"] = resolve_claims(claims, {})
                self.result["open_questions"] = [q for result in discoveries.values() for q in result["open_questions"]]
                if claims:
                    self._phase("verification", prepared, claims, verifications)
                self.result["findings"] = resolve_claims(claims, verifications)
                self.result["open_questions"] = [q for result in [*discoveries.values(), *verifications.values()] for q in result["open_questions"]]
                self._verify_fingerprint(prepared)
                self._check_budget()
                self.state["status"] = final_status(self.result["findings"], self.result["open_questions"], self.state["scope"])
                self._publish()
                self.store.indexes()
                return dict(self.state)
            except Exception as error:
                if discoveries or claims or verifications:
                    claims = discovery_claims(discoveries) + (claims if not discoveries else [c for c in claims if c["origin"] == "history"])
                    self.result["findings"] = resolve_claims(claims, verifications)
                    self.result["open_questions"] = [q for result in [*discoveries.values(), *verifications.values()] for q in result["open_questions"]]
                raise self._failure(error)
            finally:
                self.session_started = 0.0

    def _failure(self, raw: Exception) -> LoopReviewError:
        error = raw if isinstance(raw, LoopReviewError) else LoopReviewError("UNEXPECTED_ERROR", str(raw))
        self.state["status"] = "CANCELED" if error.code == "CANCELED" else "FAILED"
        self.state["failure_code"] = error.code
        self.state["message"] = error.message
        self._publish()
        self.store.indexes()
        error.details = {"run_dir": str(self.run_dir), "run_id": self.state["run_id"],
                         "status_path": str(self.run_dir / "status.json"), "report_path": str(self.run_dir / "report.md")}
        return error

    def run(self, invocation_path: Path, dry_run: bool = False) -> Dict[str, Any]:
        prepared = self.prepare(invocation_path, dry_run)
        return prepared if dry_run else self.execute(Path(prepared["run_dir"]))

    def resume(self, run_dir: Path) -> Dict[str, Any]:
        return self.execute(run_dir, resume=True)
