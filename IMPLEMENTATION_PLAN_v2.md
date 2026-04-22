# Worker Orchestrator Skill — v2.0.0 Implementation Plan

## 1. Executive Summary

Migrate the `worker-orchestrator` skill from a Hermes-only, OpenCode/Codex-specific tool into a universal orchestration framework that supports CLI agents, HTTP API workers, Python scripts, and Docker containers. The skill file format remains Hermes-native (YAML frontmatter, trigger phrases), but the patterns, scripts, and workflow documentation become portable across orchestrators via an adapter abstraction.

**Version bump:** `1.3.0` → `2.0.0`  
**Estimated effort:** 3 implementation phases, ~6-8 hours of focused work  
**Risk level:** Medium — abstraction layer must be thin enough to stay useful, thick enough to prevent provider-specific leaks.

---

## 2. Goals & Non-Goals

### Goals
- [ ] Support 4 worker categories: `CLI`, `HTTP_API`, `PYTHON_SCRIPT`, `DOCKER`
- [ ] Auto-discover available adapters at session start instead of hardcoding OpenCode+Codex
- [ ] Make every workflow step expressible in capability-neutral language (no Hermes tool names in the conceptual docs)
- [ ] Add phase-based DAG dependencies so sequential worker chains are first-class
- [ ] Add cost/token tracking with per-session budget enforcement
- [ ] Add container packaging standard (Dockerfile + entrypoint)
- [ ] Maintain 100% backward compatibility for existing Hermes-only users

### Non-Goals
- [ ] Do NOT turn the skill file itself into a cross-platform standard (it stays Hermes SKILL.md format)
- [ ] Do NOT implement a full distributed scheduler (K8s, Nomad, etc.) — local/localhost only
- [ ] Do NOT replace Hermes's native `delegate_task` or `execute_code` tools inside Hermes sessions
- [ ] Do NOT build a GUI or web dashboard

---

## 3. Architectural Principles

1. **Skill format stays native, content goes universal.** The YAML frontmatter, trigger phrases, and `requires_toolsets` remain Hermes-specific. The body text, scripts, and workflow steps are rewritten in generic terms.
2. **Adapter registry over provider enum.** Never hardcode a list like `["opencode", "codex"].` Use a runtime registry that probes the environment.
3. **Canonical data model.** Every adapter produces the same `task_spec`, `worker_handle`, `monitor_result`, and `evaluation_result` shapes regardless of category.
4. **Capability-driven selection.** The orchestrator asks "who can do this?" not "is opencode installed?"
5. **Phase-based execution.** Workers are grouped into phases. All workers in a phase run in parallel. Phases run sequentially. A worker in Phase N can depend on outputs from Phase N-1.

---

## 4. Canonical Data Model (Spec)

These JSON schemas are the contract between the orchestrator and every adapter. All scripts must read/write these shapes.

### 4.1 task_spec

```json
{
  "task_id": "uuid4",
  "worker_category": "CLI | HTTP_API | PYTHON_SCRIPT | DOCKER",
  "adapter_name": "opencode-cli | codex-cli | claude-code | ollama-http | custom-py | ...",
  "phase": 1,
  "depends_on": ["task-id-1", "task-id-2"],
  "prompt": "full self-contained worker instruction",
  "input_files": ["/abs/path/a"],
  "workdir": "/abs/path/to/workdir",
  "artifacts_dir": "/abs/path/to/artifacts",
  "env": {"KEY": "VALUE"},
  "timeout_seconds": 300,
  "stream": false,
  "capture": {
    "stdout": true,
    "stderr": true,
    "structured_events": true,
    "artifacts": true
  },
  "success_contract": {
    "expected_files": ["review.md"],
    "expected_output_markers": ["DONE"],
    "max_cost_usd": 5.0
  },
  "runtime": {}
}
```

Category-specific `runtime` fields:

**CLI:**
```json
{
  "command": ["opencode", "run", "--dir", "/workdir"],
  "stdin_text": "optional prompt override",
  "use_shell": false,
  "pty": false,
  "cwd": "/workdir",
  "extra_args": ["--json"],
  "output_files": ["review.md"]
}
```

**HTTP_API:**
```json
{
  "base_url": "http://localhost:11434",
  "method": "POST",
  "endpoint": "/api/generate",
  "headers": {"Authorization": "Bearer ..."},
  "payload": {"model": "llama3", "stream": true, "prompt": "..."},
  "stream_protocol": "ndjson | sse | blocking-json",
  "request_timeout_seconds": 300
}
```

**PYTHON_SCRIPT:**
```json
{
  "script_path": "/abs/path/worker.py",
  "interpreter": "python3",
  "args": ["--task-spec", "/abs/path/task.json"],
  "stdin_text": "optional",
  "output_files": ["result.json"]
}
```

**DOCKER:**
```json
{
  "image": "my-agent:latest",
  "entrypoint": ["/app/entrypoint.sh"],
  "command": ["run-worker"],
  "stdin_text": "optional prompt",
  "mounts": [
    {"source": "/host/workdir", "target": "/workspace", "mode": "rw"},
    {"source": "/host/artifacts", "target": "/artifacts", "mode": "rw"}
  ],
  "env": {"OPENAI_API_KEY": "..."},
  "network": "bridge",
  "user": "1000:1000",
  "output_files": ["/artifacts/review.md"]
}
```

### 4.2 worker_handle

```json
{
  "worker_id": "uuid4",
  "task_id": "uuid4",
  "phase": 1,
  "worker_category": "CLI | HTTP_API | PYTHON_SCRIPT | DOCKER",
  "adapter_name": "string",
  "status": "PENDING | SPAWNING | RUNNING | SUCCEEDED | FAILED | KILLED | TIMED_OUT | CANCEL_REQUESTED",
  "created_at": "ISO-8601",
  "started_at": "ISO-8601 | null",
  "ended_at": "ISO-8601 | null",
  "workdir": "/abs/path",
  "artifacts_dir": "/abs/path",
  "stdout_path": "/abs/path/stdout.log",
  "stderr_path": "/abs/path/stderr.log",
  "events_path": "/abs/path/events.jsonl",
  "exit_code": null,
  "transport": {},
  "runtime": {}
}
```

### 4.3 monitor_result

```json
{
  "status": "RUNNING | SUCCEEDED | FAILED | KILLED | TIMED_OUT",
  "stdout": "normalized accumulated text",
  "stderr": "normalized accumulated text",
  "artifacts": [
    {"path": "/abs/path", "kind": "report | patch | json | transcript | other", "size_bytes": 1234}
  ],
  "metrics": {
    "runtime_seconds": 12.4,
    "cost_usd": 0.34,
    "tokens_in": 1000,
    "tokens_out": 2500
  },
  "heartbeat_at": "ISO-8601",
  "raw_state": {}
}
```

### 4.4 evaluation_result

```json
{
  "score": 0.0,
  "feedback": "short rationale",
  "rubric_breakdown": {
    "presence": "pass",
    "correctness": 4,
    "completeness": 3,
    "quality": 4,
    "format_compliance": "pass",
    "artifact_compliance": "pass"
  },
  "evidence": {
    "output_markers": ["FINAL ANSWER"],
    "artifact_paths": ["/abs/path/review.md"]
  }
}
```

---

## 5. File Inventory

### New Files

| File | Purpose | Lines (est) |
|------|---------|-------------|
| `ADAPTERS.md` | Spawn/monitor/kill/evaluate mappings for Hermes, Claude Code, Aider, custom Python | 300 |
| `CAPABILITIES.md` | Per-adapter capability matrix for all known viable workers | 200 |
| `scripts/discover_workers.py` | Runtime probe for available adapters, returns JSON registry | 180 |
| `scripts/state_machine.py` | Formal state transitions, persistence, and state queries | 220 |
| `scripts/track_cost.py` | Parses worker stdout for usage blocks, maintains ledger | 150 |
| `scripts/adapter_cli.py` | Adapter implementation for CLI workers (subprocess-based) | 280 |
| `scripts/adapter_http.py` | Adapter implementation for HTTP API workers | 240 |
| `scripts/adapter_docker.py` | Adapter implementation for Docker workers | 200 |
| `scripts/adapter_python.py` | Adapter implementation for Python script workers | 120 |
| `scripts/run_orchestrator.py` | High-level orchestrator script: phases, deps, budget | 350 |
| `docker/Dockerfile.template` | Container packaging standard | 60 |
| `docker/entrypoint.sh` | Generic container entrypoint wrapper | 50 |

### Modified Files

| File | Changes |
|------|---------|
| `SKILL.md` | Rebrand description, universalize all step descriptions, add adapter taxonomy, dynamic pools, phase support, cost tracking, container section, capability matrix reference |
| `README.md` | Rebrand, add ADAPTERS.md and CAPABILITIES.md links, expand requirements, version bump |
| `scripts/synthesize_outputs.py` | Add `--phase` support, dependency merging, artifact discovery from `artifacts_dir` |
| `scripts/evaluate_output.py` | Add `format_compliance` and `artifact_compliance` rubric fields |
| `scripts/circuit_breaker.py` | Add per-adapter failure counters, backoff strategies |

---

## 6. Implementation Phases

### Phase 1: Foundation — Canonical Model + Discovery + CLI Adapter
**Goal:** Establish the data model and make existing OpenCode/Codex support run through the new adapter layer without changing external behavior.

**Tasks:**
1. **Write `scripts/discover_workers.py`**
   - Probe for binaries: `opencode`, `codex`, `claude`, `aider`, `cn`, `gemini`, `cline`, `goose`
   - Probe for HTTP endpoints: `curl http://localhost:11434/api/tags` (ollama), `curl http://localhost:8000/v1/models` (vLLM)
   - Probe for Docker: `docker version`
   - Output JSON registry to stdout, persist to `.worker-registry.json` in repo root
   - Must be pure stdlib (no external deps)

2. **Write `scripts/adapter_cli.py`**
   - Functions: `spawn(task_spec) -> worker_handle`, `monitor(worker_handle) -> monitor_result`, `kill(worker_handle) -> bool`, `evaluate(worker_handle, rubric) -> evaluation_result`
   - Uses `subprocess.Popen` with process groups for clean termination
   - Redirects stdout/stderr to log files
   - Supports both PTY and pipe modes based on `task_spec.runtime.pty`
   - Strips ANSI escape sequences during normalization
   - Returns canonical shapes from §4

3. **Write `scripts/state_machine.py`**
   - Define all states and transitions as a dict/graph
   - Persist state to `.worker-state.jsonl` (one line per event)
   - Functions: `transition(worker_id, new_status)`, `get_state(worker_id)`, `list_by_status(status)`
   - Thread-safe file locking via `fcntl` (Unix) or atomic rename (fallback)

4. **Refactor `scripts/synthesize_outputs.py`**
   - Add `--phases` flag: reads `task_spec.phase` from each workdir
   - Synthesis happens per-phase, sequentially
   - Phase N synthesis receives merged output from Phase N-1 as context
   - Dependency resolution: skip workers whose `depends_on` tasks are not `SUCCEEDED`

5. **Update `SKILL.md` — first pass**
   - Replace provider-specific spawn instructions with generic CLI adapter instructions
   - Keep OpenCode/Codex as "well-tested CLI adapter examples"
   - Add reference to `scripts/discover_workers.py` as Step 0
   - Add `phase` field to decomposition guidance

**Verification:**
- Run `python3 scripts/discover_workers.py` — must detect opencode and codex as available on the dev machine
- Run a test task through `scripts/adapter_cli.py` using opencode — must return canonical `worker_handle` and `monitor_result`
- `scripts/synthesize_outputs.py --phases` must correctly sequence 2-phase mock outputs

**Deliverable:** All Phase 1 files committed, `SKILL.md` partially updated, no regression in existing Hermes workflow.

---

### Phase 2: Expand — HTTP API + Python Script + Docker Adapters
**Goal:** Add the remaining 3 worker categories, cost tracking, and container packaging.

**Tasks:**
1. **Write `scripts/adapter_http.py`**
   - Supports `stream_protocol`: `ndjson`, `sse`, `blocking-json`
   - For streaming: uses `requests` (or stdlib `urllib` if we stay pure stdlib) to consume chunks, append to `stdout.log`
   - For blocking: single POST, write response body to `stdout.log`
   - `kill()`: if `stream_protocol` is blocking, just close connection; if async job, call cancel endpoint if known
   - Parse usage metadata from response headers/body (OpenAI-style `usage` block, ollama-style `eval_count`)
   - Must handle ollama `/api/generate` and `/api/chat`, vLLM `/v1/chat/completions`, OpenAI `/v1/chat/completions`

2. **Write `scripts/adapter_python.py`**
   - Thin wrapper around `subprocess.Popen([interpreter, script_path, ...])`
   - Passes `task_spec.json` as `--task-spec` arg or writes to stdin
   - Otherwise identical mechanics to CLI adapter

3. **Write `scripts/adapter_docker.py`**
   - `spawn()`: `docker run --name worker-<id> --rm -i -v ... -w ... image:tag`
   - `monitor()`: `docker inspect` for status/exit code, `docker logs` for output, scan mounted artifacts dir
   - `kill()`: `docker stop` → grace period → `docker kill`
   - Must handle both one-shot (`docker run`) and long-running (`docker create` + `docker start`) patterns

4. **Write `scripts/track_cost.py`**
   - Scans all `stdout.log` files in a session directory for usage patterns:
     - OpenAI: `"usage": {"prompt_tokens": N, "completion_tokens": M}`
     - Anthropic: `"usage": {"input_tokens": N, "output_tokens": M}`
     - ollama: `"prompt_eval_count": N, "eval_count": M`
   - Maintains `ledger.json`:
     ```json
     {"session_id": "...", "budget_usd": 10.0, "spent_usd": 3.45, "workers": [{"worker_id": "...", "cost_usd": 1.15, "tokens_in": 1000, "tokens_out": 2500}]}
     ```
   - `check_budget(session_id)` raises if exceeded

5. **Write `docker/Dockerfile.template` and `docker/entrypoint.sh`**
   - Base: `ubuntu:24.04`
   - Installs: `bash`, `curl`, `git`, `python3`, `python3-pip`, `tini`
   - `entrypoint.sh` reads `/state/task_spec.json`, extracts prompt, `exec`s the actual CLI agent
   - Template includes commented-out install lines for opencode, codex, aider, etc.

6. **Write `scripts/run_orchestrator.py`**
   - High-level script that ties everything together
   - Reads a `plan.json` (list of task_specs grouped by phase)
   - For each phase:
     - Filters out tasks whose `depends_on` are not satisfied
     - Spawns all eligible tasks via their category adapter
     - Monitors all running workers in a loop
     - Evaluates completed workers
     - Respawns failed workers (up to max retries)
     - Checks budget via `track_cost.py`
     - Synthesizes phase outputs before moving to next phase
   - Exits with code 0 only if all phases succeed

**Verification:**
- Launch ollama locally, run a test task through `adapter_http.py` — must return canonical monitor_result
- Run a test Python script worker through `adapter_python.py` — must work
- Build Docker image from template, run a containerized opencode task — must produce artifact files on host
- `track_cost.py` must correctly parse mock stdout logs and enforce a $1.00 budget

**Deliverable:** All 4 adapter categories functional, cost tracking live, container standard defined.

---

### Phase 3: Polish — Universal Documentation + Capability Matrix + E2E Tests
**Goal:** Rewrite all documentation to be capability-neutral, add the adapter matrix, and prove the system works end-to-end with mixed worker categories.

**Tasks:**
1. **Write `ADAPTERS.md`**
   - Section per orchestrator: Hermes, Claude Code, Aider, Custom Python
   - Each section maps the 8-step workflow to that orchestrator's native tools:
     - Hermes: `execute_code` → `scripts/adapter_cli.py`, `process()` → polling, `read_file` → log inspection
     - Claude Code: `bash()` → subprocess, `python()` → adapter script, `read_file` tool → output inspection
     - Aider: `bash()` → subprocess, same mechanics as Claude Code
     - Custom Python: direct `import adapter_cli; adapter_cli.spawn(...)`
   - Include concrete one-liner examples for each

2. **Write `CAPABILITIES.md`**
   - Table of all discovered viable workers with columns:
     - Name, Category, Available (Y/N/Maybe), Headless Spawn, PTY Required, Streaming, Cancel, Git Required, Cost Model, Notes
   - Include the CLI agents from research: Claude Code, Gemini, Continue, Cline, Cursor, Aider, Goose, OpenCode, Codex
   - Include API endpoints: ollama, vLLM, OpenAI, Anthropic
   - Include Python/Docker as generic categories

3. **Finalize `SKILL.md` rewrite**
   - Frontmatter: bump version to `2.0.0`, update description and tags
   - Architecture section: replace OpenCode+Codex pools with adapter registry diagram
   - When to Use: add HTTP API workers for lightweight tasks, Docker workers for isolation
   - Workflow steps: every step written in generic terms with Hermes examples in collapsible blocks or footnotes
   - Safety & Limits: add per-category limits (API rate limits, container resource limits)
   - Pitfalls: reorganize into CLI-specific, HTTP-specific, Docker-specific, Python-specific subsections
   - Examples: add 3 complete examples:
     1. Pure CLI fleet (like current example, but uses adapter layer)
     2. Mixed CLI + HTTP API fleet (OpenCode + ollama)
     3. Dockerized CLI worker with mounted artifacts

4. **Update `README.md`**
   - New title: "Universal Worker Orchestrator for AI Agents"
   - Add badges: version, tested-on (Hermes, Claude Code, etc.)
   - Quick start: `python3 scripts/discover_workers.py` then `python3 scripts/run_orchestrator.py plan.json`
   - Links to `ADAPTERS.md`, `CAPABILITIES.md`

5. **Write `eval/e2e-mixed-2026-XX-XX.md`**
   - End-to-end test: decompose a feature into 3 phases
     - Phase 1: Design (OpenCode CLI)
     - Phase 2: Implementation (HTTP API worker via ollama for cheap local generation)
     - Phase 3: Tests (Dockerized worker for isolated test execution)
   - Document what worked, what failed, honest assessment

6. **Update `scripts/evaluate_output.py`**
   - Add `format_compliance` and `artifact_compliance` rubric fields
   - Make rubric adapter-aware (e.g., Docker workers get artifact compliance checked against mounted dir, not stdout)

7. **Update `scripts/circuit_breaker.py`**
   - Add per-adapter failure counters
   - Exponential backoff per adapter, not globally
   - Add `adapter_unavailable` state (e.g., ollama not running)

**Verification:**
- A human can read `SKILL.md` and follow the workflow using only `bash` and `python3` — no Hermes required
- `ADAPTERS.md` contains copy-pasteable commands for at least 3 orchestrators
- `CAPABILITIES.md` accurately reflects the current state of all researched tools
- Mixed E2E test runs to completion with at least 2 different worker categories in one session

**Deliverable:** v2.0.0 release-ready: all docs, all scripts, all tests, version bumped, tagged.

---

## 7. Migration Guide (v1.3.0 → v2.0.0)

### For Existing Hermes Users

**No breaking changes.** The skill still loads in Hermes exactly the same way. The trigger phrases expand, not replace. All existing OpenCode/Codex workflows continue to work because `scripts/adapter_cli.py` supports the same spawn mechanics.

**What changes:**
- Step 0: run `python3 scripts/discover_workers.py` to see what's available (optional but recommended)
- Spawn instructions now reference `scripts/adapter_cli.py` instead of inline Python snippets in `execute_code`
- The 3+3 pool becomes a dynamic cap — you can still run 3 OpenCode + 3 Codex, but you can also run 2 Claude Code + 1 ollama + 1 Docker

### For Non-Hermes Users

**This is new.** Previously the skill was unusable outside Hermes. Now:
1. Read `ADAPTERS.md` to find your orchestrator's mapping
2. Run `scripts/discover_workers.py` to see available adapters
3. Write a `plan.json` with your task specs
4. Run `python3 scripts/run_orchestrator.py plan.json`

---

## 8. Testing Strategy

### Unit Tests
- `test_discover_workers.py`: mock `shutil.which` and `subprocess.run`, verify registry JSON shape
- `test_adapter_cli.py`: spawn a dummy `sleep 1; echo DONE` command, verify state transitions, verify kill sends SIGTERM then SIGKILL
- `test_state_machine.py`: rapid fire transitions, verify no invalid transitions allowed, verify persistence across reload
- `test_track_cost.py`: feed mock stdout logs, verify ledger math, verify budget enforcement raises

### Integration Tests
- **CLI adapter + OpenCode:** Run a real `opencode run` task with timeout=30s, verify artifact file created, verify monitor_result shape
- **HTTP adapter + ollama:** Start ollama, run `/api/generate` task with `gemma3`, verify streaming output captured, verify metrics populated
- **Docker adapter:** Build image with `echo` as entrypoint, run container, verify stdout.log contains expected text, verify artifact mount works

### End-to-End Tests
- **E2E-1 (Hermes-native):** 3 independent subtasks via OpenCode CLI, same as v1.3.0 E2E but using adapter layer
- **E2E-2 (Mixed category):** Phase 1 = OpenCode CLI (design), Phase 2 = ollama HTTP (implementation), verify synthesis merges correctly
- **E2E-3 (Dockerized):** Run a feature task inside a container worker, verify artifacts appear on host

### Regression Tests
- Run v1.3.0's exact example session through v2.0.0 SKILL.md instructions — must produce equivalent results

---

## 9. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **Adapter abstraction too thin** — leaks provider details into orchestrator | Medium | High | Strict code review: orchestrator must never access `task_spec.runtime` directly, only through adapter methods |
| **PTY vs pipe mismatch** — tool hangs without TTY or pollutes logs with ANSI | Medium | Medium | `CAPABILITIES.md` documents `requires_pty` per adapter; `adapter_cli.py` auto-strips ANSI; user overrides via `task_spec.runtime.pty` |
| **HTTP streaming protocols differ** — SSE vs NDJSON vs chunked JSON | Medium | Medium | `adapter_http.py` implements protocol-specific parsers; new protocols require adapter update, not orchestrator update |
| **Cost tracking inaccurate** — local workers (ollama) have no cost; API workers report tokens differently | Medium | Low | `track_cost.py` makes cost optional; local workers report tokens only; budget enforcement is best-effort |
| **Docker not available on target machine** | Low | Medium | `discover_workers.py` detects Docker absence; orchestrator falls back to CLI or HTTP adapters |
| **Existing users confused by universalized docs** | Medium | Medium | Keep Hermes examples prominent in `SKILL.md`; add "If you are using Hermes" callout boxes; link to `ADAPTERS.md` for other orchestrators |
| **Phase dependency cycles** | Low | High | `run_orchestrator.py` validates plan.json before execution; raises on circular deps |
| **Budget exceeded mid-stream** | Low | Medium | `track_cost.py` checks budget before every spawn; does not kill running workers but blocks new spawns |

---

## 10. Appendix A: Adapter Capability Matrix (Research Summary)

| Worker | Category | Headless | PTY Req | Streaming | Cancel | Git Req | Cost Model | Viability |
|--------|----------|----------|---------|-----------|--------|---------|------------|-----------|
| OpenCode CLI | CLI | Yes | No | No | Yes (kill) | No | API tokens | **YES** |
| Codex CLI | CLI | Yes | Yes | No | Yes (kill) | No | API tokens | **YES** |
| Claude Code | CLI | Yes (`-p --bare`) | No | No | Yes (kill) | No | API tokens | **YES** |
| Gemini CLI | CLI | Yes | No | Yes (jsonl) | Yes (kill) | No | API tokens | **YES** |
| Continue CLI (`cn`) | CLI | Yes | No | No | Yes (kill) | No | API tokens | **YES** |
| Cline CLI | CLI | Yes | No | No | Yes (kill) | No | API tokens | **YES** |
| Cursor Agent CLI | CLI | Yes (beta) | No | No | Yes (kill) | No | API tokens | **YES** |
| Aider | CLI | Maybe (`--yes-always`) | No | No | Yes (kill) | Strongly prefers | API tokens | **MAYBE** |
| Goose | CLI | Yes | No | No | Yes (kill) | No | API tokens | **MAYBE** |
| ollama | HTTP_API | Yes | N/A | Yes (NDJSON) | No (close conn) | No | Local GPU | **YES** |
| vLLM | HTTP_API | Yes | N/A | Yes (SSE) | No (close conn) | No | Local GPU | **YES** |
| OpenAI API | HTTP_API | Yes | N/A | Yes (SSE) | Partial (Assistants) | No | API tokens | **YES** |
| Anthropic API | HTTP_API | Yes | N/A | Yes (SSE) | No | No | API tokens | **YES** |
| Custom Python | PYTHON_SCRIPT | Yes | N/A | No | Yes (kill) | No | Varies | **YES** |
| Any CLI in Docker | DOCKER | Yes | Sometimes | No | Yes (docker stop) | No | Container cost | **YES** |
| Mentat | CLI | No | N/A | N/A | N/A | N/A | N/A | **NO** (remote manager) |
| Supermaven | CLI | No | N/A | N/A | N/A | N/A | N/A | **NO** (IDE only) |

---

## 11. Appendix B: plan.json Schema

The orchestrator script accepts a plan file defining tasks, phases, and dependencies.

```json
{
  "session_id": "feat-auth-redesign-2026-04-22",
  "budget_usd": 10.0,
  "global_timeout_seconds": 3600,
  "phases": [
    {
      "phase": 1,
      "description": "Design and planning",
      "tasks": [
        {
          "task_id": "design-schema",
          "adapter_name": "opencode-cli",
          "worker_category": "CLI",
          "prompt": "Design a user schema...",
          "timeout_seconds": 300,
          "success_contract": {"expected_files": ["schema.md"]}
        }
      ]
    },
    {
      "phase": 2,
      "description": "Implementation",
      "tasks": [
        {
          "task_id": "impl-backend",
          "adapter_name": "ollama-http",
          "worker_category": "HTTP_API",
          "depends_on": ["design-schema"],
          "prompt": "Implement backend code using the schema...",
          "timeout_seconds": 600,
          "runtime": {
            "base_url": "http://localhost:11434",
            "endpoint": "/api/generate",
            "payload": {"model": "codellama", "stream": true}
          }
        }
      ]
    }
  ]
}
```

---

## 12. Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-04-22 | Keep skill format Hermes-native | Universal skill format standards do not exist; trying to invent one would derail the project |
| 2026-04-22 | 4 worker categories (CLI, HTTP_API, PYTHON_SCRIPT, DOCKER) | Covers all viable worker types found in research; container category subsumes "any CLI in a box" |
| 2026-04-22 | Pure stdlib for discovery + state machine | Ensures zero-dependency bootstrap; adapters may use `requests` if available but must fall back to stdlib |
| 2026-04-22 | Cost tracking best-effort only | Local workers have no cost; API billing formats vary; preventing budget overrun is more important than exact cents |
| 2026-04-22 | Phase-based parallelism, not full DAG | Full DAG is overkill for 99% of use cases; phases are simple, enforceable, and sufficient |
| 2026-04-22 | ANSI stripping in CLI adapter | Many CLI agents emit TUI garbage; stripping is non-destructive and makes logs readable |
| 2026-04-22 | Adapter registry file (`.worker-registry.json`) not committed to git | Environment-specific; generated at runtime; add to `.gitignore` |

---

*Plan written: 2026-04-22*  
*Target completion: 3 phases over 1-2 weeks*  
*Owner: hackafrik / AFRIK*
