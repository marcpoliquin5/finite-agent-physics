# Why Agent Physics extends IBM Bob instead of imitating it

IBM announced a major Bob architecture update on July 9, 2026. Bob now provides a shared
agent and harness, reusable workflow engine, model routing, parallel native tool calls,
subagents with isolated context, background orchestration, human approvals, and Bobalytics.

Primary sources:

- [IBM newsroom announcement](https://newsroom.ibm.com/2026-07-09-ibm-advances-enterprise-ai-software-development-with-multi-agent-capabilities-and-specialized-modernization-workflows)
- [IBM architecture announcement](https://www.ibm.com/new/announcements/ibm-bob-expands-with-premium-packages-new-architecture-and-greater-enterprise-control)
- [IBM Bob documentation](https://bob.ibm.com/docs/ide)

That makes a generic “parallel multi-agent harness with a cost dashboard” strategically weak.
Agent Physics is therefore designed as a complementary **constraint verification and runtime
control layer** that Bob can call through MCP.

## Proposed Bob-facing tools

- `physics_preflight` - validate a workflow IR and return a hash-addressed feasibility certificate.
- `physics_run` - execute or simulate the admitted plan within its finite envelope.
- `physics_explain` - explain scheduling adaptations using structured decision facts.
- `physics_verify` - check conservation, quality, provenance, and effect invariants.
- `physics_replay` - reproduce a trace or evaluate a counterfactual envelope.

Bob remains the development partner and first client. Granite/watsonx provides application
runtime inference. Deterministic code—not an LLM—owns feasibility, authorization, resource
accounting, and pass/fail decisions.

## Constraint-carrying workflow IR

The interop boundary is not “a list of prompts.” Each node carries:

- required and produced artifact schemas;
- deadline and quality obligations;
- estimated backend resource profiles;
- data trust and freshness requirements;
- capability and effect declarations;
- permitted degradation steps;
- retry, compensation, and idempotency policy;
- provenance obligations for claims it can emit.

The envelope travels with the graph. A framework adapter may add information but may not
silently weaken the envelope.

## What the current simulation verifier proves

For one deterministic simulation trace and graph revision, the implemented verifier can
currently reconstruct and check:

- entries use declared qualified profiles and match their pinned resource estimates;
- every started task has exactly one completion or cancellation event;
- protected work completed on successful traces and only genuinely optional work was skipped;
- modeled token, cost, context, reliability, deadline, concurrency, and dependency limits;
- conflicting effects did not overlap;
- event ordering, terminal state, task-specific deadlines, and a model-bound consistency check.

It does **not** yet prove actual model usage, output quality, artifact provenance, durable
idempotency, authenticated approval, cross-run effect isolation, or external exactly-once
delivery. Those require the executor, artifact store, and transactional effect broker on the
roadmap.

The intended judge-facing distinction is simple: **Bobalytics explains development AI usage;
Agent Physics verifies deployed workflow promises under changing runtime conditions.**
