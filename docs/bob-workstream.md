# IBM Bob primary-development workstream

The challenge requires IBM Bob to be a core development tool. Architecture assistance or
code produced elsewhere does not remove that obligation. The participant must perform and
record real Bob sessions that materially shape the submitted system.

## Bob-owned work packages

These packages are intentionally substantial and judge-visible:

1. **B1 - Durable ledger:** implement SQLite event persistence, crash recovery, migrations,
   and replay tests from the deterministic event schema.
2. **B2 - Async executor:** convert schedule decisions into cancellable `asyncio` execution
   with provider semaphores, token buckets, timeouts, and structured retries.
3. **B3 - IBM adapter:** implement watsonx/Granite planning and synthesis, capture usage, and
   add a fixture-backed offline fallback.
4. **B4 - Control plane:** implement the live DAG/Gantt/resource/effect visualization and its
   adaptation explanation panel.
5. **B5 - Benchmark laboratory:** implement workload runners, LangGraph baseline, fault
   injection, raw result capture, and statistical report generation.
6. **B6 - Hardening:** have Bob analyze scheduler invariants, generate adversarial tests,
   diagnose failures, and improve documentation from actual test evidence.

## Minimum evidence for each package

- timestamp and Bob session identifier or screenshot reference;
- the prompt or task given to Bob;
- Bob’s plan and the files it created or changed;
- human corrections and rejected suggestions;
- tests run and their output;
- commit SHA containing the accepted work;
- short explanation of why Bob’s contribution was material.

Do not optimize for line count. Optimize for traceable responsibility across planning,
implementation, testing, and iteration.
