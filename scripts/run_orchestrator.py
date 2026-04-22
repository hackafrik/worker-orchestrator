#!/usr/bin/env python3
"""High-level orchestrator that ties adapters, state machine, and cost tracking together.

Accepts a JSON task manifest and manages the full lifecycle across worker categories.
Pure stdlib. No external dependencies.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


STATE_DIR = Path(".worker-state")


def _run_script(script_name: str, args: list[str], cwd: str | Path = ".") -> dict:
    scripts_dir = Path(__file__).parent
    script = scripts_dir / script_name
    if not script.exists():
        raise FileNotFoundError(f"Script not found: {script}")
    result = subprocess.run(
        [sys.executable, str(script)] + args,
        capture_output=True,
        text=True,
        cwd=str(cwd),
        timeout=30,
    )
    stdout = result.stdout.strip()
    if result.returncode != 0:
        return {
            "success": False,
            "exit_code": result.returncode,
            "stdout": stdout,
            "stderr": result.stderr.strip(),
        }
    try:
        return {"success": True, **json.loads(stdout)}
    except json.JSONDecodeError:
        return {"success": True, "raw_output": stdout}


def _resolve_adapter(category: str, adapter_name: str) -> str:
    mapping = {
        "CLI": "adapter_cli.py",
        "HTTP_API": "adapter_http.py",
        "PYTHON_SCRIPT": "adapter_python.py",
        "DOCKER": "adapter_docker.py",
    }
    cat = category.upper()
    if cat not in mapping:
        raise ValueError(f"Unknown category: {category}. Supported: {list(mapping.keys())}")
    return mapping[cat]


def run_task(task_spec: dict) -> dict:
    """Run a single task end-to-end."""
    worker_id = task_spec["task_id"]
    category = task_spec.get("worker_category", "CLI")
    adapter_name = task_spec.get("adapter_name", "unknown")
    phase = task_spec.get("phase", 0)

    state_machine = _run_script("state_machine.py", ["init", worker_id])
    if not state_machine.get("success"):
        return {"error": "state_machine init failed", "detail": state_machine}

    # Transition SPAWNING
    state_machine = _run_script("state_machine.py", ["transition", worker_id, "SPAWNING", "--reason", f"phase {phase} spawn"])
    if not state_machine.get("success"):
        return {"error": "state_machine transition to SPAWNING failed", "detail": state_machine}

    # Write task_spec temp file
    workdir = Path(task_spec.get("workdir", "."))
    workdir.mkdir(parents=True, exist_ok=True)
    spec_file = workdir / f"{worker_id}_task_spec.json"
    spec_file.write_text(json.dumps(task_spec, indent=2) + "\n")

    # Spawn via adapter
    adapter_script = _resolve_adapter(category, adapter_name)
    spawn_result = _run_script(adapter_script, ["spawn", str(spec_file)], cwd=workdir)
    if not spawn_result.get("success"):
        _run_script("state_machine.py", ["transition", worker_id, "FAILED", "--reason", "spawn failed"])
        return {"error": "spawn failed", "detail": spawn_result}

    handle = spawn_result
    handle_path = workdir / ".workers" / worker_id / "handle.json"
    if handle_path.exists():
        handle = json.loads(handle_path.read_text())

    # Transition RUNNING
    _run_script("state_machine.py", ["transition", worker_id, "RUNNING", "--reason", "spawn succeeded"])

    # Monitor loop
    timeout = task_spec.get("timeout_seconds", 300)
    poll_interval = task_spec.get("poll_interval_seconds", 5)
    started = time.time()
    final_monitor = {}
    while True:
        elapsed = time.time() - started
        if elapsed > timeout:
            _run_script("state_machine.py", ["transition", worker_id, "CANCEL_REQUESTED", "--reason", "timeout"])
            _run_script(adapter_script, ["kill", str(handle_path)], cwd=workdir)
            _run_script("state_machine.py", ["transition", worker_id, "TIMED_OUT", "--reason", f"timeout after {timeout}s"])
            break

        monitor_result = _run_script(adapter_script, ["monitor", str(handle_path)], cwd=workdir)
        if not monitor_result.get("success"):
            # Best-effort continue
            time.sleep(poll_interval)
            continue

        final_monitor = monitor_result
        status = monitor_result.get("status", "RUNNING")
        if status in ("SUCCEEDED", "FAILED", "KILLED", "TIMED_OUT"):
            break
        time.sleep(poll_interval)

    # Transition MONITORING -> EVALUATING or terminal
    state = _run_script("state_machine.py", ["get", worker_id])
    current_status = state.get("status", "RUNNING") if state.get("success") else "RUNNING"

    if current_status not in ("KILLED", "TIMED_OUT", "FAILED"):
        _run_script("state_machine.py", ["transition", worker_id, "MONITORING", "--reason", "monitor complete"])
        _run_script("state_machine.py", ["transition", worker_id, "EVALUATING", "--reason", "begin evaluation"])

        rubric = task_spec.get("rubric", {})
        rubric_file = workdir / f"{worker_id}_rubric.json"
        rubric_file.write_text(json.dumps(rubric, indent=2) + "\n")
        eval_result = _run_script(adapter_script, ["evaluate", str(handle_path), str(rubric_file)], cwd=workdir)

        if eval_result.get("success") and eval_result.get("score", 0) >= task_spec.get("pass_threshold", 0.5):
            _run_script("state_machine.py", ["transition", worker_id, "SUCCEEDED", "--reason", f"evaluation score {eval_result.get('score')}"])
        else:
            _run_script("state_machine.py", ["transition", worker_id, "FAILED", "--reason", f"evaluation score {eval_result.get('score')}"])

    # Record cost
    metrics = final_monitor.get("metrics", {})
    cost_args = [
        "record",
        "--worker-id", worker_id,
        "--category", category,
        "--adapter", adapter_name,
    ]
    model_id = task_spec.get("model_id")
    if model_id is not None:
        cost_args += ["--model", str(model_id)]
    cost_args += [
        "--tokens-in", str(metrics.get("tokens_in") or 0),
        "--tokens-out", str(metrics.get("tokens_out") or 0),
        "--runtime", str(metrics.get("runtime_seconds") or 0),
    ]
    _run_script("track_cost.py", cost_args, cwd=workdir)

    # Final state
    final_state = _run_script("state_machine.py", ["get", worker_id])
    return {
        "task_id": worker_id,
        "phase": phase,
        "final_status": final_state.get("status", "UNKNOWN") if final_state.get("success") else "UNKNOWN",
        "monitor": final_monitor,
        "evaluation": eval_result if "eval_result" in dir() else None,
        "cost_report": _run_script("track_cost.py", ["report", "--worker-ids", worker_id], cwd=workdir),
    }


def run_manifest(manifest: dict) -> dict:
    """Run a full manifest with phase ordering and dependency resolution."""
    phases = manifest.get("phases", [])
    results: list[dict] = []
    for phase in phases:
        phase_num = phase["phase_number"]
        tasks = phase.get("tasks", [])
        phase_results = []
        for task_spec in tasks:
            task_spec["phase"] = phase_num
            result = run_task(task_spec)
            phase_results.append(result)
        results.append({"phase": phase_num, "results": phase_results})

    # Final aggregate cost report
    all_worker_ids = []
    for phase in results:
        for r in phase["results"]:
            all_worker_ids.append(r["task_id"])
    cost = _run_script("track_cost.py", ["report"] + (["--worker-ids"] + all_worker_ids if all_worker_ids else []))
    return {"phases": results, "aggregate_cost": cost}


def _cli() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Run worker orchestrator")
    parser.add_argument("manifest", help="Path to task manifest JSON")
    parser.add_argument("--task-spec", help="Run a single task spec instead of manifest")
    args = parser.parse_args()

    if args.task_spec:
        task = json.loads(Path(args.task_spec).read_text())
        result = run_task(task)
        print(json.dumps(result, indent=2))
        return 0

    manifest = json.loads(Path(args.manifest).read_text())
    result = run_manifest(manifest)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
