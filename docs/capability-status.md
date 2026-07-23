# FINITE v5.0.0-rc.1 local-candidate capability audit

Audit date: 2026-07-22. This audit scores all 62 acceptance gates in [`PROGRAM.md`](../PROGRAM.md)
against executable repository code and tests. Roadmap prose, UI labels, and the v5 candidate name
are not implementation evidence. **Pass (local)** means the exact gate has direct local or
simulation evidence within the stated boundary; **Partial** means at least one material clause is
still unproved; and **Absent** means the gate itself is not implemented. `External-blocked` is a
qualifier, not a passing status.

The judge-facing candidate label is `v5.0.0-rc.1`; the corresponding Python distribution version
is `5.0.0rc1`. Neither label means stable v5.0.0 has shipped.

Verification snapshot: the current local full suite passed on the Windows worktree on 2026-07-22.
Final test and coverage numbers must come from generated candidate/CI evidence at the immutable
release commit; this audit does not freeze them. The worktree is mutable, so this result is not a
release attestation, stable-v5 declaration, or evidence of any live provider, Bob, hosted,
distributed, accessibility, or statistical claim.

## Executive summary

| Status | Must-win | Stretch | Total |
|---|---:|---:|---:|
| Pass (local) | 19 | 0 | 19 |
| Partial | 17 | 11 | 28 |
| Absent | 1 | 14 | 15 |
| **Total audited** | **37** | **25** | **62** |

The strongest accepted local slice now includes strict workflow v2 compilation with typed ports,
adapter-ABI admission, logical and physical admission, protected headroom, quota replay,
10,000-transition conservation, adaptive crash/restart recovery, durable artifact lineage,
bounded semantic checks, proposed-only effects, a 22-tool MCP surface, authenticated local
REST/SSE, and independent sealed-evidence verification.

These are still candidate-local proofs. There is no genuine Bob session, no captured live-watsonx
receipt, no anonymous public API, no public GitHub release/tag verification, no public video or
submission receipt, and no eligibility or SkillsBuild evidence. The current Sites console at
[finite-agent-physics.marcpoliquin5.chatgpt.site](https://finite-agent-physics.marcpoliquin5.chatgpt.site)
is saved/deployed as Sites version 5 from Sites source commit `47ba39a`, but it is verified
**owner-only**, not a judge-accessible deployment.

## Must-win vertical slice (M01-M37)

| ID | Status | Concrete implementation/test evidence | Exact work still required by the acceptance gate |
|---|---|---|---|
| M01 | Pass (local) | `workflow_ir.py` compiles strict Python/JSON/YAML schema v2 into one canonical digest; `tests/test_workflow_ir.py` covers cross-format identity, ordering, duplicate/unknown-field rejection, typed ports, adapter requirements, and physical units. | No missing clause inside the local interchange gate. |
| M02 | Partial | `TaskContract.optional` and `tests/test_scheduler.py::test_optional_work_is_shed_before_required_work` cover required-versus-optional behavior deterministically. | Add explicit alternative and speculative modes (not booleans), mode-group constraints, deterministic compilation, and permutation/golden-hash tests for all four modes. |
| M03 | Pass (local) | `contracts.py` declares deadlines, logical and physical integer caps, provider capacity, quality/reliability, typed ports, adapter requirements, and effect policy; contract, workflow, scheduler, and physical-resource tests reject malformed values. | Production enforcement remains adapter/deployment work, not part of this local contract gate. |
| M04 | Pass (local) | `InputPort`/`OutputPort`, graph validation, and workflow schema v2 bind schema, version, media type, producer task, and producer port. Incompatible, missing, or unreachable producers fail compilation in `tests/test_workflow_ir.py`. | No missing clause inside the local pre-execution compatibility gate. |
| M05 | Partial | Graph and workflow validation reject cycles, duplicate IDs/ports, unknown dependencies, missing or unreachable typed producers, illegal effects, and invalid adapter requirements. | A structured multi-finding analyzer and explicit alternative/speculation reachability analysis are still absent. |
| M06 | Pass (local) | `profile_snapshot.py` seals strict observed/estimated snapshots containing component versions, pricing, quotas, latency/resource/quality metrics, failure domains, sample provenance, validity windows, and a canonical digest. Executor tests bind a fresh registered snapshot before dispatch. | No live sampling or online calibration claim; that remains S01. |
| M07 | Pass (local) | Planning-model bounds remain separately labeled, while `physical_resources.py` derives transport, RTT, and combined dependency-path lower bounds from declared estimates. `tests/test_physical_resources.py` covers label-safe units, every cap, overflow, and deadline refusal. | The physical values are estimates, not runtime measurements; CPU execution time is a cap, not a theoretical compute lower bound. |
| M08 | Partial | `FeasibilityCertificate` emits feasible/degraded/refused, assumptions, selected backends, skipped optionals, checks, failure reason, and a verifiable digest; see all `tests/test_feasibility.py`. | Once M02 exists, include selected task modes/alternatives/speculation explicitly; also label the refusal basis/solver completeness so a heuristic refusal can be machine-distinguished from a proof. |
| M09 | Pass (local) | Executor and MCP preflight tests refuse impossible work before worker/provider/effect dispatch and assert zero calls. Physical and adapter admission use the same fail-before-call boundary. | A future production adapter must preserve this ordering. |
| M10 | Pass (local) | `resource_ledger.py` independently replays exactly 10,000 seeded integer reserve/settle/cancel transitions with conservation, cap, refund, overrun, and tamper checks. | Distributed settlement remains outside the proof. |
| M11 | Pass (local) | Scheduler tests reserve a joint token/cost/context/reliability plan for protected work and prove optional work cannot spend mandatory cost-to-go. | Runtime changes are handled separately by M15/R02. |
| M12 | Partial | HEFT-style upward ranks, least-slack urgency, protected criticality, and value ordering live in `graph.py`/`scheduler.py`; `test_least_slack_priority_preserves_task_deadline` is direct evidence. | Add an explicit downstream-unlock score, expose all priority components per dispatch, and test ordering under ties where slack, critical path, unlock count, and utility disagree. |
| M13 | Pass (local) | `provider_quota.py` models integer RPM, TPM, concurrency, fixed resets, 429 suppression, bounded retries, settlement, and deterministic replay. The seeded burst corpus proves retries do not amplify declared limits. | Scope is per guard instance; shared/distributed provider quota remains unproved. |
| M14 | Pass (local) | Optional tasks launch only after protected mandatory headroom is preserved; optional ancestors of required work remain protected. | No missing clause in deterministic planning. |
| M15 | Partial | `replanning.py` records digest-bound residual decisions for slowdown, failure, provider capacity, and envelope events. `adaptive_runtime.py` first applies the full scheduler, adapter, reliability, deadline, and physical admission boundary, binds each task to its exact admitted profile, and never falls through to an unadmitted backend. The authenticated control plane applies revision-fenced provider 429/reset/capacity, budget-cut, pause, and resume events while preserving settled work; it atomically caps concurrent controls, retires terminal in-memory sessions, and reproduces persisted control digests with zero worker/provider calls. | The general replanner is not yet wired as the mutation controller for every active executor event, and first-class settlement-trigger coverage is incomplete. |
| M16 | Partial | `RetryPolicy` and executor tests cover absolute deadlines, bounded calls, deterministic seeded jitter, bounded retry, circuit opening, redacted dead-letter events, cancellation, and restart-aware attempt counts. | Add one restart/recorded-replay proof that reproduces the complete timeout/retry/jitter/breaker/dead-letter decision trace. |
| M17 | Partial | Bounded cooperative cancellation and uncooperative-worker detection are in `executor.py`; `test_cooperative_cancellation_is_visible_inside_worker` and cancellation-during-validation tests verify local cleanup. | Add expiring execution leases, settlement fencing, a process-isolated kill path, and tests proving an expired/stale worker cannot report usage, complete work, or authorize an effect. |
| M18 | Partial | SQLite run state, adaptive-controller records, fixture execution, and the watsonx worker path resume without recalling completed work; R02 exercises unknown-inflight accounting and call-free replay after a crash. | Run/effect/artifact stores are not one atomic distributed recovery protocol, and the production cross-store crash window remains. |
| M19 | Pass (local) | `artifact_store.py` provides restart-safe SQLite put/get, content-addressed deduplication, immutable attempt-linked provenance, parent foreign keys, atomic failure, and full-store verification; `tests/test_artifact_store.py` covers restart and tampering. | No remote blob, replication, backup, or distributed-store claim. |
| M20 | Partial | Durable provenance binds run/task/attempt, producer-event digest, transformation digest, and input artifact IDs; whole-run evidence checks artifact and claim causality. | The active runtime does not yet require one unified full traversal from every final claim through every transformation to authenticated external sources. |
| M21 | Pass (local) | `ContextPacker` enforces deterministic byte/token caps, loss accounting, inclusion/exclusion reasons, and visible refusal when mandatory context cannot fit. | Provider-tokenizer calibration remains outside the conservative estimator. |
| M22 | Partial | Context obligations and bounded semantic checks cover required artifacts/claims, integrity, freshness, contradictions, literal controlled citations, trust separation, bilingual numeric/unit facts, and refusal. | General citation placement, authenticated source trust, and open-ended entailment are deliberately unsupported. |
| M23 | Pass (local) | Declared writes are refused without a broker or materialized as durable `PROPOSED` intents; fixture/model workers cannot execute them directly. | Production target rendering is not claimed. |
| M24 | Pass (local) | Exact-scope, time-bound approval grants and fences block high-risk simulated intents until approved; REST approval only advances the intent and never commits externally. | Production IAM/human identity is not proved. |
| M25 | Pass (local) | The SQLite effect broker uses a transactional outbox, run-scoped idempotency, crash-ambiguity recovery, and an idempotent simulated adapter. Sequential, concurrent, crash, duplicate, and restart tests keep same-declaration effects isolated by run and yield one physical apply. | Every production target needs equivalent target-side evidence. |
| M26 | Absent | There is no speculation-group or branch-winner state. | Add intent quarantine, winner selection, loser cancellation, and proof that a loser cannot commit. |
| M27 | Pass (local) | `adapter_capabilities.py`, workflow v2 requirements, and executor admission bind cancellation, checkpoint, streaming, usage, supported effects, fencing, and hidden retries. Missing or mismatched capabilities fail before a worker call. | Capability declarations still rely on locally trusted adapter metadata; remote attestation is not claimed. |
| M28 | Partial | `benchmark.py::generated_scenario` and `run_simulated_benchmark` are seeded and repeatable; `test_generated_scenarios_and_records_are_reproducible` proves record equality. | Accept an explicit input-event stream, emit first-class decision and trace hashes, and assert byte-identical hashes across repeated seeds/events (including fault/retry decisions), not only equality of aggregate benchmark records. |
| M29 | Partial / External-blocked | `watsonx_worker.py` executes the bounded Granite adapter inside the durable executor, requires provider usage, validates the public receipt, and resumes without recalling a completed model attempt in fake-inference tests. | Capture one genuine live-watsonx receipt tied to run, model, usage, latency, output artifact, validator, and release commit. BeeAI is not claimed. |
| M30 | Partial / External-blocked | `mcp_server.py` exposes **22** tested local tools, including durable `finite_preflight`, `finite_run`, `finite_status`, `finite_explain_run`, and `finite_verify_run`; the real STDIO handshake test lists and calls the server. | A genuine IBM Bob session must invoke the lifecycle for one run and preserve timestamped prompt/call/run/trace evidence. Local MCP tests are not Bob evidence. |
| M31 | Pass (local) | `control_api.py` and `control_service.py` expose health/readiness, bearer-protected lifecycle, start-paused adaptive runs, revision-fenced controls, call-free replay, and cursor-resumable SSE. Black-box tests cover strict parsing, auth, exact-origin CORS, restart, and no direct database access; a digest-bound default load proof independently re-verifies 64 real-loopback-TCP runs, both 32-way admission caps, 256 controls, per-run control limits, effect isolation, and zero external commits/calls during replay. | This is disclosed local fixture evidence, not hosted capacity; no TLS-termination, OIDC, tenant-RBAC, distributed rate-limit, or HA claim. |
| M32 | Partial | The console renders sealed evidence plus a real local API flow: paused launch, budget cut, provider 429/reset, resume, SSE history, zero-call replay, and run-scoped proposed-effect inspection. Locked build tests and desktop/mobile browser runs verify reflow and report no console or request errors in that tested flow. | The Sites URL is owner-only; there is no judge-accessible public API path, full assistive-technology audit, or public deployment verification. |
| M33 | Pass (local) | `decision_explanations.py` emits one digest-bound public numeric fact/rule-ID record per deterministic scheduler event, including completion and refusal, with reasoning access explicitly false. | Other control planes may need their own explanation schemas, but the scheduler gate passes. |
| M34 | Pass (local) | `adaptive_runtime.py` replays persisted controller records without workers and reproduces every state/decision/control digest; quota and ledger replayers and the whole-run verifier fail closed on mutation. | This is recorded local control replay, not re-execution of live model semantics. |
| M35 | Partial | `fair_benchmark.py` fixes one excluded warmup plus 30 preregistered measured seeds per executed system, requires actual local FINITE/plain-Python receipts, conditionally executes only the exact LangGraph pin, keeps PageAgent unexecuted and metric-free, and emits paired intervals without a winner. The separate 450-record simulator slice adds complete paired fault-transform evidence. | Run identical live tasks/prompts/models/tools/validators/cache rules across honest baselines, inject paired runtime faults, disclose hardware, and publish raw immutable-commit evidence before making a comparison claim. |
| M36 | Partial | StormShift runs a typed fictional DAG, revalidates restart outputs, stops at a proposed effect, and gates completion on bounded citation, freshness, authority-taint, bilingual controlled-fact, URL, and static accessibility checks. Its registered deterministic adversarial corpus is refused, and the local console flow has a desktop/mobile rendered-browser reflow/error audit. | No general entailment/translation-quality proof, screen-reader or full WCAG audit, live Granite synthesis, or real incident/publication claim. |
| M37 | Partial / External-blocked | CLI/MCP/API entry points, deterministic candidate generation/offline verification, cross-platform CI gates, sealed console fallback, and Bob work-package templates exist. | A clean immutable commit/tag, fresh-clone timing, genuine Bob provenance, public release assets, and externally verified judge path are still missing. |

## Stretch program (S01-S25)

| ID | Status | Concrete implementation/test evidence | Exact work still required by the acceptance gate |
|---|---|---|---|
| S01 | Partial | Strict snapshots represent observed sample counts, provenance, validity, p95 latency/resources, quality/failure calibration, and deterministic versions. | There is no online collector/updater or measured coverage report across successive versions. |
| S02 | Absent | Seeded deterministic simulation exists, but there is no Monte Carlo completion-probability estimator. | Implement seeded sampling over declared distributions/correlations, compare estimates with simulator frequencies, define tolerance/confidence, and test repeatability. |
| S03 | Absent | A small exhaustive additive oracle exists, but there is no CP-SAT dependency, bounded solve, or fallback. | Build a CP-SAT formulation, prove fixture optima, cap solve time, preserve a safe incumbent, and test fallback. |
| S04 | Absent | There is no information-value speculation controller. | Add registered noisy scenarios and equal-budget verified-utility evidence. |
| S05 | Absent | There is no correlation-aware hedge controller or loser-safe hedge execution. | Model tail benefit versus duplicate cost and prove safe loser cancellation. |
| S06 | Absent | Scheduling remains single-run with no tenant queue or weight. | Add weighted-fair isolation and sustained starvation tests. |
| S07 | Absent | No semantic/prefix cache exists. | Pin every prompt/context/model/tool/policy/validator key field and test invalidation, expiry, and cold/warm results. |
| S08 | Partial | Content-addressed artifacts have parent lineage and `executor.py` resumes from a digest-bound execution manifest with durable outputs; resume/tamper tests exist. | Add worker hibernation manifests with explicit size bounds and artifact inventories, transfer only missing/delta objects, and measure/reject redundant full transfers on resume. |
| S09 | Partial | `SQLiteEffectBroker.compensate` provides idempotent compensation for one reversible intent; `test_reversible_compensation_and_replays_are_idempotent` covers it. | Add ordered saga membership and durable chain state, inject failure at every step, compensate committed reversible steps in strict reverse order, and define/manualize irrecoverable branches. |
| S10 | Absent | Effect fences and approval grants are not least-authority attempt capability tokens. | Issue lease-bound tokens, enforce them at artifact/tool/effect boundaries, revoke them, and test cross-attempt/resource denial. |
| S11 | Partial | `Artifact.sensitivity` provides PUBLIC/INTERNAL/CONFIDENTIAL/RESTRICTED labels, but its docstring explicitly says enforcement belongs elsewhere. | Add PII taint propagation and policy enforcement for model, region, log, context, and artifact destinations; test every restricted-flow denial and approved declassification. |
| S12 | Absent | Injected callables are trusted in-process fixtures. | Add a process/container sandbox with CPU/time/filesystem/network/output enforcement and hostile tests. |
| S13 | Partial | Effect commits use monotonically increasing broker fences and `test_new_broker_fence_invalidates_stale_commit` rejects stale effect ownership; the run store explicitly lacks a distributed lease. | Add distributed run/attempt leases and fencing to dispatch, resource settlement, artifact completion, and effect authorization; test reassignment races and stale-worker rejection end to end. |
| S14 | Absent | No remote worker protocol, registry, placement, or stealing queue exists. | Add compatible pure/read stealing with fenced settlement and race/failure tests. |
| S15 | Absent | SQLite is a single-coordinator boundary with no replicated monotonic log. | Add leadership/failover, replicated ordering, reservation recovery, and failover tests. |
| S16 | Partial | Physical admission accounts for declared bytes, bandwidth, RTT, and egress cost, and snapshots bind provider/model/tool/region failure domains. | Artifact location, sovereignty/residency, cache locality, and placement optimization are absent. |
| S17 | Absent | No A2A agent-card parser or adapter exists. | Map agent cards to explicit capability levels and fail visibly on gaps. |
| S18 | Partial | `framework_conformance.py` provides an exact neutral round trip, an explicit LangGraph semantic-loss ledger, fail-closed conversion, and a conditional executed pinned LangGraph witness. | BeeAI import is absent; conversion metadata is not target-runtime enforcement; paired live fault equivalence is unproved. |
| S19 | Absent | No OpenTelemetry/OpenInference instrumentation exists. | Add redacted correlated run/task/attempt/effect spans and leakage tests. |
| S20 | Absent | Policy remains implemented as versioned Python checks rather than OPA/Rego. | Enforce one Rego bundle at compile, dispatch, context, and commit boundaries. |
| S21 | Partial | Feasibility certificates, artifacts, context manifests, experiment records, console artifacts, and executor manifests detect local digest tampering. | Create one signed run manifest/AIBOM covering prompts, profile snapshots, artifacts, policies, tools, validators, and ordered events; verify signature/trust chain and detect each field's alteration. |
| S22 | Absent | No retention worker, deletion API, legal hold, or tombstone policy exists. | Add policy-driven deletion while retaining only explicitly permitted hashes/lineage and audit it. |
| S23 | Partial | The console's 1,080 generated pressure states answer envelope/capacity what-ifs, and the scheduler prunes profile-resource vectors, but neither operates from a completed trace nor plots measured outcomes. | Build a completed-trace counterfactual engine, recompute provider/envelope scenarios without relabeling modeled results as measured, and plot a measured Pareto frontier linked to raw trace IDs. |
| S24 | Partial | Profile snapshots bind provider/model/tool/region failure domains and reject malformed domain declarations. | The planner does not yet reason over complete failure domains or require distinct-domain hedges. |
| S25 | Partial | Physical reports expose deterministic control-plane evidence fields and label energy unsupported. | No pinned-hardware CPU/memory/decision-latency/trace/storage overhead experiment or telemetry exists. |

## Integrated release proofs (R01-R08)

| ID | Status | Current evidence | Blocking boundary |
|---|---|---|---|
| R01 | Partial / External-blocked | The 22-tool MCP surface, durable Bob lifecycle, and watsonx worker seam are locally tested. | No genuine Bob session and no same-run live-watsonx receipt exist. |
| R02 | Pass (local) | `run_adaptive_recovery_drill` settles two tasks, processes a 429/reset/capacity sequence and budget cut, crashes with unknown in-flight work, restarts without recall, protects mandatory work, and reproduces the control digest through call-free replay. | Provider events and workers are deterministic local fixtures, not authenticated telemetry or live Granite. |
| R03 | Pass (local) | `whole_run_verifier.py` consumes only sealed evidence and independently checks identity, event order, conservation, artifacts/claims, context, approvals, effect uniqueness, and replay binding; mutation tests fail closed. | SHA-256 is mutation detection, not producer authentication or a signature. |
| R04 | Pass (local) | Physical admission covers CPU-ms, RAM/VRAM, storage, ingress/egress, bandwidth, RTT, egress cost, transport-path bounds, integer overflow, and a 12-row coverage matrix with energy unsupported. | Values are declared estimates, not measured physical runtime or hardware telemetry. |
| R05 | Partial | Bounded controlled-fact citation, number/unit, bilingual equality, freshness, URL, authority separation, and static accessibility checks reject the registered deterministic adversarial corpus. A local desktop/mobile browser run checks the console's reflow, heading order, controls, request results, and console errors. | No general entailment, translation-quality proof, screen-reader/full-WCAG audit, or source authentication. |
| R06 | Partial | Neutral wrappers round-trip exactly with zero loss; LangGraph conversion records explicit losses; the fair benchmark requires actual local FINITE/plain receipts and permits LangGraph metrics only at the exact pin. PageAgent remains unexecuted and metric-free. | No BeeAI support and no identical live-model paired runtime-fault benchmark. |
| R07 | Partial | Hostile evidence cannot become a grant, widen capability, escape the URL policy, or execute a page/effect intent in the bounded corpus. | Taint is not yet a universal runtime type propagated through every transformation and adapter. |
| R08 | Partial / External-blocked | The local console, sealed replay, authenticated REST/SSE/adaptive controls, candidate verifier, accessibility-oriented UI, desktop/mobile live-browser flow, and independently reverified 64-run loopback load proof exist. The browser flow launches paused, mutates the envelope, replays with zero calls, resumes, and inspects a run-scoped proposed effect without browser/request errors. | The Sites URL is owner-only; no signed-out judge path, public API, fresh-clone timing, screen-reader audit, or public release/video verification exists. |

## Highest-value remaining gates before July 31

1. **Genuine Bob evidence:** use IBM Bob for a substantive contribution, then have Bob call preflight, run, status, explain, and verify for one release run ID. Preserve timestamped prompts, tool calls, outputs, file/test mapping, and commit.
2. **Live Granite evidence:** run that same lifecycle through watsonx.ai and retain a redacted `live-watsonx` receipt containing model ID, provider usage, measured latency, output/artifact digest, validator result, and run/commit binding.
3. **Immutable GitHub release:** publish the reviewed clean commit, annotated tag, candidate artifacts/checksums, and passing CI; verify every link and hash from a signed-out session.
4. **Judge-accessible deployment:** change the current owner-only Sites access to anonymous or explicitly judge-shared access, deploy the intended API if it is claimed, and verify the complete path from the intended judge account.
5. **Human eligibility evidence:** complete event registration, team/age/enrollment checks, SkillsBuild activity, rights/consents, and retain private originals plus permitted redacted digests.
6. **Fair benchmark freeze:** finish preregistration, identical-fixture checks, raw paired records, failures-in-denominator, hardware/cache disclosure, and confidence intervals. Omit any win headline that misses its gate.
7. **Three-minute video:** record only evidence that exists at the immutable tag, include captions and visible `LOCAL`/`SIMULATION`/`LIVE` labels, and omit Bob or Granite segments if their genuine receipts are absent.
8. **Release manifest:** bind code, lockfiles, generated assets, raw evidence, external attestations, deployment/video URLs, and submission state; keep the decision blocked until every required external kind verifies.
9. **Fresh-clone and accessibility audit:** time the documented path on Windows and Linux and test keyboard, focus, screen reader, contrast, reflow, reduced motion, links, errors, and secret handling.
10. **Submission and receipts:** publish early, verify the project page and every URL signed out, then retain the final submission receipt, timestamps, artifact digests, and rollback/offline fallback.

For the operator-ready sequence and claim-safe fallback, use
[`docs/judge-handoff.md`](judge-handoff.md). The full shot list remains in
[`docs/demo-script.md`](demo-script.md).
