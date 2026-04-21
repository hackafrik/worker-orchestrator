---
name: worker-orchestrator
description: >
  Orchestrate external AI worker processes (OpenCode CLI, Codex CLI) to handle
  complex tasks in parallel. Use this skill whenever the user wants to delegate
  work to cheaper models, spawn multiple agents, parallelize a project, split
  costs across models, or supervise external workers. Also trigger when the user
  mentions "opencode workers," "codex workers," "parallel execution,"
  "multi-agent workflow," "spawn workers," "orchestrate," or "supervise agents."
  Do NOT use this skill for simple one-step tasks that Hermes can handle directly.
version: 1.1.0
author: hackafrik
license: MIT
metadata:
  hermes:
    tags: [orchestration, workers, opencode, codex, multi-agent, delegation, parallel]
    category: autonomous-ai-agents
    requires_toolsets: [terminal, process, file, execute_code]
---

# Worker Orchestrator

Hermes acts as an orchestrator that spawns, monitors, evaluates, and synthesizes
output from external AI worker processes (OpenCode CLI and Codex CLI). This
splits token costs: Hermes (orchestrator) uses a small portion for coordination,
while workers handle the heavy lifting on their own model/provider configs.

## Architecture

```
Hermes (Orchestrator)
    │
    ├── Decomposes task into subtasks
    ├── Spawns workers (max 5 parallel)
    ├── Monitors progress via process() tools
    ├── Evaluates worker output
    ├── Gives feedback / respawns if needed
    ├── Falls back OpenCode → Codex on failure
    └── Synthesizes final deliverable
```

## When to Use

- Complex tasks that can be split into independent subtasks
- Tasks where cheaper models can do the heavy lifting
- Projects requiring parallel workstreams (e.g., backend + frontend + docs)
- Any time the user explicitly mentions workers, delegation, or parallel agents
- When the user says "use opencode for this" or "use codex for this"

## When NOT to Use

- Simple one-step tasks (read file, search web, run single command)
- Tasks requiring tight real-time coordination between workers
- Tasks where Hermes can complete faster than spawn overhead (~30s per worker)

## Workflow

### Step 1: Decompose

Break the user's task into 2-5 independent subtasks. Each subtask should:
- Be self-contained (worker gets all context it needs)
- Produce a concrete artifact (file, code, report)
- Not depend on other workers' output (unless you plan sequential phases)

**Example decomposition:**
- User: "Build a FastAPI auth service with tests and docs"
- Subtask A: "Implement FastAPI auth endpoints (login, register, JWT)"
- Subtask B: "Write pytest tests for auth endpoints"
- Subtask C: "Write README with setup and API usage"

### Step 2: Prepare Workdirs & Spawn Workers (Max 5 Parallel)

**CRITICAL: Workers are sandboxed.** OpenCode/Codex cannot access files outside their workdir. If workers need to read existing project files, copy them into the workdir BEFORE spawning.

```bash
# 1. Create isolated workdir for each worker
mkdir -p /tmp/worker-a /tmp/worker-b

# 2. Copy required files into workdir (if reviewing/modifying existing code)
cp -r /path/to/project/* /tmp/worker-a/

# 3. Init git if using Codex (Codex refuses to run outside git repos)
cd /tmp/worker-a && git init
```

**Spawning OpenCode Workers: Use `execute_code`, NOT `terminal(background=true)`**

`terminal(background=true)` with `opencode run` hangs indefinitely — the opencode TUI blocks on a pseudo-TTY that never receives input. The output stream stays empty and the process never completes.

**The reliable method:** Spawn workers via `execute_code` using Python `subprocess.Popen`:

```python
from hermes_tools import terminal
import subprocess
import os

proc = subprocess.Popen(
    ["opencode", "run", "YOUR_PROMPT_HERE"],
    cwd="/tmp/worker-a",
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    env=os.environ.copy()
)
stdout, _ = proc.communicate(timeout=300)
```

**Trade-off:** `execute_code` workers cannot be monitored live with `process()` tools. You get all output when the process exits. For live monitoring, there is currently no reliable method with opencode.

**Fallback Worker: Codex**

Codex is an interactive CLI app that requires a PTY. For Codex, use `terminal(pty=true)`:
```bash
terminal(command="codex exec 'YOUR_TASK_PROMPT_HERE'", workdir="/tmp/worker-b", pty=true)
```
Note: Codex may also have TTY issues in background mode. Foreground is more reliable.

**Important:**
- Always create separate workdirs per worker to avoid file collisions
- Track all spawned workers: `[{workdir, provider, task, status}]`
- Each worker needs its own workdir to avoid file collisions

### Step 3: Monitor

**With `execute_code` spawning, monitoring is batch-style, not live.**

Since workers are spawned via Python subprocess inside `execute_code`, you cannot use `process(action="poll|log")` on them. Instead:

1. Spawn multiple workers in parallel within a single `execute_code` block
2. Use `proc.communicate(timeout=300)` to wait for each
3. Capture all stdout when the process exits
4. Parse the output for progress, errors, and results

**Example batch monitoring:**
```python
import subprocess

workers = [
    ("worker-a", ["opencode", "run", "prompt-a"], "/tmp/worker-a"),
    ("worker-b", ["opencode", "run", "prompt-b"], "/tmp/worker-b"),
]

results = {}
for name, cmd, cwd in workers:
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    stdout, _ = proc.communicate(timeout=300)
    results[name] = {"exit_code": proc.returncode, "output": stdout}
    print(f"=== {name} completed (exit {proc.returncode}) ===")
    print(stdout[-2000:] if len(stdout) > 2000 else stdout)
```

**If you must monitor a `terminal(background=true)` process** (e.g., for Codex on a PTY):
```bash
process(action="poll", session_id="WORKER_SESSION_ID")
process(action="log", session_id="WORKER_SESSION_ID", limit=50)
```

**What to look for in output:**
- Progress markers: file reads, edits, tool calls
- Error patterns: `Error:`, `Exception:`, `timeout`, `failed`, `abort`
- Completion signals: `apply_patch Success`, `Wrote to...`, `Created`

**Timeout handling:**
- Set `timeout=300` (5 min) for most tasks
- Set `timeout=600` (10 min) for large refactors
- If a worker times out, read whatever output was produced before the timeout
- Partial output often contains actionable findings even if the worker didn't finish

### Step 4: Evaluate Output

For each completed worker, evaluate its output using this rubric:

| Criterion | Scale | Description |
|-----------|-------|-------------|
| **Presence** | Pass/Fail | Did worker produce any output file or result? |
| **Correctness** | 1-5 | Does output match what the subtask asked for? |
| **Completeness** | 1-5 | All parts of the subtask addressed? |
| **Quality** | 1-5 | Professional standard, no obvious errors? |

**Scoring:**
- Calculate average: `(Correctness + Completeness + Quality) / 3`
- **PASS** if: Presence=Pass AND average >= 3.0
- **FAIL** if: Presence=Fail OR average < 3.0

**Evaluation method:**
1. First, do a quick objective check (file exists, not empty, contains expected keywords)
2. If objective check passes, use Hermes reasoning to score Correctness/Completeness/Quality
3. If uncertain, use `execute_code` to run a quick validation script

### Step 5: Feedback & Respawn

For any FAILED worker:

1. **Analyze why it failed** — which criteria scored low?
2. **Generate specific corrective feedback** — tell the worker exactly what to fix
3. **Respawn** with the same provider (or fallback if provider failed)

**Respawn prompt template:**
```
You previously attempted: [original task]
Your output was insufficient because: [specific reason]
Please redo with these corrections: [specific instructions]
```

**Respawn limits:**
- Max 2 respawns per subtask (original + 2 retries = 3 total attempts)
- After 3 failures, synthesize what you have or flag for user review

### Step 6: Dynamic Worker Spawning (After Initial Batch)

**The orchestrator does NOT stop after the first batch of workers.** After evaluating outputs:
- If a worker's output reveals NEW subtasks or gaps, spawn additional workers
- If synthesis shows missing pieces, spawn workers to fill them
- If user feedback arrives mid-orchestration, spawn workers to address it

**Rules for dynamic spawning:**
- Count total ACTIVE workers (running + pending). Never exceed 5.
- Workers that have finished (DONE/FAILED) no longer count toward the limit.
- Example: 3 workers finish, 2 still running → you can spawn up to 3 more.

**Dynamic spawn triggers:**
1. **Gap filling:** Worker A found 5 bugs but only fixed 3 → spawn Worker D to fix the remaining 2
2. **Dependency resolution:** Worker B needs a function that Worker C was supposed to write → spawn Worker E after C finishes
3. **Quality boost:** Synthesis is thin → spawn Worker F to add depth (docs, examples, edge cases)
4. **User pivot:** User says "also add tests" after workers finished → spawn Worker G for tests

**Dynamic spawn workflow:**
```
1. Initial batch: Workers A, B, C (3 active)
2. A finishes → evaluate → PASS
3. B finishes → evaluate → FAIL → respawn B2
4. C finishes → evaluate → PASS, but output reveals missing piece → spawn D
5. Now active: B2, D (2 active, under limit)
6. B2 finishes → evaluate → PASS
7. D finishes → evaluate → PASS
8. Synthesize A + B2 + C + D
```

### Step 7: Fallback Chain

If OpenCode fails (crash, no output, bad output after respawn):

1. **First fallback:** Respawn same task with OpenCode (different wording)
2. **Second fallback:** Spawn with Codex: `codex exec 'task'`
3. **Last resort:** Do the subtask directly in Hermes (this session)

Do NOT use Claude Code anywhere in this workflow.

### Step 8: Synthesize

Combine all worker outputs into a single coherent deliverable:

1. Read terminal logs from all workers (primary source)
2. Read any files workers were instructed to create (secondary source)
3. Ignore node_modules, vendor dirs, and auto-generated noise
4. Merge related outputs (e.g., combine code + tests + docs into one project)
5. Resolve any conflicts between worker outputs
6. Produce final deliverable in the format the user requested
7. Report what each worker did, what failed, what was respawned, what was dynamically spawned

**Synthesis from terminal logs:**
When workers stream output to terminal instead of writing files:
- Extract code blocks, findings, and analysis from the terminal log
- Write extracted content to structured files for the final deliverable
- Credit which worker produced which content

**Avoiding synthesis noise:**
If workers copied entire project directories (e.g., `cp -r project/* workdir/`), the
workdir will contain node_modules, READMEs, and other files. Do NOT synthesize everything.
Instead:
- Instruct workers to write findings to a specific file (e.g., `review.md`)
- Read only the specific output files from each worker
- Use `find <workdir> -name "review.md" -o -name "output.py"` to locate deliverables

## Worker Prompt Engineering

Each worker prompt must be:
- **Self-contained** — include all context needed (file paths, constraints, format)
- **Atomic** — one clear deliverable per worker
- **Constrained** — specify output format, file names, and success criteria
- **Output-directed** — explicitly tell the worker WHERE to write its output

**Good worker prompt:**
```
Review the Python files in this directory. Check for bugs, security issues,
and performance problems. Write your findings to /tmp/worker-a/review.md
as a structured markdown report with HIGH/MEDIUM/LOW severity sections.
```

**Bad worker prompt:**
```
Review the code and tell me what you find.
```
(too vague — worker may stream findings in terminal instead of writing a file)

## Safety & Limits

- **Max 5 parallel workers** — never exceed this
- **Max 2 respawns per subtask**
- **Kill stuck workers** — if no output for 5+ minutes, kill and retry
- **Clean up temp dirs** — after synthesis, remove worker temp directories
- **Git repos:** Codex refuses to run outside a git repo. OpenCode does not require git.
  Initialize git in workdirs when using Codex: `git init`
- **Copy files before spawn** — workers are sandboxed and cannot access parent filesystem

## Helper Scripts

Use the bundled scripts for evaluation and synthesis:

- `scripts/evaluate_output.py` — structured evaluation of worker output
- `scripts/synthesize_outputs.py` — merge multiple worker outputs

## Pitfalls & Lessons Learned

**`terminal(background=true)` with `opencode run` hangs indefinitely.**
The opencode TUI blocks on a pseudo-TTY that never receives input. Output stream stays empty and the process never completes. **Always spawn OpenCode workers via `execute_code` with Python `subprocess.Popen`.** Trade-off: you lose live `process()` monitoring and must wait for the subprocess to return all output at once.

**Shell quoting with `opencode run` is fragile.**
Complex prompts containing quotes, newlines, or backticks will break shell parsing when passed directly to `terminal(command="opencode run '...'")`. Safer approach: write the prompt to a file, then reference it:
```bash
# Write prompt to file first
echo "your prompt here" > /tmp/prompt.txt
# Then spawn with file reference
opencode run "$(cat /tmp/prompt.txt)"
```

**Multiple workers modifying the same config files will overwrite each other.**
If Worker A updates `package.json` for security fixes and Worker B updates `package.json` to add test dependencies, copying both `package.json` files sequentially will cause the second copy to overwrite the first. **Always diff and merge config files** rather than blind copy:
```python
import json
# Read both versions, merge fields, write merged result
```

**Workers may create tests with wrong assumptions about function names/APIs.**
A worker writing tests for existing code may assume function names that don't exist or expect return shapes that differ from reality. After a worker creates tests:
1. Check the imports in the test file against actual exports in the source
2. Verify test expectations match actual function behavior
3. Be prepared to add wrapper functions or fix test assertions

**Worker sandboxing is strict.**
OpenCode cannot read files outside its workdir — confirmed by `permission denied` errors. Always `cp -r project/* workdir/` before spawning.

**Synthesis picks up node_modules noise.**
When workers copy entire projects, `synthesize_outputs.py` will ingest node_modules READMEs. Always instruct workers to write findings to a SPECIFIC file (e.g., `review.md`) and read only that file during synthesis. Alternatively, diff modified files against originals instead of running a generic synthesis.

**Git init timing matters.**
If you `git init` an empty workdir BEFORE copying project files, git won't track the copied files as modifications. Either:
- Copy files first, then `git init && git add -A`
- Or use `diff original_file modified_file` instead of `git diff`

## Example Session

**User:** "I need a Python CLI tool that converts CSV to JSON. Have workers build the core converter, the CLI interface, and the tests. Then combine everything."

**Orchestrator response:**
1. Decompose:
   - Worker A: Core CSV→JSON conversion logic
   - Worker B: Click/Typer CLI interface
   - Worker C: pytest test suite
2. Spawn 3 OpenCode workers in parallel (via `terminal(background=true)`)
3. Monitor all 3 via `process(action="log")` every 15s
4. Evaluate outputs (all pass)
5. Synthesize into single project at user-requested location
6. Report: "3 workers completed. Core logic + CLI + tests merged into ~/csv2json/"
