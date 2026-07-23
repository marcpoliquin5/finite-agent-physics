# Local adaptive-control load proof

`scripts/run_live_load.py` starts the real FINITE ASGI service on an ephemeral loopback TCP
socket and drives a preregistered, bounded workload through HTTP. The default is two rounds of
32 simultaneously submitted runs (64 total). Every run begins paused, receives a budget cut, a
provider 429, a provider reset, and an explicit resume, then reaches `awaiting_effects`.

Run the default proof and independently re-verify its files with:

```powershell
python scripts/run_live_load.py --output artifacts/live-load
python scripts/run_live_load.py --verify-only artifacts/live-load
```

The pass gate requires all of the following:

- exactly 32 active paused runs are admitted in each round and the next run is rejected with
  `active_run_limit`;
- each run accepts exactly four revision-fenced controls and rejects the fifth with
  `control_event_limit`;
- all accepted runs reach `awaiting_effects` and expose exactly one unique run-scoped effect
  intent and idempotency key;
- every effect remains `proposed`, every effect-bearing output says `executed_externally=false`,
  and the externally committed count is zero;
- all final adaptive replays pass, report zero worker/provider calls, and leave the instrumented
  fixture call counters unchanged;
- the report has zero unexpected errors and includes run throughput plus p50/p95 submission,
  control, replay, and end-to-end timings.

The output directory contains canonical `contract.json`, `environment.json`, `report.json`, and
`evidence.json`, one digest-bound receipt per run in `raw-records.jsonl`, and `manifest.json` with
the exact byte count and SHA-256 of every evidence file. Verification re-reads all files, rejects
duplicates/non-canonical JSON, checks every nested digest, and re-evaluates the registered pass
conditions.

This is local deterministic control-plane evidence, not a live Granite, watsonx, PageAgent, or
production-capacity result. It opens no public network connection and commits no external effect.
