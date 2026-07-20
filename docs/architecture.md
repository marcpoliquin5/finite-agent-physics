# Architecture

## North-star invariant

Agent Physics maximizes useful, verified work subject to a finite execution envelope. It
must never create compute by hiding it, improve latency by quietly weakening required
quality, or recover from failure by duplicating an irreversible action.

## Core mathematical model

For a directed acyclic task graph `G = (V, E)`, let `W` be measured selected work, `S` the
measured critical-path span, and `K` the effective capacity. A theoretical runtime lower
bound is:

```text
T_lower = max(S, W / K, required network round trips)
```

The runtime minimizes the gap between observed makespan and `T_lower` while satisfying:

```text
tokens <= token_budget
cost <= cost_budget
context_bytes <= context_budget
parallelism <= worker and provider capacity
quality(task) >= declared quality floor
effects obey conflict, idempotency, capability, and approval rules
```

The first simulator cannot measure that physical bound. It computes a planning-model bound
from pinned p95 duration estimates and labels it accordingly.

## Planes

```text
Goal
  -> Intent compiler
  -> Validated task/effect graph
  -> Admission + feasibility
  -> Adaptive scheduler <-> Resource governor
  -> Context fabric      <-> Effect kernel
  -> Model/tool workers
  -> Evidence ledger
  -> Control plane + final artifact
```

### Intent compiler

The compiler may use an LLM, but its output is not executable authority. It produces an
untrusted candidate graph which the deterministic validator checks. Dependencies can be
suggested automatically; permissions and irreversible effects cannot be self-granted.

### Admission and feasibility

Before execution, the runtime computes conservative resource bounds. It either admits the
plan, selects a degraded but valid profile, asks for a changed envelope, or rejects it with
an explanation. “Start and hope” is not an admissible scheduling strategy.

### Adaptive scheduler

Ready work is ranked by downstream critical-path pressure. Backend selection considers the
deadline slack, remaining budgets, quality floor, failure history, and provider capacity.
The scheduler is intentionally allowed to choose one agent when coordination overhead would
erase the theoretical benefit of a team.

### Context fabric

Tasks exchange typed, immutable artifacts. The fabric accounts for bytes moved, deduplicates
content, records lineage, and can enforce task-level read sets. This turns context from an
invisible prompt-construction detail into schedulable data movement.

### Effect kernel

Every tool call declares an effect class and resource. Conflicting writes serialize.
Irreversible actions require approval and idempotency. Untrusted content can propose work but
cannot widen capabilities or authorize an effect.

### Evidence ledger

An append-only event stream records graph versions, scheduler decisions, profile estimates,
reservations, actual use, artifacts, approvals, retries, cancellations, and outcomes. It is
the basis for replay, debugging, evaluation, and judge-visible proof.

## First vertical slice

The first implementation is a deterministic discrete-event simulator. That is deliberate:
we can unit-test scheduling invariants and compare policies without cloud variance. The same
contracts and events will drive the async executor in the next milestone.
