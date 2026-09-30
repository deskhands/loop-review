from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


class ReviewerAdapter:
    def __init__(self, name: str, config: Dict[str, Any]):
        self.name = name
        self.config = config
        self.limits: Dict[str, int] = {}
        self.progress: Optional[Callable[[Dict[str, Any]], None]] = None
        self.cancel: Optional[Callable[[], None]] = None

    def version(self) -> str:
        raise NotImplementedError

    def healthcheck(self, cwd: Path) -> Dict[str, Any]:
        raise NotImplementedError

    def parse_saved_output(self, stdout: str) -> Dict[str, Any]:
        """Parse a completed saved reviewer stdout without making a model call."""
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
