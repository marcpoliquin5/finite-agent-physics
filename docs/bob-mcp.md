# IBM Bob MCP integration

FINITE exposes a local STDIO MCP server through the committed project configuration at
`.bob/mcp.json`. This is the first Bob-facing seam; every current tool explicitly reports
that it operates on deterministic simulation data.

IBM documents project MCP configuration, STDIO transport, per-tool approval, and the
`mcpServers` JSON shape in [Using MCP in Bob](https://bob.ibm.com/docs/ide/configuration/mcp/mcp-in-bob).
IBM's [security guidance](https://bob.ibm.com/docs/ide/security/bob-security-guidance)
recommends least privilege, reviewed servers, `.bobignore`, and keeping credentials out of
configuration and prompts. FINITE therefore commits no credentials and configures no
auto-approved tool.

## Install and verify outside Bob

```powershell
python -m pip install -e ".[dev]"
python -m pytest tests/test_mcp_stdio.py
```

The integration test launches the exact STDIO command Bob will launch, performs the MCP
initialize handshake, lists tools, and calls `finite_capabilities`.

## Use from Bob

1. Open this repository as the Bob workspace.
2. Enable MCP servers in Bob and inspect the project-level server configuration.
3. Install the project into the Python environment resolved by the `python` command, or
   replace only the local command path in an uncommitted configuration when necessary.
4. Restart `finite-agent-physics` from Bob's MCP panel.
5. Ask Bob to call `finite_capabilities` first.
6. Call `finite_preflight` once with the default envelope and once with `max_tokens=1`.
7. Call `finite_executor_drill`, `finite_stormshift_validate`,
   `finite_replanning_drill`, `finite_quota_corpus`, and
   `finite_decision_explanation_drill`; retain Bob's tool-call evidence and returned digests.
8. Call `finite_verify` and retain the fail-closed verification result.

The expected contrast is a feasible schedule witness versus a conservative pre-spend
refusal. The refusal is deliberately not called a proof of mathematical infeasibility.

## Exposed tools

- `finite_capabilities`: implementation and limitation statement.
- `finite_preflight`: deterministic Miami EOC admission certificate.
- `finite_simulate`: deterministic schedule trace for one development-reference policy.
- `finite_verify`: fail-closed reconstruction of a fresh trace.
- `finite_registered_faults`: preregistered faults, all labeled not yet executed.
- `finite_context_drill`: data-only hostile-context packing and cap refusal.
- `finite_effect_drill`: simulation-only crash/idempotency/approval drill.
- `finite_stormshift_validate`: structural validation of the typed fictional workload and
  adversarial fixture transformations.
- `finite_fault_experiment`: one nominal control plus four pre-dispatch simulated faults,
  across 30 paired seeds and three development policies (450 raw records).
- `finite_executor_drill`: durable local fixture execution and restart reconstruction; the
  publication task stops in `awaiting_effects` with a `PROPOSED` intent.
- `finite_quota_corpus`: seeded declared-RPM/TPM/concurrency/reset/retry accounting with
  independent local event replay; never provider telemetry.
- `finite_replanning_drill`: one modeled StormShift capacity loss that sheds optional work,
  preserves mandatory work, and binds the residual decision to a durable state revision.
- `finite_decision_explanation_drill`: one content-addressed public-fact record per scheduler
  event for nominal, degraded, or refused runs; never chain-of-thought.

There is intentionally no external effect-commit tool and no generic provider-backed tool
named `run` yet. Those surfaces remain blocked until live adapter-side caps, production IAM,
worker isolation, and distributed run ownership exist.

## Evidence rule

Only real Bob sessions belong in `docs/bob-build-log.md`. Capture the date, Bob version,
prompt, MCP tool call, result digest, affected files, tests, and screenshot or video segment.
Never backfill a Bob record from a Codex session.
