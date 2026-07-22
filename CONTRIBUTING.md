# Contributing

The fastest way to help is to attack an invariant, reproduce the failure, and add the smallest
test that proves it.

The active label is `v5.0.0-rc.1` / Python `5.0.0rc1`. Treat it as a release candidate; do not
describe it as stable v5.0.0 or broaden a local/simulated result into a live claim.

## Local checks

```powershell
python -m pip install -e ".[dev]"
python -m ruff check .
python -m pytest --cov=agent_physics --cov-report=term-missing --cov-fail-under=90
python -m agent_physics.cli preflight
```

## Change requirements

- Reference a `PROGRAM.md` capability or explain the new acceptance gate.
- Add tests for the normal path and at least one failure path.
- Do not introduce benchmark claims without raw reproducible evidence.
- Keep deterministic authorization and accounting outside LLM decisions.
- Document new effect semantics and adapter capability gaps.
- Update the Bob build log only when the change actually came from a real Bob session.
- Keep PageAgent metric-free unless an actual pinned integration executes the registered workload.
- Preserve `LOCAL`, `SIMULATION`, `SEALED REPLAY`, and `LIVE` distinctions in tests, artifacts,
  docs, screenshots, and benchmark records.
- Update [`docs/capability-status.md`](docs/capability-status.md) when a `PROGRAM.md` gate changes;
  passing tests alone do not satisfy external Bob, watsonx, deployment, eligibility, or submission
  gates.

## Commit scope

Keep runtime, benchmark, UI, and submission-material changes separable when possible. Never
commit credentials, private student information, or live emergency-operational data.
