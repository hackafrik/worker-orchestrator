#!/usr/bin/env python3
"""Workflow-level CLI parity test between worker-orchestrator v1.4 and v2.

This compares:
- v1.4's real local helper workflow: run worker -> inspect log -> inspect output file
- v2's real local orchestration workflow: run_orchestrator -> adapter_cli -> state/eval

The goal is not byte-for-byte internal equivalence. The goal is parity of outcome on
comparable CLI worker scenarios using the tools each repo actually exposes.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

V1_DIR = Path("/home/hackafrik/worker-orchestrator")
V2_DIR = Path("/home/hackafrik/worker-orchestrator-v2")
WORK_DIR = Path(tempfile.mkdtemp(prefix="worker-orchestrator-cli-parity-"))


SUCCESS_SCRIPT = """#!/usr/bin/env python3
import pathlib
print('opencode v1.14.19 boot')
print('mcp key=exa connected')
print('session id=ses_success123')
report = pathlib.Path('report.md')
report.write_text('# Success report\\n\\nThis worker complete payload contains main findings and implementation details.\\n')
print(f'Wrote to {report.resolve()}')
print('Task complete')
"""


FAILURE_SCRIPT = """#!/usr/bin/env python3
print('opencode v1.14.19 boot')
print('mcp key=exa connected')
print('session id=ses_failure123')
print('HTTP 429 usage_limit_reached resets_in_seconds: 1300')
print('rate limit detected')
raise SystemExit(1)
"""


OUTPUT_CRITERIA = {
    "expected_keywords": ["report", "worker", "complete"],
    "required_sections": ["success report", "main findings"],
    "forbidden_patterns": ["TODO", "FIXME", "PLACEHOLDER"],
}


def _run(cmd: list[str], cwd: Path) -> dict:
    result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=120)
    stdout = result.stdout.strip()
    parsed_stdout = None
    if stdout:
        try:
            parsed_stdout = json.loads(stdout)
        except json.JSONDecodeError:
            parsed_stdout = {"raw_stdout": stdout}
    else:
        parsed_stdout = {}
    return {
        "exit_code": result.returncode,
        "stdout": parsed_stdout,
        "stderr": result.stderr.strip(),
    }


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(0o755)


def _normalize(value):
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    if isinstance(value, str):
        return value.replace(str(WORK_DIR), "<WORK_DIR>")
    return value


def run_v1_helper_flow(worker_script: Path, scenario_dir: Path, expect_success: bool) -> dict:
    log_path = scenario_dir / "worker.log"
    report_path = scenario_dir / "report.md"

    proc = subprocess.run(
        [sys.executable, str(worker_script)],
        cwd=str(scenario_dir),
        capture_output=True,
        text=True,
        timeout=60,
    )
    log_path.write_text(proc.stdout + proc.stderr)

    log_eval = _run(
        [sys.executable, str(V1_DIR / "scripts/evaluate_worker_logs.py"), "--log", str(log_path), "--provider", "opencode"],
        cwd=scenario_dir,
    )

    output_eval = _run(
        [
            sys.executable,
            str(V1_DIR / "scripts/evaluate_output.py"),
            "--file",
            str(report_path),
            "--criteria",
            json.dumps(OUTPUT_CRITERIA),
        ],
        cwd=scenario_dir,
    )

    output_passed = bool(output_eval["stdout"].get("passed"))
    log_score = int(log_eval["stdout"].get("score", 0))
    derived_status = "SUCCEEDED" if (output_passed and log_score >= 4) else "FAILED"

    return {
        "raw_process_exit_code": proc.returncode,
        "log_eval": log_eval,
        "output_eval": output_eval,
        "report_exists": report_path.exists(),
        "derived_status": derived_status,
        "expected_status": "SUCCEEDED" if expect_success else "FAILED",
        "passed": derived_status == ("SUCCEEDED" if expect_success else "FAILED"),
    }


def run_v2_orchestrator_flow(worker_script: Path, scenario_dir: Path, task_id: str, expect_success: bool) -> dict:
    task_spec = {
        "task_id": task_id,
        "phase": 0,
        "worker_category": "CLI",
        "adapter_name": "dummy-cli",
        "prompt": "",
        "workdir": str(scenario_dir),
        "artifacts_dir": str(scenario_dir),
        "timeout_seconds": 20,
        "poll_interval_seconds": 1,
        "pass_threshold": 1.0 if expect_success else 0.5,
        "rubric": {
            "expected_output_markers": ["Task complete"] if expect_success else ["Task complete"],
            "expected_files": ["report.md"] if expect_success else ["report.md"],
        },
        "runtime": {
            "command": [sys.executable, str(worker_script)],
            "extra_args": [],
            "use_shell": False,
            "pty": False,
            "cwd": str(scenario_dir),
            "stdin_text": "",
        },
    }

    spec_path = scenario_dir / f"{task_id}.json"
    spec_path.write_text(json.dumps(task_spec, indent=2) + "\n")

    result = _run(
        [
            sys.executable,
            str(V2_DIR / "scripts/run_orchestrator.py"),
            str(spec_path),
            "--task-spec",
            str(spec_path),
        ],
        cwd=scenario_dir,
    )

    stdout = result["stdout"]
    final_status = stdout.get("final_status", "UNKNOWN") if isinstance(stdout, dict) else "UNKNOWN"
    report_exists = (scenario_dir / "report.md").exists()
    return {
        "orchestrator": result,
        "report_exists": report_exists,
        "final_status": final_status,
        "expected_status": "SUCCEEDED" if expect_success else "FAILED",
        "passed": final_status == ("SUCCEEDED" if expect_success else "FAILED"),
    }


def run_scenario(name: str, script_content: str, expect_success: bool) -> dict:
    scenario_dir = WORK_DIR / name
    scenario_dir.mkdir(parents=True, exist_ok=True)
    worker_script = scenario_dir / "worker.py"
    _write(worker_script, script_content)

    v1_dir = scenario_dir / "v1"
    v2_dir = scenario_dir / "v2"
    v1_dir.mkdir(parents=True, exist_ok=True)
    v2_dir.mkdir(parents=True, exist_ok=True)

    v1 = run_v1_helper_flow(worker_script, v1_dir, expect_success)
    v2 = run_v2_orchestrator_flow(worker_script, v2_dir, f"{name}-task", expect_success)

    parity = {
        "both_match_expectation": v1["passed"] and v2["passed"],
        "status_match": v1["derived_status"] == v2["final_status"],
        "artifact_match": v1["report_exists"] == v2["report_exists"],
    }

    return {
        "scenario": name,
        "expected_status": "SUCCEEDED" if expect_success else "FAILED",
        "v1_helper_flow": _normalize(v1),
        "v2_orchestrator_flow": _normalize(v2),
        "parity": parity,
        "passed": all(parity.values()),
    }


def main() -> int:
    scenarios = [
        run_scenario("cli_success", SUCCESS_SCRIPT, True),
        run_scenario("cli_failure", FAILURE_SCRIPT, False),
    ]

    summary = {
        "work_dir": str(WORK_DIR),
        "v1_repo": str(V1_DIR),
        "v2_repo": str(V2_DIR),
        "scenarios": scenarios,
        "all_passed": all(s["passed"] for s in scenarios),
    }

    report_path = V2_DIR / "eval" / "parity-cli-workflow-report.json"
    report_path.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))

    if summary["all_passed"]:
        shutil.rmtree(WORK_DIR, ignore_errors=True)
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
