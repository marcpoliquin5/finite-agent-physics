# Known limitations and claim boundaries

FINITE is a v5 **release candidate**, not a production control plane and not a stable v5 release.
This file is normative for demos and project-page claims. A passing local test narrows uncertainty;
it does not turn fixture, simulated, estimated, private, or human-unverified evidence into live fact.

## IBM and challenge evidence

- The repository does not yet contain a genuine entrant-owned IBM Bob build-log entry.
- MCP configuration and protocol tests prove compatibility, not substantive Bob use.
- No environment available during the current build contained watsonx credentials or a Bob session
  reference, so no live Granite receipt was produced.
- Injected inference tests are labeled `injected-test-double`; they are never IBM provider evidence.
- Eligibility, registration, team membership, rule acceptance, SkillsBuild completion, video
  publication, project-page publication, and submission receipts require entrant action.

## Workflow and scheduling limits

- Workflow execution is DAG-oriented. General loops, dynamic fan-out, recursive subgraphs, and
  speculative winner/loser branches are not production-supported semantics.
- Explicit integers in the JSON/YAML wire format must stay within JavaScript's exact safe-integer
  range. Internal sentinel defaults and signed-64-bit resource arithmetic remain wider; unbounded
  internal physical defaults are omitted from the wire and restored during compilation.
- Admission is conservative and can refuse work that might succeed under favorable stochastic
  conditions. A refusal is not a mathematical proof covering every possible backend or plan.
- Profile p50/p95 latency, failure, quality, cost, quota, and physical values are declared inputs;
  they are not continuously calibrated production telemetry.
- Heuristic scheduling is deterministic for the declared model but is not a globally optimal
  multi-resource solver.
- Runtime replanning is local and single-coordinator. There is no replicated scheduler, leader
  election, distributed lease service, work stealing, or high-availability failover.
- Live adaptive execution is intentionally locked to the backend profile selected during admission.
  A blocked provider can cause waiting, optional shedding, or a conservative residual refusal; the
  controller does not search for or silently use an unadmitted fallback at runtime.

## Physical-resource limits

- CPU time, peak RAM, peak VRAM, storage IO, network bytes, bandwidth, RTT, and egress cost are
  integer profile estimates used for conservative admission.
- Peak RAM, VRAM, and bandwidth use conservative top-`max_parallelism` aggregation and can
  overestimate concurrency when graph dependencies prevent overlap.
- Transfer bounds omit protocol overhead, retransmission, time-varying throughput, queueing,
  allocator fragmentation, cache/IO amplification, and infrastructure replication.
- Egress cost is a declared profile value and is not reconciled with a cloud invoice.
- The transport/RTT critical-path figure is a lower bound, not measured makespan.
- Energy and thermal use are explicitly unsupported because no hardware telemetry is present.

## Executor and adapter limits

- Fixture workers are trusted in-process Python callables. There is no hostile-code sandbox,
  filesystem/network isolation, syscall policy, or container boundary.
- One active process owns each locally active run. SQLite protects durable data but does not grant
  distributed ownership semantics.
- Cooperative cancellation is visible; uncooperative in-process work cannot always be force-killed.
- The watsonx SDK call runs in a thread and has no safe Python hard-kill. Late settlement can be
  refused, but production hard cancellation requires process isolation.
- Worst-case retry reservation is intentionally conservative and can refuse a probabilistically
  feasible attempt.
- Provider token use must be reported for Granite settlement. Missing usage fails rather than being
  fabricated as zero.
- Admitted micro-USD is a profile upper bound, not watsonx billing telemetry.

## Artifact, context, and semantic limits

- The artifact store is local and content-addressed; it is not a replicated, encrypted,
  multi-region object service.
- Sensitivity and trust fields are declared metadata. There is no enterprise DLP classifier,
  residency enforcement, KMS integration, or legal-hold service.
- Token fit uses a conservative estimator unless a provider tokenizer is explicitly integrated.
- Semantic safety checks cover declared StormShift invariants and structured evidence. They do not
  prove general natural-language entailment, factual truth, translation quality, accessibility
  conformance, or safety for arbitrary domains.
- Accessibility evidence combines structural checks with a recorded local desktop/mobile browser
  reflow, heading, control-label, console-error, and request-error audit. It is not a screen-reader
  test, assistive-technology certification, or full WCAG conformance audit.
- Hostile text is prevented from widening authority in tested paths; this is not a claim of universal
  prompt-injection immunity.

## Effect-system limits

- The effect adapter used in tests is simulation-only. No real alert, message, purchase, filing,
  account mutation, or government/agency system is contacted.
- Run and effect ledgers are separate SQLite transaction domains; there is no cross-database atomic
  transaction.
- Runtime idempotency keys are scoped by run, task, attempt, and declared logical key, preventing
  same-declaration effects from colliding across tested sequential, concurrent, and restarted runs.
  Crash ambiguity is repaired only when the target honors that same key and semantics; every
  production target requires its own proof.
- Approval grants use a local deterministic authority for testing; production human identity,
  OIDC/IAM, delegation, revocation, and audit review are not implemented.
- A run with an uncommitted proposed effect reports `awaiting_effects`, never `completed`.

## API and deployment limits

- The REST/SSE service implements exact-origin CORS, optional bearer authentication, bounded local
  active-run/control-event admission, health/readiness, and revision-fenced adaptive controls. It
  does not implement TLS termination, distributed rate limiting, OIDC, tenant RBAC, WAF policy,
  automated retention, or HA deployment.
- The in-memory active-task registry is process-local. Durable events survive restart, but automatic
  reattachment/relaunch policy remains an operator responsibility.
- The console's bearer token is kept in browser memory for the session; it is not an enterprise
  credential-management solution.
- The current Sites console deployment is owner-only. It is not an anonymous public judge path.
- No public FINITE API deployment has been verified from an unsigned or judge account.

## Verification and release limits

- Hashes are tamper-evident identities, not signatures or trusted timestamps. Authenticity requires
  an immutable public commit/tag or another trusted attestation.
- Production Survival timing comes from deterministic local fixtures and local SQLite. Its
  descriptive `pass^k`, recovery latency, and orchestration overhead do not measure live providers,
  distributed workers, sandbox startup, model correctness, or production infrastructure.
- The whole-run verifier is separate from live planner state in code and inputs, but ships in the
  same Python distribution; it is not an independently administered third-party audit.
- Coverage supports test evidence but does not prove missing semantics or external integrations.
- Release-candidate SBOM and SLSA-style provenance are deterministic local metadata. They are not a
  Sigstore signature, transparency-log entry, or trusted remote builder attestation.
- Candidate artifacts set `release_ready=false`. Stable `v5.0.0` is prohibited until every
  conjunctive gate in `V5_RELEASE_CONTRACT.md` passes at one clean public commit.

## Framework-comparison limits

- The neutral runner and exact-pinned LangGraph witness use the same local fixture fingerprint and
  guardrails. They do not represent every tuning option or production deployment of LangGraph.
- Plain Python and LangGraph provide reference/conformance evidence; a local benchmark cannot prove
  FINITE universally better than an ecosystem or framework.
- PageAgent is a DOM-native browser agent. It is documented for architectural comparison but is not
  executed on the non-equivalent StormShift orchestration workload. `not-executed` is not zero.
- LangChain ecosystem breadth, integrations, hosted LangSmith capabilities, and user adoption are
  outside this repository's comparison proof.
- τ-bench/τ²/τ³, BFCL, SWE-bench Verified, and Terminal-bench are not executed. The first two need
  a pinned paired live-model protocol; the code-execution suites additionally require a hardened
  hostile-code sandbox that FINITE does not yet implement.
- Any statistical headline must come from the preregistered contract, include failures, publish raw
  records, and pass its threshold. Negative or baseline wins must remain visible.

## Enterprise and compliance limits

- FINITE has no SOC 2 report, ISO 27001 certificate, or independently audited control period.
- No SAML, SCIM, tenant RBAC, customer VPC/BYOC, managed key custody, legal hold, regulated
  retention, WORM storage, or SEC/FINRA compliance attestation is implemented.
- Append-only local events and content digests are engineering primitives, not a regulated
  recordkeeping service. Any future Rule 17a-4 language must remain `aligned` or `designed for`
  until counsel and independent validation support a stronger claim.

## StormShift and public-safety boundary

- StormShift is fictional and demonstrates control-plane mechanics, not operational emergency
  expertise.
- No output should be used as an emergency instruction, accessibility certification, translation
  certification, or agency recommendation.
- Miami-Dade is challenge context only. FINITE is not affiliated with, endorsed by, connected to,
  or deployed by Miami-Dade County or any emergency/public-safety organization.
