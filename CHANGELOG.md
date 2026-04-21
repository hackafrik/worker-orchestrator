# Changelog

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
