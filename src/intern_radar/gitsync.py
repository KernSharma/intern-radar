"""The only place the watcher runs git.

The run itself owns sync and push (both runners): reset to origin, apply the
change set, commit data/, push; on rejection reset and let the caller
re-apply. A run never merges; replay replaces merging.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class GitError(Exception):
    pass


class GitSync:
    def __init__(self, repo: Path, runner: Runner = subprocess.run) -> None:
        self.repo = repo
        self._run = runner

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = self._run(["git", "-C", str(self.repo), *args],
                           capture_output=True, text=True, timeout=120, check=False)
        if check and result.returncode != 0:
            raise GitError(f"git {' '.join(args)}: {result.stderr.strip()}")
        return result

    def branch(self) -> str:
        return self._git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

    def reset_to_origin(self) -> None:
        branch = self.branch()
        self._git("fetch", "-q", "origin", branch)
        self._git("reset", "-q", "--hard", f"origin/{branch}")

    def commit_push(self, paths: Sequence[str], message: str) -> bool:
        """True when origin holds this run's state (pushed, or nothing to push)."""
        self._git("add", "--", *paths)
        if self._git("diff", "--cached", "--quiet", check=False).returncode == 0:
            return True
        self._git("commit", "-q", "-m", message)
        return self._git("push", "-q", "origin", "HEAD", check=False).returncode == 0
