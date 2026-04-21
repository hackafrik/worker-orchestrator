# Changelog

## [1.4.0] - 2026-04-21

### Added
- Live end-to-end real-worker evaluation using actual OpenCode and Codex CLI processes
  on a controlled fixture repo (6 review workers + 3 fix workers, mixed-provider).
- Stdin-pipe spawn pattern for OpenCode workers: avoids all shell-quoting issues with
  multi-line prompts containing quotes, backticks, and newlines.
- `scripts/evaluate_worker_logs.py` helper for parsing worker terminal output into
  structured JSON findings.
- Provider circuit-breaker: detect 429 rate-limit errors and auto-respawn
  on alternate provider immediately. Includes `scripts/circuit_breaker.py` helper.

### Changed
- Description trigger keywords optimized: added `use opencode`, `use codex`,
  `spawn opencode/codex`, `run opencode/codex`, `parallel code review`,
  `parallel fixes`, `delegate to codex/opencode`, `run agents in parallel`;
  removed overly broad generic triggers (`batch process`, `async workers`,
  `map-reduce`, `parallel execution`) that caused false positives.
- Spawning guidance updated to recommend stdin piping via
  `subprocess.Popen(stdin=open(prompt_file))` as the primary method.
- Worker timeout guidance tightened: 60-90s when API quota is constrained.

### Fixed
- N/A (no bugfixes in this cycle)
