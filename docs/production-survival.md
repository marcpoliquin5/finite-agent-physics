# FINITE Production Survival benchmark

`production-survival` is FINITE's executable local durability proof. It asks whether admitted work
survives specific failures without silently repeating completed work or applying a simulated effect
twice. It does not measure model quality.

## Preregistered scenarios

The contract in `src/agent_physics/production_survival.py` freezes six scenarios before timing:

| Scenario | Injected condition | Required result |
|---|---|---|
| `adaptive-compound-recovery` | 429/reset, provider capacity loss, budget cut, and coordinator crash after dispatch | Mandatory work completes; completed work is not recalled; ambiguous work is fully charged; call-free replay reaches the same control digest |
| `hard-effect-crash` | Process death after target application and before SQLite commit | A fresh broker fences the stale owner and reconciles to one physical application and one committed outbox event |
| `ambiguous-effect-ack` | Target applies but acknowledgement is lost | Durable `AMBIGUOUS` state reconciles without a second physical application |
| `stale-effect-fence` | Prior coordinator commits with an obsolete fence | Stale ownership is rejected before target application; the new owner commits once |
| `delayed-human-approval` | Irreversible intent remains unapproved across restart and one year of logical time | No application occurs before an exact signed grant; approved work commits once |
| `local-orchestration-overhead` | No injected fault | Direct fixture calls and FINITE produce the same output digest; measured FINITE time includes SQLite/runtime construction and durable completion |

Every worker and effect target is a deterministic local fixture. No IBM Bob, watsonx, model,
network, sandbox, remote worker, or external effect is invoked.

## Reproduce

```powershell
python -m pip install -e ".[dev,mcp]"
agent-physics production-survival `
  --trials 10 `
  --output artifacts/production-survival
agent-physics production-survival `
  --verify-only artifacts/production-survival
```

The output contains:

- `contract.json` — preregistered scenarios, seed derivation, timer, metric definitions, and claim
  boundaries;
- `records.jsonl` — one digest-bound raw record per scenario/trial;
- `report.json` — per-scenario pass rate, descriptive `pass^k`, observed all-`k`, p50/p95/p99
  duration and recovery timing, direct-call timing, local overhead, provider calls, and duplicate
  effect applications; and
- `manifest.json` — SHA-256 identities for the three evidence inputs plus contract/report digests.

The command exits nonzero if any trial fails. The Bob MCP surface exposes the compact
`finite_production_survival_drill`; Bob can run three or more trials per scenario without receiving
the full raw record set. The CLI is the release-evidence path.

## Reliability definition

For `k` trials:

```text
p_hat = passed trials / k
descriptive pass^k = p_hat ** k
observed all-k = every one of the k raw trials passed
```

The plug-in value is descriptive and does not assert independent, identically distributed
production runs. A report must retain the trial count, failures, and raw records.

## Claim boundary

This proof supports:

> On the recorded machine and commit, FINITE's local deterministic fixtures survived the
> preregistered faults with the reported repeated-run reliability, recovery timing, and duplicate
> simulated-effect count.

It does not support claims of distributed consensus, high availability, hostile-code isolation,
live-provider recovery, model correctness, SOC 2, ISO 27001, SEC/FINRA compliance, or universal
performance superiority.
