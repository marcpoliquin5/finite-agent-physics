# Static LangGraph StormShift comparator

This comparator is a semantic/conformance control, not evidence that FINITE is faster or more
reliable than LangGraph. It executes the committed fictional StormShift DAG with the real
LangGraph 1.2.9 `StateGraph` API and the real `langgraph-checkpoint-sqlite` 3.1.0 async
checkpointer. It reuses the same deterministic task callables and structural validators as the
FINITE fixture runtime.

## Reproduce it

```powershell
python -m pip install -e ".[langgraph]"
python -m pytest -q tests/test_langgraph_baseline.py
python -m agent_physics.cli langgraph-baseline --output artifacts/langgraph-baseline.json
```

The extra pins both framework packages exactly. The base package remains importable when the
extra is absent, and the focused test module skips cleanly.

## Frozen static mapping

The comparator chooses the highest-quality eligible declared profile for each non-effect task.
Ties are resolved by p95 duration, cost, tokens, context, declared failure probability,
provider, and profile name. In the committed graph, all ten worker tasks select
`simulated-granite-accurate` on `simulated-watsonx`. Those profiles are a frozen scheduling
manifest only: the shared deterministic fixture callables make zero model and external calls.
The normalized record includes every selected profile and a digest over the complete snapshot.

LangGraph is configured with `max_concurrency=4`. A hand-authored provider semaphore enforces
the committed `simulated-watsonx` cap of two. Multi-parent tasks use LangGraph's list-edge form,
which waits for all declared predecessors. No FINITE scheduler or executor participates.

## What the record proves

`run_langgraph_stormshift_baseline` returns a normalized, self-digested record containing:

- exact LangGraph and checkpoint-package versions;
- source graph and selected-profile-snapshot digests;
- complete output, comparable pure-output, validation, and effect-proposal digests;
- exact dependency IDs and predecessor-output digests observed by every task;
- per-task call counts and configured/observed concurrency bounds;
- a SQLite checkpoint equality check; and
- explicit zero values for cache, retries, model calls, external calls, and executed effects.

The terminal write is represented only as a deterministic `proposed` record. It has no approval
grant and no adapter that could publish it. That proposal is not equivalent to FINITE's durable
effect broker, fencing, approval, or outbox protocol.

## What it does not prove

This static comparator performs no feasibility admission, resource reservation, budget or
deadline enforcement, dynamic replanning, retry policy, live model call, external tool call, or
framework-level effect authorization. Its SQLite checkpoint demonstrates state persistence for
one nominal run; it is not a crash/restart or exactly-once result. The current tests establish
graph and output conformance only. Any future latency, cost, quality, or fault-tolerance claim
must follow the preregistered paired protocol in `BENCHMARK.md` and include framework scheduling
and checkpoint overhead.

CI runs this as an isolated optional-dependency job and uploads the normalized JSON record as
`langgraph-static-comparator`. The main FINITE package and judge bundle do not silently acquire
LangGraph as a runtime dependency.
