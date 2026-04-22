# Migration Guide: v1.4 → v2.0.0

This guide covers every structural change when upgrading from v1.4 to v2.0.0.

## tl;dr

| v1.4 Concept | v2.0.0 Equivalent |
|---|---|
| `python orchestrator.py` (imperative) | `python src/orchestrator.py manifest.json` (declarative) |
| OpenCode / Codex pools | `adapter: "cli"` or `"python_script"` or `"http_api"` or `"docker"` |
| 6-worker hard cap | `budget.max_workers` in manifest |
| `synthesize_outputs.py` | Still works — backward-compatible merge engine |
| Circuit breaker + respawn | Respawn with feedback via `max_retries` in manifest |

---

## 1. From Imperative to Declarative

### v1.4 (imperative Python loop)

```python
# orchestrator.py — v1.4
spawn_workers(num_workers=3)
wait_for_completion()
results = collect_outputs()
merged = synthesize_outputs(results, "best_of_n")
```

### v2.0.0 (declarative manifest)

```json
{
  "version": "2.0.0",
  "task": {"description": "...", "strategy": "parallel-voting"},
  "phases": [
    {
      "id": "generate",
      "type": "parallel",
      "workers": [
        {"id": "w1", "adapter": "cli", "command": "..."},
        {"id": "w2", "adapter": "cli", "command": "..."},
        {"id": "w3", "adapter": "cli", "command": "..."}
      ]
    },
    {
      "id": "synthesize",
      "type": "sequential",
      "depends_on": ["generate"],
      "workers": [{"id": "merge", "adapter": "cli", "command": "..."}]
    }
  ],
  "synthesis": {"mode": "best-of-n", "merge_field": "output", "output_file": "result.txt"},
  "budget": {"max_workers": 3, "cost_ceiling_usd": 0.01, "time_limit_seconds": 60}
}
```

**Change**: You now write a JSON file instead of Python code. The orchestrator reads the manifest and executes the graph.

---

## 2. Worker Configuration

### v1.4 (provider pools)

```python
workers = [
    {"provider": "opencode", "model": "gpt-4o", "task": "code review"},
    {"provider": "codex", "model": "o3-mini", "task": "code review"},
]
```

### v2.0.0 (adapter registry)

```json
{
  "workers": [
    {
      "id": "reviewer-1",
      "adapter": "python_script",
      "script_file": "review.py",
      "rubric": [
        {"criterion": "bugs", "weight": 0.5},
        {"criterion": "style", "weight": 0.5}
      ]
    },
    {
      "id": "reviewer-2",
      "adapter": "http_api",
      "endpoint": "https://api.openai.com/v1/chat/completions",
      "headers": {"Authorization": "Bearer ${OPENAI_API_KEY}"},
      "body": {"model": "gpt-4o", "messages": [...]},
      "rubric": [
        {"criterion": "bugs", "weight": 0.5},
        {"criterion": "style", "weight": 0.5}
      ]
    }
  ]
}
```

**Change**: Providers become adapters. Model selection is pushed into adapter config (e.g., the HTTP body). The orchestrator is now provider-agnostic.

---

## 3. Budget & Limits

### v1.4 (hardcoded)

```python
MAX_WORKERS = 6
OPENCODE_CAP = 3
CODEX_CAP = 3
```

### v2.0.0 (manifest-configurable)

```json
{
  "budget": {
    "max_workers": 6,
    "cost_ceiling_usd": 0.50,
    "time_limit_seconds": 300
  }
}
```

**Change**: Limits are per-manifest, not global constants. Each task declares its own resources.

---

## 4. Output Synthesis

### v1.4 (imperative call)

```python
from synthesize_outputs import synthesize
result = synthesize(outputs, strategy="best_of_n", merge_field="output")
```

### v2.0.0 (declarative manifest)

```json
{
  "synthesis": {
    "mode": "best-of-n",
    "merge_field": "output",
    "output_file": "final_result.json"
  }
}
```

**Backward compatibility**: `synthesize_outputs.py` still exports `synthesize()` with the same signature. v1.4 scripts importing it will continue to work.

---

## 5. Error Handling & Respawn

### v1.4 (circuit breaker)

```python
if rate_limited:
    sleep(backoff)
    respawn_worker()
```

### v2.0.0 (manifest retry config)

```json
{
  "workers": [
    {
      "id": "worker-1",
      "adapter": "http_api",
      "max_retries": 3,
      "retry_backoff_seconds": 5,
      "rubric": [...]
    }
  ]
}
```

**Change**: Retry logic is declared per-worker in the manifest. The orchestrator automatically respawns with error context appended to the task description.

---

## 6. Evaluation / Rubrics

**New in v2.0.0**. Every worker can declare a rubric:

```json
{
  "rubric": [
    {"criterion": "completeness", "weight": 0.4, "description": "Covers all requirements"},
    {"criterion": "correctness", "weight": 0.4, "description": "No factual errors"},
    {"criterion": "clarity", "weight": 0.2, "description": "Readable and well-structured"}
  ]
}
```

**Score** = Σ (criterion_score × weight), range 0.0–1.0.

Used by `best-of-n` synthesis to select the winning output.

---

## 7. Phase Dependencies

**New in v2.0.0**. Workers in sequential phases must wait for prior phases:

```json
{
  "phases": [
    {"id": "search", "type": "parallel", "workers": [...]},
    {
      "id": "summarize",
      "type": "sequential",
      "depends_on": ["search"],
      "workers": [...]
    }
  ]
}
```

`summarize` phase workers do not start until **all** workers in the `search` phase complete.

---

## 8. Step-by-Step Upgrade Checklist

- [ ] Install v2.0.0: `pip install -r requirements.txt` (same deps, no new requirements)
- [ ] Create `manifest.json` for your first task (see `examples/01-hello-world/`)
- [ ] Map v1.4 provider pools to v2.0.0 adapters:
  - OpenCode CLI → `"adapter": "cli"` with `command`
  - Codex CLI → `"adapter": "cli"` with `command`
  - HTTP API calls → `"adapter": "http_api"`
  - Python scripts → `"adapter": "python_script"`
- [ ] Add rubrics to workers that need scoring
- [ ] Move hardcoded limits (`MAX_WORKERS`, etc.) into `budget` object
- [ ] Move synthesis strategy into `synthesis` object
- [ ] Test with: `python src/orchestrator.py manifest.json`
- [ ] Verify output matches v1.4 behavior using `eval/structural_parity_test.py`
- [ ] If your code imports `synthesize_outputs.synthesize`, it still works — no change needed

---

## 9. Common Pitfalls

| Pitfall | Fix |
|---|---|
| Forgetting `"depends_on"` | Sequential phases without dependencies run immediately — add `depends_on` |
| Missing `"version": "2.0.0"` | The orchestrator validates the manifest schema; version must match |
| Rubric weights not summing to 1.0 | Weights are normalized, but best practice is Σ weights = 1.0 |
| `"output_file"` path not writable | Ensure the orchestrator has write permissions to the output directory |

---

## Need help?

- Open an issue: https://github.com/hackafrik/worker-orchestrator/issues
- Read the docs: https://hackafrik.gitlab.io/worker-orchestration-website/v2/docs/
- See examples: `examples/` directory in this repo
