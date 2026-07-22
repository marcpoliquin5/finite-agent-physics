## Promise or invariant changed

Describe the user-visible promise, failure mode, or `PROGRAM.md` acceptance gate this changes.

## Evidence

- [ ] Normal-path test
- [ ] Failure-path test
- [ ] Replay/tamper test where evidence or state changes
- [ ] `python -m ruff check .`
- [ ] `python -m pytest --cov=agent_physics --cov-fail-under=90`
- [ ] Wheel/sdist build, package-content validation, SBOM/provenance/checksum verification
- [ ] Python and npm advisory audits (network-backed and time-varying)
- [ ] Console tests/lint/audit when UI or exported artifacts change

## Claim and trust boundary

- [ ] Simulation, fixture, static comparator, replay, and live evidence remain explicitly labeled.
- [ ] No model output authorizes resource use, policy changes, approvals, or effects.
- [ ] No credentials, private data, or real emergency-operational data are included.
- [ ] Bob provenance was changed only for a real Bob session.

## Compatibility and release notes

Document schema/API changes, migration needs, optional dependencies, and any new limitation.
