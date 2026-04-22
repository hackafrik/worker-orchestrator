#!/usr/bin/env python3
"""Adapter for HTTP API worker category.

Supports streaming (SSE, NDJSON) and blocking JSON protocols.
Pure stdlib fallback; uses urllib. Optional requests if available.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _ensure_dirs(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _http_post(url: str, payload: dict, headers: dict, timeout: int) -> tuple[int, str]:
    """Blocking POST. Returns (status_code, response_body)."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


def _http_get(url: str, headers: dict, timeout: int) -> tuple[int, str]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", errors="replace")


def _write_event(events_path: Path, event: dict) -> None:
    with open(events_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def spawn(task_spec: dict) -> dict:
    runtime = task_spec.get("runtime", {})
    base_url = runtime["base_url"]
    endpoint = runtime["endpoint"]
    method = runtime.get("method", "POST")
    headers = runtime.get("headers", {})
    payload = runtime.get("payload", {})
    stream_protocol = runtime.get("stream_protocol", "blocking-json")
    timeout = runtime.get("request_timeout_seconds", task_spec.get("timeout_seconds", 300))

    # Inject prompt into payload if placeholder exists
    prompt = task_spec.get("prompt", "")
    if "{prompt}" in json.dumps(payload):
        payload = json.loads(json.dumps(payload).replace("{prompt}", prompt))
    elif "prompt" in payload and payload["prompt"] == "":
        payload["prompt"] = prompt
    elif "messages" in payload and isinstance(payload["messages"], list):
        # OpenAI-compatible chat format
        for msg in payload["messages"]:
            if isinstance(msg, dict) and msg.get("content") in ("", "{prompt}"):
                msg["content"] = prompt

    worker_id = task_spec["task_id"]
    state_dir = Path(task_spec.get("workdir", ".")) / ".workers" / worker_id
    _ensure_dirs(state_dir)

    stdout_path = state_dir / "stdout.log"
    stderr_path = state_dir / "stderr.log"
    events_path = state_dir / "events.jsonl"
    handle_path = state_dir / "handle.json"

    # Persist request for reference
    request_file = state_dir / "request.json"
    request_file.write_text(json.dumps({"url": f"{base_url}{endpoint}", "method": method, "headers": headers, "payload": payload}, indent=2) + "\n")

    # For blocking mode, execute immediately and persist the response.
    # For streaming mode, spawn a background process/thread to consume the stream.
    transport: dict[str, Any]
    status = "RUNNING"
    exit_code = None
    ended_at = None
    if stream_protocol in ("sse", "ndjson"):
        # Background stream consumer via subprocess so it survives our return
        stream_script = state_dir / "_stream_consumer.py"
        stream_script.write_text(_STREAM_CONSUMER_TEMPLATE)
        # Write payload to temp file
        payload_file = state_dir / "_payload.json"
        payload_file.write_text(json.dumps(payload))

        import subprocess
        proc = subprocess.Popen(
            [sys.executable, str(stream_script), f"{base_url}{endpoint}", json.dumps(headers), str(stdout_path), str(events_path), stream_protocol, str(timeout)],
            stdout=open(stderr_path, "w"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        transport = {"request_id": None, "stream_pid": proc.pid, "pgid": os.getpgid(proc.pid)}
    else:
        # Blocking: execute now and persist canonical output files.
        url = f"{base_url}{endpoint}"
        result_file = state_dir / "_result.json"
        try:
            if method.upper() == "POST":
                status_code, body = _http_post(url, payload, headers, timeout)
            elif method.upper() == "GET":
                status_code, body = _http_get(url, headers, timeout)
            else:
                raise ValueError(f"Unsupported HTTP method for adapter_http: {method}")

            stdout_path.write_text(body)
            result_file.write_text(body)
            _write_event(events_path, {
                "type": "http_response",
                "status_code": status_code,
                "at": _iso_now(),
            })
            transport = {"request_id": None, "stream_pid": None, "pgid": None, "status_code": status_code}
            exit_code = 0 if 200 <= status_code < 300 else 1
            status = "SUCCEEDED" if exit_code == 0 else "FAILED"
        except Exception as e:
            stderr_path.write_text(str(e) + "\n")
            _write_event(events_path, {
                "type": "http_error",
                "error": str(e),
                "at": _iso_now(),
            })
            transport = {"request_id": None, "stream_pid": None, "pgid": None, "status_code": None}
            exit_code = 1
            status = "FAILED"
        ended_at = _iso_now()

    handle = {
        "worker_id": worker_id,
        "task_id": worker_id,
        "phase": task_spec.get("phase", 0),
        "worker_category": "HTTP_API",
        "adapter_name": task_spec.get("adapter_name", "unknown-http"),
        "status": status,
        "created_at": _iso_now(),
        "started_at": _iso_now(),
        "ended_at": ended_at,
        "workdir": str(Path(task_spec.get("workdir", ".")).resolve()),
        "artifacts_dir": task_spec.get("artifacts_dir", str(state_dir)),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "events_path": str(events_path),
        "exit_code": exit_code,
        "transport": transport,
        "runtime": runtime,
    }
    handle_path.write_text(json.dumps(handle, indent=2) + "\n")
    return handle


def monitor(worker_handle: dict) -> dict:
    worker_id = worker_handle["worker_id"]
    stdout_path = Path(worker_handle["stdout_path"])
    stderr_path = Path(worker_handle["stderr_path"])
    events_path = Path(worker_handle["events_path"])
    state_dir = stdout_path.parent
    handle_path = state_dir / "handle.json"

    stdout_text = ""
    stderr_text = ""
    if stdout_path.exists():
        stdout_text = stdout_path.read_text(errors="replace")
    if stderr_path.exists():
        stderr_text = stderr_path.read_text(errors="replace")

    # Determine status
    status = worker_handle.get("status", "RUNNING")
    stream_pid = worker_handle.get("transport", {}).get("stream_pid")
    exit_code = worker_handle.get("exit_code")

    if stream_pid:
        import subprocess
        try:
            proc = subprocess.Popen(["ps", "-p", str(stream_pid)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            proc.communicate(timeout=2)
            if proc.returncode != 0:
                status = "SUCCEEDED"
        except Exception:
            status = "SUCCEEDED"
    else:
        # Blocking mode: preserve spawn-time terminal outcome instead of blindly
        # treating any persisted result file as success.
        result_file = state_dir / "_result.json"
        if exit_code is not None:
            status = "SUCCEEDED" if exit_code == 0 else "FAILED"
        elif result_file.exists():
            status = worker_handle.get("status", status)

    # Metrics extraction
    metrics = {"runtime_seconds": 0.0, "cost_usd": None, "tokens_in": None, "tokens_out": None}
    if stdout_text:
        try:
            # Try parsing as JSON for usage info
            data = json.loads(stdout_text)
            usage = data.get("usage", {})
            if "prompt_tokens" in usage:
                metrics["tokens_in"] = usage["prompt_tokens"]
                metrics["tokens_out"] = usage.get("completion_tokens")
            elif "input_tokens" in usage:
                metrics["tokens_in"] = usage["input_tokens"]
                metrics["tokens_out"] = usage.get("output_tokens")
            elif "prompt_eval_count" in data:
                metrics["tokens_in"] = data.get("prompt_eval_count")
                metrics["tokens_out"] = data.get("eval_count")
        except json.JSONDecodeError:
            pass

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
    metrics["runtime_seconds"] = round(runtime_seconds, 2)

    result = {
        "status": status,
        "stdout": stdout_text,
        "stderr": stderr_text,
        "artifacts": [],
        "metrics": metrics,
        "heartbeat_at": _iso_now(),
        "raw_state": {"stream_pid": stream_pid},
    }

    worker_handle["status"] = status
    handle_path.write_text(json.dumps(worker_handle, indent=2) + "\n")
    return result


def kill(worker_handle: dict) -> bool:
    stream_pid = worker_handle.get("transport", {}).get("stream_pid")
    if not stream_pid:
        return False
    try:
        import os
        import signal
        os.kill(stream_pid, signal.SIGTERM)
        time.sleep(0.5)
        try:
            os.kill(stream_pid, 0)
            os.kill(stream_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    except Exception:
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

    score = 0.0
    breakdown: dict[str, Any] = {}
    evidence: dict[str, Any] = {"output_markers": [], "artifact_paths": []}

    expected_markers = rubric.get("expected_output_markers", [])
    found_markers = [m for m in expected_markers if m in stdout]
    breakdown["presence"] = "pass" if len(found_markers) == len(expected_markers) else "fail"
    evidence["output_markers"] = found_markers

    fmt = rubric.get("expected_format")
    if fmt == "json":
        try:
            json.loads(stdout)
            breakdown["format_compliance"] = "pass"
        except json.JSONDecodeError:
            breakdown["format_compliance"] = "fail"
    else:
        breakdown["format_compliance"] = "not_checked"

    checks = [v for v in breakdown.values() if v in ("pass", "fail")]
    if checks:
        score = sum(1 for c in checks if c == "pass") / len(checks)

    return {
        "score": round(score, 2),
        "feedback": f"Passed {sum(1 for c in checks if c == 'pass')}/{len(checks)} checks.",
        "rubric_breakdown": breakdown,
        "evidence": evidence,
    }


_STREAM_CONSUMER_TEMPLATE = '''
import json
import sys
import urllib.request

url, headers_json, stdout_path, events_path, protocol, timeout = sys.argv[1:]
headers = json.loads(headers_json)
timeout = int(timeout)

req = urllib.request.Request(url, data=sys.stdin.read().encode(), headers={"Content-Type": "application/json", **headers}, method="POST")
with urllib.request.urlopen(req, timeout=timeout) as resp:
    if protocol == "sse":
        for line in resp:
            line = line.decode("utf-8", errors="replace").strip()
            if line.startswith("data: "):
                data = line[6:]
                with open(stdout_path, "a", encoding="utf-8") as f:
                    f.write(data + "\\n")
                with open(events_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"type": "chunk", "data": data}, ensure_ascii=False) + "\\n")
    elif protocol == "ndjson":
        for line in resp:
            line = line.decode("utf-8", errors="replace").strip()
            if line:
                with open(stdout_path, "a", encoding="utf-8") as f:
                    f.write(line + "\\n")
                with open(events_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"type": "chunk", "data": line}, ensure_ascii=False) + "\\n")
'''


def _cli() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="HTTP API adapter for worker orchestrator")
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
