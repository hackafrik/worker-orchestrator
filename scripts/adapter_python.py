#!/usr/bin/env python3
"""Adapter for PYTHON_SCRIPT worker category.

Thin wrapper around subprocess.Popen for custom Python worker scripts.
Pure stdlib. No external dependencies.
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _ensure_dirs(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def spawn(task_spec: dict) -> dict:
    runtime = task_spec.get("runtime", {})
    script_path = runtime.get("script_path")
    interpreter = runtime.get("interpreter", sys.executable)
    args = runtime.get("args", [])
    stdin_text = runtime.get("stdin_text", task_spec.get("prompt", ""))
    env = task_spec.get("env", {})
    cwd = runtime.get("cwd", task_spec.get("workdir", "."))
    timeout = task_spec.get("timeout_seconds", 300)

    if not script_path or not Path(script_path).exists():
        raise ValueError(f"Python script adapter requires existing runtime.script_path: {script_path}")

    worker_id = task_spec["task_id"]
    state_dir = Path(task_spec.get("workdir", ".")) / ".workers" / worker_id
    _ensure_dirs(state_dir)

    stdout_path = state_dir / "stdout.log"
    stderr_path = state_dir / "stderr.log"
    events_path = state_dir / "events.jsonl"
    handle_path = state_dir / "handle.json"

    # Write task_spec to temp file for --task-spec arg patterns
    task_spec_file = state_dir / "task_spec.json"
    task_spec_file.write_text(json.dumps(task_spec, indent=2) + "\n")

    # Build args — substitute {task_spec_file} placeholder
    resolved_args = []
    for arg in args:
        if arg == "{task_spec_file}":
            resolved_args.append(str(task_spec_file))
        else:
            resolved_args.append(arg)

    full_cmd = [interpreter, script_path] + resolved_args

    stdin_source = None
    if stdin_text:
        stdin_file = state_dir / "stdin.txt"
        stdin_file.write_text(stdin_text)
        stdin_source = open(stdin_file, "r")

    merged_env = {**os.environ, **env}

    proc = subprocess.Popen(
        full_cmd,
        stdin=stdin_source,
        stdout=open(stdout_path, "w"),
        stderr=open(stderr_path, "w"),
        cwd=cwd,
        env=merged_env,
        start_new_session=True,
    )

    if stdin_source:
        stdin_source.close()

    handle = {
        "worker_id": worker_id,
        "task_id": worker_id,
        "phase": task_spec.get("phase", 0),
        "worker_category": "PYTHON_SCRIPT",
        "adapter_name": task_spec.get("adapter_name", "python3"),
        "status": "RUNNING",
        "created_at": _iso_now(),
        "started_at": _iso_now(),
        "ended_at": None,
        "workdir": str(Path(cwd).resolve()),
        "artifacts_dir": task_spec.get("artifacts_dir", str(state_dir)),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "events_path": str(events_path),
        "exit_code": None,
        "transport": {
            "pid": proc.pid,
            "pgid": os.getpgid(proc.pid),
            "invocation": full_cmd,
        },
        "runtime": runtime,
    }
    handle_path.write_text(json.dumps(handle, indent=2) + "\n")
    return handle


def monitor(worker_handle: dict) -> dict:
    pid = worker_handle.get("transport", {}).get("pid")
    stdout_path = Path(worker_handle["stdout_path"])
    stderr_path = Path(worker_handle["stderr_path"])
    state_dir = stdout_path.parent
    handle_path = state_dir / "handle.json"

    stdout_text = stdout_path.read_text(errors="replace") if stdout_path.exists() else ""
    stderr_text = stderr_path.read_text(errors="replace") if stderr_path.exists() else ""

    status = worker_handle.get("status", "RUNNING")
    exit_code = worker_handle.get("exit_code")

    if pid:
        try:
            proc = subprocess.Popen(["ps", "-p", str(pid)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            proc.communicate(timeout=2)
            if proc.returncode != 0:
                status = "FAILED"
                exitcode_file = state_dir / ".exitcode"
                if exitcode_file.exists():
                    try:
                        exit_code = int(exitcode_file.read_text().strip())
                        status = "SUCCEEDED" if exit_code == 0 else "FAILED"
                    except ValueError:
                        pass
        except Exception:
            status = "FAILED"

    if exit_code is not None:
        status = "SUCCEEDED" if exit_code == 0 else "FAILED"

    # Artifact discovery
    artifacts_dir = Path(worker_handle.get("artifacts_dir", state_dir))
    artifacts: list[dict] = []
    if artifacts_dir.exists():
        for f in artifacts_dir.iterdir():
            if f.is_file() and f.name not in ("stdout.log", "stderr.log", "handle.json", "events.jsonl", "stdin.txt", ".exitcode", "task_spec.json"):
                kind = "other"
                if f.suffix == ".md":
                    kind = "report"
                elif f.suffix in (".diff", ".patch"):
                    kind = "patch"
                elif f.suffix == ".json":
                    kind = "json"
                artifacts.append({"path": str(f), "kind": kind, "size_bytes": f.stat().st_size})

    started = worker_handle.get("started_at")
    runtime_seconds = 0.0
    if started:
        try:
            from datetime import datetime, timezone
            started_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
            runtime_seconds = (datetime.now(timezone.utc) - started_dt).total_seconds()
        except Exception:
            pass

    result = {
        "status": status,
        "stdout": stdout_text,
        "stderr": stderr_text,
        "artifacts": artifacts,
        "metrics": {
            "runtime_seconds": round(runtime_seconds, 2),
            "cost_usd": None,
            "tokens_in": None,
            "tokens_out": None,
        },
        "heartbeat_at": _iso_now(),
        "raw_state": {"pid": pid, "stdout_size": stdout_path.stat().st_size if stdout_path.exists() else 0},
    }

    worker_handle["status"] = status
    worker_handle["exit_code"] = exit_code
    handle_path.write_text(json.dumps(worker_handle, indent=2) + "\n")
    return result


def kill(worker_handle: dict) -> bool:
    pgid = worker_handle.get("transport", {}).get("pgid")
    pid = worker_handle.get("transport", {}).get("pid")
    target = pgid if pgid else pid
    if not target:
        return False
    try:
        os.killpg(target, signal.SIGTERM)
        time.sleep(0.5)
        try:
            os.killpg(target, 0)
            os.killpg(target, signal.SIGKILL)
        except ProcessLookupError:
            pass
    except ProcessLookupError:
        pass
    except PermissionError:
        return False

    state_dir = Path(worker_handle["stdout_path"]).parent
    handle_path = state_dir / "handle.json"
    worker_handle["status"] = "KILLED"
    worker_handle["ended_at"] = _iso_now()
    handle_path.write_text(json.dumps(worker_handle, indent=2) + "\n")
    return True


def evaluate(worker_handle: dict, rubric: dict) -> dict:
    result = monitor(worker_handle)
    stdout = result["stdout"]
    artifacts = result["artifacts"]

    score = 0.0
    breakdown: dict[str, Any] = {}
    evidence: dict[str, Any] = {"output_markers": [], "artifact_paths": []}

    expected_markers = rubric.get("expected_output_markers", [])
    found_markers = [m for m in expected_markers if m in stdout]
    breakdown["presence"] = "pass" if len(found_markers) == len(expected_markers) else "fail"
    evidence["output_markers"] = found_markers

    expected_files = rubric.get("expected_files", [])
    found_files = [a["path"] for a in artifacts if Path(a["path"]).name in expected_files]
    breakdown["artifact_compliance"] = "pass" if len(found_files) >= len(expected_files) else "fail"
    evidence["artifact_paths"] = found_files

    checks = [v for v in breakdown.values() if v in ("pass", "fail")]
    if checks:
        score = sum(1 for c in checks if c == "pass") / len(checks)

    return {
        "score": round(score, 2),
        "feedback": f"Passed {sum(1 for c in checks if c == 'pass')}/{len(checks)} checks.",
        "rubric_breakdown": breakdown,
        "evidence": evidence,
    }


def _cli() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Python script adapter for worker orchestrator")
    sub = parser.add_subparsers(dest="cmd")
    for action in ("spawn", "monitor", "kill", "evaluate"):
        p = sub.add_parser(action)
        p.add_argument("file", help="task_spec.json (spawn) or handle.json (others)")
        if action == "evaluate":
            p.add_argument("rubric", help="rubric.json")
    args = parser.parse_args()

    if args.cmd == "spawn":
        spec = json.loads(Path(args.file).read_text())
        handle = spawn(spec)
        print(json.dumps(handle, indent=2))
        return 0
    elif args.cmd == "monitor":
        handle = json.loads(Path(args.file).read_text())
        result = monitor(handle)
        print(json.dumps(result, indent=2))
        return 0
    elif args.cmd == "kill":
        handle = json.loads(Path(args.file).read_text())
        ok = kill(handle)
        print(json.dumps({"killed": ok}))
        return 0
    elif args.cmd == "evaluate":
        handle = json.loads(Path(args.file).read_text())
        rubric = json.loads(Path(args.rubric).read_text())
        result = evaluate(handle, rubric)
        print(json.dumps(result, indent=2))
        return 0
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(_cli())
