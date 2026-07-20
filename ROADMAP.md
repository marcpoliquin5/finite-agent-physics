# Agent Physics: 50-capability competition program

This is a coherent systems program, not a promise that every item will be production-complete
by July 31. `P0` items form the judged vertical slice; `P1` items deepen the evidence and
experience; `P2` items demonstrate a credible post-hackathon trajectory.

## A. Constraint language and compiler

1. **P0 - Typed task contracts:** dependencies, inputs, outputs, optionality, value, and quality floor.
2. **P0 - Resource profiles:** p50/p95 latency, tokens, cost, context bytes, failure probability, provider.
3. **P0 - Effect contracts:** pure, read, idempotent write, reversible write, irreversible write.
4. **P0 - Graph validation:** missing dependencies, cycles, duplicate IDs, and invalid effects fail closed.
5. **P0 - Execution envelopes:** global deadline, token, cost, context, concurrency, and provider quotas.
6. **P0 - Constraint-carrying workflow IR:** preserve SLO, artifact, trust, degradation, and effect obligations across adapters.
7. **P1 - Static dependency inference:** infer data dependencies while requiring confirmation for effects.
8. **P0 - Admission certificate:** provide a feasible schedule witness or a conservative refusal;
   heuristic failure is never labeled proof of infeasibility.
9. **P2 - Temporal constraints:** earliest start, latest finish, recurring windows, and calendar dependencies.
10. **P2 - Multi-tenant fairness contracts:** per-team quotas, priorities, and starvation protection.

## B. Scheduling and topology

11. **P0 - Upward-rank critical-path scheduler:** prioritize work whose delay threatens the run.
12. **P0 - Bounded provider queues:** enforce global and provider-specific parallelism.
13. **P0 - Adaptive backend routing:** choose the least expensive profile that preserves the SLO.
14. **P0 - Optional-work shedding:** drop low-value branches before violating required work.
15. **P0 - Serial bypass:** avoid multi-agent overhead when estimated parallelism is too low.
16. **P0 - Receding-horizon controller:** re-solve the remaining plan when capacity, budget, or latency changes.
17. **P1 - Tail-aware speculation:** race only safe work when expected deadline benefit exceeds cost.
18. **P1 - Early cancellation:** stop losing speculative branches and irrelevant downstream work.
19. **P1 - Dynamic topology selection:** switch among solo, pipeline, fan-out, map-reduce, and debate.
20. **P2 - Distributed work stealing:** rebalance ready tasks across heterogeneous workers.

## C. Context and memory physics

21. **P0 - Context byte accounting:** charge context movement as a real resource.
22. **P1 - Content-addressed artifacts:** pass immutable references rather than full transcripts.
23. **P0 - Artifact obligations:** declare required schemas, trust, freshness, citations, and permitted read sets.
24. **P1 - Deduplicated retrieval:** reuse shared evidence across branches without repeated model input.
25. **P1 - Loss-aware compaction:** measure retained facts and citations after compression.
26. **P2 - Context locality scheduler:** place work near cached artifacts and data-sovereignty zones.
27. **P2 - Epistemic freshness budgets:** expire or revalidate stale evidence based on risk.
28. **P2 - Shared-memory conflict detection:** prevent agents from silently overwriting assumptions.

## D. Effect safety and governance

29. **P0 - Effect conflict serialization:** incompatible writes to one resource cannot overlap.
30. **P0 - Approval gates:** irreversible effects require an explicit grant.
31. **P0 - Idempotency enforcement:** retryable writes require a stable operation key.
32. **P1 - Capability-scoped tools:** each task sees only the network, data, and tools it needs.
33. **P1 - Transactional outbox:** separate durable intent from external side-effect delivery.
34. **P1 - Compensation plans:** reversible effects declare and test rollback handlers.
35. **P1 - Data-class policies:** constrain PII, secrets, regulated data, and residency.
36. **P1 - Prompt-injection taint tracking:** untrusted artifacts cannot silently authorize effects.
37. **P2 - Policy-as-code bundles:** signed, versioned rules with preflight and runtime enforcement.
38. **P2 - Human accountability map:** every consequential decision has an owner and escalation path.

## E. Reliability, evidence, and observability

39. **P0 - Deterministic event stream:** starts, choices, skips, completions, and policy decisions are replayable.
40. **P0 - Trace verifier and conservation ledger:** reconstruct simulator reservations now;
   distinguish estimated, reserved, and actual executor usage before broader claims.
41. **P1 - Durable SQLite checkpoints:** resume without replaying completed model or tool work.
42. **P1 - Causal trace graph:** connect inputs, artifacts, decisions, effects, and final claims.
43. **P1 - Counterfactual replay:** ask how a different budget or provider would have changed the run.
44. **P1 - Fault injection:** latency spikes, provider errors, malformed output, crashes, and stale data.
45. **P1 - Live control plane:** Gantt, critical path, queues, budgets, effects, and adaptation explanations.
46. **P1 - OpenTelemetry export:** integrate with existing production observability systems.

## F. Interoperability, evaluation, and delivery

47. **P0 - Bob MCP + Granite/watsonx adapters:** make Bob the first client and IBM inference structurally visible.
48. **P0 - Reproducible baselines:** sequential ReAct and tuned static LangGraph under identical constraints.
49. **P0 - Statistical benchmark report:** p50/p95, success, quality, cost, context, SLO misses, and confidence intervals.
50. **P0 - Submission-grade delivery:** public repo, tests, architecture, Bob evidence, accessible demo, and three-minute video.

## Definition of “first-place ready”

The entry is not ready because the feature count reaches 50. It is ready only when:

- the core adaptation is visible in one glance;
- every headline claim is backed by reproducible data;
- the tuned static baseline receives a fair comparison;
- an injected failure demonstrates recovery and zero duplicate effects;
- IBM Bob usage is substantive and auditable;
- IBM technology is visible in the running system;
- the demo works live and has a deterministic fallback;
- the README lets a judge reproduce the result in under ten minutes.
