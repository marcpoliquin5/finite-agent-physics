# Why FINITE extends IBM Bob instead of imitating it

IBM Bob already provides an agent harness, reusable workflows, model routing, parallel tool calls,
subagents, background orchestration, approvals, and usage analytics. Building another generic
"multi-agent harness with a dashboard" would duplicate Bob's product layer and weaken the entry.

Primary IBM sources:

- [IBM newsroom: multi-agent capabilities and modernization workflows](https://newsroom.ibm.com/2026-07-09-ibm-advances-enterprise-ai-software-development-with-multi-agent-capabilities-and-specialized-modernization-workflows)
- [IBM announcement: Bob architecture and enterprise control](https://www.ibm.com/new/announcements/ibm-bob-expands-with-premium-packages-new-architecture-and-greater-enterprise-control)
- [IBM Bob documentation](https://bob.ibm.com/docs/ide)

FINITE is therefore a complementary **constraint verification and runtime control layer** that Bob
can call through MCP. Bob remains the development partner and first client; Granite/watsonx is the
optional runtime model path; deterministic code owns feasibility, resource accounting, authority,
and pass/fail decisions.

## Division of responsibility

| Bob supplies | FINITE supplies |
|---|---|
| Goal understanding, planning, coding, test generation, workflow invocation | Strict contract compilation and typed-port validation |
| Agent/subagent orchestration and broad development tools | Conservative logical and physical admission before dispatch |
| Model routing and parallel execution capabilities | Residual replanning under settled budgets, deadlines, and effects |
| Human interaction and development approvals | Runtime effect intent, exact-scope approval, fencing, and idempotency |
| Development usage analytics | Per-run reservation, settlement, causal evidence, and no-call replay |
| Natural-language explanation | Structured public facts and rule IDs without hidden chain-of-thought |

This is not a claim that Bob lacks runtime controls in every product configuration. It is the
boundary demonstrated by this repository and its MCP seam.

## Bob-facing lifecycle

The 22-tool server includes one coherent lifecycle:

```text
finite_capabilities
  -> finite_preflight / finite_granite_preflight
  -> finite_run
  -> finite_status
  -> finite_explain_run
  -> finite_verify_run
```

Supporting drills expose simulation, resource conservation, quota windows, hostile context,
effects, StormShift validation, executor restart, residual replanning, decision explanations,
physical admission, adaptive recovery, framework conformance, and artifact integrity. The complete
inventory and evidence labels are in [`bob-mcp.md`](bob-mcp.md).

Bob can therefore ask two operationally useful questions before or during work:

1. **Can this exact set of required promises fit the available envelope?**
2. **After conditions changed, which residual plan still keeps the promises already made?**

The answer may be an admitted witness, a degraded plan that sheds only optional work, or a
conservative refusal with public numeric facts.

## Constraint-carrying workflow IR

Workflow IR v2 carries:

- task dependencies and required/optional status;
- typed, versioned input and output ports;
- backend identity, quality/reliability, duration, token, cost, and context estimates;
- CPU, RAM/VRAM, storage, network, bandwidth, RTT, and egress estimates;
- capability and effect declarations;
- deadline, global/provider capacity, budget, and physical envelope limits; and
- canonical workflow identity.

A framework adapter may add information but cannot silently weaken the envelope. Capability
conversion produces an explicit supported/loss/refused record.

## What FINITE currently verifies locally

- exact workflow and execution-manifest identity;
- dependency, typed-port, profile, quality, and effect-contract validity;
- pre-dispatch logical and physical admission;
- integer reservation/settlement conservation and provider quota state;
- global/provider concurrency, deadlines, retry bounds, and cancellation state;
- durable output reuse and no recall of completed fixture/Granite work on resume;
- content-addressed artifact lineage and required context obligations;
- bounded StormShift semantic/safety invariants;
- proposed effect, approval, fencing, idempotency, and ambiguity behavior;
- public-fact decision explanations; and
- sealed whole-run replay without planner, model, tool, or database calls.

## What still requires genuine external evidence

- an entrant-owned Bob session that materially changes or verifies the release;
- Bob invoking the same-run lifecycle through its real MCP UI;
- one real Granite/watsonx attempt with complete provider usage and a redacted receipt;
- public GitHub, tag, CI, anonymous/judge deployment, video, SkillsBuild, eligibility, and
  submission receipts.

The intended judge-facing distinction is: **Bob builds and invokes intelligent workflows;
FINITE decides whether their declared runtime promises can still be kept and preserves evidence of
why.**
