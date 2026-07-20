# FINITE benchmark protocol (preregistration draft)

No chart or performance claim may be published until this protocol is frozen for the tagged
benchmark release. Changes after freezing require a new protocol version and explanation.

## Primary outcome

```text
SLO pass = mandatory validators pass
           AND deadline met
           AND token/cost/context caps met
           AND zero unauthorized external effects
```

## Systems compared

1. **Sequential ReAct:** one Granite-backed agent with the same tools and output schema.
2. **Naive async fan-out:** maximum parallelism with bounded retries.
3. **Tuned static LangGraph:** native parallel branches, persistence, bounded concurrency,
   retries, and hand-authored routing.
4. **FINITE:** identical task implementations under the constraint controller.
5. **Oracle:** simulator only, with future task durations known, to measure scheduling regret.

The current `static_parallel` simulator policy is a development reference, not the tuned
LangGraph baseline and not valid for a public superiority claim.

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
