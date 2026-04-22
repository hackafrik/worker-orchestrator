#!/usr/bin/env python3
"""Workflow-level HTTP parity test between worker-orchestrator v1.4 and v2.

Compares:
- v1.4 helper-based judgment over HTTP-like logs/output artifacts
- v2 full run_orchestrator + adapter_http path

Scenarios:
- blocking JSON success
- blocking JSON rate-limit / 429 failure
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

V1_DIR = Path("/home/hackafrik/worker-orchestrator")
V2_DIR = Path("/home/hackafrik/worker-orchestrator-v2")
WORK_DIR = Path(tempfile.mkdtemp(prefix="worker-orchestrator-http-parity-"))

SUCCESS_SERVER = """#!/usr/bin/env python3
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get('Content-Length', '0'))
        body = self.rfile.read(length).decode('utf-8')
        data = json.loads(body)
        response = {
            'result': f"ok:{data.get('prompt', '')}",
            'details': 'Task complete',
            'usage': {'prompt_tokens': 9, 'completion_tokens': 4}
        }
        payload = json.dumps(response).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        return

HTTPServer(('127.0.0.1', 8775), Handler).serve_forever()
"""

ERROR_SERVER = """#!/usr/bin/env python3
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        payload = json.dumps({'error': 'usage_limit_reached', 'details': 'rate limit detected resets_in_seconds: 1300'}).encode('utf-8')
        self.send_response(429)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        return

HTTPServer(('127.0.0.1', 8776), Handler).serve_forever()
"""

OUTPUT_CRITERIA = {
    "expected_keywords": ["ok", "Task complete"],
    "required_sections": ["result", "details"],
    "forbidden_patterns": ["TODO", "FIXME", "PLACEHOLDER"],
}


def _run(cmd: list[str], cwd: Path, timeout: int = 120) -> dict:
    result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
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


def run_v1_helper_flow(scenario_dir: Path, expect_success: bool) -> dict:
    log_path = scenario_dir / "http_worker.log"
    output_path = scenario_dir / "http_response.json"

    log_eval = _run(
        [sys.executable, str(V1_DIR / "scripts/evaluate_worker_logs.py"), "--log", str(log_path), "--provider", "http"],
        cwd=scenario_dir,
    )
    output_eval = _run(
        [
            sys.executable,
            str(V1_DIR / "scripts/evaluate_output.py"),
            "--file",
            str(output_path),
            "--criteria",
            json.dumps(OUTPUT_CRITERIA),
        ],
        cwd=scenario_dir,
    )

    output_passed = bool(output_eval["stdout"].get("passed"))
    log_score = int(log_eval["stdout"].get("score", 0))
    derived_status = "SUCCEEDED" if (output_passed and log_score >= 4) else "FAILED"
    return {
        "log_eval": log_eval,
        "output_eval": output_eval,
        "output_exists": output_path.exists(),
        "derived_status": derived_status,
        "expected_status": "SUCCEEDED" if expect_success else "FAILED",
        "passed": derived_status == ("SUCCEEDED" if expect_success else "FAILED"),
    }


def run_v2_orchestrator_flow(scenario_dir: Path, port: int, task_id: str, expect_success: bool) -> dict:
    task_spec = {
        "task_id": task_id,
        "phase": 0,
        "worker_category": "HTTP_API",
        "adapter_name": "dummy-http",
        "prompt": "hello-http",
        "workdir": str(scenario_dir),
        "timeout_seconds": 20,
        "poll_interval_seconds": 1,
        "pass_threshold": 1.0,
        "rubric": {
            "expected_output_markers": ["ok:hello-http"] if expect_success else ["ok:hello-http"],
            "expected_format": "json",
        },
        "runtime": {
            "base_url": f"http://127.0.0.1:{port}",
            "endpoint": "/generate",
            "method": "POST",
            "headers": {},
            "payload": {"prompt": "{prompt}"},
            "stream_protocol": "blocking-json",
            "request_timeout_seconds": 10,
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
    response_exists = (scenario_dir / ".workers" / task_id / "stdout.log").exists()
    return {
        "orchestrator": result,
        "response_exists": response_exists,
        "final_status": final_status,
        "expected_status": "SUCCEEDED" if expect_success else "FAILED",
        "passed": final_status == ("SUCCEEDED" if expect_success else "FAILED"),
    }


def run_scenario(name: str, server_script_content: str, port: int, expect_success: bool) -> dict:
    scenario_dir = WORK_DIR / name
    scenario_dir.mkdir(parents=True, exist_ok=True)
    server_script = scenario_dir / "server.py"
    _write(server_script, server_script_content)

    v1_dir = scenario_dir / "v1"
    v2_dir = scenario_dir / "v2"
    v1_dir.mkdir(parents=True, exist_ok=True)
    v2_dir.mkdir(parents=True, exist_ok=True)

    server = subprocess.Popen([sys.executable, str(server_script)], cwd=str(scenario_dir))
    try:
        time.sleep(0.6)

        # Build v1-style artifacts by making one HTTP call and saving log/body for helper judgment.
        body_file = v1_dir / "http_response.json"
        log_file = v1_dir / "http_worker.log"
        request_script = scenario_dir / "request_once.py"
        _write(request_script, f"""#!/usr/bin/env python3
import json, urllib.request, urllib.error
payload = json.dumps({{'prompt': 'hello-http'}}).encode('utf-8')
req = urllib.request.Request('http://127.0.0.1:{port}/generate', data=payload, headers={{'Content-Type': 'application/json'}}, method='POST')
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read().decode('utf-8')
        print('opencode v1.14.19 boot')
        print('session id=ses_http_success')
        print('Wrote to {body_file}')
        print('Task complete')
        open(r'{body_file}', 'w').write(body)
except urllib.error.HTTPError as e:
    body = e.read().decode('utf-8')
    print('opencode v1.14.19 boot')
    print('session id=ses_http_error')
    print(f'HTTP {{e.code}} usage_limit_reached resets_in_seconds: 1300')
    print('rate limit detected')
    open(r'{body_file}', 'w').write(body)
""")
        proc = subprocess.run([sys.executable, str(request_script)], cwd=str(scenario_dir), capture_output=True, text=True, timeout=30)
        log_file.write_text(proc.stdout + proc.stderr)

        v1 = run_v1_helper_flow(v1_dir, expect_success)
        v2 = run_v2_orchestrator_flow(v2_dir, port, f"{name}-task", expect_success)

        parity = {
            "both_match_expectation": v1["passed"] and v2["passed"],
            "status_match": v1["derived_status"] == v2["final_status"],
            "artifact_match": v1["output_exists"] == v2["response_exists"],
        }
        return {
            "scenario": name,
            "expected_status": "SUCCEEDED" if expect_success else "FAILED",
            "v1_helper_flow": _normalize(v1),
            "v2_orchestrator_flow": _normalize(v2),
            "parity": parity,
            "passed": all(parity.values()),
        }
    finally:
        server.terminate()
        try:
            server.wait(timeout=2)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=2)


def main() -> int:
    scenarios = [
        run_scenario("http_success", SUCCESS_SERVER, 8775, True),
        run_scenario("http_failure", ERROR_SERVER, 8776, False),
    ]
    summary = {
        "work_dir": str(WORK_DIR),
        "v1_repo": str(V1_DIR),
        "v2_repo": str(V2_DIR),
        "scenarios": scenarios,
        "all_passed": all(s["passed"] for s in scenarios),
    }
    report_path = V2_DIR / "eval" / "parity-http-workflow-report.json"
    report_path.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    if summary["all_passed"]:
        shutil.rmtree(WORK_DIR, ignore_errors=True)
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
