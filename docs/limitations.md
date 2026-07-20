# Implemented boundary and known limitations

FINITE is an ambitious systems prototype, but its claims are intentionally narrower than its
roadmap. This file is the fastest way to distinguish working behavior from future work.

## What the current evidence supports

- Deterministic schedule simulation over pinned task and backend profiles.
- Strict workflow schema v1 compilation from Python, JSON, and safe YAML to one canonical
  digest. Schema v1 intentionally lacks alternatives, speculation, and typed artifact ports.
- Conservative adaptive admission against deadline, quality, modeled reliability, token,
  cost, context, global concurrency, provider concurrency, and effect-conflict constraints.
- A fixture-only async executor that applies adaptive admission before dispatch, reserves
  declared retry worst case, checks reported actual use against each reservation, and resumes
  validated durable outputs from an immutable execution manifest.
- Proposed effect intents, approval and fencing checks, idempotent simulation-only delivery,
  ambiguous-crash recovery, and compensation behavior against a local simulated target.
- Typed, fictional StormShift inputs and structural validators for declared capacity, route,
  accessibility fields, bilingual numeric parity, evidence identity/freshness, and publication
  disposition. Ten trusted fixture nodes now produce a meaningful response plan and alert
  preview, while the declared publication node stops at a proposed intent; restart reconstructs
  every durable output without repeating a worker call.
- A complete deterministic experiment matrix: one nominal control and four pre-dispatch fault
  transformations, 30 frozen paired seeds, and three simulator policies.
- A deterministic 10,000-transition local resource-ledger corpus with independent replay; it
  proves the integer accounting model, not remote-provider containment or distributed locking.
- A digest-bound console artifact with 1,080 generated pressure-grid states.

## What the current evidence does not support

- No claim that FINITE defeats physical or computational limits. It exposes constraints and
  refuses or changes plans within them.
- No measured speed, cost, quality, energy, or reliability superiority over LangChain,
  LangGraph, BeeAI, or any other production framework.
- `static_parallel` and `sequential` are development references, not tuned framework baselines.
- No physical-runtime benchmark. Simulator durations and success probabilities are pinned
  model inputs; planning-model bounds are not measurements of hardware or network physics.
- No live Granite/watsonx result until a real provider receipt is captured and published.
- No completed IBM Bob provenance until genuine Bob sessions and Bob MCP calls are logged.
- StormShift structural checks do not prove semantic translation equivalence, claim
  entailment, rendered accessibility, or any external agency or delivery-system state.
- Provider-429 StormShift data is marker-only. Its latency and budget parameter transforms are
  not wired into the StormShift executor or scheduler path.

## Runtime limitations

- Exactly one active executor is assumed per run ID. Distributed use needs a durable lease,
  fencing, and leader/failover design.
- Injected Python workers and validators are trusted cooperative fixtures, not a security
  sandbox. A falsely declared pure callable could perform undeclared work.
- Provider-side token, cost, cancellation, and capability enforcement are adapter work; local
  accounting cannot prevent a remote provider from overrunning its own call boundary.
- A hard process crash before usage reporting leaves actual consumption unknown, although the
  attempt's declared worst-case reservation remains inside the admitted envelope.
- Worst-case retry reservation is intentionally conservative and can refuse a plan that might
  fit probabilistically.
- Worker task IDs are manifest-bound; production manifests should additionally pin explicit
  worker implementation digests.
- Run and effect ledgers are separate SQLite transaction domains. Stable idempotency repairs
  the demonstrated crash gap, but there is no cross-database atomic transaction.
- A run with a proposed, uncommitted effect reports `awaiting_effects`; a later executor pass or
  monitor is required to observe the effect's eventual terminal state.

## Experimental limitations

- Faults transform declared graph or envelope data before dispatch; they do not disrupt a live
  model, network, provider queue, or worker process.
- Results are descriptive deterministic evidence, not a causal estimate or public superiority
  claim.
- Confidence intervals summarize frozen simulator outputs; they do not turn modeled inputs
  into empirical provider measurements.
- The experiment revision string is caller supplied. Record digests detect ordinary mutation
  and incomplete designs, but a coordinated relabel-and-recompute attack requires an external
  trusted manifest, signature, or published commit to authenticate provenance.
