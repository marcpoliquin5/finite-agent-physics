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

## Current Bob-facing tools

The committed STDIO MCP server exposes 13 explicitly local/simulated tools:

- `finite_capabilities` - implemented and blocked capability boundary;
- `finite_preflight` - hash-addressed Miami EOC feasibility certificate;
- `finite_simulate` - deterministic schedule trace for one simulator policy;
- `finite_verify` - fail-closed reconstruction of a fresh simulation trace;
- `finite_registered_faults` - fault-registry and execution-status labels;
- `finite_context_drill` - hostile context packing and all-or-refuse cap behavior;
- `finite_effect_drill` - simulation-only approval/idempotency/crash rehearsal;
- `finite_stormshift_validate` - structural fictional-workload validation;
- `finite_fault_experiment` - complete 450-record paired deterministic design;
- `finite_executor_drill` - meaningful StormShift fixture execution and zero-recall restart.
- `finite_quota_corpus` - declared local quota pressure, reset suppression, and event replay;
- `finite_replanning_drill` - digest-bound residual planning after a modeled capacity drop;
- `finite_decision_explanation_drill` - public numeric facts for every scheduler event, with
  no hidden-reasoning access.

A generic provider-backed `run/status/explain` lifecycle remains future work. The repository
does not rename a deterministic fixture drill to imply live production execution.

Bob remains the development partner and first client. Granite/watsonx provides application
runtime inference. Deterministic code—not an LLM—owns feasibility, authorization, resource
accounting, and pass/fail decisions.

## Constraint-carrying workflow IR

Schema v1 currently carries tasks, dependencies, backend resource profiles, effect contracts,
and the run envelope through strict Python/JSON/YAML compilation. It deliberately rejects
unknown fields and does not yet encode alternative/speculative modes or typed artifact ports.

The target interop contract is not “a list of prompts.” Each node will additionally carry:

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

## What the current verifier and local runtime prove

For one deterministic simulation trace and graph revision, the implemented verifier can
currently reconstruct and check:

- entries use declared qualified profiles and match their pinned resource estimates;
- every started task has exactly one completion or cancellation event;
- protected work completed on successful traces and only genuinely optional work was skipped;
- modeled token, cost, context, reliability, deadline, concurrency, and dependency limits;
- conflicting effects did not overlap;
- event ordering, terminal state, task-specific deadlines, and a model-bound consistency check.

The local fixture executor additionally proves conservative pre-dispatch admission, bounded
concurrency and retries, reported-use-versus-reservation enforcement, manifest-bound durable
resume, output revalidation, and the rule that writes stop at proposed effect intents. The
simulation-only effect broker tests exact-scope approval, fencing, idempotency, crash ambiguity,
and compensation against its built-in local adapter.

It does **not** prove live model usage, semantic output quality, production artifact storage,
distributed run ownership, remote-provider containment, human IAM, or external exactly-once
delivery. Those require real adapters and production coordination outside the current slice.

The intended judge-facing distinction is simple: **Bobalytics explains development AI usage;
Agent Physics verifies deployed workflow promises under changing runtime conditions.**
