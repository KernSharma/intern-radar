import subprocess
from pathlib import Path
from typing import Any

from intern_radar.gitsync import GitSync


class FakeGit:
    def __init__(self, staged: bool, push_ok: bool) -> None:
        self.staged, self.push_ok, self.cmds = staged, push_ok, []

    def __call__(self, cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        self.cmds.append(cmd[3:])
        args, rc, out = cmd[3:], 0, ""
        if args[:2] == ["rev-parse", "--abbrev-ref"]:
            out = "main\n"
        elif args[:2] == ["diff", "--cached"]:
            rc = 1 if self.staged else 0
        elif args[0] == "push":
            rc = 0 if self.push_ok else 1
        return subprocess.CompletedProcess(cmd, rc, out, "")


def test_nothing_staged_counts_as_pushed() -> None:
    fake = FakeGit(staged=False, push_ok=False)
    assert GitSync(Path("/r"), fake).commit_push(["data"], "m")
    assert not any(c[0] == "push" for c in fake.cmds)


def test_rejected_push_returns_false_and_reset_targets_origin() -> None:
    fake = FakeGit(staged=True, push_ok=False)
    g = GitSync(Path("/r"), fake)
    assert not g.commit_push(["data"], "m")
    g.reset_to_origin()
    assert ["reset", "-q", "--hard", "origin/main"] in fake.cmds


def test_git_failure_or_timeout_returns_false() -> None:
    def timing_out(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        if cmd[3] == "add":
            raise subprocess.TimeoutExpired(cmd, 120)
        return subprocess.CompletedProcess(cmd, 0, "main\n", "")

    assert not GitSync(Path("/r"), timing_out).commit_push(["data"], "m")
