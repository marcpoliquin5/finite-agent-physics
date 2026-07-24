# External benchmark integration contracts

This document freezes FINITE's claim boundary before any external leaderboard task is executed.
Until an exact version, model, configuration, raw trace, and release commit exist, every suite below
is `not-executed` and receives no score.

## Three separate proof planes

1. **Agent correctness:** tool choice, arguments, policy compliance, and task outcome.
2. **Execution durability:** restart, replay, recovery, duplicate-effect prevention, overhead, and
   cost per completed task.
3. **Enterprise controls:** isolation, identity, authorization, telemetry, retention, and
   deployment topology.

One plane cannot substitute for another. A function-calling score is not a durability result, and a
local recovery proof is not a security certification.

## τ-bench family

Source: [τ-bench paper](https://arxiv.org/abs/2406.12045) and the current
[Sierra τ²/τ³ repository](https://github.com/sierra-research/tau2-bench).

Required FINITE protocol:

- pin the benchmark repository to an exact commit and preserve its license/version metadata;
- publish the selected domains, task IDs, user simulator, tools, policies, model ID, model
  parameters, prompts, seeds, and maximum turns;
- run raw-provider and FINITE-mediated arms with identical model/tool/task inputs;
- use at least five repeated attempts per selected task and report pass rate, pass^k, and observed
  all-`k` with every failure retained;
- preserve tool calls, policy violations, final outcomes, FINITE run IDs, recovery events, usage,
  latency, and cost; and
- separate agent correctness from FINITE overhead and fault-recovery measurements.

Current status: **not executed**. Live Granite credentials and the exact competition model choice
are still required.

## Berkeley Function Calling Leaderboard

Source: [official BFCL V4 leaderboard](https://gorilla.cs.berkeley.edu/leaderboard).

Required FINITE protocol:

- freeze the BFCL release/commit, categories, language/runtime, model, prompt, decoder, and tool
  schemas;
- compare the same raw model endpoint with and without FINITE;
- distinguish FINITE schema/admission refusals from model tool-selection or argument errors;
- report accuracy by category, hallucinated calls, irrelevance handling, multi-turn completion,
  FINITE-added latency, and failures in the denominator; and
- make no claim that FINITE improves model intelligence unless the paired result and confidence
  interval support it.

Current status: **not executed**.

## SWE-bench Verified and Terminal-bench

These suites become relevant only after FINITE executes untrusted repository or shell work inside a
real per-run sandbox. The present worker boundary is an in-process trusted fixture, so publishing a
SWE-bench or Terminal-bench score now would test an unsafe and non-production execution path.

Prerequisites:

- gVisor or Firecracker isolation with non-root execution, default-deny network, syscall,
  filesystem, CPU, memory, PID, wall-time, and output limits;
- ephemeral credentials and secret-leak tests;
- immutable base-image and dependency identities;
- process-tree termination and artifact export controls; and
- adversarial escape, fork-bomb, disk-fill, network-exfiltration, and oversized-output tests.

Current status: **blocked by sandbox prerequisite; not executed**.

## Enterprise evidence roadmap

The repository may claim only implemented, tested controls:

| Area | Current status | Next admissible proof |
|---|---|---|
| OpenTelemetry | Absent | Redacted run/task/attempt/effect spans with correlation and leakage tests |
| Isolation | Absent for workers | Per-run gVisor or Firecracker executor and hostile corpus |
| Distributed execution | Absent | Leased dispatch/settlement/artifact/effect ownership with stale-worker rejection |
| Identity | Local bearer/HMAC only | OIDC and tenant RBAC, then SAML/SCIM |
| HA | Absent | Replicated coordinator/log, failover drill, and recovery objectives |
| Retention | Absent | Policy retention, deletion, legal hold, tombstones, and audit-trail export |
| VPC/BYOC | Absent | Documented deployment and customer-controlled key/network proof |
| SOC 2 / ISO 27001 | External | Auditor-issued report/certificate after controls operate; never self-declared |

SEC Rule 17a-4 architecture must be described as **17a-4-aligned** until counsel and independent
validation exist. The SEC's
[electronic recordkeeping amendments](https://www.sec.gov/investment/amendments-electronic-recordkeeping-requirements-broker-dealers)
permit either the traditional WORM approach or an audit-trail alternative; FINITE currently
implements neither a regulated retention service nor a compliance attestation.
