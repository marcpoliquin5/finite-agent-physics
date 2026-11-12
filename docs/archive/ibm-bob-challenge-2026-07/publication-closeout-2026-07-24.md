# Publication closeout — 2026-07-24

This record closes the repository, IBM Bob, verification, and GitHub publication work completed
on 2026-07-24. It separates public evidence from machine-local diagnostics and does not upgrade
the candidate beyond `v5.0.0-rc.1`.

## Exact public state

- Public repository:
  [`marcpoliquin5/finite-agent-physics`](https://github.com/marcpoliquin5/finite-agent-physics)
- Published default branch: `main`
- Verified executable commit before this documentation-only closeout:
  `ab51ff9fe5294faa2c0bdba2101e440cf943bce4`
- Final `main` workflow:
  [GitHub Actions run 30137617555](https://github.com/marcpoliquin5/finite-agent-physics/actions/runs/30137617555)
- Final workflow result: 10/10 jobs passed
- Pull requests merged:
  [#1](https://github.com/marcpoliquin5/finite-agent-physics/pull/1),
  [#2](https://github.com/marcpoliquin5/finite-agent-physics/pull/2), and
  [#3](https://github.com/marcpoliquin5/finite-agent-physics/pull/3)
- Open pull requests after closeout: zero

The final workflow passed Python 3.11 through 3.14 on Ubuntu, Python 3.11 on Windows, Python 3.14
on macOS, the console gate, the pinned LangGraph comparator, deterministic release-candidate
inspection, and the hardened API-container gate. The closeout document is published by a later
documentation-only merge, so it records its tested executable parent rather than pretending to
self-reference its future merge SHA.

## Genuine IBM Bob status

IBM Bob Shell `1.0.6` was installed from the official IBM distribution path after the installer
and package were downloaded, inspected, and hashed. Bob connected to
`python -m agent_physics.mcp_server` over STDIO and genuine Bob telemetry recorded calls to all
23 FINITE MCP tools. The accepted sessions covered the durable lifecycle, fault and recovery
drills, framework and artifact checks, 450 paired fault-experiment records, and 60/60 Production
Survival trials.

The session IDs, raw-session SHA-256 values, rejected no-tool negative controls, installer
provenance, and exact claim boundary are preserved in
[`bob-live-validation-2026-07-24.md`](bob-live-validation-2026-07-24.md).

## Verification closed on the published source

The isolated PowerShell release verifier passed after the SQLite contention repair:

- 1,020/1,020 Python tests passed;
- zero failures, errors, skips, xfails, or disabled tests;
- statement coverage: `93.821729%`;
- branch coverage: `85.774135%`;
- 64/64 real-loopback, proposal-only load records passed and were independently reverified;
- Ruff, dependency consistency, Bandit, pip-audit, license policy, console
  build/test/lint/audit, and sealed judge-evidence checks passed;
- Bandit reported zero medium/high findings;
- Python and npm audits found no known vulnerabilities; and
- the source tree remained clean before and after the gate.

The exact published `main` commit then passed the complete GitHub matrix. That run retained ten
GitHub-hosted artifacts covering platform JUnit evidence, Ubuntu coverage/load evidence,
LangGraph conformance, console evidence, deterministic release-candidate packages, and container
security results.

## Defects found and closed during publication

1. The 32-way Windows workload exposed bounded SQLite writer contention. Both durable ledgers now
   allow a 30-second SQLite busy window, still inside the 90-second load-proof request deadline.
   Regression tests bind the configured limit. The repaired source passed the isolated 64-run
   workload and both push- and pull-request-triggered Windows workflows.
2. A capability-manifest test used a one-second absolute deadline even though it did not test
   deadline behavior. A paused Python 3.14 hosted runner exhausted that test-only window. The test
   now provides ten seconds of scheduler headroom; production deadline enforcement is unchanged.
   The focused test passed 20 consecutive local executions and the full dual GitHub matrix.

## What was deliberately not published

- Watsonx credentials or other secrets were never added to Git, evidence, logs, or GitHub.
- Raw Bob session JSON was not committed because it contains Bob-private `thoughts` fields and
  machine-local metadata. Non-secret hashes and accepted outcomes are committed instead.
- Workspace-local virtual environments, coverage databases, downloaded packages, diagnostic
  logs, SQLite state, and regenerated test outputs remain ignored. Equivalent clean-run evidence
  is retained as GitHub Actions artifacts.
- No stable `v5.0.0` tag or GitHub release was created.
- No live-watsonx/Granite receipt, anonymous judge deployment, public video, eligibility record,
  SkillsBuild record, project-page receipt, or final submission receipt is claimed.

## Remaining IBM boundary

The Bob/MCP integration is ready. A genuine Granite execution still requires
`WATSONX_URL`, `WATSONX_API_KEY`, `WATSONX_PROJECT_ID`, and `WATSONX_MODEL_ID` in the same
environment that launches Bob and FINITE. After injection through the operator's secret
mechanism, preserve one redacted same-run preflight/run/status/explain/verify receipt at the
reviewed release commit. Until then, the accurate status remains: **public, tested release
candidate with genuine Bob execution; no live Granite evidence and no stable-v5 release.**
