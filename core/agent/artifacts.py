"""Run artifact helpers for agent tasks."""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path

from core.agent.runtime import AgentPermissions, AgentTaskLike, LogCallback
from core.agent.toolbox import AgentToolbox


class AgentRunArtifactsMixin:
    """Model agent run artifacts mixin."""
    def _write_diff_artifacts(
        self,
        run_dir: Path,
        tools: AgentToolbox,
        permissions: AgentPermissions,
        log: LogCallback,
        verbose: Callable[[str, object], None] | None = None,
    ) -> None:
        """Write diff artifacts."""
        if not permissions.allow_git:
            return
        status = tools.git_status()
        diff = tools.git_diff()
        self._write_json(run_dir / "git_status.json", asdict(status))
        self._write_json(run_dir / "git_diff.json", asdict(diff))
        if verbose:
            verbose("git status", asdict(status))
            verbose("git diff", asdict(diff))
        if diff.ok and isinstance(diff.data, dict):
            patch = str(diff.data.get("stdout", ""))
            (run_dir / "diff.patch").write_text(patch, encoding="utf-8")
            log("git diff artifact written")

    def _make_run_dir(self, title: str) -> Path:
        """Create run dir."""
        safe_title = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in title.lower())
        safe_title = "-".join(part for part in safe_title.split("-") if part)[:48] or "task"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir = self.log_root / f"{stamp}-{safe_title}"
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    @staticmethod
    def _write_json(path: Path, data) -> None:
        """Write json."""
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _truncate(text: str, max_chars: int) -> str:
        """Handle truncate for agent run artifacts mixin."""
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + f"\n... [truncated {len(text) - max_chars} chars]"

    @staticmethod
    def _spec_dict(spec: AgentTaskLike) -> dict:
        """Handle spec dict for agent run artifacts mixin."""
        if is_dataclass(spec):
            return asdict(spec)
        return {name: getattr(spec, name, None) for name in AgentTaskLike.__annotations__}
