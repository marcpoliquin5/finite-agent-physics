# FINITE v5.0.0-rc.1 judge handoff

This is the operator page for the `v5.0.0-rc.1` candidate (Python `5.0.0rc1`). It is not a
stable-v5 declaration. Use it with the exact reviewed commit and the detailed
[three-minute script](demo-script.md).

## Evidence state at handoff

The current local full suite passed on the Windows worktree on 2026-07-22. Final test and coverage
numbers must come from generated candidate/CI evidence at the immutable release commit. The local
result is useful development evidence, but it is not an immutable release attestation. The current
capability disposition is in the
[capability audit](capability-status.md).

| Judge-visible claim | Evidence available now | Boundary to say aloud |
|---|---|---|
| Typed workflow and adapters | Workflow v2 typed-port tests and pre-dispatch adapter-ABI rejection tests | Local contracts and trusted adapter declarations |
| Refusal before spend | Logical admission, quota, and physical-resource tests assert zero worker/provider calls | Declared estimates and local fixtures |
| Adaptive recovery | Integrated 429/reset, budget-cut, crash/restart, unknown-use, and call-free replay drill | Caller-supplied events; no live provider telemetry |
| Durable evidence | Restart-safe artifact deduplication/provenance plus an independent whole-run mutation verifier | SQLite and SHA-256 are local durability/integrity boundaries, not identity signatures |
| Semantic safety | Controlled citation, bilingual fact, freshness, URL, taint/authority, and static accessibility checks; the registered adversarial corpus is refused | Bounded grammar/declarations, not general entailment, translation quality, or rendered WCAG proof |
| IBM integration seam | 22 local MCP tools and durable preflight/run/status/explain/verify lifecycle; watsonx worker has fake-inference integration tests | No genuine Bob session and no captured live-watsonx receipt yet |
| Product surface | Bearer-protected local REST/SSE and a sealed/live-aware console | No public API; current Sites access is owner-only |
| Framework evidence | Exact neutral round trip, explicit LangGraph loss ledger, and conditional pinned LangGraph execution | No BeeAI or PageAgent execution; no universal equivalence or superiority claim |

Current Sites URL:
[finite-agent-physics.marcpoliquin5.chatgpt.site](https://finite-agent-physics.marcpoliquin5.chatgpt.site).
The saved/deployed build is Sites version 5 from Sites source commit `47ba39a`. It is
**owner-only**. Do not give it to judges as an anonymous demo until access is changed and a
signed-out or judge-account test passes.

## Recording go/no-go checks

Before recording, answer these in order:

1. Is there a genuine, timestamped Bob contribution tied to the release commit? If no, omit the
   Bob-builder footage and do not say Bob built or ran this candidate.
2. Did Bob call `finite_preflight`, `finite_run`, `finite_status`, `finite_explain_run`, and
   `finite_verify_run` for one preserved run ID? If no, describe only the Bob-compatible local MCP
   surface.
3. Does that same run contain a redacted receipt classified `live-watsonx` with provider usage,
   measured latency, artifact/output digest, and validator result? If no, omit the live-Granite
   segment.
4. Is the site anonymous or explicitly shared with the judge account, and was it tested from that
   access state? If no, show the local console or sealed recording and say the hosted URL is
   owner-only.
5. Did the frozen fair benchmark pass every preregistered completeness and comparison gate at the
   tagged commit? If no, show the protocol/evidence mechanics but narrate no percentage win.
6. Do GitHub, tag, CI, video, eligibility, SkillsBuild, project-page, and submission receipts exist?
   If no, keep the release manifest blocked.

## Three-minute operator sequence

### 0:00-0:25 — The finite contract

Open the console locally or through a verified judge-accessible URL. Show one StormShift graph
with deadline, logical budgets, provider limits, typed ports, adapter requirements, physical caps,
quality, and effect policy. Say that the scenario is fictional and the physical values are declared
estimates; energy is unsupported.

### 0:25-0:50 — Refuse before spending

Choose the pinned impossible envelope. Show the binding observed/limit facts, certificate/report
digest, and zero call count. Do not describe a conservative/heuristic refusal as a mathematical
proof of infeasibility.

### 0:50-1:25 — Recover without rewriting history

Show the adaptive drill after two settled tasks: provider 429/reset and capacity change, remaining
budget cut, optional shedding, injected crash, conservative unknown in-flight reservation, restart,
and the matching call-free replay digest. Emphasize that completed tasks are not recalled and
mandatory work is not silently marked complete.

### 1:25-1:50 — Stop consequences at intent

Show the alert write as `PROPOSED`, then the exact-scope approval and one-apply simulated crash
drill. State that neither the model/fixture worker nor the REST approval route can directly publish
to Miami-Dade or any public target.

### 1:50-2:20 — Evidence that attacks itself

Show artifact restart/lineage verification, the whole-run mutation matrix, and the bounded semantic
adversarial corpus. Mutate one resource or artifact field and show independent verification fail.
Keep the digest-versus-signature boundary visible.

### 2:20-2:45 — Fair comparison without theater

Show the digest-bound fair-benchmark contract, one excluded warmup plus all 30 preregistered
measured receipts per executed system, paired seeds, and confidence intervals. FINITE and plain
Python must have actual rows; LangGraph has metrics only at the exact pin. PageAgent must remain
`not-executed` with no metric, zero, or inferred ranking. Narrate no winner unless the frozen
evidence supports the exact claim.

### 2:45-3:00 — IBM proof or honest fallback

If genuine Bob plus same-run live-watsonx evidence exists, show the redacted lifecycle receipt and
commit binding. Otherwise use these seconds for the verifier and say: "The local IBM integration
seams are tested; genuine Bob and live Granite evidence are still external gates." End on the
candidate label, not stable v5.

## Reproduction commands

Run from the repository root in a clean environment:

```powershell
python -m pip install -e ".[dev,api]"
python -m ruff check .
python -m pytest
python -m agent_physics.cli preflight
python -m agent_physics.cli fair-benchmark --output artifacts/fair-benchmark
```

The optional executed LangGraph row additionally requires the exact pinned `langgraph` extras.
Live Granite requires explicit watsonx credentials and authorization; never place credentials in
commands, logs, screenshots, or evidence artifacts.

## External evidence still missing

- Genuine Bob planning/build/testing contribution and same-run MCP lifecycle capture.
- Genuine live-watsonx/Granite receipt tied to the Bob run and release commit.
- Public GitHub repository verification, clean immutable commit, annotated tag, and passing CI at
  that commit.
- Anonymous or explicitly judge-shared site access and signed-out/judge-account verification; a
  public API only if the submission claims one.
- Entrant age/enrollment, team registration, IBM SkillsBuild completion, rights, and consent
  evidence.
- Frozen fair-benchmark raw evidence and any threshold-qualified comparison headline.
- Captioned sub-three-minute public video, project page, link/QR audit, and accessibility review.
- Final release manifest, submission publication, receipt, timestamps, and retained artifact
  digests.

## Claims that remain prohibited

- Stable `v5.0.0`, production readiness, remote exactly-once delivery, or distributed safety.
- Deployment by or endorsement from Miami-Dade County or any external agency.
- Genuine Bob use or live Granite execution without the corresponding external receipt.
- General semantic equivalence, translation-quality proof, full prompt-injection immunity, or a
  rendered accessibility/WCAG audit.
- Measured physical runtime or energy efficiency from declared profile estimates.
- Alibaba PageAgent or BeeAI integration, execution, benchmark metrics, or inferred zero results.
- Universal superiority over LangChain, LangGraph, PageAgent, or any other framework.

The final human-owned freeze and submission checklist is
[`docs/submission-checklist.md`](submission-checklist.md).
