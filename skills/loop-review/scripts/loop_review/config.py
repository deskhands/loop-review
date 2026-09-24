from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Any, Dict

from .util import LoopReviewError, expand_path


DEFAULT_CONFIG = Path("~/.config/loop-review/config.toml").expanduser()


def _parse_value(raw: str) -> Any:
    raw = raw.strip()
    if raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    if raw.startswith(('"', "'")):
        return ast.literal_eval(raw)
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    raise LoopReviewError("CONFIG_ERROR", f"Unsupported TOML value: {raw}")


def load_simple_toml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise LoopReviewError("CONFIG_ERROR", f"Config not found: {path}")
    root: Dict[str, Any] = {}
    section = root
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            names = [x.strip() for x in stripped[1:-1].split(".") if x.strip()]
            section = root
            for name in names:
                section = section.setdefault(name, {})
            continue
        if "=" not in stripped:
            raise LoopReviewError("CONFIG_ERROR", f"Invalid config line {lineno}: {line}")
        key, raw = stripped.split("=", 1)
        section[key.strip()] = _parse_value(raw)
    return root


def _normalize_executable(value: str) -> str:
    expanded = os.path.expandvars(os.path.expanduser(value))
    if os.sep in expanded or (os.altsep and os.altsep in expanded) or expanded.startswith("."):
        return str(Path(expanded).resolve())
    return expanded


def load_config(path: Path = DEFAULT_CONFIG) -> Dict[str, Any]:
    cfg = load_simple_toml(path)
    try:
        run_root = expand_path(cfg["paths"]["run_root"])
        loop = cfg["loop"]
        reviewers = cfg["reviewers"]
        expected_adapters = {"grok": "opencode", "deepseek": "claude"}
        for name in ("grok", "deepseek"):
            r = reviewers[name]
            r["executable"] = _normalize_executable(r["executable"])
            for required in ("adapter", "model", "reasoning", "timeout_seconds"):
                if required not in r:
                    raise KeyError(f"reviewers.{name}.{required}")
            if r["adapter"] != expected_adapters[name]:
                raise LoopReviewError(
                    "CONFIG_ERROR",
                    f"V1 requires reviewers.{name}.adapter = {expected_adapters[name]!r}",
                )
        cfg["paths"]["run_root"] = str(run_root)
        if int(loop["max_cycles"]) != 2:
            raise LoopReviewError("CONFIG_ERROR", "V1 requires loop.max_cycles = 2")
        if int(loop["max_model_calls"]) != 6:
            raise LoopReviewError("CONFIG_ERROR", "V1 requires loop.max_model_calls = 6")
    except KeyError as e:
        raise LoopReviewError("CONFIG_ERROR", f"Missing config key: {e}")
    return cfg
