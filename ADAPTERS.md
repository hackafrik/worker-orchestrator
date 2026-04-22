# Worker Adapter Architecture

The worker orchestrator supports four worker categories via pluggable adapters.
Every adapter exposes a unified interface: **spawn**, **monitor**, **kill**, **evaluate**.

## Adapter Interface (Python module)

Each adapter module (`scripts/adapter_<category>.py`) MUST expose these functions:

```python
def spawn(task_spec: dict) -> dict:
    """Create a worker handle and start the worker."""
    ...

def monitor(worker_handle: dict) -> dict:
    """Poll worker state and return canonical monitor_result."""
    ...

def kill(worker_handle: dict) -> bool:
    """Best-effort terminate the worker. Returns True if kill was dispatched."""
    ...

def evaluate(worker_handle: dict, rubric: dict) -> dict:
    """Apply a rubric to a completed (or timed-out) worker output."""
    ...
```

### spawn() → worker_handle schema

```json
{
  "worker_id": "<unique-id>",
  "task_id": "<matches worker_id for simple tasks>",
  "phase": 0,
  "worker_category": "CLI|HTTP_API|PYTHON_SCRIPT|DOCKER",
  "adapter_name": "<e.g. ollama-http, docker-runtime>",
  "status": "RUNNING",
  "created_at": "2026-04-22T12:00:00Z",
  "started_at": "2026-04-22T12:00:01Z",
  "ended_at": null,
  "workdir": "/abs/path/to/cwd",
  "artifacts_dir": "/abs/path/to/artifacts",
  "stdout_path": "/abs/path/to/stdout.log",
  "stderr_path": "/abs/path/to/stderr.log",
  "events_path": "/abs/path/to/events.jsonl",
  "exit_code": null,
  "transport": { "category-specific transport blob" },
  "runtime": { "raw task_spec runtime block" }
}
```

### monitor() → monitor_result schema

```json
{
  "status": "RUNNING|SUCCEEDED|FAILED|KILLED|TIMED_OUT",
  "stdout": "<cumulative stdout text>",
  "stderr": "<cumulative stderr text>",
  "artifacts": [
    {"path": "/abs/path", "kind": "report|patch|json|other", "size_bytes": 1234}
  ],
  "metrics": {
    "runtime_seconds": 45.3,
    "cost_usd": null,
    "tokens_in": 1024,
    "tokens_out": 512
  },
  "heartbeat_at": "2026-04-22T12:01:00Z",
  "raw_state": { "category-specific raw state" }
}
```

### evaluate() → evaluation_result schema

```json
{
  "score": 0.85,
  "feedback": "Passed 3/4 checks.",
  "rubric_breakdown": {
    "presence": "pass",
    "artifact_compliance": "pass",
    "format_compliance": "not_checked"
  },
  "evidence": {
    "output_markers": ["expected-marker"],
    "artifact_paths": ["/abs/path/to/artifact.md"]
  }
}
```

## CLI Adapter (`adapter_cli.py`)

Spawns subprocess via `subprocess.Popen` in a new session for clean tree-kill.

- **Transport**: `pid`, `pgid`, `pty`, `invocation`
- **Kill**: `os.killpg(SIGTERM)` → grace period → `SIGKILL`
- **Artifacts**: Any files in `artifacts_dir` excluding internal logs
- **Monitor**: Checks `ps -p` to detect process exit; reads `.exitcode` sentinel if present

## HTTP API Adapter (`adapter_http.py`)

Speaks to HTTP/S APIs (REST, SSE, NDJSON). Designed for ollama, vLLM, and any OpenAI-compatible endpoint.

- **Transport**: `request_id`, `stream_pid` (background stream consumer), `pgid`
- **Spawn**: Writes `request.json` for audit; for streaming protocols spawns a background consumer subprocess
- **Monitor**: Checks if stream consumer process has exited; parses JSON usage metadata for tokens
- **Kill**: SIGKILL on stream consumer process
- **Artifacts**: Response body saved to `stdout.log`; events to `events.jsonl`

## PYTHON_SCRIPT Adapter (`adapter_python.py`)

Subprocess wrapper for arbitrary Python scripts.

- **Transport**: `pid`, `pgid`, `invocation`
- **Use case**: Custom inference scripts, data processors, local model frontends
- **Args**: Supports `{task_spec_file}` placeholder substitution

## DOCKER Adapter (`adapter_docker.py`)

Wraps `docker run` lifecycle.

- **Transport**: `container_name`, `docker_cmd`, `local_pid`, `pgid`
- **Monitor**: `docker inspect` to read container `State.Status` and `ExitCode`
- **Kill**: `docker stop -t 5` → `docker kill`
- **Mounting**: `runtime.mounts` array with `source`, `target`, `mode`

## Adding a New Adapter

1. Create `scripts/adapter_<category>.py`
2. Implement `spawn`, `monitor`, `kill`, `evaluate`
3. Add to `_resolve_adapter()` in `run_orchestrator.py`
4. Add to `discover_workers.py` detection logic
5. Update this doc with transport schema and test notes

## task_spec `runtime` block by category

### CLI
```json
{
  "command": ["opencode", "--acp", "--stdio"],
  "extra_args": ["--model", "gpt-4o"],
  "stdin_text": "{prompt}",
  "use_shell": false,
  "pty": false,
  "cwd": "."
}
```

### HTTP_API
```json
{
  "base_url": "http://localhost:11434",
  "endpoint": "/api/generate",
  "method": "POST",
  "headers": {"Content-Type": "application/json"},
  "payload": {"model": "llama3.1", "stream": true},
  "stream_protocol": "sse"
}
```

### PYTHON_SCRIPT
```json
{
  "script_path": "/path/to/worker.py",
  "interpreter": "python3",
  "args": ["--task-spec", "{task_spec_file}"],
  "stdin_text": "{prompt}"
}
```

### DOCKER
```json
{
  "image": "ghcr.io/someorg/inference-worker:latest",
  "entrypoint": ["python3", "worker.py"],
  "command": ["--mode", "generate"],
  "mounts": [{"source": "/host/data", "target": "/data", "mode": "ro"}],
  "env": {"API_KEY": "..."},
  "network": "bridge",
  "user": "1000:1000"
}
```
