# FINITE v5.0.0 release contract

- Status: normative release contract
- Current candidate: `v5.0.0-rc.1` / Python `5.0.0rc1`
- Audit target: the published release-candidate commit; record its exact SHA in the release receipt
- Audit date: 2026-07-22
- Submission deadline: 2026-07-31 23:59 America/New_York

This document turns the ambition of a "v5" release into evidence gates. A version label does not
upgrade an unproved capability. `v5.0.0` may be tagged only when every gate in this contract passes
against one immutable commit. Until then, release candidates must use a prerelease label such as
`v5.0.0-rc.1`, and repository claims must remain inside the evidence boundary below.

`PROGRAM.md` remains the source of the individual M01-M37, S01-S25, and R01-R08 acceptance-gate
wording. This contract adds the release-wide conditions, ownership, sequencing, and manifest rules
needed to make those gates auditable.

## Status vocabulary

| Status | Meaning in this audit |
|---|---|
| **Pass** | The complete `PROGRAM.md` acceptance gate has direct code and executable evidence at the audited commit, within the explicitly stated local or simulation scope. |
| **Partial** | Useful implementation exists, but one or more material clauses of the gate are missing or unproved. |
| **Absent** | The capability described by the gate is not implemented. Adjacent primitives do not count. |
| **External-blocked** | Code may exist, but the required Bob, provider, account, public-service, identity, or human evidence is not present. External-blocked never counts as Pass. |

No documentation statement, UI label, test count, star count, or generated mock receipt is itself
proof of a capability.

## Evidence-bound current state

The release-candidate label is `v5.0.0-rc.1` and its Python distribution version is `5.0.0rc1`.
Stable `v5.0.0` remains blocked by this contract. The current local full suite passed on the
Windows worktree; final test and coverage numbers must be read from generated candidate/CI
evidence at the immutable release commit. That verification was local: it did not call IBM Bob,
watsonx, Granite, GitHub, a public FINITE service, Alibaba PageAgent, BeeAI, or an external effect
target.

Strong evidence in the current worktree includes:

- strict workflow v2 compilation with typed artifact ports and adapter requirements;
- logical and declared physical-resource admission with protected mandatory cost-to-go;
- an independently replayed 10,000-transition integer resource ledger;
- a modeled RPM/TPM/concurrency/reset quota state machine with a seeded burst corpus;
- digest-bound residual replanning plus an adaptive crash/restart/call-free replay drill;
- public-fact scheduler explanations with one record per scheduler event;
- bounded context packing and controlled semantic/adversarial safety checks;
- a simulation-only SQLite approval, fencing, idempotency, outbox, ambiguity, and compensation
  effect kernel;
- durable fixture/model-worker seams, run state, and restart-safe artifact lineage;
- a structural StormShift runtime and validator;
- a 450-record deterministic simulated experiment design;
- neutral/LangGraph conformance and semantic-loss accounting;
- a digest-bound local fair-comparison contract with one excluded warmup and 30 preregistered
  measured seeds per executed system, plus a hard metric-free PageAgent boundary;
- an independent sealed whole-run verifier;
- a bearer-protected local REST/SSE interface; and
- a 22-tool Bob-compatible MCP surface plus a watsonx worker tested with fake inference.

The candidate contains no genuine Bob session entry in `docs/bob-build-log.md`, no redacted
live-Granite receipt, no public GitHub/tag/CI evidence, no public API, no live faulted
cross-framework benchmark, and no eligibility, SkillsBuild, public video, project-page, or
submission receipt. The documented Sites deployment is verified owner-only, not an anonymous judge
path. Its mutable deployment version is intentionally not a release identity; the immutable
repository commit and evidence digests are. Those absences are release blockers, not paperwork to
infer later.

### Capability count in the current audit

| Program | Pass | Partial | Absent | Total |
|---|---:|---:|---:|---:|
| Must-win M01-M37 | 19 | 17 | 1 | 37 |
| Stretch S01-S25 | 0 | 11 | 14 | 25 |
| **All capabilities** | **19** | **28** | **15** | **62** |
| Integrated proofs R01-R08 | 3 | 5 | 0 | 8 |

These counts are deliberately conservative. A partial gate is not rounded up because its remaining
work appears straightforward.

## M01-M37 audit

| ID | Status | Current candidate evidence | Blocking remainder |
|---|---|---|---|
| M01 | Pass | `workflow_ir.py`; strict Python/JSON/YAML schema-v2 identity, typed ports/adapter/physical fields, and fail-closed rejection tests. | None inside the local interchange scope. |
| M02 | Partial | Required/optional scheduling is deterministic. | Alternative and speculative modes, groups, and compile-hash tests are absent. |
| M03 | Pass | Typed deadlines, integer caps, quality/reliability, and effect policy in `contracts.py` with contract/scheduler tests. | None inside the local typed-contract scope. |
| M04 | Pass | Versioned input/output ports bind schema, version, media type, producer task, and producer port; incompatible, missing, and unreachable producers fail before execution. | None inside the local typed-port gate. |
| M05 | Partial | Graph validation rejects cycles, duplicate IDs, unknown dependencies, and illegal effects. | Missing-producer, unreachable-task, unbounded-retry, and structured multi-finding analysis are incomplete. |
| M06 | Pass | Strict profile snapshots bind component versions, prices, quotas, latency/resources, quality/failure calibration, failure domains, samples, validity, and one canonical digest; executor admission requires the registered snapshot when configured. | Online calibration remains S01. |
| M07 | Pass | Planning-model bounds and declared physical transport/RTT/dependency-path bounds are separately named, derived, and tested. | No physical measurement claim. |
| M08 | Partial | `FeasibilityCertificate` is stable, verifiable, and emits feasible/degraded/refused outcomes. | Include all task modes and machine-readable solver completeness/refusal basis. |
| M09 | Pass | Executor admission-refusal tests assert zero worker calls before dispatch. | Real adapters must preserve the same ordering, but the local gate passes. |
| M10 | Pass | `resource_ledger.py` and `tests/test_resource_ledger_10k.py` prove local integer conservation and tamper rejection over exactly 10,000 seeded transitions. | None inside the deterministic local-ledger scope. |
| M11 | Pass | Scheduler tests prove optional work cannot consume protected mandatory multi-resource cost-to-go. | None inside declared additive resources. |
| M12 | Partial | Slack, criticality, rank, and utility influence scheduling. | Explicit downstream-unlock score, surfaced priority vector, and adversarial tie tests are missing. |
| M13 | Pass | `provider_quota.py` and its tests cover modeled RPM, TPM, concurrency, fixed resets, 429 suppression, bounded retry, settlement, and replay. | Production/shared quota is a separate S13/runtime concern; it is not claimed here. |
| M14 | Pass | Scheduler admits optional work only from headroom remaining after protected completion. | None for deterministic planning. |
| M15 | Partial | `replanning.py` records deterministic residual decisions for slowdown, failure, capacity, and envelope events. The live controller applies full scheduler/adapter/physical admission, binds exact admitted profiles, refuses unsafe fallback, preserves settled work, atomically caps concurrent controls, retires terminal sessions, and supports call-free durable replay. | Make the general replanner the mutation controller for every active-executor event and complete first-class settlement-trigger coverage. |
| M16 | Partial | Executor tests cover deadlines, bounded retries, deterministic seeded jitter, circuit opening, redacted dead-letter events, cancellation, and restart-aware attempt counts. | One restart/recorded-replay proof must reproduce the complete resilience decision trace. |
| M17 | Partial | Cooperative cancellation and uncooperative-worker detection are tested locally. | Expiring attempt leases, resource/effect fencing, and process-isolated termination are absent. |
| M18 | Partial | Durable fixture/watsonx-worker state and adaptive restart avoid recalling completed work; unknown in-flight use is charged conservatively. | Run/effect/artifact stores are not one atomic distributed recovery boundary. |
| M19 | Pass | SQLite artifact put/get/dedup persists across restart with parent referential integrity, immutable attempt/transformation provenance, and full-store verification. | No remote replication or distributed-store claim. |
| M20 | Partial | Attempt-linked artifact provenance and whole-run artifact/claim causality checks now exist. | The active runtime does not universally require a complete traversal to authenticated external sources for every final claim. |
| M21 | Pass | `ContextPacker` enforces caps and records deterministic inclusion/exclusion and refusal reasons. | None inside its conservative token-estimation scope. |
| M22 | Partial | Required claims/artifacts, freshness, contradictions, bounded controlled citations, trust separation, and all-or-refuse packing are tested. | General citation placement, authenticated source trust, and open-ended entailment remain unsupported. |
| M23 | Pass | Declared writes become durable proposed intents or are refused; fixture workers do not directly write. | Production adapters remain outside this local gate. |
| M24 | Pass | Exact-scope, time-bound approval is required for high-risk simulated intents. | Production identity/IAM evidence is not claimed. |
| M25 | Pass | Simulation-only transactional outbox, run-scoped target idempotency, ambiguity recovery, and sequential/concurrent/restart/duplicate tests isolate same-declaration effects and yield one apply. | Each production target needs equivalent idempotency evidence. |
| M26 | Absent | No speculation-group or winner state exists. | Add quarantine, winner selection, loser cancellation, and non-committable loser proof. |
| M27 | Pass | Compiler-visible adapter requirements and executor admission enforce cancellation, checkpoint, streaming, usage, supported effects, fencing, and hidden-retry bounds before dispatch. | Remote adapter attestation is not claimed. |
| M28 | Partial | Seeded benchmarks, quota traces, replans, and scheduler explanations replay deterministically in their subsystems. | One input-event-driven simulator with unified decision and trace hashes is absent. |
| M29 | Partial / External-blocked | The watsonx worker runs inside durable executor state, requires provider usage, validates a public receipt, and resumes without recall in fake-inference tests. | Capture one genuine live-watsonx receipt bound to the release run and commit. |
| M30 | Partial / External-blocked | Twenty-two local MCP tools and a real STDIO handshake are tested; durable preflight/run/status/explain/verify exists. | Capture those calls in one genuine IBM Bob session with preserved run/trace/commit evidence. |
| M31 | Pass | Health/readiness plus bearer-protected local lifecycle, start-paused controls, zero-call replay, approval, caps, and resumable SSE operate over durable state. A digest-bound default load proof independently re-verifies 64 real-loopback runs, effect isolation, and zero external commits/calls during replay. | This is local fixture evidence, not a hosted-capacity claim; public hosting, TLS termination, OIDC, tenant RBAC, distributed rate limits, and HA are not claimed. |
| M32 | Partial | The sealed/live-aware console renders DAG/Gantt/critical path, logical/physical pressure, protected budgets, effects, and replay evidence. A local desktop/mobile browser flow exercises paused launch, controls, resume, SSE, replay, and run-scoped effect inspection without console/request errors. | The Sites URL is owner-only and the complete judge-accessible hosted path and assistive-technology audit are unverified. |
| M33 | Pass | `decision_explanations.py` and its tests bind one public-fact/rule-ID record to every deterministic scheduler event, including completion and refusal, with reasoning access explicitly false. | Other planes need comparable explanations, but the scheduler gate passes. |
| M34 | Pass | Adaptive controller records replay without workers and reproduce state/decision/control digests; whole-run replay binding and subsystem replayers fail closed on mutation. | No live-model semantic re-execution claim. |
| M35 | Partial | The executable fair benchmark fixes one excluded warmup plus 30 preregistered measured seeds per executed system, requires actual local FINITE/plain receipts, conditionally executes only the exact LangGraph pin, keeps PageAgent unexecuted and metric-free, and emits paired intervals without a winner; the separate simulator slice adds paired fault transforms. | Identical live tasks/models/tools/validators/cache rules, paired runtime faults, hardware disclosure, and immutable raw live evidence are absent. |
| M36 | Partial | StormShift adds bounded citation, bilingual controlled-fact, freshness, URL, authority-taint, safety, and static-accessibility gating plus a registered adversarial corpus and a local desktop/mobile rendered-browser reflow/error audit. | Live Granite, general entailment/translation quality, screen-reader testing, and full WCAG conformance remain unproved. |
| M37 | Partial / External-blocked | CLI/MCP/API entry points, deterministic candidate generation/offline verification, cross-platform CI gates, and Bob templates exist. | Fresh-clone timing, genuine Bob provenance, immutable public release, and judge-path verification are absent. |

## S01-S25 audit

| ID | Status | Current candidate evidence | Blocking remainder |
|---|---|---|---|
| S01 | Partial | Strict snapshots represent observed sample counts/provenance, p95 metrics, quality/failure calibration, validity, and deterministic versions. | No online collector/updater or successive-version coverage report. |
| S02 | Absent | Deterministic scenario simulation exists. | Monte Carlo completion estimator and frequency-calibration proof. |
| S03 | Absent | A small exhaustive additive oracle exists. | CP-SAT model, known optima, bounded timeout, safe incumbent, and fallback. |
| S04 | Absent | No information-value speculation exists. | Registered noisy scenarios and equal-budget verified-utility proof. |
| S05 | Absent | No hedging controller exists. | Correlation-aware tail benefit/cost decision and safely cancelled loser. |
| S06 | Absent | Scheduling is single-run. | Weighted-fair tenant queues, isolation, and starvation proof. |
| S07 | Absent | No semantic/prefix cache exists. | Fully versioned cache key, invalidation, expiry, and cold/warm evidence. |
| S08 | Partial | Content addresses and manifest-bound resume exist. | Bounded hibernation manifests, delta-only transfer, and measured transfer proof. |
| S09 | Partial | One reversible intent can compensate idempotently. | Durable multi-step saga and strict reverse-order compensation under injected failures. |
| S10 | Absent | Effect fences/approvals are not attempt capability tokens. | Lease-bound least-authority access across artifacts, tools, and effects. |
| S11 | Partial | Artifacts carry sensitivity labels. | PII taint propagation and enforced model/region/log/context/residency policy. |
| S12 | Absent | Fixture callables are trusted in-process code. | CPU/time/filesystem/network/output sandbox with hostile-fixture tests. |
| S13 | Partial | The effect broker rejects stale effect fences. | Distributed run/attempt leases and stale-worker rejection across settlement, artifacts, and effects. |
| S14 | Absent | No remote worker protocol or stealing queue. | Compatible pure/read work stealing with fenced settlement. |
| S15 | Absent | SQLite is a single-coordinator boundary. | Leader election, replicated monotonic ordering, and active-reservation failover. |
| S16 | Partial | Physical admission accounts for bytes, bandwidth, RTT, and egress; snapshots bind provider/model/tool/region failure domains. | Artifact location, sovereignty, residency, cache locality, and placement optimization are absent. |
| S17 | Absent | No A2A adapter exists. | Agent-card parsing, explicit capability levels, gaps, and conformance fixtures. |
| S18 | Partial | Neutral wrappers round-trip exactly; LangGraph conversion records explicit losses and has a conditional pinned execution witness. | BeeAI is absent; paired live fault equivalence and target-runtime enforcement are unproved. |
| S19 | Absent | No OpenTelemetry/OpenInference instrumentation exists. | Redacted correlated run/task/attempt/effect spans and leakage tests. |
| S20 | Absent | Policy is hard-coded in Python. | One versioned Rego bundle enforced at compile, dispatch, context, and commit. |
| S21 | Partial | Multiple local artifacts and manifests are digest-bound and tamper-evident. | One signed AIBOM/run manifest covering the full ordered run and trust chain. |
| S22 | Absent | No retention/deletion subsystem exists. | Expiry, legal hold, payload deletion, permitted tombstones, and audit proof. |
| S23 | Partial | Generated pressure states support modeled what-ifs. | Completed-trace counterfactuals and a measured Pareto frontier linked to raw runs. |
| S24 | Partial | Snapshots bind provider/model/tool/region failure domains and reject malformed declarations. | Planner-level domain correlation and distinct-domain hedge proof are absent. |
| S25 | Partial | Physical reports expose deterministic evidence fields and explicitly label energy unsupported. | No pinned-hardware CPU/memory/latency/trace/storage overhead report or telemetry. |

## R01-R08 integrated-proof audit

| ID | Status | Evidence present | Exact missing proof |
|---|---|---|---|
| R01 | Partial / External-blocked | The 22-tool MCP surface, durable lifecycle, and watsonx worker seam exist. | One genuine Bob session and same-run live-Granite receipt must bind every R01 field. |
| R02 | Pass | One deterministic drill settles two tasks, applies 429/reset/capacity and budget-cut events, crashes with unknown in-flight use, restarts without recall, protects mandatory work, and reproduces the control digest call-free. | Local fixture/control inputs only; no live-provider claim. |
| R03 | Pass | The independent whole-run verifier consumes sealed evidence only and checks identity, ordering, conservation, artifacts/claims, context, approvals, effects, and replay; mutation classes fail closed. | Digests are not signatures or producer authentication. |
| R04 | Pass | Typed CPU, RAM/VRAM, storage, network bytes, bandwidth, RTT, egress, overflow, and transport-path bounds participate in admission; the coverage matrix labels energy unsupported. | Values are declared estimates, not runtime measurements. |
| R05 | Partial | Bounded citation, number/unit, bilingual equality, freshness, URL, authority separation, and static accessibility checks reject the adversarial corpus. | General entailment/translation quality, rendered accessibility, and source authentication remain unproved. |
| R06 | Partial | Neutral round trip and LangGraph semantic-loss accounting fail closed; the local fair benchmark requires actual FINITE/plain execution, permits LangGraph metrics only at the exact pin, and gives PageAgent no metrics. | No BeeAI and no identical live paired runtime-fault benchmark. |
| R07 | Partial | Hostile evidence cannot grant authority, broaden capability, escape URL policy, or execute a bounded page/effect intent. | Taint is not a universal runtime type across every transformation and adapter. |
| R08 | Partial / External-blocked | Local console, sealed replay, REST/SSE, candidate verifier, and accessibility-oriented UI exist. | Sites is owner-only; signed-out judge access, fresh-clone timing, full accessibility, video, and public release checks remain. |

## Exact v5.0.0 acceptance gates

Every gate below is conjunctive. There are no silent waivers and no substitution of a simulated
receipt for a live one.

### V5-01: program completeness

- The evidence manifest contains exactly M01-M37 and S01-S25 once each.
- Every capability status is `pass` against the exact `PROGRAM.md` gate text at the release commit.
- The manifest contains exactly R01-R08 once each and every proof status is `pass`.
- A gate with a narrower local/simulation scope may pass only if that scope is present in both its
  evidence record and every linked claim.
- Any `partial`, `blocked`, `waived`, `skipped`, missing, or stale record blocks `v5.0.0`.

### V5-02: immutable and reproducible release

- The public repository exposes the exact release commit, an annotated `v5.0.0` tag, source archive,
  lockfiles, checksums, license, security policy, changelog, and machine-readable evidence manifest.
- The release tree is clean, the tag resolves to the manifest commit, and the manifest binds the
  source tree, dependency locks, generated assets, benchmark inputs, and judge bundle by SHA-256.
- Fresh anonymous clones on supported Windows and Linux environments complete the documented judge
  path in under ten minutes without database editing or undisclosed local files.
- The Python package, MCP server, API service, console, verifier, and sealed replay have versioned,
  copyable start commands and fail with actionable diagnostics when prerequisites are missing.

### V5-03: verification quality

- All unit, property, integration, protocol, browser, accessibility, replay, migration, security, and
  end-to-end tests pass at the release commit.
- The 10,000-transition conservation corpus passes with zero negative balance, hidden spend, duplicate
  settlement, or identity ambiguity.
- Core Python line coverage is at least 90%; changed safety-kernel code has at least 90% line coverage;
  the manifest records the tool/version and raw report. Coverage is supporting evidence, never a
  substitute for a missing acceptance test.
- Every supported schema and persistent-store version has forward migration and incompatible-version
  refusal tests.
- Secret scanning finds zero exposed credentials; dependency and container scans contain zero known
  critical or high vulnerabilities; all distributed assets pass a recorded license-policy check.

### V5-04: IBM structural proof

- A genuine, timestamped IBM Bob session makes substantive planning/coding/testing contributions and
  invokes the FINITE MCP lifecycle: preflight, run, status, explain, and verify.
- Those calls reference one run ID and one exact release commit.
- The same run invokes live IBM Granite through watsonx and emits a redacted receipt binding model ID,
  actual usage, measured latency, request/output artifact digests, validator result, run ID, Bob prompt
  evidence, changed files, tests, and commit.
- The Bob and provider artifacts are preserved without credentials or private chain-of-thought. Human
  attestations identify what Bob did and what was independently reviewed.

### V5-05: integrated recovery and effects

- Through the public API, start StormShift, allow two tasks to settle, inject a declared 429/reset
  burst, cut the remaining budget, and crash the coordinator.
- The resumed run honors provider reset windows, retains worst-case unknown usage, never recalls
  completed model/tool work, never duplicates an effect, and derives its residual schedule from the
  durable prior state rather than a rebased budget or deadline.
- A no-provider-call replay reproduces the same ordered control-decision digest.
- The public alert remains a preview until an exact, authenticated approval is issued; duplicate,
  reordered, expired, ambiguous, and stale-fence events still result in at most one target-side commit.
- An impossible follow-up contract refuses before any model/tool/external-effect call and identifies the
  binding constraint using public numeric facts and rule IDs.

### V5-06: independent evidence and semantic safety

- A verifier in a separate process/package consumes only sealed evidence, not planner objects or live
  databases, and validates conservation, artifacts, causality, context obligations, approvals, effect
  uniqueness, and replay identity.
- Mutation tests alter each event/resource/artifact/context/approval/effect/manifest identity class;
  every alteration fails closed.
- Numerically plausible but unsupported, stale, mistranslated, inaccessible, unsafe, or policy-violating
  StormShift outputs cannot complete a required task or create a committable effect intent.
- Untrusted-evidence taint propagates through transformations and context; hostile text cannot broaden
  tools, capabilities, policies, approvals, or effects, and each denial is structured and replayable.

### V5-07: fair measured differentiation

- The preregistration fixes DAGs, prompts, models, model parameters, tools, validators, budgets, retry
  rules, cache state, hardware, framework versions, and paired fault seeds before results are inspected.
- FINITE, sequential, tuned static-parallel, and tuned LangGraph/BeeAI comparison paths execute the
  same work. Conversion loss either blocks the comparison or is reported as a separate non-equivalent
  condition.
- Each reported simulated condition has at least 30 paired deterministic seeds. Selected live-model
  conditions have at least 10 paired trials when the release makes a live comparative claim.
- Cold and warm cache results are separate. Pass rates use Wilson intervals; latency/cost use paired
  bootstrap intervals; failures remain in denominators; raw JSONL, summary CSV, aggregation code,
  environment, and commit are public.
- In at least two preregistered stress regimes, FINITE demonstrates either at least 25% lower faulted
  p95 latency at equal verified quality/budget or at least 20% greater verified utility at equal
  deadline/budget. Faulted SLO pass rate improves by at least 20 percentage points over the best
  baseline. Scheduler overhead is below 5% of wall time.
- Token-reduction, energy, thermal, universal-superiority, and production claims are omitted unless
  their specific measured gate passes. Negative baseline wins are published.

### V5-08: public product and judge path

- An anonymous HTTPS URL supports submit, stream, inspect, cancel, approve, explain, verify, and replay,
  or clearly labels any unavailable live function and offers an unmistakable sealed replay.
- The UI shows the typed DAG, critical path, slack, quota windows, reservations/settlements, protected
  mandatory budget, adaptation, evidence lineage, and effect state from runtime API data rather than a
  browser-side reimplementation.
- Keyboard-only operation, focus order, semantic landmarks, screen-reader names/status, contrast,
  reduced motion, zoom/reflow, captions, and error recovery pass the recorded accessibility checklist.
- The deployed service is least-privilege, rate-limited, secret-redacted, retention-aware, and isolated
  from any real emergency/publication system. Rollback and sealed-demo fallback are rehearsed.

### V5-09: challenge delivery and human eligibility

- Entrant eligibility, team registration, rules acceptance, and IBM SkillsBuild completion are
  attested by the entrant and retained privately where they contain personal data.
- The public project page links the exact repository tag, anonymous demo, and public video of no more
  than three minutes; all links work signed out.
- The project page and video describe the problem, Future-of-Work fit, IBM Bob's substantive role,
  architecture, measured result, limitations, and real-world effect boundary without implying Miami-Dade
  or another agency deployed or endorsed FINITE.
- A criterion-to-file/test/artifact/timestamp map passes link and digest verification before submission.

### Release decision

The release verifier must emit exactly one of:

- `pass`: every V5-01 through V5-09 condition and every manifest record passes;
- `fail`: code or evidence contradicts a gate; or
- `blocked`: required external or human evidence is unavailable.

Only `pass` permits the stable `v5.0.0` tag. `blocked` is not failure of the engineering work, but it
still forbids the stable tag and any claim that the missing proof exists.

## P0/P1 execution sequence through July 31

P0 means submission-blocking. P1 begins only after that day's P0 exit criterion is green. Starting
distributed-HA or ecosystem breadth before the judge path works would increase risk without improving
the evidence chain.

| Date (ET) | P0 deliverable and exit criterion | P1 only after P0 is green |
|---|---|---|
| Jul 22 | Freeze the `v5.0.0-rc.1` local candidate scope, docs, and generated evidence contracts. Exit: local gates are coherent and no stale claim masks an external blocker. | Only low-risk defects in existing local proof paths. |
| Jul 23 | Publish the reviewed repository and run CI/candidate packaging at the exact commit. Exit: clean signed-out clone, immutable commit, candidate artifacts, and CI evidence agree. | Documentation/link polish after hashes stabilize. |
| Jul 24 | Conduct the substantive Bob work session and same-run MCP lifecycle; execute one authorized bounded Granite call. Exit: redacted Bob/Granite evidence binds one commit/run without secrets. | Additional Bob UX only after R01 evidence is complete. |
| Jul 25 | Freeze and run the fair benchmark contract. Exit: one excluded warmup plus all 30 preregistered measured seeds per executed system, actual receipts, complete pairs, failure denominators, intervals, hardware/cache disclosure, and verification that PageAgent has no fabricated metrics. | Do not change the seed count after observing results; publish no win unless the evidence supports it. |
| Jul 26 | Change Sites to anonymous or explicitly judge-shared access and verify the intended console/API path from that account state; retain the sealed fallback. | UI polish only after the full judge path works. |
| Jul 27 | Complete eligibility, team/rules, SkillsBuild, rights, consent, privacy, secrets, license, migration, and accessibility evidence. Exit: each human/external gate has an allowed attestation or remains blocked. | Nonblocking presentation polish. |
| Jul 28 | Generate and independently verify the full candidate manifest, SBOM, checksums, provenance, source/package artifacts, benchmark evidence, and external receipts. | No new capability scope. |
| Jul 29 | Cut an immutable release candidate; run clean Windows/Linux clone, security, license, privacy, accessibility, migration, and link checks. Exit: zero unresolved P0 defects and one sealed manifest. | Package polish, examples, and documentation site improvements. |
| Jul 30 | Record and publish the captioned three-minute video; complete SkillsBuild evidence and project-page draft; rehearse the criterion-to-timestamp path. Exit: every URL and timestamp works signed out. | Only measured-result polish or low-risk documentation fixes. |
| Jul 31 | Freeze code by 15:00 ET, rerun the full release verifier, submit by 20:00 ET, and preserve a 3h59m recovery buffer. Exit: submission receipt and exact submitted commit/tag are recorded. | No new scope. Fix only a release blocker, then rerun the entire affected evidence chain. |

If a P0 item misses its exit criterion, defer P1 and narrow presentation claims. Never fabricate a
Bob entry, provider receipt, benchmark win, deployment state, or submission confirmation to preserve
the schedule.

## External and user-owned evidence gates

| Gate | Required owner/action | Repository automation may do | What cannot be inferred or fabricated |
|---|---|---|---|
| GitHub publication | Entrant authenticates and authorizes the public repository/release. | Push, run CI, create tag/release, verify anonymous clone. | Account ownership, consent, or a public state that was never observed. |
| IBM Bob provenance | Entrant opens genuine Bob sessions and performs substantive planning/coding/testing plus MCP calls. | Prepare runbooks, capture returned IDs, hash/redact artifacts, validate mappings. | Prompts, screenshots, session logs, or Bob contributions that did not occur. |
| Granite/watsonx | Entrant supplies authorized credentials/project/region and permits bounded spend. | Execute the bounded call, redact secrets, bind receipt/artifact/validator/run. | Live usage, model identity, latency, or provider success from fake inference. |
| Eligibility/team/rules | Entrant confirms age, enrollment, team membership, registration, and rule acceptance. | Track a private attestation reference and checklist status. | Identity, enrollment, legal acceptance, or other teams. |
| IBM SkillsBuild | Entrant completes the required activity and retains evidence. | Hash a redacted completion artifact or record a private reference. | Course completion. |
| Public deployment | Entrant authorizes hosting/DNS/account use. | Build, deploy, health-check, signed-out test, and preserve rollback. | Continued third-party uptime or authorization. |
| Video and project page | Entrant approves narration/appearance and submits through challenge accounts. | Generate script, captions, proof map, link check, and final asset hashes. | Human consent, publication, or a platform submission receipt. |
| External effect target | Entrant authorizes only an isolated reversible sandbox, if used. | Enforce intent/approval/idempotency and capture receipt. | Permission to contact or publish to Miami-Dade, emergency services, or any real public audience. |

Personal evidence may remain private, but the manifest must record a redacted digest, reviewer,
timestamp, and availability-to-judges status. Secrets, access tokens, student identifiers, and raw
private Bob/provider content must never be committed.

## Claim boundaries

### Claims supported by the current local candidate

- FINITE compiles strict typed-port workflows, checks declared logical and physical resources, and
  can conservatively refuse tested impossible fixture contracts before worker/provider dispatch.
- Its local integer resource and modeled provider-quota traces replay and fail closed on the tested
  mutation classes.
- Its adaptive local controller survives the tested 429/budget-cut/crash sequence, retains settled
  work, charges unknown use conservatively, and replays without worker calls.
- Its local artifact store preserves content addresses and attempt-linked lineage across restart,
  while a separate sealed-evidence verifier checks the tested whole-run invariants.
- Its simulation-only effect kernel enforces the tested preview, approval, fencing, idempotency,
  ambiguity, outbox, and compensation rules.
- Its bounded semantic verifier catches the registered controlled-fact, freshness, URL,
  authority-taint, bilingual, and static-accessibility mutations.
- Its local REST/SSE and 22-tool MCP surfaces are tested; this is not public deployment or Bob use.
- Its neutral/LangGraph wrapper evidence records preserved semantics and explicit losses; PageAgent
  remains not executed and metric-free.

### Claims prohibited until their manifest gates pass

- FINITE is faster, cheaper, safer, more reliable, or generally better than LangGraph, PageAgent,
  LangChain, BeeAI, or another production system.
- FINITE provides exactly-once external effects, distributed quota correctness, high availability,
  remote-worker fencing, production sandboxing, PII residency, or universal prompt-injection safety.
- Current simulated Granite profiles or fake-inference tests are live IBM model evidence.
- IBM Bob built or invoked FINITE before genuine session evidence exists.
- Structural citation, bilingual-number, or accessibility-attestation checks establish semantic truth,
  translation quality, or rendered accessibility.
- A local console or hosting configuration proves an anonymous public deployment.
- Test count, coverage, feature count, repository stars, or an ambitious version label predicts a
  hackathon result.

After v5 gates pass, every performance or reliability sentence must still state its workload,
baseline, provider/model, hardware, cache condition, fault regime, sample count, interval, commit, and
date. Evidence for one regime must not be generalized to production or to unmeasured regimes.

## Machine-checkable evidence manifest outline

The canonical artifact is UTF-8 JSON using sorted keys, no duplicate keys, no NaN/Infinity, integer
units where defined, RFC 3339 UTC timestamps, normalized relative paths, and SHA-256 content
addresses. The actual release must publish a JSON Schema and a verifier; the outline below defines the
minimum data model.

```json
{
  "$schema": "https://finite.example/schemas/evidence-manifest-v1.json",
  "schema_version": "finite.evidence-manifest/v1",
  "release": {
    "name": "FINITE",
    "version": "5.0.0",
    "decision": "pass|fail|blocked",
    "generated_at": "2026-07-31T00:00:00Z"
  },
  "source": {
    "repository": "https://github.com/OWNER/REPOSITORY",
    "commit": "40-lowercase-hex",
    "tree_sha256": "64-lowercase-hex",
    "tag": "v5.0.0",
    "dirty": false,
    "program_sha256": "64-lowercase-hex",
    "release_contract_sha256": "64-lowercase-hex"
  },
  "environments": [
    {
      "id": "env-windows-ci",
      "os": "...",
      "architecture": "...",
      "python": "...",
      "node": "...",
      "hardware": "...",
      "dependency_lock_refs": ["artifact:python-lock", "artifact:npm-lock"]
    }
  ],
  "capabilities": [
    {
      "id": "M01",
      "gate_text_sha256": "64-lowercase-hex",
      "status": "pass|partial|absent|blocked",
      "scope": "local|simulation|live|distributed",
      "evidence_refs": ["artifact:pytest-junit", "artifact:workflow-ir-fixture"],
      "test_refs": ["tests/test_workflow_ir.py"],
      "claim_ids": ["claim:strict-workflow-ir"],
      "limitations": []
    }
  ],
  "integrated_proofs": [
    {
      "id": "R01",
      "gate_text_sha256": "64-lowercase-hex",
      "status": "pass|partial|absent|blocked",
      "run_ids": ["run:..."],
      "evidence_refs": ["artifact:bob-redacted", "artifact:granite-redacted"]
    }
  ],
  "release_gates": [
    {
      "id": "V5-01",
      "status": "pass|fail|blocked",
      "validator": "python -m agent_physics.release_verify --gate V5-01",
      "evidence_refs": ["artifact:release-verifier-result"]
    }
  ],
  "artifacts": [
    {
      "id": "artifact:pytest-junit",
      "path_or_uri": "artifacts/pytest-junit.xml",
      "media_type": "application/xml",
      "sha256": "64-lowercase-hex",
      "bytes": 0,
      "produced_by": "python -m pytest ...",
      "exit_code": 0,
      "environment_ref": "env-windows-ci",
      "contains_secrets": false,
      "redaction": "none|documented",
      "validator": "python -m agent_physics.evidence verify artifact:pytest-junit"
    }
  ],
  "external_attestations": [
    {
      "id": "attestation:bob-session-1",
      "kind": "bob|watsonx|eligibility|skillsbuild|deployment|video|submission",
      "owner": "entrant|provider|platform",
      "observed_at": "2026-07-31T00:00:00Z",
      "redacted_artifact_ref": "artifact:...",
      "original_sha256": "64-lowercase-hex",
      "reviewed_by": "...",
      "availability": "public|available-to-judges|private-attested"
    }
  ],
  "claims": [
    {
      "id": "claim:...",
      "text": "...",
      "scope": "...",
      "evidence_refs": ["artifact:..."],
      "proof_refs": ["R06"],
      "limitations": ["..."]
    }
  ],
  "signatures": [
    {
      "subject_sha256": "canonical-manifest-sha256-with-signatures-omitted",
      "scheme": "sigstore|gpg|minisign",
      "identity": "...",
      "signature_artifact_ref": "artifact:manifest-signature"
    }
  ]
}
```

The manifest verifier must enforce all of the following:

1. The capability ID set is exactly M01-M37 plus S01-S25; the proof set is exactly R01-R08; the
   release-gate set is exactly V5-01 through V5-09. IDs are unique.
2. Stable `v5.0.0` requires every capability and proof status to be `pass`, every release gate to be
   `pass`, `release.decision == "pass"`, a clean tree, and a matching public tag.
3. Every evidence reference resolves to one declared artifact; local paths stay inside the release
   bundle; size and SHA-256 match the bytes; no artifact declares or reveals a secret.
4. Every test/validator command records environment, exit code, start/end time, tool versions, and raw
   output digest. A passing summary cannot point to a failing or missing raw result.
5. Every live/external record has an issuer/owner, observation time, redacted artifact, original digest,
   reviewer, and availability. Fake, fixture, simulated, and live labels are mutually exclusive.
6. Every claim resolves to evidence and integrated proof with a scope at least as strong as the claim.
   A simulation-scoped record cannot support a live, distributed, production, or comparative claim.
7. Benchmark summaries resolve every aggregate to complete raw run IDs and a preregistration digest;
   missing pairs, seed selection, changed denominators, or environment drift fail verification.
8. The canonical manifest digest and signature verify after excluding only the `signatures` value from
   its own subject digest. Unknown top-level fields and duplicate JSON keys fail closed.

## Final rule

The ultra measure is not the number five. It is the inability of a failed, simulated, stale, private,
or human-unverified result to masquerade as a passing one. FINITE v5.0.0 exists only when the public
product, IBM proof chain, integrated recovery, independent verifier, semantic safety, fair benchmark,
and all 62 program capabilities resolve to the same immutable evidence graph.
