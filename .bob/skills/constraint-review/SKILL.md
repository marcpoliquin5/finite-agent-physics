---
name: constraint-review
description: Review Agent Physics workflow, scheduler, or adapter changes for envelope, quality, provenance, and side-effect invariant violations
---

<Steps>
<Step>
Read `docs/architecture.md`, `docs/ibm-bob-fit.md`, and the changed code before reviewing.
</Step>
<Step>
Identify every constraint the change reads, transforms, reserves, enforces, or reports.
</Step>
<Step>
Attempt to construct counterexamples involving deadline pressure, exhausted budgets, stale
artifacts, provider failure, retries, conflicting writes, delayed approval, and malformed IR.
</Step>
<Step>
Verify that no LLM output can authorize itself, widen a capability, or weaken a hard floor.
</Step>
<Step>
Add focused regression tests for confirmed gaps. Run the full test and lint suites.
</Step>
<Step>
Summarize findings by invariant, severity, evidence, and accepted correction. Record the real
session in `docs/bob-build-log.md` after human review.
</Step>
</Steps>
