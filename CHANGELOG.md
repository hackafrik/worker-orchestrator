# Changelog

## [1.2.0] - 2026-04-21

### Added
- Live end-to-end real-worker evaluation using actual OpenCode and Codex CLI processes
  on a controlled fixture repo (6 review workers + 3 fix workers, mixed-provider).
- Stdin-pipe spawn pattern for OpenCode workers: avoids all shell-quoting issues with
  multi-line prompts containing quotes, backticks, and newlines.
- `scripts/evaluate_worker_logs.py` helper for parsing worker terminal output into
  structured JSON findings.

### Changed
- Description trigger keywords optimized: added `use opencode`, `use codex`,
  `spawn opencode/codex`, `run opencode/codex`, `parallel code review`,
  `parallel fixes`, `delegate to codex/opencode`, `run agents in parallel`;
  removed overly broad generic triggers (`batch process`, `async workers`,
  `map-reduce`, `parallel execution`) that caused false positives.
- Spawning guidance in Step 2 and Pitfalls updated to recommend stdin piping
  via `subprocess.Popen(stdin=open(prompt_file))` as the primary method.

## [1.3.0] - 2026-04-21

### Added
- Expanded trigger keyword coverage: `delegate`, `distribute`, `fan-out`, `offload`,
  `scale out`, `agent swarm`, `subagent`, `spawn agents`, `async workers`,
  `run in parallel`, `batch process`, `map-reduce`, `worker pool`, `team of agents`.
- Live E2E evaluation report documenting provider rate-limit as primary failure mode.
- Provider circuit-breaker recommendation in Pitfalls section.

### Changed
- Description block restructured for compound-phrase matching (reduces false positives).
- Worker timeout guidance tightened: 60-90s when API quota is constrained.

### Fixed
- N/A (no bugfixes in this cycle)
