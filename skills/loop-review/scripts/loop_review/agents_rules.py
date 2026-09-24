from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List

from .util import sha256_file, unique_preserve


def rules_for_path(repo: Path, target: Path) -> List[Path]:
    repo = repo.resolve()
    target = target.resolve()
    try:
        rel = target.relative_to(repo)
    except ValueError:
        return [repo / "AGENTS.md"] if (repo / "AGENTS.md").is_file() else []
    current = repo
    out: List[Path] = []
    root_rule = repo / "AGENTS.md"
    if root_rule.is_file():
        out.append(root_rule)
    parts = rel.parts[:-1] if rel.parts else ()
    for part in parts:
        current = current / part
        candidate = current / "AGENTS.md"
        if candidate.is_file():
            out.append(candidate)
    return out


def discover_rules(repo: Path, target_paths: Iterable[Path]) -> Dict[str, object]:
    mapping: Dict[str, List[str]] = {}
    all_rules: List[str] = []
    paths = list(target_paths)
    if not paths:
        root_rule = repo / "AGENTS.md"
        if root_rule.is_file():
            all_rules.append(str(root_rule.resolve()))
    for target in paths:
        rules = rules_for_path(repo, target)
        mapping[str(target.resolve())] = [str(p.resolve()) for p in rules]
        all_rules.extend(mapping[str(target.resolve())])
    all_rules = unique_preserve(all_rules)
    fingerprints = [{"path": p, "sha256": sha256_file(Path(p))} for p in all_rules]
    return {"map": mapping, "files": fingerprints}
