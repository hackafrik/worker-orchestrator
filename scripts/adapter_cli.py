#!/usr/bin/env python3
"""Adapter for CLI worker category.

Implements spawn/monitor/kill/evaluate for local subprocess-based AI agents.
Pure stdlib. No external dependencies.
"""

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def _strip_ansi(text: str) -> str:
    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    return ansi_escape.sub("", text)


def _ensure_dirs(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def spawn(task_spec: dict) -> dict:
    """Spawn a CLI worker and return a canonical worker_handle."""
    runtime = task_spec.get("runtime", {})
    command = runtime.get("command", [])
    stdin_text = runtime.get("stdin_text", task_spec.get("prompt", ""))
    use_shell = runtime.get("use_shell", False)
    pty = runtime.get("pty", False)
    cwd = runtime.get("cwd", task_spec.get("workdir", "."))
    extra_args = runtime.get("extra_args", [])
    env = task_spec.get("env", {})
    timeout = task_spec.get("timeout_seconds", 300)

    # Build full command
    full_cmd = list(command) + list(extra_args)
    if not full_cmd:
        raise ValueError("CLI adapter requires runtime.command")

    # State directory
    worker_id = task_spec["task_id"]  # caller must ensure unique
    state_dir = Path(task_spec.get("workdir", ".")) / ".workers" / worker_id
    _ensure_dirs(state_dir)

    stdout_path = state_dir / "stdout.log"
    stderr_path = state_dir / "stderr.log"
    events_path = state_dir / "events.jsonl"
    handle_path = state_dir / "handle.json"

    # Write prompt to stdin file if needed
    stdin_source = None
    if stdin_text:
        stdin_file = state_dir / "stdin.txt"
        stdin_file.write_text(stdin_text)
        stdin_source = open(stdin_file, "r")

    # Environment
    merged_env = {**os.environ, **env}

    # Spawn in new process group for clean tree-kill
    if use_shell:
        cmd_str = " ".join(full_cmd) if isinstance(full_cmd, list) else str(full_cmd)
        proc = subprocess.Popen(
            cmd_str,
            shell=True,
            stdin=stdin_source,
            stdout=open(stdout_path, "w"),
            stderr=open(stderr_path, "w"),
            cwd=cwd,
            env=merged_env,
            start_new_session=True,
        )
    else:
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
        "worker_category": "CLI",
        "adapter_name": task_spec.get("adapter_name", "unknown-cli"),
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
            "pty": pty,
            "invocation": full_cmd,
        },
        "runtime": runtime,
    }

    handle_path.write_text(json.dumps(handle, indent=2) + "\n")
    return handle


def monitor(worker_handle: dict) -> dict:
    """Poll a CLI worker and return a canonical monitor_result."""
    pid = worker_handle.get("transport", {}).get("pid")
    stdout_path = Path(worker_handle["stdout_path"])
    stderr_path = Path(worker_handle["stderr_path"])
    state_dir = stdout_path.parent
    handle_path = state_dir / "handle.json"

    stdout_text = ""
    stderr_text = ""
    if stdout_path.exists():
        stdout_text = _strip_ansi(stdout_path.read_text(errors="replace"))
    if stderr_path.exists():
        stderr_text = _strip_ansi(stderr_path.read_text(errors="replace"))

    exit_code = None
    status = worker_handle.get("status", "RUNNING")

    if pid:
        try:
            proc = subprocess.Popen(["ps", "-p", str(pid)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            proc.communicate(timeout=2)
            if proc.returncode != 0:
                # Process exited — try to reap exit code
                # Since we detached with start_new_session, we can't easily call .wait()
                # Best effort: check if stdout/stderr files were closed / final sizes stabilized
                # For robustness, we check a sentinel file written by a wrapper, or fall back to FAILED
                status = "FAILED"  # conservative until proven otherwise
                # Look for a .exitcode file written by a thin wrapper if present
                exitcode_file = state_dir / ".exitcode"
                if exitcode_file.exists():
                    try:
                        exit_code = int(exitcode_file.read_text().strip())
                        status = "SUCCEEDED" if exit_code == 0 else "FAILED"
                    except ValueError:
                        pass
        except Exception:
            pass

    # If we already have an exit_code in handle, use it
    if worker_handle.get("exit_code") is not None:
        exit_code = worker_handle["exit_code"]
        status = "SUCCEEDED" if exit_code == 0 else "FAILED"

    # Artifact discovery
    artifacts_dir = Path(worker_handle.get("artifacts_dir", state_dir))
    artifacts: list[dict] = []
    if artifacts_dir.exists():
        for f in artifacts_dir.iterdir():
            if f.is_file() and f.name not in ("stdout.log", "stderr.log", "handle.json", "events.jsonl", "stdin.txt", ".exitcode"):
                kind = "other"
                if f.suffix == ".md":
                    kind = "report"
                elif f.suffix in (".diff", ".patch"):
                    kind = "patch"
                elif f.suffix == ".json":
                    kind = "json"
                artifacts.append({
                    "path": str(f),
                    "kind": kind,
                    "size_bytes": f.stat().st_size,
                })

    # Runtime
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
        "raw_state": {
            "pid": pid,
            "stdout_size": stdout_path.stat().st_size if stdout_path.exists() else 0,
            "stderr_size": stderr_path.stat().st_size if stderr_path.exists() else 0,
        },
    }

    # Persist updated handle
    worker_handle["status"] = status
    worker_handle["exit_code"] = exit_code
    handle_path.write_text(json.dumps(worker_handle, indent=2) + "\n")
    return result


def kill(worker_handle: dict) -> bool:
    """Best-effort kill of a CLI worker process tree."""
    pgid = worker_handle.get("transport", {}).get("pgid")
    pid = worker_handle.get("transport", {}).get("pid")
    state_dir = Path(worker_handle["stdout_path"]).parent
    handle_path = state_dir / "handle.json"

    target = pgid if pgid else pid
    if not target:
        return False

    try:
        os.killpg(target, signal.SIGTERM)
        # Grace period
        time.sleep(1.0)
        # Check if still alive
        try:
            os.killpg(target, 0)  # signal 0 is existence check
            # Still alive — force kill
            os.killpg(target, signal.SIGKILL)
        except ProcessLookupError:
            pass  # Already gone
    except ProcessLookupError:
        pass
    except PermissionError:
        return False

    worker_handle["status"] = "KILLED"
    worker_handle["ended_at"] = _iso_now()
    handle_path.write_text(json.dumps(worker_handle, indent=2) + "\n")
    return True


def evaluate(worker_handle: dict, rubric: dict) -> dict:
    """Run a rubric-based evaluation against a completed CLI worker."""
    monitor_result = monitor(worker_handle)
    stdout = monitor_result["stdout"]
    artifacts = monitor_result["artifacts"]

    score = 0.0
    breakdown: dict[str, Any] = {}
    evidence: dict[str, Any] = {"output_markers": [], "artifact_paths": []}

    # Presence check
    expected_markers = rubric.get("expected_output_markers", [])
    found_markers = [m for m in expected_markers if m in stdout]
    breakdown["presence"] = "pass" if len(found_markers) == len(expected_markers) else "fail"
    evidence["output_markers"] = found_markers

    # Artifact compliance
    expected_files = rubric.get("expected_files", [])
    found_files = [a["path"] for a in artifacts if Path(a["path"]).name in expected_files]
    breakdown["artifact_compliance"] = "pass" if len(found_files) >= len(expected_files) else "fail"
    evidence["artifact_paths"] = found_files

    # Format compliance — naive markdown/json/diff check
    fmt = rubric.get("expected_format")
    if fmt == "markdown" and stdout.strip().startswith("#"):
        breakdown["format_compliance"] = "pass"
    elif fmt == "json":
        try:
            json.loads(stdout)
            breakdown["format_compliance"] = "pass"
        except json.JSONDecodeError:
            breakdown["format_compliance"] = "fail"
    else:
        breakdown["format_compliance"] = "not_checked"

    # Scoring: simple pass/fail weighted average
    checks = [v for v in breakdown.values() if v in ("pass", "fail")]
    if checks:
        score = sum(1 for c in checks if c == "pass") / len(checks)

    return {
        "score": round(score, 2),
        "feedback": f"Passed {sum(1 for c in checks if c == 'pass')}/{len(checks)} checks.",
        "rubric_breakdown": breakdown,
        "evidence": evidence,
    }


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _cli() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="CLI adapter for worker orchestrator")
    sub = parser.add_subparsers(dest="cmd")

    spawn_p = sub.add_parser("spawn")
    spawn_p.add_argument("task_spec", help="Path to task_spec JSON file")

    monitor_p = sub.add_parser("monitor")
    monitor_p.add_argument("handle", help="Path to worker_handle JSON file")

    kill_p = sub.add_parser("kill")
    kill_p.add_argument("handle", help="Path to worker_handle JSON file")

    eval_p = sub.add_parser("evaluate")
    eval_p.add_argument("handle", help="Path to worker_handle JSON file")
    eval_p.add_argument("rubric", help="Path to rubric JSON file")

    args = parser.parse_args()

    if args.cmd == "spawn":
        spec = json.loads(Path(args.task_spec).read_text())
        handle = spawn(spec)
        print(json.dumps(handle, indent=2))
        return 0
    elif args.cmd == "monitor":
        handle = json.loads(Path(args.handle).read_text())
        result = monitor(handle)
        print(json.dumps(result, indent=2))
        return 0
    elif args.cmd == "kill":
        handle = json.loads(Path(args.handle).read_text())
        ok = kill(handle)
        print(json.dumps({"killed": ok}))
        return 0
    elif args.cmd == "evaluate":
        handle = json.loads(Path(args.handle).read_text())
        rubric = json.loads(Path(args.rubric).read_text())
        result = evaluate(handle, rubric)
        print(json.dumps(result, indent=2))
        return 0
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(_cli())
