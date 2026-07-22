# FINITE benchmark protocol (preregistration draft)

Candidate scope: `v5.0.0-rc.1` / Python `5.0.0rc1`. This protocol and any generated local evidence
do not declare stable v5.0.0 or a benchmark winner.

No chart or performance claim may be published until this protocol is frozen for the tagged
benchmark release. Changes after freezing require a new protocol version and explanation.

## Primary outcome

```text
SLO pass = mandatory validators pass
           AND deadline met
           AND token/cost/context caps met
           AND zero unauthorized external effects
```

## Current executable fair comparison

[`src/agent_physics/fair_benchmark.py`](src/agent_physics/fair_benchmark.py) preregisters four
rows in a fixed order:

1. **FINITE durable runtime:** actual local invocation required.
2. **Plain Python sequential control:** actual local invocation required.
3. **LangGraph static comparator:** actual local invocation only when exactly
   `langgraph==1.2.9` and `langgraph-checkpoint-sqlite==3.1.0` are installed; otherwise it remains
   visibly `not-executed` and receives no metrics.
4. **Alibaba PageAgent:** permanently `unexecuted_reference_only` in this harness, with no version
   pin, raw records, summary row, inferred zero, ranking, or performance metric.

Every metric-bearing row must be backed by an actual invocation on the same digest-bound
StormShift fixture. The contract binds graph, envelope, fixture, expected comparable output,
structural validation, source fingerprint, timer, order method, common SLO, and claim boundaries
before timed trials run. One warmup per executed system is retained in raw evidence but excluded
from statistics. The 30 preregistered measured seeds are paired and order-balanced; Wilson intervals
cover pass/guardrail proportions and deterministic bootstrap intervals cover successful-run local
duration and paired deltas.

This executable slice measures local orchestration/control-plane wall time for deterministic
fixture workers. It makes zero model, provider, network, or external-effect calls. FINITE performs
extra admission, durable persistence, effect handling, and bounded semantic checks that the
baselines do not; selected declared profiles also differ. Therefore token/cost/context estimates
are audit fields, not fair measured-resource comparisons, and the report emits no winner or
superiority claim.

Run and verify the byte-stable evidence bundle with:

```powershell
python -m agent_physics.cli fair-benchmark --output artifacts/fair-benchmark
```

The executable contract fixes one excluded warmup plus 30 measured paired seeds per executed
system. The target competition comparison still requires more than this local slice: identical live
prompts/models/tools/validators and cache/retry rules where live claims are made, hardware
disclosure, runtime fault injection, and published raw evidence tied to an immutable commit.
PageAgent remains not executed until a real, workload-equivalent pinned integration exists;
browser-agent repository features are not a substitute for execution evidence.

The older `static_parallel` simulator policy is only a development reference. The standalone
pinned comparator in `src/agent_physics/langgraph_baseline.py` and the wrapper/loss ledger in
`src/agent_physics/framework_conformance.py` establish bounded conformance, not production
performance or cross-framework semantic equivalence.

## Separate 450-record simulated-fault slice

This is evidence from `experiments.py`, not the actual-invocation fair benchmark above. The
committed experiment runner freezes the `mixed` generated scenario and executes a full
Cartesian design with no condition or seed selection:

- one identity-preserving nominal control;
- provider-b declared p50/p95 duration multiplied by three;
- declared economy profile removed before scheduling;
- provider-b declared concurrency reduced from two to one;
- token, cost, and context budgets uniformly reduced to 1/75 of their declared values;
- 30 frozen paired seeds and three simulator policies, producing 450 raw records.

Every record binds its complete contents, pre-fault and transformed graph/envelope digests,
pair identity, modeled result, and claim labels. Validation regenerates the frozen design and
rejects missing, duplicate, modified, or cherry-picked records. Summaries keep success rate,
performance conditional on success, and modeled time-to-failure separate. Policy comparisons
are per-seed deltas versus adaptive with Wilson pass-rate intervals and paired-seed bootstrap
intervals.

These are pre-dispatch deterministic transformations, not live-provider faults. The revision
label is caller supplied and unauthenticated; a trusted release commit or signed external
manifest is still required to authenticate provenance against coordinated relabeling.

## Evaluation tiers

- Scheduler microbenchmarks over seeded chain, fork-join, diamond, wide-fan-out, heavy-tail,
  rate-limited, and effectful DAGs.
- Recorded StormShift response cassettes to isolate control behavior.
- Paired live Granite runs to confirm end-to-end integration.
- Blind human review of a small output sample; LLM judging is secondary only.

## Required metrics

- SLO pass rate and deadline miss magnitude.
- Mandatory-output correctness and normalized verified utility.
- p50/p95 latency and scheduler overhead.
- Tokens, micro-USD estimate, calls, and context bytes per successful run.
- Context amplification relative to unique source content.
- Queue time, 429s, retry amplification, cancelled work, and recovery time.
- Provenance completeness and artifact-freshness violations.
- Unauthorized, duplicate, compensated, and ambiguous external effects.
- Critical-path stretch and budget regret versus the oracle.

## Registered fault regimes

Each fault is labeled real, simulated, or replayed:

The list below is the target protocol. Only the four transformations in the implemented
deterministic slice above currently produce the complete paired evidence set.

- provider 429 burst and reset;
- slow-tail inference;
- cloud-model outage;
- tool timeout or malformed JSON;
- stale, contradictory, or prompt-injected artifact;
- worker crash and restart;
- artifact checksum mismatch;
- duplicate effect delivery;
- delayed or denied approval;
- context ceiling;
- network partition;
- mid-run budget reduction or deadline change;
- secret/capability violation;
- transient persistence failure.

## StormShift deterministic validators

A result fails when any of these occurs:

- assigned demand exceeds shelter capacity;
- a closed/unavailable shelter is selected;
- a route crosses a simulated closure;
- accessibility or required-zone fields are missing;
- English and Spanish alerts disagree on numbers or times;
- an unsupported number or mandatory claim lacks evidence;
- an expired artifact is used;
- publication is attempted without approval;
- the run finishes beyond its envelope.

## Experimental design

- Freeze graph, prompts, schemas, model IDs, temperatures, output caps, tools, fixtures,
  hardware, caches, and failure seeds.
- Use paired runs with the identical seed for every system.
- Run at least 30 simulator seeds per condition.
- Run at least 10 paired live-model trials per selected condition where credits permit.
- Report cold and warm caches separately.
- Preserve raw JSONL traces and generate summaries from committed code.
- Use Wilson 95% intervals for pass rates and bootstrap 95% intervals for distributions.
- Report scheduler overhead inside end-to-end latency.
- Link every plotted point to trace IDs and the exact Git commit.

## Internal go/no-go targets—not claims

- At least +20 percentage points in faulted SLO pass rate over the best baseline.
- At least 30% fewer tokens per successful run.
- No more than three percentage points of mandatory-quality loss.
- At least 20% lower faulted p95 latency.
- Scheduler overhead below 5%.
- Zero unauthorized effects and 100% successful-run provenance coverage.

If these targets are not met fairly, change the product or report the negative result. Do not
weaken the baseline, hide conditions, or cherry-pick seeds.
