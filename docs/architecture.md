# worker-orchestrator-v2 architecture

## Goal

Build a clean-room v2 implementation in a separate repo so v1.4 remains stable and untouched while we validate a more general worker-orchestration design.

## Design principles

1. Keep v1.4 intact until v2 proves better.
2. Standardize all worker categories behind one adapter contract.
3. Use pure-stdlib Python for the orchestration core where practical.
4. Preserve auditable state, logs, artifacts, and cost data.
5. Favor local and inspectable execution over hidden magic.

## Canonical lifecycle

PENDING -> SPAWNING -> RUNNING -> MONITORING -> EVALUATING -> SUCCEEDED|FAILED|KILLED|TIMED_OUT

The state machine is implemented in `scripts/state_machine.py` and should be the only source of truth for allowed transitions.

## Core components

### 1. Discovery

`scripts/discover_workers.py`

Discovers available worker backends and emits a registry describing:
- adapter name
- worker category
- detected command or endpoint
- capabilities
- local availability

### 2. Adapters

Each adapter exposes the same lifecycle surface:
- `spawn(task_spec)`
- `monitor(worker_handle)`
- `kill(worker_handle)`
- `evaluate(worker_handle, rubric)`

Current adapters:
- `scripts/adapter_cli.py`
- `scripts/adapter_http.py`
- `scripts/adapter_python.py`
- `scripts/adapter_docker.py`

### 3. Orchestrator

`scripts/run_orchestrator.py`

Responsibilities:
- load task specs or manifests
- group work by phase
- spawn workers through category adapters
- poll status until terminal
- evaluate outputs against rubric
- synthesize outputs for downstream phases
- record usage and costs

### 4. Evaluation and synthesis

- `scripts/evaluate_output.py`
- `scripts/evaluate_worker_logs.py`
- `scripts/synthesize_outputs.py`

These convert raw worker output into comparable evidence so v2 can be judged against v1.4 on real results instead of subjective impressions.

### 5. Cost tracking

`scripts/track_cost.py`

Tracks:
- tokens in
- tokens out
- runtime
- estimated USD cost by model/provider mapping

## Data model

The core interchange objects are:
- `task_spec`
- `worker_handle`
- `monitor_result`
- `evaluation_result`

These are documented in `IMPLEMENTATION_PLAN_v2.md` and should stay stable across adapters.

## Repository layout

- `scripts/` orchestration and adapter code
- `eval/` end-to-end and comparative evaluation harnesses
- `docs/` design and migration criteria
- root docs: `README.md`, `ADAPTERS.md`, `CAPABILITIES.md`, `SKILL.md`

## Near-term gaps

1. Strengthen e2e coverage beyond the Python adapter path.
2. Add direct comparative evals against v1.4 behavior.
3. Define migration gate clearly before any replacement decision.
4. Validate Docker and HTTP adapters with realistic fixtures.

## Promotion rule

v2 does not replace v1.4 until it demonstrates:
- equal or better reliability
- equal or better observability
- easier adapter extensibility
- no regressions on existing workflows that matter
