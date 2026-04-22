# v2 migration criteria

This repo exists to prove whether the v2 architecture is genuinely better than v1.4 before touching the original implementation.

## Hard gate

Do not modify or replace v1.4 based on architecture preference alone.
Promote only after measured comparison.

## Success criteria

### Functional parity

v2 must cover the important v1.4 use cases that we actually care about:
- worker discovery
- worker spawning
- monitoring and cancellation
- evaluation of outputs
- output synthesis
- documentation of capabilities

### Reliability

v2 must show:
- passing local end-to-end evals
- reproducible runs
- clear terminal states
- no silent worker loss
- no ambiguous artifacts/log paths

### Extensibility

v2 should be easier than v1.4 to extend with:
- new CLI workers
- new HTTP endpoints
- custom Python workers
- Dockerized workers

Evidence:
- adapter contract is consistent
- new backends do not require orchestrator rewrites

### Observability

v2 must improve debugging versus v1.4 through:
- persisted handle files
- stdout/stderr logs per worker
- state transition history
- cost ledger
- evaluation artifacts

### Safety of migration

Before any update to the original repo:
- document the migration plan
- identify compatible pieces to port back
- avoid destructive tag/history rewrites
- preserve v1.4 as a rollback point

## Comparison checklist

Use this when comparing v2 to v1.4.

- [ ] Discovery works on the target machine
- [ ] Python adapter works end-to-end
- [ ] CLI adapter works end-to-end
- [ ] HTTP adapter works with at least one real endpoint or fixture
- [ ] Docker adapter works or is explicitly deferred
- [ ] State machine transitions are validated and auditable
- [ ] Cost tracking produces sane totals
- [ ] Synthesis works for multi-worker code outputs
- [ ] README and docs match actual repo behavior
- [ ] No critical regression relative to v1.4 workflows

## Decision outcomes

### If v2 is clearly better

Options:
1. Back-port selected scripts and docs into the original repo.
2. Make v2 the canonical repo and archive v1.4.
3. Keep both, with v1.4 as stable and v2 as experimental, until more runtime evidence exists.

### If v2 is not clearly better

Keep v1.4 as canonical.
Use v2 only as a sandbox for isolated experimentation.

## Minimum evidence required before promotion

At minimum, produce:
- passing `eval/e2e_v2.py`
- a short comparison note against v1.4
- a list of wins, regressions, and unknowns
- a recommendation: promote, partial back-port, or hold
