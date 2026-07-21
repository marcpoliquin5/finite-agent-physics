# Challenge project-page draft

## Project name

**FINITE — The SLO Runtime for AI Agents**

## Tagline

Keep the promises. Change the plan.

## Selected theme

Wildcard Challenge — Build Intelligent Systems for the Future of Work

## The problem

Today's agent frameworks make it easy to connect reasoning steps, models, and tools. Real work
still runs under hard constraints: deadlines, provider queues, token and cost ceilings, context
movement, reliability floors, retries, approvals, and actions that cannot safely be repeated.
When those limits are invisible, an apparently successful workflow can finish late, overspend,
silently drop required work, or duplicate a consequential action.

## The solution

FINITE is a framework-neutral execution control plane for AI workflows. It turns promises into
typed contracts, preflights a graph against a finite envelope, chooses an admissible plan,
refuses impossible runs before dispatch, persists restart state, and converts declared writes
into reviewable effect intents. Its companion Physics Console makes the critical path, modeled
capacity, protected work, refusal reason, evidence digest, and effect boundary visible.

The demonstration uses StormShift, a fictional emergency-operations workload where capacity,
routing, bilingual numeric consistency, declared accessibility fields, evidence freshness,
deadlines, and publication safety must remain visible under pressure. It is not affiliated with
or deployed by Miami-Dade County or any external agency.

## AI and technical approach

The architecture separates probabilistic work from deterministic authority:

1. typed task, backend, resource, artifact, and effect contracts;
2. conservative feasibility analysis and adaptive critical-path scheduling;
3. explicit accounting for token, cost, context, modeled reliability, and concurrency;
4. an append-only SQLite run ledger with manifest-bound resume;
5. content-addressed evidence and context obligations;
6. an effect-intent/outbox kernel with approval, fencing, idempotency, and crash recovery;
7. a Bob-compatible MCP surface and optional bounded Granite/watsonx adapter;
8. a digest-bound control plane generated from kernel artifacts.

The registered deterministic study contains 450 complete records: one nominal control plus four
pre-dispatch fault transformations, 30 paired seeds, and three simulator policies. These results
are descriptive only; no external-framework performance claim is made without a tuned fair
baseline.

## How IBM Bob was used

**Submission blocker — replace this section only from genuine evidence.** Summarize the real Bob
work packages, material code/test contributions, human corrections, and Bob-to-FINITE MCP calls
recorded in `docs/bob-build-log.md`. IBM Bob must be a core development component; a configured
MCP file or intended future session is not sufficient.

## Why it matters

Future-of-work systems need more than autonomous reasoning. Teams need to know whether an AI
workflow can keep a deadline, budget, quality, evidence, and safety promise—and to get a useful
refusal when it cannot. FINITE's control layer can complement existing workflow engines in
operations, research, software delivery, customer support, incident response, and regulated
processes without forcing teams into a new graph authoring framework.

## Current proof and limitations

- Full Python suite with an enforced 85% statement-coverage floor; insert the exact final
  test count and coverage from the tagged release check.
- Real MCP initialize/list/call protocol test covering 13 Bob-facing tools.
- Digest-bound console artifact with 1,080 kernel-generated pressure states.
- Locked console dependencies with a zero-vulnerability npm audit at verification time.
- No external effects or agency systems are called by the demo.
- Live Granite, genuine Bob provenance, public links, and tagged release numbers must be updated
  from the final evidence bundle before submission.

## Links to fill at release

- Public GitHub: `[PENDING]`
- Public three-minute video: `[PENDING]`
- Judge-accessible demo: `[PENDING]`
- Exact release tag: `[PENDING]`
