"""Drive MiniMax over the constructed context and verify the resulting patch.

The flow the task asks for is implemented literally:

1. run the target tests on the untouched snapshot (they must fail);
2. send the context pack to MiniMax and ask for a unified diff only;
3. apply the diff to the disposable worktree, run the target tests;
4. if they still fail, send the failure back for one bounded repair attempt;
5. once the target tests pass, run the regression suite;
6. persist the request/response metadata and the final diff as records.

Only prompts, model ids, usage counters and diffs are stored. Any reasoning
text a provider returns is counted and dropped, never written to disk.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import requests

from .context_pack import ContextPack
from .testing import TestRun, prepare_workdir, run_pytest, select_failed_tests, write_test_log

SYSTEM_PROMPT = """You are a senior software engineer working on a repository that follows \
Spec-Driven Development. You receive the specification items that apply to one task, the code \
symbols that need to change, the tests that will judge you, and the persisted task state.

Rules:
- The specification is authoritative. Where it disagrees with the current code, the code is wrong.
- Change only the code shown under "Code to change" unless a specification forces a new file.
- Never edit, weaken or delete tests, and never rename a public symbol that the tests import.
- Keep every behavior the specification lists as preserved.
- Answer with a single unified diff in a ```diff fenced block and nothing else.
- Use repository-relative `a/<path>` and `b/<path>` headers so `git apply -p1` accepts the diff.
"""


class MiniMaxError(RuntimeError):
    """The MiniMax API rejected the request or returned an unusable answer."""


@dataclass
class ChatResult:
    content: str
    model: str
    endpoint: str
    usage: dict[str, Any]
    latency_s: float
    finish_reason: str | None = None
    request_id: str | None = None
    reasoning_chars: int = 0


class MiniMaxClient:
    """Minimal client for MiniMax's chat APIs.

    ``style`` selects the wire format: ``chatcompletion_v2`` (MiniMax native),
    ``openai`` (OpenAI compatible ``/chat/completions``) or ``auto`` which tries
    the native endpoint first and falls back on 404.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.minimaxi.com/v1",
        model: str = "MiniMax-M3",
        style: str = "auto",
        group_id: str | None = None,
        timeout: float = 240.0,
    ) -> None:
        if not api_key or not api_key.strip():
            raise MiniMaxError(
                "MINIMAX_API_KEY is not set. Export a MiniMax API key before running the agent step; "
                "scripts/replay.sh reproduces the recorded patch without a key."
            )
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.style = style
        self.group_id = (group_id or "").strip() or None
        self.timeout = timeout

    def _endpoints(self) -> list[tuple[str, str]]:
        native = ("/text/chatcompletion_v2", "chatcompletion_v2")
        compatible = ("/chat/completions", "openai")
        if self.style == "openai":
            chosen = [compatible]
        elif self.style == "chatcompletion_v2":
            chosen = [native]
        else:
            chosen = [native, compatible]
        suffix = f"?GroupId={self.group_id}" if self.group_id else ""
        return [(f"{self.base_url}{path}{suffix}", style) for path, style in chosen]

    def chat(
        self,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 8000,
    ) -> ChatResult:
        payload = {
            "model": self.model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        errors: list[str] = []
        for url, style in self._endpoints():
            started = time.time()
            try:
                response = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                errors.append(f"{style}: {exc}")
                continue
            latency = time.time() - started
            if response.status_code == 404 and self.style == "auto":
                errors.append(f"{style}: HTTP 404")
                continue
            if response.status_code >= 400:
                raise MiniMaxError(f"{style} HTTP {response.status_code}: {response.text[:800]}")
            try:
                body = response.json()
            except ValueError as exc:
                raise MiniMaxError(f"{style} returned non-JSON content: {response.text[:400]}") from exc
            base_resp = body.get("base_resp") or {}
            if base_resp.get("status_code") not in (None, 0):
                code = base_resp.get("status_code")
                message = base_resp.get("status_msg")
                hint = ""
                if code == 1008:
                    hint = (
                        " — the account has no remaining quota; redeem the MiniMax M Plan for this "
                        "account's team (GroupId) or top up the balance"
                    )
                elif code == 1004:
                    hint = " — set MINIMAX_GROUP_ID to the team id that owns this API key"
                elif code == 2069:
                    hint = " — pick a model that /v1/models still lists (e.g. MiniMax-M3)"
                raise MiniMaxError(f"{style} error {code}: {message}{hint}")
            choices = body.get("choices") or []
            if not choices:
                raise MiniMaxError(f"{style} returned no choices: {json.dumps(body)[:400]}")
            message = choices[0].get("message") or {}
            content = message.get("content") or ""
            reasoning = message.get("reasoning_content") or ""
            if not content and isinstance(message.get("content"), list):
                content = "".join(
                    part.get("text", "") for part in message["content"] if isinstance(part, dict)
                )
            if not content.strip():
                raise MiniMaxError(f"{style} returned an empty message")
            return ChatResult(
                content=content,
                model=str(body.get("model") or self.model),
                endpoint=url,
                usage=body.get("usage") or {},
                latency_s=latency,
                finish_reason=choices[0].get("finish_reason"),
                request_id=body.get("id") or body.get("request_id"),
                reasoning_chars=len(reasoning),
            )
        raise MiniMaxError("no MiniMax endpoint accepted the request: " + "; ".join(errors))


_DIFF_BLOCK = re.compile(r"```(?:diff|patch)\s*\n(?P<body>.*?)```", re.DOTALL)


def extract_patch(text: str) -> str | None:
    """Pull a unified diff out of a model answer."""
    candidates: list[str] = []
    for match in _DIFF_BLOCK.finditer(text):
        candidates.append(match.group("body"))
    if not candidates and "diff --git" in text:
        candidates.append(text[text.index("diff --git") :])
    if not candidates:
        return None
    patch = candidates[0].replace("\r\n", "\n").strip("\n")
    if not patch.endswith("\n"):
        patch += "\n"
    return patch if ("--- " in patch and "+++ " in patch) else None


def build_messages(pack: ContextPack, *, feedback: str | None = None) -> list[dict[str, str]]:
    context = pack.to_markdown()
    user = [
        "## Task",
        "",
        f"**{pack.task.title}**",
        "",
        pack.task.description.strip(),
        "",
        context,
        "",
        "## Output",
        "",
        "Return one unified diff in a ```diff block that makes the acceptance tests pass.",
        "If a previous attempt failed, the failure output is included below; fix it without widening the change.",
    ]
    if feedback:
        user.extend(["", "## Failure of the previous attempt", "", "```text", feedback[-6000:], "```"])
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(user)},
    ]


@dataclass
class ApplyResult:
    ok: bool
    command: str
    output: str


def reset_workdir(workdir: Path) -> None:
    subprocess.run(["git", "checkout", "--", "."], cwd=workdir, capture_output=True, text=True)
    subprocess.run(["git", "clean", "-fdq"], cwd=workdir, capture_output=True, text=True)


def apply_patch(workdir: Path, patch: str) -> ApplyResult:
    patch_file = workdir.parent / "attempt.patch"
    patch_file.write_text(patch, encoding="utf-8")
    check = subprocess.run(
        ["git", "apply", "--check", "--whitespace=nowarn", str(patch_file)],
        cwd=workdir, capture_output=True, text=True,
    )
    if check.returncode != 0:
        fallback = subprocess.run(
            ["git", "apply", "--check", "-p1", "--whitespace=nowarn", str(patch_file)],
            cwd=workdir, capture_output=True, text=True,
        )
        if fallback.returncode != 0:
            return ApplyResult(False, "git apply --check", check.stderr or fallback.stderr)
    applied = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", str(patch_file)],
        cwd=workdir, capture_output=True, text=True,
    )
    return ApplyResult(applied.returncode == 0, "git apply", applied.stdout + applied.stderr)


def git_diff(workdir: Path) -> str:
    process = subprocess.run(["git", "diff", "--no-color"], cwd=workdir, capture_output=True, text=True)
    return process.stdout


@dataclass
class AttemptRecord:
    number: int
    model: str
    endpoint: str
    usage: dict[str, Any]
    latency_s: float
    patch_chars: int
    applied: bool
    apply_output: str
    acceptance: TestRun | None = None
    regression: TestRun | None = None
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "model": self.model,
            "endpoint": self.endpoint,
            "usage": self.usage,
            "latency_s": round(self.latency_s, 2),
            "patch_chars": self.patch_chars,
            "applied": self.applied,
            "apply_output": self.apply_output[-2000:],
            "error": self.error,
            "acceptance": self.acceptance.as_dict() if self.acceptance else None,
            "regression": self.regression.as_dict() if self.regression else None,
        }


@dataclass
class AgentRun:
    task_id: str
    baseline_commit: str
    baseline_acceptance: TestRun
    attempts: list[AttemptRecord] = field(default_factory=list)
    patch: str = ""
    success: bool = False
    acceptance_after: TestRun | None = None
    regression_after: TestRun | None = None
    elapsed_s: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "baseline_commit": self.baseline_commit,
            "baseline_acceptance": self.baseline_acceptance.as_dict(),
            "attempts": [attempt.as_dict() for attempt in self.attempts],
            "success": self.success,
            "acceptance_after": self.acceptance_after.as_dict() if self.acceptance_after else None,
            "regression_after": self.regression_after.as_dict() if self.regression_after else None,
            "elapsed_s": round(self.elapsed_s, 2),
            "patch_chars": len(self.patch),
        }


def run_agent(
    *,
    pack: ContextPack,
    workdir: Path,
    client: MiniMaxClient,
    records_dir: Path,
    artifacts_dir: Path,
    max_attempts: int = 2,
    apply_changes: bool = True,
    reset: bool = True,
    regression_targets: Sequence[str] | None = None,
) -> AgentRun:
    """Run the full MiniMax → patch → test loop and persist every step."""
    started = time.time()
    task = pack.task
    sample = Path(pack.repo_root)
    baseline_commit = prepare_workdir(sample, workdir, reset=reset)
    run_dir = records_dir / "minimax" / task.task_id
    run_dir.mkdir(parents=True, exist_ok=True)

    baseline = run_pytest(workdir, task.acceptance_tests)
    write_test_log(run_dir / "tests-before.log", baseline)

    run = AgentRun(task_id=task.task_id, baseline_commit=baseline_commit, baseline_acceptance=baseline)
    feedback: str | None = None
    regression = list(regression_targets if regression_targets is not None else task.regression_tests)

    for number in range(1, max_attempts + 1):
        messages = build_messages(pack, feedback=feedback)
        (run_dir / f"attempt-{number}-prompt.json").write_text(
            json.dumps({"messages": messages, "model": client.model}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        attempt = AttemptRecord(number=number, model=client.model, endpoint="", usage={}, latency_s=0.0,
                                patch_chars=0, applied=False, apply_output="")
        try:
            result = client.chat(messages)
        except MiniMaxError as exc:
            attempt.error = str(exc)
            run.attempts.append(attempt)
            feedback = f"the previous request failed: {exc}"
            continue
        attempt.endpoint = result.endpoint
        attempt.usage = result.usage
        attempt.latency_s = result.latency_s
        (run_dir / f"attempt-{number}-response.json").write_text(
            json.dumps(
                {
                    "model": result.model,
                    "endpoint": result.endpoint,
                    "finish_reason": result.finish_reason,
                    "request_id": result.request_id,
                    "usage": result.usage,
                    "latency_s": result.latency_s,
                    "reasoning_chars": result.reasoning_chars,
                    "content_chars": len(result.content),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (run_dir / f"attempt-{number}-response.md").write_text(result.content, encoding="utf-8")

        patch = extract_patch(result.content)
        if patch is None:
            attempt.error = "the answer did not contain a unified diff"
            run.attempts.append(attempt)
            feedback = "your answer did not contain a unified diff in a ```diff block"
            continue
        attempt.patch_chars = len(patch)
        run.patch = patch
        (run_dir / f"attempt-{number}.patch").write_text(patch, encoding="utf-8")

        if not apply_changes:
            attempt.error = "apply disabled"
            run.attempts.append(attempt)
            continue

        reset_workdir(workdir)
        applied = apply_patch(workdir, patch)
        attempt.applied = applied.ok
        attempt.apply_output = applied.output
        if not applied.ok:
            run.attempts.append(attempt)
            feedback = f"the patch did not apply:\n{applied.output[-2000:]}"
            continue

        acceptance = run_pytest(workdir, task.acceptance_tests)
        attempt.acceptance = acceptance
        write_test_log(run_dir / f"tests-after-attempt-{number}.log", acceptance)
        if acceptance.exit_code != 0:
            run.attempts.append(attempt)
            feedback = (
                "the acceptance tests still fail after applying your patch:\n"
                + "\n".join(select_failed_tests(acceptance.output) or [])
                + "\n\n"
                + acceptance.output[-4000:]
            )
            continue

        regression_run = run_pytest(workdir, regression)
        attempt.regression = regression_run
        write_test_log(run_dir / f"tests-regression-attempt-{number}.log", regression_run)
        run.attempts.append(attempt)
        if regression_run.exit_code != 0:
            feedback = (
                "the acceptance tests pass but the regression suite fails:\n"
                + regression_run.output[-4000:]
            )
            continue

        run.success = True
        run.acceptance_after = acceptance
        run.regression_after = regression_run
        break

    if not run.success and run.attempts and run.attempts[-1].acceptance is not None:
        run.acceptance_after = run.attempts[-1].acceptance
        run.regression_after = run.attempts[-1].regression

    if apply_changes and run.attempts and run.attempts[-1].applied:
        diff = git_diff(workdir)
        if diff.strip():
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            (artifacts_dir / "code.diff").write_text(diff, encoding="utf-8")

    run.elapsed_s = time.time() - started
    (run_dir / "agent-run.json").write_text(json.dumps(run.as_dict(), ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    return run
