# Contributing

The fastest way to help is to attack an invariant, reproduce the failure, and add the smallest
test that proves it.

## Local checks

```powershell
python -m pip install -e ".[dev]"
python -m ruff check .
python -m pytest --cov=agent_physics --cov-fail-under=85
python -m agent_physics.cli preflight
```

## Change requirements

- Reference a `PROGRAM.md` capability or explain the new acceptance gate.
- Add tests for the normal path and at least one failure path.
- Do not introduce benchmark claims without raw reproducible evidence.
- Keep deterministic authorization and accounting outside LLM decisions.
- Document new effect semantics and adapter capability gaps.
- Update the Bob build log only when the change actually came from a real Bob session.

## Commit scope

Keep runtime, benchmark, UI, and submission-material changes separable when possible. Never
commit credentials, private student information, or live emergency-operational data.
