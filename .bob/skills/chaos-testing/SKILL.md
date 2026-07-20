---
name: chaos-testing
description: Design and run reproducible Agent Physics failure-injection tests for deadlines, providers, artifacts, workers, budgets, and side effects
---

<Steps>
<Step>
Select one committed scenario and seed. State which behavior is real, simulated, or replayed.
</Step>
<Step>
Inject one fault at a time before combining faults: 429 burst, tail latency, outage, timeout,
malformed output, stale evidence, worker crash, checksum mismatch, budget cut, or approval denial.
</Step>
<Step>
Assert the SLO result, mandatory output status, resource totals, recovery path, provenance,
and external-effect count. A graceful declared failure is preferable to false success.
</Step>
<Step>
Persist raw results and make the exact run reproducible from one documented command.
</Step>
<Step>
Run regression tests and record only measured outcomes in the build log and benchmark report.
</Step>
</Steps>
