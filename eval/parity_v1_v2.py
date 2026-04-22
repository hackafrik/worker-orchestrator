#!/usr/bin/env python3
"""First true parity test between worker-orchestrator v1.4 and v2.

Compares the local helper-script workflow both repos actually implement today:
- evaluate_output.py
- evaluate_worker_logs.py
- synthesize_outputs.py

This is intentionally limited to the shared executable surface area. v1.4 does not
ship a standalone orchestrator runner, so orchestration-level parity would be a
false comparison at this stage.
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
WORK_DIR = Path(tempfile.mkdtemp(prefix="worker-orchestrator-parity-"))


def run(repo: Path, script_rel: str, args: list[str]) -> dict:
    script = repo / script_rel
    result = subprocess.run(
        [sys.executable, str(script)] + args,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(WORK_DIR),
    )
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    parsed = None
    if stdout:
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            parsed = {"raw_stdout": stdout}
    else:
        parsed = {}
    return {
        "exit_code": result.returncode,
        "stdout": parsed,
        "stderr": stderr,
    }


def normalize_for_compare(value):
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in {"output_dir"}:
                continue
            out[k] = normalize_for_compare(v)
        return out
    if isinstance(value, list):
        return [normalize_for_compare(v) for v in value]
    if isinstance(value, str):
        normalized = value.replace(str(WORK_DIR), "<WORK_DIR>")
        normalized = normalized.replace("/v1_merged", "/<MERGED_DIR>")
        normalized = normalized.replace("/v2_merged", "/<MERGED_DIR>")
        normalized = normalized.replace("v1_merged", "<MERGED_DIR>")
        normalized = normalized.replace("v2_merged", "<MERGED_DIR>")
        return normalized
    return value


def build_fixture() -> dict:
    # Output-eval fixture
    output_file = WORK_DIR / "sample_worker_output.py"
    output_file.write_text(
        'import json\n\n'
        'def main():\n'
        '    """Main entrypoint for the sample worker output."""\n'
        '    payload = {"status": "ok", "message": "worker complete"}\n'
        '    print(json.dumps(payload))\n\n'
        'if __name__ == "__main__":\n'
        '    main()\n'
    )
    criteria = {
        "expected_keywords": ["import", "def", "json", "main"],
        "required_sections": ["main", "payload", "worker complete"],
        "forbidden_patterns": ["TODO", "FIXME", "PLACEHOLDER"],
    }

    # Log-eval fixture
    log_file = WORK_DIR / "worker.log"
    log_file.write_text(
        "opencode v1.14.19 boot\n"
        "mcp key=exa connected\n"
        "session id=ses_demo123\n"
        "Wrote to /tmp/report.md\n"
        "Task complete\n"
    )

    # Synthesis fixture: code merge with one conflict and one unique file
    synth_base = WORK_DIR / "synthesis"
    worker_a = synth_base / "worker_a"
    worker_b = synth_base / "worker_b"
    worker_a.mkdir(parents=True)
    worker_b.mkdir(parents=True)
    (worker_a / "main.py").write_text("# from worker a\n")
    (worker_b / "main.py").write_text("# from worker b\n")
    (worker_b / "utils.py").write_text("# utils\n")

    return {
        "output_file": output_file,
        "criteria": criteria,
        "log_file": log_file,
        "worker_a": worker_a,
        "worker_b": worker_b,
    }


def compare_case(name: str, v1_result: dict, v2_result: dict) -> dict:
    normalized_v1 = normalize_for_compare(v1_result)
    normalized_v2 = normalize_for_compare(v2_result)
    passed = normalized_v1 == normalized_v2
    return {
        "name": name,
        "passed": passed,
        "v1": normalized_v1,
        "v2": normalized_v2,
    }


def main() -> int:
    fixture = build_fixture()

    cases = []

    criteria_json = json.dumps(fixture["criteria"])
    v1_eval_output = run(V1_DIR, "scripts/evaluate_output.py", [
        "--file", str(fixture["output_file"]),
        "--criteria", criteria_json,
    ])
    v2_eval_output = run(V2_DIR, "scripts/evaluate_output.py", [
        "--file", str(fixture["output_file"]),
        "--criteria", criteria_json,
    ])
    cases.append(compare_case("evaluate_output", v1_eval_output, v2_eval_output))

    v1_eval_logs = run(V1_DIR, "scripts/evaluate_worker_logs.py", [
        "--log", str(fixture["log_file"]),
        "--provider", "opencode",
    ])
    v2_eval_logs = run(V2_DIR, "scripts/evaluate_worker_logs.py", [
        "--log", str(fixture["log_file"]),
        "--provider", "opencode",
    ])
    cases.append(compare_case("evaluate_worker_logs", v1_eval_logs, v2_eval_logs))

    v1_synth = run(V1_DIR, "scripts/synthesize_outputs.py", [
        "--inputs", str(fixture["worker_a"]), str(fixture["worker_b"]),
        "--output", str(WORK_DIR / "v1_merged"),
        "--type", "code",
    ])
    v2_synth = run(V2_DIR, "scripts/synthesize_outputs.py", [
        "--inputs", str(fixture["worker_a"]), str(fixture["worker_b"]),
        "--output", str(WORK_DIR / "v2_merged"),
        "--type", "code",
    ])
    cases.append(compare_case("synthesize_outputs", v1_synth, v2_synth))

    summary = {
        "work_dir": str(WORK_DIR),
        "v1_repo": str(V1_DIR),
        "v2_repo": str(V2_DIR),
        "cases": cases,
        "all_passed": all(case["passed"] for case in cases),
    }

    report_path = V2_DIR / "eval" / "parity-report-v1-v2.json"
    report_path.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))

    # Keep WORK_DIR for inspection if anything fails, otherwise clean it.
    if summary["all_passed"]:
        shutil.rmtree(WORK_DIR, ignore_errors=True)
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
