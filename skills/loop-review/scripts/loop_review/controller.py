from __future__ import annotations

import concurrent.futures
import copy
import json
import os
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .adapters import ClaudeAdapter, OpenCodeAdapter
from .agents_rules import discover_rules
from .git_state import (
    ensure_repo,
    git_range_fingerprint,
    repo_meta,
    working_tree_fingerprint,
    write_range_diff,
    write_working_tree_diff,
)
from .ledger import (
    accepted_blocking_ids,
    active_ids,
    add_discovery_result,
    apply_crosscheck_result,
    final_status,
    new_ledger,
    status_counts,
)
from .prompts import build_prompt
from .renderer import render_final, render_round
from .schemas import validate_result
from .util import (
    LoopReviewError,
    atomic_json,
    expand_path,
    load_json,
    sha256_file,
    write_text,
)


class LoopReviewController:
    def __init__(self, config: Dict[str, Any], skill_root: Path):
        self.config = config
        self.skill_root = skill_root.resolve()
        self.reviewers = {
            "grok": OpenCodeAdapter("grok", config["reviewers"]["grok"]),
            "deepseek": ClaudeAdapter("deepseek", config["reviewers"]["deepseek"]),
        }
        self.aliases = {"grok": "Reviewer-A", "deepseek": "Reviewer-B"}

    def doctor(self, cwd: Optional[Path] = None) -> Dict[str, Any]:
        cwd = (cwd or Path.home()).resolve()
        results: Dict[str, Any] = {}
        errors: Dict[str, Any] = {}

        def run_one(name: str) -> Tuple[str, Dict[str, Any]]:
            return name, self.reviewers[name].healthcheck(cwd)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = {pool.submit(run_one, name): name for name in self.reviewers}
            for future, name in [(f, futures[f]) for f in futures]:
                try:
                    key, result = future.result()
                    results[key] = result
                except LoopReviewError as e:
                    errors[name] = {"code": e.code, "message": e.message, "details": e.details}
                except Exception as e:
                    errors[name] = {"code": "UNEXPECTED_ERROR", "message": str(e)}

        if errors:
            raise LoopReviewError("FAILED_PREFLIGHT", "One or more reviewers failed preflight", errors)
        return {"ok": True, "reviewers": results}

    def _new_run_dir(self, repo: Path, mode: str) -> Tuple[str, Path]:
        run_id = secrets.token_hex(3)
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", repo.name)[:48] or "repo"
        root = Path(self.config["paths"]["run_root"]).resolve()
        try:
            root.relative_to(repo.resolve())
        except ValueError:
            pass
        else:
            raise LoopReviewError("CONFIG_ERROR", "paths.run_root must be outside the reviewed repository")
        root.mkdir(parents=True, exist_ok=True)
        run_dir = root / f"{stamp}__{slug}__{mode}__{run_id}"
        run_dir.mkdir(parents=True, exist_ok=False)
        return run_id, run_dir

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

        target = invocation.get("target")
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
        elif target_kind == "working-tree":
            if mode == "design":
                raise LoopReviewError("INVALID_INVOCATION", "design mode does not support working-tree target")
            diff_path = input_dir / "target.diff"
            changed = write_working_tree_diff(repo, diff_path)
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
        for candidate in (target_path, seed_review_path):
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
        agents_files = prepared["rules"]["files"]
        base_fp = working_tree_fingerprint(repo, agents_files)
        kind = prepared["target_kind"]
        target_path = prepared.get("target_path")
        if kind in ("text", "file") and target_path:
            base_fp["target"] = {
                "path": str(target_path),
                "sha256": sha256_file(target_path),
                "size": target_path.stat().st_size,
            }
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

    def _preflight(self, repo: Path, run_dir: Path) -> Dict[str, Any]:
        preflight_dir = run_dir / "preflight"
        preflight_dir.mkdir(parents=True, exist_ok=True)
        result = self.doctor(repo)
        for name, info in result["reviewers"].items():
            atomic_json(preflight_dir / f"{name}.json", info)
        write_text(preflight_dir / "preflight.log", "Both reviewer health checks passed.\n")
        return result

    def _prior_review_paths(self, run_dir: Path) -> List[str]:
        return sorted(str(p.resolve()) for p in (run_dir / "rounds").glob("**/review.md"))

    def _run_worker(
        self,
        *,
        name: str,
        out_dir: Path,
        prepared: Dict[str, Any],
        run_dir: Path,
        ledger: Optional[Dict[str, Any]],
        active: List[str],
        phase: str,
    ) -> Dict[str, Any]:
        prompt = build_prompt(
            skill_root=self.skill_root,
            mode=prepared["mode"],
            request_text=prepared["request_text"],
            target_description=prepared["target_description"],
            policy_paths=prepared["policy_paths"],
            ledger=ledger,
            active_ids=active,
            prior_reviews=self._prior_review_paths(run_dir),
            phase=phase,
            reviewer_alias=self.aliases[name],
            seed_review_path=str(prepared["seed_review_path"]) if prepared.get("seed_review_path") else None,
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        write_text(out_dir / "prompt.md", prompt)
        response = self.reviewers[name].review(
            prompt,
            prepared["repo"],
            run_dir,
            out_dir,
            prepared.get("read_dirs", []),
        )
        result = validate_result(response["result"], prepared["policy_paths"], active if phase != "discovery" else None)
        if phase == "discovery" and result["adjudications"]:
            raise LoopReviewError("SCHEMA_VALIDATION_FAILED", "Discovery result must not adjudicate findings")
        atomic_json(out_dir / "result.json", result)
        return {"result": result, "meta": response["meta"], "prompt": prompt}

    def run(self, invocation_path: Path, dry_run: bool = False) -> Dict[str, Any]:
        invocation = load_json(invocation_path)
        if not isinstance(invocation, dict):
            raise LoopReviewError("INVALID_INVOCATION", "Invocation must be a JSON object")
        repo_raw = invocation.get("repo")
        if not isinstance(repo_raw, str):
            raise LoopReviewError("INVALID_INVOCATION", "repo is required")
        repo = ensure_repo(expand_path(repo_raw))
        mode = invocation.get("mode")
        if mode not in ("design", "code", "review"):
            raise LoopReviewError("INVALID_INVOCATION", "mode must be design, code, or review")
        run_id, run_dir = self._new_run_dir(repo, mode)

        state = {
            "run_id": run_id,
            "status": "INIT",
            "review_calls": 0,
            "cycles_completed": 0,
            "run_dir": str(run_dir),
        }
        atomic_json(run_dir / "state" / "state.json", state)
        try:
            prepared = self._prepare(invocation, run_dir, repo)
            manifest = {
                "schema_version": "1.0",
                "run_id": run_id,
                "started_at": datetime.now().astimezone().isoformat(),
                "mode": mode,
                "repo": repo_meta(repo),
                "loop": copy.deepcopy(self.config["loop"]),
                "reviewers": {
                    "reviewer-a": {
                        "adapter": self.config["reviewers"]["grok"]["adapter"],
                        "model": self.config["reviewers"]["grok"]["model"],
                        "reasoning": self.config["reviewers"]["grok"]["reasoning"],
                    },
                    "reviewer-b": {
                        "adapter": self.config["reviewers"]["deepseek"]["adapter"],
                        "model": self.config["reviewers"]["deepseek"]["model"],
                        "reasoning": self.config["reviewers"]["deepseek"]["reasoning"],
                    },
                },
            }
            atomic_json(run_dir / "manifest.json", manifest)

            state["status"] = "PREPARED"
            atomic_json(run_dir / "state" / "state.json", state)

            if dry_run:
                ledger = new_ledger()
                for name in ("grok", "deepseek"):
                    out_dir = run_dir / "rounds" / "00-discovery" / name
                    prompt = build_prompt(
                        skill_root=self.skill_root,
                        mode=prepared["mode"],
                        request_text=prepared["request_text"],
                        target_description=prepared["target_description"],
                        policy_paths=prepared["policy_paths"],
                        ledger=None,
                        active_ids=[],
                        prior_reviews=[],
                        phase="discovery",
                        reviewer_alias=self.aliases[name],
                        seed_review_path=str(prepared["seed_review_path"]) if prepared.get("seed_review_path") else None,
                    )
                    write_text(out_dir / "prompt.md", prompt)
                atomic_json(run_dir / "state" / "issue-ledger.json", ledger)
                status_obj = {
                    "status": "PREPARED_DRY_RUN",
                    "run_id": run_id,
                    "run_dir": str(run_dir),
                    "review_path": None,
                    "status_path": str((run_dir / "final" / "status.json").resolve()),
                }
                atomic_json(run_dir / "final" / "status.json", status_obj)
                return status_obj

            state["status"] = "PREFLIGHT"
            atomic_json(run_dir / "state" / "state.json", state)
            self._preflight(repo, run_dir)
            self._verify_fingerprint(prepared)

            state["status"] = "DISCOVERY_PARALLEL"
            atomic_json(run_dir / "state" / "state.json", state)
            discovery: Dict[str, Dict[str, Any]] = {}

            def discovery_call(name: str) -> Tuple[str, Dict[str, Any]]:
                out_dir = run_dir / "rounds" / "00-discovery" / name
                return name, self._run_worker(
                    name=name,
                    out_dir=out_dir,
                    prepared=prepared,
                    run_dir=run_dir,
                    ledger=None,
                    active=[],
                    phase="discovery",
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(discovery_call, name) for name in ("grok", "deepseek")]
                for future in futures:
                    name, response = future.result()
                    discovery[name] = response
                    state["review_calls"] += 1
            self._verify_fingerprint(prepared)

            ledger = new_ledger()
            summaries: List[str] = []
            for name in ("grok", "deepseek"):
                stable_map = add_discovery_result(ledger, discovery[name]["result"], self.aliases[name])
                summaries.append(discovery[name]["result"]["summary"])
                out_dir = run_dir / "rounds" / "00-discovery" / name
                write_text(out_dir / "review.md", render_round(discovery[name]["result"], stable_map))
                meta = discovery[name]["meta"]
                meta["reviewer_alias"] = self.aliases[name]
                atomic_json(out_dir / "meta.json", meta)
            atomic_json(run_dir / "state" / "issue-ledger.json", ledger)

            state["status"] = "CROSS_CHECK"
            atomic_json(run_dir / "state" / "state.json", state)
            converged = False
            last_cycle_had_new = False
            max_cycles = int(self.config["loop"]["max_cycles"])
            max_calls = int(self.config["loop"]["max_model_calls"])

            for cycle in range(1, max_cycles + 1):
                cycle_added: List[str] = []
                order = ("grok", "deepseek") if cycle == 1 else ("deepseek", "grok")
                for name in order:
                    if state["review_calls"] >= max_calls:
                        raise LoopReviewError("MODEL_CALL_BUDGET_EXCEEDED", "Review model call budget exceeded")
                    current_active = active_ids(ledger)
                    out_dir = run_dir / "rounds" / f"{cycle:02d}-cross-check" / name
                    response = self._run_worker(
                        name=name,
                        out_dir=out_dir,
                        prepared=prepared,
                        run_dir=run_dir,
                        ledger=ledger,
                        active=current_active,
                        phase="cross-check",
                    )
                    state["review_calls"] += 1
                    stable_map, added = apply_crosscheck_result(
                        ledger, response["result"], self.aliases[name], cycle
                    )
                    cycle_added.extend(added)
                    summaries.append(response["result"]["summary"])
                    write_text(out_dir / "review.md", render_round(response["result"], stable_map))
                    meta = response["meta"]
                    meta["reviewer_alias"] = self.aliases[name]
                    meta["active_finding_ids"] = current_active
                    atomic_json(out_dir / "meta.json", meta)
                    atomic_json(run_dir / "state" / "issue-ledger.json", ledger)
                    self._verify_fingerprint(prepared)

                state["cycles_completed"] = cycle
                last_cycle_had_new = bool(cycle_added)
                current_active = active_ids(ledger)
                converged = (not current_active) and (not last_cycle_had_new)
                atomic_json(run_dir / "state" / "state.json", state)
                if converged:
                    break

            status = final_status(ledger, converged, last_cycle_had_new)
            final_json = {
                "schema_version": "1.0",
                "status": status,
                "run_id": run_id,
                "counts": status_counts(ledger),
                "accepted_blocking_ids": accepted_blocking_ids(ledger),
                "cycles": state["cycles_completed"],
                "review_calls": state["review_calls"],
                "ledger_path": str((run_dir / "state" / "issue-ledger.json").resolve()),
            }
            atomic_json(run_dir / "final" / "review.json", final_json)
            write_text(run_dir / "final" / "review.md", render_final(status, ledger, summaries))
            status_obj = {
                "status": status,
                "run_id": run_id,
                "run_dir": str(run_dir),
                "review_path": str((run_dir / "final" / "review.md").resolve()),
                "review_json_path": str((run_dir / "final" / "review.json").resolve()),
                "status_path": str((run_dir / "final" / "status.json").resolve()),
                "blocking_count": len(accepted_blocking_ids(ledger)),
                "cycles": state["cycles_completed"],
                "review_calls": state["review_calls"],
            }
            atomic_json(run_dir / "final" / "status.json", status_obj)
            state["status"] = status
            atomic_json(run_dir / "state" / "state.json", state)
            return status_obj

        except Exception as raw_error:
            if isinstance(raw_error, LoopReviewError):
                error = raw_error
            else:
                error = LoopReviewError(
                    "UNEXPECTED_ERROR",
                    str(raw_error),
                    {"exception_type": type(raw_error).__name__},
                )
            failure = {
                "status": "FAILED",
                "failure_code": error.code,
                "message": error.message,
                "details": error.details,
                "run_id": run_id,
                "run_dir": str(run_dir),
                "review_path": str((run_dir / "final" / "review.md").resolve()),
                "status_path": str((run_dir / "final" / "status.json").resolve()),
            }
            write_text(
                run_dir / "final" / "review.md",
                f"# Loop Review Failed\n\n**{error.code}**\n\n{error.message}\n",
            )
            atomic_json(run_dir / "final" / "status.json", failure)
            state["status"] = "FAILED"
            state["failure_code"] = error.code
            atomic_json(run_dir / "state" / "state.json", state)
            error.details = dict(error.details)
            error.details.update({
                "run_dir": str(run_dir),
                "status_path": str((run_dir / "final" / "status.json").resolve()),
            })
            raise error
