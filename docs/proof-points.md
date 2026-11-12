# Fifty proof points

The full list of behaviors that FINITE implements and tests locally, each inside its stated
boundary. The [README](../README.md) summarizes these in six groups; the acceptance gate for each
capability is in [PROGRAM.md](PROGRAM.md) and its audited status in
[capability-status.md](capability-status.md).

Everything below is implemented and locally test-backed inside its stated boundary:

1. Strict schema versions and unknown-field rejection.
2. Duplicate-key rejection for JSON and YAML.
3. Canonical cross-format workflow digests.
4. Typed task input/output ports and pre-execution compatibility checks.
5. Dependency, cycle, missing-producer, and graph-shape validation.
6. Required and optional work with protected mandatory ancestry.
7. Conservative deadline, token, cost, context, quality, and reliability admission.
8. Signed-int64 overflow refusal rather than wraparound.
9. CPU-time admission in integer CPU-milliseconds.
10. Conservative peak RAM and VRAM admission.
11. Storage-read and storage-write byte admission.
12. Network ingress, egress, bandwidth, RTT, and egress-cost admission.
13. A separately labeled transport/RTT critical-path lower bound.
14. An explicit physical-resource coverage and limitations matrix.
15. Global and per-provider concurrency enforcement.
16. RPM, TPM, reset-window, bounded-retry, and 429-suppression accounting.
17. Protected multi-resource cost-to-go for mandatory work.
18. Optional-work shedding when headroom disappears.
19. Runtime residual replanning after bounded provider, capacity, budget, and coordinator-recovery events,
    with no fallback to a profile that did not pass logical and physical admission.
20. Durable revision and decision digests for every replan.
21. Absolute deadlines, bounded retries, cooperative cancellation, and visible uncooperative work.
22. Manifest-bound SQLite resume without recalling completed fixture work.
23. Durable content-addressed artifact put/get/dedup with lineage checks.
24. All-or-refuse context packing under byte and token caps.
25. Explicit freshness, contradiction, evidence, and hostile-context handling.
26. Declared adapter capabilities with structured semantic-loss reporting.
27. An optional bounded watsonx.ai/Granite executor adapter with redacted receipts.
28. A structural and semantic-safety pipeline for the fictional StormShift workload.
29. Declared writes diverted from workers into durable proposed effect intents.
30. Exact-scope, time-bound approval grants for high-risk simulated effects.
31. Transactional outbox, idempotency, fencing, crash ambiguity, and compensation drills.
32. Append-only causal run events with public-fact decision explanations.
33. A no-provider-call whole-run verifier that consumes sealed evidence.
34. Mutation rejection across resource, artifact, context, approval, effect, and manifest identities.
35. A real pinned LangGraph conformance witness plus an explicit conversion-loss ledger.
36. Preregistered paired fault and production-survival laboratories with raw records, pass^k,
    p50/p95/p99 recovery timing, local overhead, and duplicate-effect metrics.
37. Twenty-three Bob-callable MCP tools tested through a real STDIO handshake.
38. Typed submit, status, inspect, cancel, approve, and resumable event-stream HTTP routes.
39. A live console that keeps bearer credentials in memory and distinguishes static evidence.
40. Deterministic release-candidate checksums, SBOM, provenance, package inspection, and offline verification.
41. JavaScript-safe workflow integers with browser round-trip and overflow rejection tests.
42. Public liveness and fail-closed readiness probes that exercise both durable stores.
43. Exact-origin CORS, constant-time bearer checks, bounded request bodies, and strict JSON parsing.
44. Start-paused execution with revision-fenced budget, provider, capacity, reset, and resume controls.
45. Call-free controller replay that binds every transition to prior/next state and control digests.
46. Coordinator-crash recovery that charges ambiguous in-flight work without recalling it.
47. Explicit admission caps and monotonic SSE cursors, exercised by a digest-bound 64-run real-socket load proof.
48. Run-scoped effect idempotency proven across sequential, concurrent, and restarted executions.
49. A non-root, read-only, capability-dropped OCI runtime with bounded CPU, memory, PIDs, and writable state.
50. Zero-skip JUnit evidence, separate statement/branch gates, pinned dependency audits, and source/image scans.

These are not claims of universal superiority, production readiness, live Granite success, or
deployment by Miami-Dade County. The exact limits are maintained in [limitations](limitations.md).
