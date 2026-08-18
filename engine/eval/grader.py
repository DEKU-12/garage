"""Thin wrapper over the official SWE-bench Docker evaluation harness (FR-5).

Build week: 1. This wrapper working for one task IS the week-1 exit criterion.

Untrusted repo code executes ONLY inside Docker (NFR-3, rules.md §4.1.5) --
the engine never runs a checkout's tests on the host, not even to debug. We
shell out to the official harness rather than importing it (TAD §3.5) so the
whole grading run sits behind one enforceable timeout.

A timeout or a missing report is `GradingInfraError` -> task status `crashed`,
NOT `failed_tests`. Never blame the model for infrastructure (rules.md §3.1).

Emits: tests_run, gate_verdict (once events.py lands in week 3).
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from engine.errors import GradingInfraError

# No "/" -- the harness uses this verbatim as a log directory name.
MODEL_NAME = "garage"

HARNESS_TIMEOUT_S = 1200  # per-instance test timeout (rules.md §3.2: 20 min)
BUILD_GRACE_S = 3600  # extra headroom for a first-time image pull/build
LOG_TAIL_CHARS = 4000  # what the builder gets back as failure feedback
IMAGE_PLATFORM = "linux/amd64"  # SWE-bench publishes x86_64 images only
IMAGE_PULL_TIMEOUT_S = 1800


@dataclass(frozen=True)
class GradeResult:
    """The harness's verdict on one patch."""

    task_id: str
    verdict: str  # "pass" | "fail"
    resolved: bool
    reason: str  # "" when graded normally; else why, e.g. "empty_patch"
    log_tail: str
    report_path: Path
    test_output_path: Path | None
    wall_ms: int


def _write_predictions(path: Path, task_id: str, patch: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "instance_id": task_id,
        "model_name_or_path": MODEL_NAME,
        "model_patch": patch,
    }
    path.write_text(json.dumps(row) + "\n")


def _tail(path: Path, limit: int = LOG_TAIL_CHARS) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(errors="replace")
    return text[-limit:]


def _ensure_image(image: str) -> None:
    """Put the amd64 image in the local store before the harness looks for it.

    SWE-bench publishes x86_64 images only. On Apple Silicon the harness's own
    `client.images.pull()` 404s with "no matching manifest for linux/arm64",
    which kills grading before a single test runs (risk R1).

    `create_container` calls `client.images.get()` BEFORE it pulls, so an image
    already present locally is used as-is -- pulling it here with an explicit
    --platform makes the harness run it under emulation. No fork, no patched
    harness, no custom test runner: ADR-3 stays intact.
    """
    if not image or platform.machine() in {"x86_64", "AMD64"}:
        return
    probe = subprocess.run(
        ["docker", "image", "inspect", image], capture_output=True, text=True
    )
    if probe.returncode == 0:
        return
    pull = subprocess.run(
        ["docker", "pull", "--platform", IMAGE_PLATFORM, image],
        capture_output=True,
        text=True,
        timeout=IMAGE_PULL_TIMEOUT_S,
        check=False,
    )
    if pull.returncode != 0:
        raise GradingInfraError(
            f"could not pull {image} for {IMAGE_PLATFORM}: {pull.stderr[-800:]}"
        )


def grade(
    task_id: str,
    patch: str,
    run_id: str,
    work_dir: Path,
    image: str = "",
    dataset: str = "SWE-bench/SWE-bench_Lite",
    split: str = "test",
    timeout_s: int = HARNESS_TIMEOUT_S,
) -> GradeResult:
    """Grade one patch in Docker and return the harness's verdict.

    Raises GradingInfraError if the harness times out or produces no report --
    both are infrastructure failures, not test failures.
    """
    _ensure_image(image)

    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    predictions = work_dir / f"predictions_{task_id}.jsonl"
    _write_predictions(predictions, task_id, patch)

    cmd = [
        sys.executable,
        "-m",
        "swebench.harness.run_evaluation",
        "--dataset_name", dataset,
        "--split", split,
        "--instance_ids", task_id,
        "--predictions_path", str(predictions.name),
        "--run_id", run_id,
        "--max_workers", "1",
        "--timeout", str(timeout_s),
        "--report_dir", ".",
    ]

    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=timeout_s + BUILD_GRACE_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GradingInfraError(
            f"{task_id}: harness exceeded {timeout_s + BUILD_GRACE_S}s wall clock"
        ) from exc
    wall_ms = int((time.monotonic() - started) * 1000)

    log_dir = work_dir / "logs" / "run_evaluation" / run_id / MODEL_NAME / task_id
    report_path = log_dir / "report.json"
    test_output = log_dir / "test_output.txt"

    # Normal path: the harness graded the patch and wrote a per-instance report.
    if report_path.is_file():
        report = json.loads(report_path.read_text())
        resolved = bool(report.get(task_id, {}).get("resolved", False))
        return GradeResult(
            task_id=task_id,
            verdict="pass" if resolved else "fail",
            resolved=resolved,
            reason="",
            log_tail=_tail(test_output),
            report_path=report_path,
            test_output_path=test_output if test_output.is_file() else None,
            wall_ms=wall_ms,
        )

    # No per-instance report. The harness still classifies the instance in its
    # run-level summary -- an empty patch, for one, is filtered out before any
    # container starts. Consult it rather than guessing: the harness stays the
    # authority on every verdict, this code only reads it.
    summary_path = work_dir / f"{MODEL_NAME}.{run_id}.json"
    if summary_path.is_file():
        summary = json.loads(summary_path.read_text())
        if task_id in summary.get("empty_patch_ids", []):
            # A patch that does not exist cannot resolve a task. That is a
            # model failure, not an infrastructure one (rules.md §3.1).
            return GradeResult(
                task_id=task_id,
                verdict="fail",
                resolved=False,
                reason="empty_patch",
                log_tail="",
                report_path=summary_path,
                test_output_path=None,
                wall_ms=wall_ms,
            )
        for key in ("infra_failure_ids", "error_ids"):
            if task_id in summary.get(key, []):
                raise GradingInfraError(f"{task_id}: harness reported {key}")

    raise GradingInfraError(
        f"{task_id}: harness wrote no report at {report_path} "
        f"(exit {proc.returncode}); stderr tail: {proc.stderr[-1500:]}"
    )
