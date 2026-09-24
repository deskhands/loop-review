from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional


class ReviewerAdapter:
    def __init__(self, name: str, config: Dict[str, Any]):
        self.name = name
        self.config = config

    def version(self) -> str:
        raise NotImplementedError

    def healthcheck(self, cwd: Path) -> Dict[str, Any]:
        raise NotImplementedError

    def review(
        self,
        prompt: str,
        cwd: Path,
        run_dir: Path,
        out_dir: Path,
        read_dirs: Optional[List[Path]] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError
