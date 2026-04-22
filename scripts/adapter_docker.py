#!/usr/bin/env python3
"""Adapter for DOCKER worker category.

Manages container lifecycle: run, monitor, stop/kill.
Requires docker CLI available on host.
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


def _ensure_dirs(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _docker(args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            ["docker"] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError:
        return 127, "", "docker command not found"
    except subprocess.TimeoutExpired:
        return -1, "", "docker command timed out"


def spawn(task_spec: dict) -> dict:
    runtime = task_spec.get("runtime", {})
    image = runtime["image"]
    entrypoint = runtime.get("entrypoint", [])
    command = runtime.get("command", [])
    stdin_text = runtime.get("stdin_text", task_spec.get("prompt", ""))
    mounts = runtime.get("mounts", [])
    env = runtime.get("env", {})
    network = runtime.get("network", "bridge")
    user = runtime.get("user")
    timeout = task_spec.get("timeout_seconds", 300)

    worker_id = task_spec["task_id"]
    state_dir = Path(task_spec.get("workdir", ".")) / ".workers" / worker_id
    _ensure_dirs(state_dir)

    stdout_path = state_dir / "stdout.log"
    stderr_path = state_dir / "stderr.log"
    events_path = state_dir / "events.jsonl"
    handle_path = state_dir / "handle.json"

    container_name = f"worker-{worker_id}"

    # Write stdin to file for docker cp or pipe
    stdin_file = None
    if stdin_text:
        stdin_file = state_dir / "stdin.txt"
        stdin_file.write_text(stdin_text)

    # Build docker run command
    docker_cmd = [
        "run", "--rm", "-i",
        "--name", container_name,
        "--network", network,
    ]
    if user:
        docker_cmd += ["--user", user]
    for m in mounts:
        docker_cmd += ["-v", f"{m['source']}:{m['target']}:{m.get('mode', 'rw')}"]
    for k, v in env.items():
        docker_cmd += ["-e", f"{k}={v}"]
    if entrypoint:
        docker_cmd += ["--entrypoint", json.dumps(entrypoint)]
    docker_cmd.append(image)
    docker_cmd += command

    # Start container via a shell wrapper that persists the docker exit code.
    exitcode_path = state_dir / ".exitcode"
    quoted = " ".join(subprocess.list2cmdline([part]) for part in (["docker"] + docker_cmd))
    wrapper_cmd = f"{quoted}; code=$?; printf '%s\\n' \"$code\" > {str(exitcode_path)!r}; exit $code"

    stdout_f = open(stdout_path, "w")
    stderr_f = open(stderr_path, "w")
    stdin_f = open(stdin_file, "r") if stdin_file else subprocess.DEVNULL

    proc = subprocess.Popen(
        ["/bin/sh", "-lc", wrapper_cmd],
        stdin=stdin_f,
        stdout=stdout_f,
        stderr=stderr_f,
        start_new_session=True,
    )

    if stdin_f not in (None, subprocess.DEVNULL):
        stdin_f.close()

    handle = {
        "worker_id": worker_id,
        "task_id": worker_id,
        "phase": task_spec.get("phase", 0),
        "worker_category": "DOCKER",
        "adapter_name": task_spec.get("adapter_name", "docker-runtime"),
        "status": "RUNNING",
        "created_at": _iso_now(),
        "started_at": _iso_now(),
        "ended_at": None,
        "workdir": str(Path(task_spec.get("workdir", ".")).resolve()),
        "artifacts_dir": task_spec.get("artifacts_dir", str(state_dir)),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "events_path": str(events_path),
        "exit_code": None,
        "transport": {
            "container_name": container_name,
            "docker_cmd": docker_cmd,
            "local_pid": proc.pid,
            "pgid": os.getpgid(proc.pid),
        },
        "runtime": runtime,
    }
    handle_path.write_text(json.dumps(handle, indent=2) + "\n")
    return handle


def monitor(worker_handle: dict) -> dict:
    container_name = worker_handle.get("transport", {}).get("container_name")
    stdout_path = Path(worker_handle["stdout_path"])
    stderr_path = Path(worker_handle["stderr_path"])
    state_dir = stdout_path.parent
    handle_path = state_dir / "handle.json"

    stdout_text = stdout_path.read_text(errors="replace") if stdout_path.exists() else ""
    stderr_text = stderr_path.read_text(errors="replace") if stderr_path.exists() else ""

    status = worker_handle.get("status", "RUNNING")
    exit_code = worker_handle.get("exit_code")
    exitcode_file = state_dir / ".exitcode"
    if exit_code is None and exitcode_file.exists():
        try:
            exit_code = int(exitcode_file.read_text().strip())
        except ValueError:
            exit_code = None

    # Inspect container
    rc, inspect_out, _ = _docker(["inspect", container_name])
    if rc == 0:
        try:
            data = json.loads(inspect_out)
            if data and isinstance(data, list) and len(data) > 0:
                state = data[0].get("State", {})
                if state.get("Status") == "exited":
                    exit_code = state.get("ExitCode")
                    if exit_code == 0:
                        status = "SUCCEEDED"
                    else:
                        status = "FAILED"
                elif state.get("Status") in ("running", "restarting"):
                    status = "RUNNING"
                elif state.get("Status") == "dead":
                    status = "FAILED"
        except json.JSONDecodeError:
            pass
    else:
        # Container may not exist because `docker run --rm` already completed.
        # In that case, rely on the persisted exit code from the wrapper.
        if exit_code is not None:
            status = "SUCCEEDED" if exit_code == 0 else "FAILED"
        else:
            local_pid = worker_handle.get("transport", {}).get("local_pid")
            if local_pid:
                try:
                    proc = subprocess.Popen(["ps", "-p", str(local_pid)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    proc.communicate(timeout=2)
                    if proc.returncode != 0:
                        status = "FAILED"
                except Exception:
                    status = "FAILED"

    # Artifact discovery from mounted artifacts_dir
    artifacts_dir = Path(worker_handle.get("artifacts_dir", state_dir))
    artifacts: list[dict] = []
    if artifacts_dir.exists():
        for f in artifacts_dir.iterdir():
            if f.is_file():
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
        "raw_state": {"container_name": container_name, "exit_code": exit_code},
    }

    worker_handle["status"] = status
    worker_handle["exit_code"] = exit_code
    handle_path.write_text(json.dumps(worker_handle, indent=2) + "\n")
    return result


def kill(worker_handle: dict) -> bool:
    container_name = worker_handle.get("transport", {}).get("container_name")
    if not container_name:
        return False

    rc, _, _ = _docker(["stop", "-t", "5", container_name])
    if rc != 0:
        rc, _, _ = _docker(["kill", container_name])

    state_dir = Path(worker_handle["stdout_path"]).parent
    handle_path = state_dir / "handle.json"
    worker_handle["status"] = "KILLED"
    worker_handle["ended_at"] = _iso_now()
    handle_path.write_text(json.dumps(worker_handle, indent=2) + "\n")
    return rc == 0


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
    parser = argparse.ArgumentParser(description="Docker adapter for worker orchestrator")
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
