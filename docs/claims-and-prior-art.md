# Claim boundary and prior art

## Claims we will not make

- “The first agent scheduler.”
- “The first durable agent runtime.”
- “LangChain or LangGraph cannot parallelize, checkpoint, cache, or pause.”
- “More agents are always faster or more accurate.”
- “The runtime beats physical limits.”
- Any percentage improvement that is not reproduced from a committed benchmark configuration.

## Defensible thesis

> Agent Physics combines multi-resource critical-path scheduling, explicit context movement,
> conservative admission, durable local restart, and effect safety behind a strict
> framework-neutral workflow envelope—and exposes decisions and distance from an explicitly
> labeled planning-model bound as evidence.

The deterministic simulator's bound uses selected p95 profile estimates. It is not a
measured physical lower bound and is never compared directly with live runtime data.

Individual ingredients have substantial prior art. The contribution must be judged on the
integration, runtime behavior, developer contract, effect invariants, and reproducible evidence.

## Required comparisons

This is the target comparison program, not a list of completed public results. Topology and
speculation ablations remain blocked until those capabilities exist.

- A sequential ReAct-style loop.
- A tuned static graph using native parallelism, not a deliberately weak baseline.
- Agent Physics with adaptation enabled.
- Ablations disabling routing, artifact references, topology selection, and speculation.
- Serial, parallel, mixed, straggler-heavy, rate-limited, and effectful workloads.

## Evidence policy

Every chart must link to:

1. the workload fixture;
2. model and provider configuration;
3. exact code revision;
4. raw run records;
5. aggregation script;
6. sample size and confidence interval;
7. known threats to validity.
