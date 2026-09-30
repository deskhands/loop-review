from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Dict

from .execution import DEFAULT_LIMITS
from .util import LoopReviewError, expand_path

DEFAULT_CONFIG = Path("~/.config/loop-review/config.toml").expanduser()


def load_config(path: Path = DEFAULT_CONFIG) -> Dict[str, Any]:
    try:
        with path.open("rb") as stream:
            cfg = tomllib.load(stream)
        cfg["paths"]["run_root"] = str(expand_path(cfg["paths"]["run_root"]))
        cfg["limits"] = {**DEFAULT_LIMITS, **cfg.get("limits", {})}
        # Existing installations keep their model choices. Old loop counts no
        # longer drive orchestration; retries use the cumulative attempt limit.
        for name, adapter in {"qwen": "pi", "deepseek": "claude"}.items():
            reviewer = cfg["reviewers"][name]
            for key in ("executable", "model", "reasoning", "timeout_seconds"):
                if key not in reviewer:
                    raise KeyError(f"reviewers.{name}.{key}")
            if reviewer.get("adapter") != adapter:
                raise LoopReviewError("CONFIG_ERROR", f"reviewers.{name}.adapter must be {adapter}")
            executable = os.path.expandvars(os.path.expanduser(reviewer["executable"]))
            reviewer["executable"] = str(Path(executable).resolve()) if "/" in executable else executable
            if type(reviewer["timeout_seconds"]) is not int or reviewer["timeout_seconds"] < 1:
                raise LoopReviewError("CONFIG_ERROR", "Reviewer timeout must be a positive integer")
        for key, value in cfg["limits"].items():
            if key not in DEFAULT_LIMITS or type(value) is not int or value < 1:
                raise LoopReviewError("CONFIG_ERROR", f"Invalid limit: {key}")
        return cfg
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as error:
        raise LoopReviewError("CONFIG_ERROR", f"Invalid configuration: {error}")
