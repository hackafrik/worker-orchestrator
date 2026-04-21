# Worker Orchestrator

A [Hermes Agent](https://github.com/NousResearch/hermes-agent) skill that turns your AI agent into a fleet commander. Spawn external worker processes (OpenCode, Codex), evaluate their output, rebalance work across provider pools, and dynamically respawn with corrective feedback — all while staying under a 6-worker global limit with a 3+3 provider split.

## Why

Running everything on one expensive model burns tokens fast. This skill splits the load across an orchestrator plus two worker pools:

| Layer | Model | Role | Token Share |
|-------|-------|------|-------------|
| **Orchestrator** (Hermes) | Your main model | Decomposition, evaluation, provider allocation, synthesis | ~15-25% |
| **OpenCode pool** | OpenCode-configured model | Parallel coding, research, structured repo work | Variable |
| **Codex pool** | Codex-configured model | Parallel coding, patching, validation, overflow capacity | Variable |

Pool policy:
- Global cap: 6 active workers total
- OpenCode cap: 3 active workers
- Codex cap: 3 active workers
- Use both pools concurrently when useful; Codex is not just a last-resort fallback

## What It Does

1. **Decomposes** your task into 2-5 atomic subtasks
2. **Spawns** OpenCode and Codex workers as actual OS processes (not subagents)
3. **Captures and inspects** worker output/logs before trusting files alone
4. **Evaluates** output with a structured rubric (Presence + Correctness + Completeness + Quality)
5. **Respawns or rebalances** failed work with specific feedback (max 2 retries per subtask)
6. **Dynamically spawns** additional workers after the initial batch if gaps remain
7. **Rebalances** work across OpenCode and Codex while enforcing the 3+3 caps
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
    ┌─────────┼─────────┬─────────┐
    ▼         ▼         ▼         ▼
┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐
│OpenCode│ │OpenCode│ │ Codex  │ │ Codex  │
│Pool 1  │ │Pool 2  │ │Pool 1  │ │Pool 2  │
└───────┘ └───────┘ └───────┘ └───────┘

OpenCode max: 3 active workers
Codex max: 3 active workers
Global max: 6 active workers
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

- [Hermes Agent](https://github.com/NousResearch/hermes-agent)
- [OpenCode CLI](https://github.com/opencode-ai/opencode) installed and authenticated
- [Codex CLI](https://github.com/openai/codex) installed and authenticated

## Usage

Once installed, the skill auto-triggers when you say things like:

> "Spawn workers to refactor these files"
> "Use OpenCode to research this topic and supervise"
> "Delegate this task to cheaper models"
> "Parallelize this work across multiple agents"

Hermes will:
1. Break the task into subtasks
2. Allocate work across OpenCode and Codex pools
3. Enforce provider caps of 3 OpenCode + 3 Codex, max 6 total
4. Inspect worker logs/output and evaluate quality
5. Respawn or rebalance failed work
6. Combine everything into a final answer

Example valid fleet mixes:
- 3 OpenCode + 3 Codex
- 3 OpenCode + 1 Codex
- 2 OpenCode + 3 Codex

Example invalid fleet mixes:
- 4 OpenCode + 2 Codex
- 3 OpenCode + 4 Codex

## Helper Scripts

| Script | Purpose |
|--------|---------|
| `scripts/evaluate_output.py` | Score worker output against criteria (Presence / Correctness / Completeness / Quality) |
| `scripts/synthesize_outputs.py` | Merge multiple worker outputs into one deliverable |


## License

MIT
