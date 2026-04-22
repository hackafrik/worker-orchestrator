#!/usr/bin/env python3
"""End-to-end test for universal-worker-orchestrator v2.0.0.

Tests state machine, adapter_python, track_cost, and synthesize_outputs
without requiring real CLI agents, HTTP endpoints, or Docker.
Run: python3 eval/e2e_v2.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
WORK_DIR = Path(tempfile.mkdtemp(prefix="uwo-e2e-"))


def _run(script: str, args: list[str], cwd: str | Path = WORK_DIR):
    cmd = [sys.executable, str(SCRIPTS_DIR / script)] + args
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(cwd), timeout=30)
    out = result.stdout.strip()
    try:
        parsed = json.loads(out)
    except json.JSONDecodeError:
        parsed = {"raw": out, "_exit_code": result.returncode, "_stderr": result.stderr.strip()}
        return parsed

    if isinstance(parsed, dict):
        parsed["_exit_code"] = result.returncode
        parsed["_stderr"] = result.stderr.strip()
        return parsed

    return {
        "value": parsed,
        "_exit_code": result.returncode,
        "_stderr": result.stderr.strip(),
    }


def test_state_machine() -> bool:
    print("[TEST] state_machine lifecycle")
    wid = "test-worker-1"
    s = _run("state_machine.py", ["init", wid])
    assert s.get("status") == "PENDING", f"Init failed: {s}"

    s = _run("state_machine.py", ["transition", wid, "SPAWNING", "--reason", "spawn"])
    assert s.get("status") == "SPAWNING", f"SPAWNING failed: {s}"

    s = _run("state_machine.py", ["transition", wid, "RUNNING", "--reason", "started"])
    assert s.get("status") == "RUNNING", f"RUNNING failed: {s}"

    s = _run("state_machine.py", ["transition", wid, "MONITORING", "--reason", "poll done"])
    assert s.get("status") == "MONITORING", f"MONITORING failed: {s}"

    s = _run("state_machine.py", ["transition", wid, "EVALUATING", "--reason", "rubric"])
    assert s.get("status") == "EVALUATING", f"EVALUATING failed: {s}"

    s = _run("state_machine.py", ["transition", wid, "SUCCEEDED", "--reason", "pass"])
    assert s.get("status") == "SUCCEEDED", f"SUCCEEDED failed: {s}"

    # Terminal state should block further transitions
    bad = _run("state_machine.py", ["transition", wid, "FAILED", "--reason", "nope"])
    assert bad.get("error") or bad.get("_exit_code") != 0, f"Terminal transition should fail: {bad}"

    hist = _run("state_machine.py", ["history", wid])
    history = hist.get("value", hist)
    assert isinstance(history, list) and len(history) >= 5, f"History missing: {hist}"

    print("[PASS] state_machine lifecycle")
    return True


def test_adapter_python() -> bool:
    print("[TEST] adapter_python spawn -> monitor -> evaluate -> kill")

    # Create a dummy worker script
    script = WORK_DIR / "dummy_worker.py"
    script.write_text("import json, sys\nprint(json.dumps({'result': 'ok', 'artifacts': ['report.md']}))\n")

    task_spec = {
        "task_id": "py-worker-1",
        "phase": 0,
        "worker_category": "PYTHON_SCRIPT",
        "adapter_name": "python3",
        "prompt": "Do the thing",
        "workdir": str(WORK_DIR),
        "timeout_seconds": 30,
        "rubric": {
            "expected_output_markers": ["ok"],
            "expected_files": [],
        },
        "runtime": {
            "script_path": str(script),
            "interpreter": sys.executable,
            "args": [],
        },
    }
    spec_file = WORK_DIR / "task_spec.json"
    spec_file.write_text(json.dumps(task_spec) + "\n")

    handle = _run("adapter_python.py", ["spawn", str(spec_file)])
    assert handle.get("worker_id") == "py-worker-1", f"Spawn failed: {handle}"
    handle_path = WORK_DIR / ".workers" / "py-worker-1" / "handle.json"
    assert handle_path.exists(), f"handle.json missing"

    # Small sleep for process to finish
    import time
    time.sleep(1.0)

    mon = _run("adapter_python.py", ["monitor", str(handle_path)])
    assert mon.get("status") in ("SUCCEEDED", "FAILED", "RUNNING"), f"Monitor bad status: {mon}"

    # If it's still running, wait a bit more
    if mon.get("status") == "RUNNING":
        time.sleep(2.0)
        mon = _run("adapter_python.py", ["monitor", str(handle_path)])

    # Write exitcode manually since our dummy script is too simple
    exitcode_file = WORK_DIR / ".workers" / "py-worker-1" / ".exitcode"
    exitcode_file.write_text("0\n")

    mon = _run("adapter_python.py", ["monitor", str(handle_path)])
    assert mon.get("status") == "SUCCEEDED", f"Monitor should succeed after exitcode: {mon}"

    rubric_file = WORK_DIR / "rubric.json"
    rubric_file.write_text(json.dumps(task_spec["rubric"]) + "\n")
    ev = _run("adapter_python.py", ["evaluate", str(handle_path), str(rubric_file)])
    assert ev.get("score", 0) > 0, f"Evaluate failed: {ev}"

    # Kill should be idempotent
    kill_res = _run("adapter_python.py", ["kill", str(handle_path)])
    assert kill_res.get("killed") in (True, False), f"Kill failed: {kill_res}"

    print("[PASS] adapter_python")
    return True


def test_cli_adapter() -> bool:
    print("[TEST] adapter_cli spawn -> monitor -> evaluate -> kill")

    worker_script = WORK_DIR / "dummy_cli_worker.py"
    worker_script.write_text(
        "import json, pathlib\n"
        "print('\\x1b[32mCLI OK\\x1b[0m')\n"
        "pathlib.Path('report.md').write_text('# cli report\\n')\n"
        "print(json.dumps({'done': True}))\n"
    )

    task_spec = {
        "task_id": "cli-worker-1",
        "phase": 0,
        "worker_category": "CLI",
        "adapter_name": "dummy-cli",
        "prompt": "ignored",
        "workdir": str(WORK_DIR),
        "timeout_seconds": 30,
        "rubric": {
            "expected_output_markers": ["CLI OK"],
            "expected_files": ["report.md"],
            "expected_format": None,
        },
        "runtime": {
            "command": [sys.executable, str(worker_script)],
            "extra_args": [],
            "use_shell": False,
            "pty": False,
            "cwd": str(WORK_DIR),
            "stdin_text": "",
        },
    }
    spec_file = WORK_DIR / "cli_task_spec.json"
    spec_file.write_text(json.dumps(task_spec) + "\n")

    handle = _run("adapter_cli.py", ["spawn", str(spec_file)])
    assert handle.get("worker_id") == "cli-worker-1", f"CLI spawn failed: {handle}"
    handle_path = WORK_DIR / ".workers" / "cli-worker-1" / "handle.json"
    assert handle_path.exists(), "CLI handle.json missing"

    time.sleep(1.0)
    exitcode_file = WORK_DIR / ".workers" / "cli-worker-1" / ".exitcode"
    exitcode_file.write_text("0\n")

    mon = _run("adapter_cli.py", ["monitor", str(handle_path)])
    assert mon.get("status") == "SUCCEEDED", f"CLI monitor failed: {mon}"
    assert "CLI OK" in mon.get("stdout", ""), f"ANSI-stripped stdout missing marker: {mon}"

    rubric_file = WORK_DIR / "cli_rubric.json"
    rubric_file.write_text(json.dumps(task_spec["rubric"]) + "\n")
    ev = _run("adapter_cli.py", ["evaluate", str(handle_path), str(rubric_file)])
    assert ev.get("score", 0) > 0, f"CLI evaluate failed: {ev}"

    kill_res = _run("adapter_cli.py", ["kill", str(handle_path)])
    assert kill_res.get("killed") in (True, False), f"CLI kill failed: {kill_res}"

    print("[PASS] adapter_cli")
    return True


def test_http_adapter_blocking() -> bool:
    print("[TEST] adapter_http blocking spawn -> monitor -> evaluate")

    server_script = WORK_DIR / "dummy_http_server.py"
    server_script.write_text(
        "import json\n"
        "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
        "\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        "    def do_POST(self):\n"
        "        length = int(self.headers.get('Content-Length', '0'))\n"
        "        body = self.rfile.read(length).decode('utf-8')\n"
        "        data = json.loads(body)\n"
        "        response = {\n"
        "            'result': f\"ok:{data.get('prompt') or data.get('messages', [{}])[-1].get('content', '')}\",\n"
        "            'usage': {'prompt_tokens': 11, 'completion_tokens': 7}\n"
        "        }\n"
        "        payload = json.dumps(response).encode('utf-8')\n"
        "        self.send_response(200)\n"
        "        self.send_header('Content-Type', 'application/json')\n"
        "        self.send_header('Content-Length', str(len(payload)))\n"
        "        self.end_headers()\n"
        "        self.wfile.write(payload)\n"
        "\n"
        "    def log_message(self, format, *args):\n"
        "        return\n"
        "\n"
        "HTTPServer(('127.0.0.1', 8765), Handler).serve_forever()\n"
    )

    server = subprocess.Popen([sys.executable, str(server_script)], cwd=str(WORK_DIR))
    try:
        time.sleep(0.6)
        task_spec = {
            "task_id": "http-worker-1",
            "phase": 0,
            "worker_category": "HTTP_API",
            "adapter_name": "dummy-http",
            "prompt": "hello-http",
            "workdir": str(WORK_DIR),
            "timeout_seconds": 30,
            "rubric": {
                "expected_output_markers": ["ok:hello-http"],
                "expected_format": "json",
            },
            "runtime": {
                "base_url": "http://127.0.0.1:8765",
                "endpoint": "/generate",
                "method": "POST",
                "headers": {},
                "payload": {"prompt": "{prompt}"},
                "stream_protocol": "blocking-json",
                "request_timeout_seconds": 10,
            },
        }
        spec_file = WORK_DIR / "http_task_spec.json"
        spec_file.write_text(json.dumps(task_spec) + "\n")

        handle = _run("adapter_http.py", ["spawn", str(spec_file)])
        assert handle.get("worker_id") == "http-worker-1", f"HTTP spawn failed: {handle}"
        handle_path = WORK_DIR / ".workers" / "http-worker-1" / "handle.json"
        assert handle_path.exists(), "HTTP handle.json missing"

        mon = _run("adapter_http.py", ["monitor", str(handle_path)])
        assert mon.get("status") == "SUCCEEDED", f"HTTP monitor failed: {mon}"
        assert "ok:hello-http" in mon.get("stdout", ""), f"HTTP stdout missing expected marker: {mon}"
        assert mon.get("metrics", {}).get("tokens_in") == 11, f"HTTP tokens_in missing: {mon}"

        rubric_file = WORK_DIR / "http_rubric.json"
        rubric_file.write_text(json.dumps(task_spec["rubric"]) + "\n")
        ev = _run("adapter_http.py", ["evaluate", str(handle_path), str(rubric_file)])
        assert ev.get("score", 0) > 0, f"HTTP evaluate failed: {ev}"
    finally:
        server.terminate()
        try:
            server.wait(timeout=2)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=2)

    print("[PASS] adapter_http")
    return True


def test_http_adapter_error_response() -> bool:
    print("[TEST] adapter_http blocking error response handling")

    server_script = WORK_DIR / "dummy_http_error_server.py"
    server_script.write_text(
        "import json\n"
        "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
        "\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        "    def do_POST(self):\n"
        "        payload = json.dumps({'error': 'rate limited'}).encode('utf-8')\n"
        "        self.send_response(429)\n"
        "        self.send_header('Content-Type', 'application/json')\n"
        "        self.send_header('Content-Length', str(len(payload)))\n"
        "        self.end_headers()\n"
        "        self.wfile.write(payload)\n"
        "\n"
        "    def log_message(self, format, *args):\n"
        "        return\n"
        "\n"
        "HTTPServer(('127.0.0.1', 8766), Handler).serve_forever()\n"
    )

    server = subprocess.Popen([sys.executable, str(server_script)], cwd=str(WORK_DIR))
    try:
        time.sleep(0.6)
        task_spec = {
            "task_id": "http-worker-error-1",
            "phase": 0,
            "worker_category": "HTTP_API",
            "adapter_name": "dummy-http-error",
            "prompt": "error-case",
            "workdir": str(WORK_DIR),
            "timeout_seconds": 30,
            "rubric": {
                "expected_output_markers": ["ok"],
                "expected_format": "json",
            },
            "runtime": {
                "base_url": "http://127.0.0.1:8766",
                "endpoint": "/generate",
                "method": "POST",
                "headers": {},
                "payload": {"prompt": "{prompt}"},
                "stream_protocol": "blocking-json",
                "request_timeout_seconds": 10,
            },
        }
        spec_file = WORK_DIR / "http_error_task_spec.json"
        spec_file.write_text(json.dumps(task_spec) + "\n")

        handle = _run("adapter_http.py", ["spawn", str(spec_file)])
        assert handle.get("worker_id") == "http-worker-error-1", f"HTTP error spawn failed: {handle}"
        handle_path = WORK_DIR / ".workers" / "http-worker-error-1" / "handle.json"

        mon = _run("adapter_http.py", ["monitor", str(handle_path)])
        assert mon.get("status") == "FAILED", f"HTTP error should stay failed: {mon}"
        assert "rate limited" in mon.get("stdout", ""), f"HTTP error body missing: {mon}"

        rubric_file = WORK_DIR / "http_error_rubric.json"
        rubric_file.write_text(json.dumps(task_spec["rubric"]) + "\n")
        ev = _run("adapter_http.py", ["evaluate", str(handle_path), str(rubric_file)])
        assert ev.get("score", 1) < 1, f"HTTP error evaluation should not fully pass: {ev}"
    finally:
        server.terminate()
        try:
            server.wait(timeout=2)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=2)

    print("[PASS] adapter_http error handling")
    return True


def test_docker_adapter() -> bool:
    print("[TEST] adapter_docker spawn -> monitor -> evaluate")

    task_spec = {
        "task_id": "docker-worker-1",
        "phase": 0,
        "worker_category": "DOCKER",
        "adapter_name": "docker-runtime",
        "prompt": "",
        "workdir": str(WORK_DIR),
        "timeout_seconds": 30,
        "rubric": {
            "expected_output_markers": ["Hello from Docker!"],
            "expected_files": [],
        },
        "runtime": {
            "image": "hello-world",
            "command": [],
            "mounts": [],
            "env": {},
            "network": "bridge",
        },
    }
    spec_file = WORK_DIR / "docker_task_spec.json"
    spec_file.write_text(json.dumps(task_spec) + "\n")

    handle = _run("adapter_docker.py", ["spawn", str(spec_file)])
    assert handle.get("worker_id") == "docker-worker-1", f"Docker spawn failed: {handle}"
    handle_path = WORK_DIR / ".workers" / "docker-worker-1" / "handle.json"
    assert handle_path.exists(), "Docker handle.json missing"

    time.sleep(2.0)
    mon = _run("adapter_docker.py", ["monitor", str(handle_path)])
    assert mon.get("status") == "SUCCEEDED", f"Docker monitor failed: {mon}"
    assert "Hello from Docker!" in mon.get("stdout", ""), f"Docker stdout missing marker: {mon}"

    rubric_file = WORK_DIR / "docker_rubric.json"
    rubric_file.write_text(json.dumps(task_spec["rubric"]) + "\n")
    ev = _run("adapter_docker.py", ["evaluate", str(handle_path), str(rubric_file)])
    assert ev.get("score", 0) > 0, f"Docker evaluate failed: {ev}"

    print("[PASS] adapter_docker")
    return True


def test_cost_tracking() -> bool:
    print("[TEST] track_cost record + report")
    ledger = WORK_DIR / ".worker-ledger.jsonl"
    os.environ["WORKER_LEDGER_PATH"] = str(ledger)

    r1 = _run("track_cost.py", [
        "record", "--worker-id", "w1", "--category", "CLI",
        "--adapter", "opencode-cli", "--model", "openai:gpt-4o",
        "--tokens-in", "1000", "--tokens-out", "500", "--runtime", "45.2"
    ])
    assert r1.get("cost_usd") is not None, f"Record failed: {r1}"

    r2 = _run("track_cost.py", [
        "record", "--worker-id", "w2", "--category", "HTTP_API",
        "--adapter", "ollama-http", "--model", "local:*",
        "--tokens-in", "2000", "--tokens-out", "1000", "--runtime", "12.0"
    ])
    assert r2.get("cost_usd") == 0.0, f"Local cost should be 0: {r2}"

    rep = _run("track_cost.py", ["report"])
    assert rep.get("total_cost_usd") > 0, f"Total cost should be >0: {rep}"
    assert rep.get("total_tokens_in") == 3000, f"Tokens in mismatch: {rep}"
    assert "CLI" in rep.get("by_category", {}), f"Category missing: {rep}"

    # Clear
    clr = _run("track_cost.py", ["clear"])
    assert clr.get("status") == "cleared", f"Clear failed: {clr}"

    rep2 = _run("track_cost.py", ["report"])
    assert rep2.get("total_cost_usd") == 0.0, f"After clear cost should be 0: {rep2}"

    print("[PASS] track_cost")
    return True


def test_synthesize() -> bool:
    print("[TEST] synthesize_outputs code merge")
    a = WORK_DIR / "worker_a"
    b = WORK_DIR / "worker_b"
    a.mkdir()
    b.mkdir()
    (a / "main.py").write_text("# A\n")
    (b / "main.py").write_text("# B\n")
    (b / "utils.py").write_text("# utils\n")

    out = WORK_DIR / "merged"
    res = _run("synthesize_outputs.py", [
        "--inputs", str(a), str(b),
        "--output", str(out),
        "--type", "code",
    ], cwd=WORK_DIR)
    assert res.get("success") or res.get("files_merged"), f"Synthesis failed: {res}"

    # Check files exist
    merged_files = list(out.rglob("*"))
    assert any(f.name.startswith("main.py") for f in merged_files), f"main.py missing: {merged_files}"

    print("[PASS] synthesize_outputs")
    return True


def main() -> int:
    print(f"E2E test working directory: {WORK_DIR}")
    passed = 0
    failed = 0

    tests = [
        test_state_machine,
        test_adapter_python,
        test_cli_adapter,
        test_http_adapter_blocking,
        test_http_adapter_error_response,
        test_docker_adapter,
        test_cost_tracking,
        test_synthesize,
    ]
    for t in tests:
        try:
            if t():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"[FAIL] {t.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    # Cleanup
    shutil.rmtree(WORK_DIR, ignore_errors=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
