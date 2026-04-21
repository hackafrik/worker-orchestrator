# Worker Orchestrator

A [Hermes Agent](https://github.com/NousResearch/hermes-agent) skill that turns your AI agent into a fleet commander. Spawn external worker processes (OpenCode, Codex), monitor them in real-time, evaluate their output, and dynamically respawn with corrective feedback — all while staying under a 5-worker parallel limit.

## Why

Running everything on one expensive model burns tokens fast. This skill splits the load:

| Layer | Model | Role | Token Share |
|-------|-------|------|-------------|
| **Orchestrator** (Hermes) | Your main model | Decomposition, monitoring, evaluation, synthesis | ~20% |
| **Workers** (OpenCode) | Cheaper model | Heavy lifting: coding, writing, research | ~60% |
| **Fallback** (Codex) | Reserve model | Only when OpenCode fails | ~20% if needed |

## What It Does

1. **Decomposes** your task into 2-5 atomic subtasks
2. **Spawns** OpenCode workers as actual OS processes (not subagents)
3. **Monitors** terminal output in real-time via `process()` polling
4. **Evaluates** output with a structured rubric (Presence + Correctness + Completeness + Quality)
5. **Respawns** failed workers with specific feedback (max 2 retries)
6. **Dynamically spawns** additional workers after initial batch if gaps remain
7. **Falls back** OpenCode → Codex → direct Hermes on failure
8. **Synthesizes** all worker outputs into a unified deliverable

## Architecture

```
┌─────────────────────────────────────┐
│         HERMES (Orchestrator)       │
│  - Receives master task             │
│  - Decomposes into subtasks         │
│  - Spawns workers via terminal()    │
│  - Monitors via process()           │
│  - Evaluates output quality         │
│  - Gives feedback / respawns        │
│  - Synthesizes final deliverable    │
└─────────────┬───────────────────────┘
              │
    ┌─────────┼─────────┐
    ▼         ▼         ▼
┌───────┐ ┌───────┐ ┌───────┐
│OpenCode│ │OpenCode│ │ Codex  │
│Worker 1│ │Worker 2│ │Fallback│
└───────┘ └───────┘ └───────┘
```

## Installation

Option 1 — direct GitHub clone into Hermes:

```bash
git clone https://github.com/hackafrik/worker-orchestrator.git ~/.hermes/skills/worker-orchestrator
```

Option 2 — install through the open Agent Skills ecosystem (`skills.sh`):

```bash
npx skills add hackafrik/worker-orchestrator
```

Verified with:

```bash
npx skills add hackafrik/worker-orchestrator -l
```

Hermes auto-discovers skills from `~/.hermes/skills/` on startup.

## Public Discovery / Publishing Notes

This repo is published as a public GitHub repository and is directly consumable by the open Agent Skills ecosystem.

- GitHub: public source of truth for the skill
- skills.sh / Agent Skills: installable as `hackafrik/worker-orchestrator`
- Hermes Skills Hub: supports GitHub and skills.sh sources, so public GitHub compatibility matters
- Hermes Atlas: community-curated ecosystem map; public docs do not currently expose a self-serve submission API, so inclusion appears curator-driven rather than automatic


## Requirements

- [Hermes Agent](https://github.com/hacktheon/hacktheon)
- [OpenCode CLI](https://github.com/opencode-ai/opencode) installed and authenticated
- [Codex CLI](https://github.com/openai/codex) installed and authenticated (fallback)

## Usage

Once installed, the skill auto-triggers when you say things like:

> "Spawn workers to refactor these files"
> "Use OpenCode to research this topic and supervise"
> "Delegate this task to cheaper models"
> "Parallelize this work across multiple agents"

Hermes will:
1. Break the task into subtasks
2. Spawn up to 5 OpenCode workers
3. Monitor their terminal output
4. Grade their output
5. Combine everything into a final answer

## Helper Scripts

| Script | Purpose |
|--------|---------|
| `scripts/evaluate_output.py` | Score worker output against criteria (Presence / Correctness / Completeness / Quality) |
| `scripts/synthesize_outputs.py` | Merge multiple worker outputs into one deliverable |

## Example Session

See the [Auto-Apply refactor session](https://github.com/hackafrik/Auto-Apply) where 6 workers fixed 10+ bugs, added tests, and cleared security vulnerabilities — all supervised by Hermes.

## License

MIT
