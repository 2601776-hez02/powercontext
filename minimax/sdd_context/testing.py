"""Prepare a disposable worktree and run pytest against it.

The delivered ``sample-project`` is never modified. Every run copies it to
``.runtime/workdir``, commits the baseline in a throwaway Git repository and
then lets the agent patch that copy, so ``git diff`` is the authoritative code
change and ``git checkout`` is a complete reset.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

IGNORE = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc", ".mypy_cache", ".ruff_cache", ".git")
GIT_ENV = {
    "GIT_AUTHOR_NAME": "sdd-context",
    "GIT_AUTHOR_EMAIL": "sdd-context@example.invalid",
    "GIT_COMMITTER_NAME": "sdd-context",
    "GIT_COMMITTER_EMAIL": "sdd-context@example.invalid",
}

_SUMMARY = re.compile(r"(?:(?P<passed>\d+) passed)?(?:(?:, )?(?P<failed>\d+) failed)?(?:(?:, )?(?P<errors>\d+) error)?")


@dataclass
class TestRun:
    command: list[str]
    exit_code: int
    output: str
    passed: int = 0
    failed: int = 0
    errors: int = 0
    duration_s: float = 0.0
    started_at: str = ""
    workdir: str = ""
    targets: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        parts = []
        if self.passed:
            parts.append(f"{self.passed} passed")
        if self.failed:
            parts.append(f"{self.failed} failed")
        if self.errors:
            parts.append(f"{self.errors} error")
        return ", ".join(parts) or f"exit {self.exit_code}"

    def as_dict(self) -> dict[str, object]:
        return {
            "command": " ".join(self.command),
            "targets": self.targets,
            "workdir": self.workdir,
            "exit_code": self.exit_code,
            "passed": self.passed,
            "failed": self.failed,
            "errors": self.errors,
            "duration_s": round(self.duration_s, 3),
            "started_at": self.started_at,
            "summary": self.summary,
        }


def prepare_workdir(sample: Path, workdir: Path, *, reset: bool = True) -> str:
    """Copy ``sample`` into ``workdir`` and commit the baseline; return the commit."""
    if reset and workdir.exists():
        shutil.rmtree(workdir)
    workdir.parent.mkdir(parents=True, exist_ok=True)
    if not workdir.exists():
        shutil.copytree(sample, workdir, ignore=IGNORE)
    _git(workdir, ["init", "-q"], check=True)
    _git(workdir, ["add", "-A"], check=True)
    _git(workdir, ["-c", "user.name=sdd-context", "-c", "user.email=sdd-context@example.invalid",
                   "commit", "-q", "-m", "baseline: delivered sample snapshot"], check=True)
    return _git(workdir, ["rev-parse", "HEAD"], check=True).strip()


def _git(workdir: Path, args: Sequence[str], *, check: bool = False) -> str:
    process = subprocess.run(
        ["git", *args],
        cwd=workdir,
        capture_output=True,
        text=True,
        env={**_base_env(), **GIT_ENV},
    )
    if check and process.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {process.stderr.strip()}")
    return process.stdout


def _base_env() -> dict[str, str]:
    import os

    return {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def run_pytest(
    workdir: Path,
    targets: Sequence[str],
    *,
    extra_args: Sequence[str] = (),
    timeout: float = 300.0,
) -> TestRun:
    """Run pytest on ``targets`` inside ``workdir`` and capture the result."""
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *extra_args, *targets]
    if not targets:
        command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *extra_args]
    env = {
        **_base_env(),
        "PYTHONPATH": str((workdir / "src").resolve()),
        "PYTEST_ADDOPTS": "",
        "COLUMNS": "120",
    }
    started = time.time()
    started_iso = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    try:
        process = subprocess.run(
            command, cwd=workdir, capture_output=True, text=True, timeout=timeout, env=env
        )
        output = (process.stdout or "") + (process.stderr or "")
        exit_code = process.returncode
    except subprocess.TimeoutExpired as exc:  # pragma: no cover - defensive
        output = f"pytest timed out after {timeout}s\n{exc.stdout or ''}\n{exc.stderr or ''}"
        exit_code = 124
    duration = time.time() - started
    if "No module named pytest" in output or "No module named 'pytest'" in output:
        raise RuntimeError(
            "pytest is not installed for this interpreter; run scripts/bootstrap.sh "
            "(an environment failure must never be reported as a failing target test)"
        )
    passed, failed, errors = _parse_counts(output)
    return TestRun(
        command=command,
        exit_code=exit_code,
        output=output,
        passed=passed,
        failed=failed,
        errors=errors,
        duration_s=duration,
        started_at=started_iso,
        workdir=str(workdir),
        targets=list(targets),
    )


def _parse_counts(output: str) -> tuple[int, int, int]:
    """Parse pytest's terminal summary, tolerating ``-q``/``-qq`` output."""
    passed = failed = errors = 0
    for line in reversed(output.splitlines()):
        if not re.search(r"\b\d+ (passed|failed|error|errors|skipped|deselected)\b", line):
            continue
        for match in re.finditer(r"(\d+) (passed|failed|error|errors)", line):
            count, kind = int(match.group(1)), match.group(2)
            if kind == "passed":
                passed = count
            elif kind == "failed":
                failed = count
            else:
                errors = count
        if passed or failed or errors:
            break
    if not (passed or failed or errors):
        # ``pytest -qq`` prints only the progress line: count its characters.
        for line in reversed(output.splitlines()):
            stripped = line.strip()
            if stripped.endswith("[100%]"):
                dots = stripped.split("[")[0]
                if dots and set(dots) <= {".", "s", "x", "F", "E"}:
                    passed = dots.count(".")
                    failed = dots.count("F")
                    errors = dots.count("E")
                    break
    return passed, failed, errors


def select_failed_tests(output: str) -> list[str]:
    """Extract ``file::test`` ids from a pytest failure report for the repair prompt."""
    names: list[str] = []
    for line in output.splitlines():
        if line.startswith("FAILED ") or line.startswith("ERROR "):
            name = line.split(None, 1)[1].split(" ")[0]
            if name not in names:
                names.append(name)
    return names


def write_test_log(path: Path, run: TestRun) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"$ {' '.join(run.command)}\n"
        f"# cwd: {run.workdir}\n"
        f"# started: {run.started_at}  duration: {run.duration_s:.2f}s\n"
        f"# exit code: {run.exit_code}  summary: {run.summary}\n\n"
    )
    path.write_text(header + run.output, encoding="utf-8")


def json_dumps(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)
