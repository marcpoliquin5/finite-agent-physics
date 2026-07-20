# FINITE delivery program: 60 capabilities with acceptance gates

`ROADMAP.md` explains the product surface. This file is the engineering contract. A capability
is not “done” because a UI element exists; it is done only when its acceptance gate passes.

## Must-win vertical slice (M01–M37)

| ID | Capability | Acceptance gate |
|---|---|---|
| M01 | Versioned workflow IR | YAML/JSON and Python compile to one stable hash; unknown fields fail closed. |
| M02 | Multi-mode tasks | Required, optional, alternative, and speculative modes compile deterministically. |
| M03 | Hard run contract | Deadline, integer resource caps, quality floor, and effect policy are explicit. |
| M04 | Typed artifact ports | Incompatible producer/consumer schemas fail before execution. |
| M05 | Static graph analyzer | Detect cycles, missing producers, unreachable tasks, unbounded retries, and illegal effects. |
| M06 | Immutable profile snapshot | Each run pins model/tool/version, prices, quotas, latency, quality, and sample time. |
| M07 | Bound analyzer | Separately labels theoretical physical bounds and deterministic planning-model bounds. |
| M08 | Admission certificate | Returns feasible/degraded/refused, assumptions, selected modes, and a stable digest; heuristic refusal is never called proof of infeasibility. |
| M09 | Admission controller | An impossible hard-resource run makes zero provider or external-tool calls. |
| M10 | Conservation ledger | 10,000 randomized transitions yield zero negative balances or hidden overspend. |
| M11 | Protected mandatory cost-to-go | Optional work cannot consume the resources reserved for mandatory completion. |
| M12 | Critical-path scheduler | Ready work orders by slack, criticality, downstream unlock, and declared utility. |
| M13 | Quota-aware backpressure | Simulated RPM/TPM/concurrency caps are never exceeded; retries do not amplify 429s. |
| M14 | Utility-aware optional work | Optional work launches only from unprotected headroom. |
| M15 | Event-driven replanning | Slowdown, failure, settlement, or envelope change records a new schedule decision. |
| M16 | Bounded resilience | Timeout, retry, jitter, circuit-breaker, and dead-letter behavior is deterministic in replay. |
| M17 | Cancellation and zombie control | Cancelled work exits within a bound; expired leases cannot settle or commit. |
| M18 | Persistent run state machine | Coordinator restart resumes without replaying completed effects or model work. |
| M19 | Content-addressed artifact store | Identical payloads deduplicate; mutations create new IDs and lineage. |
| M20 | Causal evidence graph | Every final claim traces to sources, transformations, and producing attempts. |
| M21 | Token-bounded context compiler | Packed context never exceeds cap and records inclusion/exclusion reasons. |
| M22 | Context obligations | Required facts, trust, freshness, and citations fit or execution refuses visibly. |
| M23 | Effect intent and preview | Mutating tasks emit a reviewable intent/diff, never a direct external write. |
| M24 | Risk policy and approval | High-risk intent stays blocked until a valid approval event. |
| M25 | Transactional effect outbox | Crash and duplicate-event tests yield one commit with an idempotent adapter. |
| M26 | Speculation firewall | Losing speculative branches cannot emit a committable external effect. |
| M27 | Adapter capability ABI | Compiler rejects required cancellation/checkpoint/effect semantics an adapter lacks. |
| M28 | Seeded simulator | Identical seed and input events produce identical decision and trace hashes. |
| M29 | Granite/BeeAI runtime adapter | Real run reports model ID, actual usage, latency, artifacts, and validation result. |
| M30 | Bob MCP integration | Bob calls `physics_preflight/run/status/explain/verify`; demo records real calls. |
| M31 | Typed REST/SSE interface | Client can submit, stream, approve, cancel, and inspect without database access. |
| M32 | Physics Console | DAG/Gantt shows critical path, slack, quotas, protected budget, spend, and effect state. |
| M33 | Structured decision explanations | Every dispatch/skip/downgrade cites numeric facts, never hidden chain-of-thought. |
| M34 | Recorded replay | Replay reproduces control decisions without recalling models or tools. |
| M35 | Fair benchmark and chaos harness | Same tasks/models/prompts/tools run all baselines under paired seeded faults. |
| M36 | StormShift workload | Capacity, routing, bilingual, accessibility, grounding, synthesis, and alert preview validate. |
| M37 | One-command delivery and Bob provenance | Demo starts predictably; Bob prompts, files, tests, commits, and evidence are mapped. |

## Stretch program (S01–S23)

| ID | Capability | Acceptance gate |
|---|---|---|
| S01 | Online profile calibration | P95 coverage and quality calibration report sample count and versioned updates. |
| S02 | Monte Carlo deadline estimator | Seeded completion probability matches simulator frequency within tolerance. |
| S03 | CP-SAT planner | Finds known optimum on small fixtures and safely falls back on timeout. |
| S04 | Information-value speculation | Improves verified utility under equal budget in registered noisy scenarios. |
| S05 | Correlation-aware hedging | Hedge launches only when tail benefit exceeds duplicate cost; loser cancels safely. |
| S06 | Multi-tenant fairness | Weighted-fair scheduling prevents starvation during sustained load. |
| S07 | Semantic/prefix cache | Cache key pins prompt, context, model, tool, policy, and validator versions. |
| S08 | Delta artifacts and hibernation | Workers transfer only changed artifacts and resume from bounded manifests. |
| S09 | Saga compensation | Reversible effect chains compensate in reverse order after injected failure. |
| S10 | Capability tokens | Attempt can access only resources/effects granted to its active lease. |
| S11 | PII taint and residency | Restricted artifacts cannot enter disallowed models, regions, logs, or contexts. |
| S12 | Sandboxed tool execution | CPU, time, filesystem, network, and output bounds stop hostile fixtures. |
| S13 | Distributed leases and fencing | A stale worker cannot settle resources or commit after reassignment. |
| S14 | Remote workers and work stealing | Compatible idle worker steals pure/read work without double settlement. |
| S15 | HA coordinator | Leader failover preserves monotonic event order and active reservations. |
| S16 | Data-locality scheduling | Scheduler accounts for RTT, bytes, egress cost, sovereignty, and cache locality. |
| S17 | A2A adapter | Agent-card capabilities map to explicit adapter levels; gaps remain visible. |
| S18 | LangGraph and BeeAI wrappers | Imported workflows preserve semantics or emit blocking conversion warnings. |
| S19 | OpenTelemetry/OpenInference export | Spans correlate by run/attempt without exposing secrets or private payloads. |
| S20 | OPA/Rego policy engine | Same policy applies at compile, dispatch, context, and commit boundaries. |
| S21 | Signed run manifest/AIBOM | Verify detects altered prompt, profile, artifact, policy, tool, or event data. |
| S22 | Retention and deletion | Expired payloads delete while permitted hashes and lineage remain auditable. |
| S23 | Counterfactual/Pareto lab | Completed trace answers envelope/provider what-ifs and plots a measured frontier. |

## First-place evaluation gates

### Correctness

- Zero hard-budget violations across at least 10,000 randomized ledger/state-machine trials.
- Zero duplicate local commits across duplicate, reordered, crash, and restart effect tests.
- Required context obligations either reach 100% recall or produce explicit infeasibility.
- Recorded replay produces identical decision hashes.
- Impossible contracts issue zero model/tool calls.
- No irreversible effect can execute without approval and idempotency evidence.

### Performance hypotheses

These are go/no-go targets, not claims:

- Against the better of sequential and tuned static-parallel baselines, achieve either
  25% lower faulted p95 latency at equal verified quality/budget or 20% greater verified
  utility at equal deadline/budget in at least two preregistered stress regimes.
- Reduce tokens per successful StormShift run by at least 30% without losing more than three
  percentage points of mandatory-output quality.
- Keep scheduler overhead below 5% of wall time.
- Improve faulted SLO pass rate by at least 20 percentage points over the best baseline.

### Experimental discipline

- At least 30 paired deterministic seeds per condition and 10 paired live-model trials where credits permit.
- Identical DAG, prompts, model menu, tools, validators, failure seeds, hardware, and cache rules.
- Cold- and warm-cache results reported separately.
- Wilson intervals for pass rates and bootstrap intervals for latency/cost distributions.
- Raw JSONL traces, summary CSV, aggregation code, scenario generator, and exact commit published.
- Energy claims prohibited without measured hardware telemetry.

## Three-minute proof sequence

1. Bob calls FINITE to preflight the StormShift contract.
2. Console shows the planning-model bound, binding quota, critical path, protected budget, and quality frontier.
3. Baseline and FINITE start with identical Granite tasks and a visible countdown.
4. One control injects compute loss, a 429 burst, and a reduced remaining budget.
5. FINITE replans, preserves bilingual/accessibility/safety work, and cancels optional enrichment.
6. A final sentence opens into its artifact, checksum, model/tool call, validator, and policy lineage.
7. The publication effect remains blocked until the human approves the exact preview.
8. An impossible eight-second replay refuses before spending a token and names the binding constraint.
9. Paired benchmark results and confidence intervals appear.
10. Bob build evidence and a live Granite request prove IBM technology is structural.
