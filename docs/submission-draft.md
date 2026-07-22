# FINITE challenge project-page draft

## Project name

**FINITE - The SLO Runtime for AI Agents**

## Tagline

**Keep the promises. Change the plan.**

## Selected theme

Wildcard Challenge - Build Intelligent Systems for the Future of Work

## The problem

Today's agent frameworks make it easy to connect reasoning steps, models, and tools. Real work
still runs under finite constraints: deadlines, provider queues, token and cost ceilings, context
movement, CPU/memory/network limits, reliability floors, retries, evidence requirements,
approvals, and actions that cannot safely be repeated. When those limits are invisible, an
apparently successful workflow can finish late, overspend, silently drop required work, use
unsupported evidence, or duplicate a consequential action.

## The solution

FINITE is a framework-neutral execution control plane for AI workflows. It compiles promises into
typed contracts, checks logical and physical feasibility before dispatch, chooses an admissible
plan, accounts for actual use, changes only the residual plan when conditions move, and converts
declared writes into durable reviewable intents. The Physics Console makes critical path, resource
pressure, reservations and settlements, adaptation, evidence lineage, and effect state visible.

The demonstration uses **StormShift**, a fictional emergency-operations workload where routing,
freshness, bilingual numeric consistency, accessibility, safety, deadlines, physical resources,
and publication authority remain visible under pressure. It is not affiliated with or deployed by
Miami-Dade County or any external agency.

## What is technically different

Most orchestration systems begin after a graph already exists. FINITE treats the graph as an
untrusted candidate and makes its promises executable through ten planes:

1. strict cross-format workflow IR and typed artifact ports;
2. conservative logical and signed-int64 physical admission;
3. adaptive residual control that preserves settled history;
4. quota and multi-resource reservation/settlement ledgers;
5. durable content-addressed artifacts and bounded context obligations;
6. an adapter capability ABI for cancellation, usage, checkpoint, fencing, and effects;
7. bounded semantic, freshness, bilingual, accessibility, and safety gates;
8. proposed effect intents with approval, fencing, idempotency, and ambiguity recovery;
9. sealed evidence, whole-run no-call replay, mutation checks, SBOM, and provenance; and
10. a 22-tool Bob MCP surface, authenticated REST/SSE, and live-capable Physics Console.

The core rule is simple: **refuse before spending when the required promises cannot fit**. When an
admitted run loses capacity, FINITE can shed optional work and recompute the residual schedule, but
it cannot erase completed work, elapsed time, settled use, or an existing effect boundary.

## How IBM Bob was used

> **Submission blocker: replace this section only from genuine evidence.**

Summarize the entrant-owned Bob work packages, material code/test contributions, human review,
and Bob-to-FINITE calls recorded in `docs/bob-build-log.md`. The final version must name one run ID
for preflight, run, status, explain, and verify at the release commit. A configured MCP file, Codex
work, or a protocol unit test is not evidence that Bob was used.

## IBM Granite path

FINITE includes a bounded watsonx.ai worker that disables SDK retries, binds provider calls to an
admitted attempt, requires provider token counts for settlement, validates the result, stores a
redacted receipt, and resumes without recalling completed work.

> **Submission blocker: add a live claim only after the real IBM SDK path runs.** Test-double
> receipts prove integration behavior but are labeled `injected-test-double` and never count as
> Granite evidence.

## Why it matters

Future-of-work systems need more than autonomous reasoning. Teams need to know whether an AI
workflow can keep a deadline, budget, quality, evidence, accessibility, and safety promise - and
to receive a useful refusal when it cannot. FINITE's control layer can complement graph and agent
frameworks in operations, research, software delivery, customer support, incident response, and
regulated processes without forcing teams into a new authoring ecosystem.

## Evidence and comparison boundary

- The local suite covers compiler, scheduler, executor, resource physics, artifact lineage,
  semantic safety, effects, replay, API, MCP, package, and release-manifest behavior.
- A black-box local HTTP run exercises reference workflow fetch, submit, exact-origin CORS,
  resumable SSE, durable status, and the `awaiting_effects` boundary.
- The console has locked dependencies and a zero-vulnerability npm audit at verification time.
- A real exact-pinned LangGraph witness and plain-Python runner can execute the same fixture
  fingerprint under one preregistered comparison contract.
- Alibaba PageAgent is documented as a DOM-native browser agent and is not executed on this
  non-equivalent orchestration workload; no PageAgent metric is invented.
- Current results are local, fixture, simulated, or sealed replay unless a receipt explicitly says
  live. No universal superiority, production, energy, or Miami-Dade deployment claim is made.

## Judge path

1. Bob inspects capabilities and asks FINITE to preflight StormShift.
2. An impossible envelope refuses before any model, tool, or effect call.
3. An admitted run streams durable events while resource use settles.
4. Capacity loss changes the residual plan without erasing completed work.
5. The publication node stops at a proposed effect and exact approval boundary.
6. A separate verifier replays sealed evidence and rejects targeted mutations.
7. The fair-comparison record shows identical inputs, versions, seeds, raw failures, and honest
   non-equivalence.

## Links to replace at release

- Public GitHub: `[PENDING AUTHENTICATED PUBLICATION]`
- Exact release tag: `[PENDING V5 GATES]`
- Judge-accessible demo: `[CURRENT SITES BUILD IS OWNER-ONLY]`
- Public video, <= 3 minutes: `[PENDING]`
- Bob evidence reference: `[PENDING GENUINE SESSION]`
- Granite receipt digest: `[PENDING LIVE RUN]`
- Submission receipt: `[PENDING]`
