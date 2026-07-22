# IBM Bob MCP integration

FINITE exposes a local STDIO MCP server through the committed project configuration at
`.bob/mcp.json`. The server is both a development-evidence seam and a real orchestration client
surface: Bob can preflight a contract, start an admitted run, inspect durable status, request
public-fact explanations, and invoke independent verification.

IBM documents project MCP configuration, STDIO transport, per-tool approval, and the
`mcpServers` JSON shape in [Using MCP in Bob](https://bob.ibm.com/docs/ide/configuration/mcp/mcp-in-bob).
IBM's [security guidance](https://bob.ibm.com/docs/ide/security/bob-security-guidance) recommends
least privilege, reviewed servers, `.bobignore`, and keeping credentials out of configuration and
prompts. FINITE therefore commits no credentials and auto-approves no tools.

## Install and verify outside Bob

```powershell
python -m pip install -e ".[dev,mcp]"
python -m pytest tests/test_mcp_stdio.py tests/test_mcp_tools.py
finite-mcp
```

The protocol test launches the same STDIO entry point Bob launches, performs MCP initialize,
lists the tools, and makes a real tool call. Passing this test proves protocol compatibility; it
does not prove that an entrant used Bob.

## Required Bob lifecycle for the release

Use one exact repository commit and preserve one run ID across these calls:

1. `finite_capabilities` - record the server boundary and tool inventory.
2. `finite_preflight` - record an admitted witness and a deliberately constrained refusal.
3. `finite_run` - start the bundled StormShift fixture first; use `backend="granite"` only when
   real watsonx credentials are configured and the preflight passes.
4. `finite_status` - poll the same run ID until a durable boundary is reached.
5. `finite_explain_run` - retain public facts, rule IDs, manifest revision, and event identities.
6. `finite_verify_run` - independently check the same durable run.
7. Run the physical, adaptive, conformance, and artifact-integrity drills as supporting evidence.

The fixture path should reach `awaiting_effects`: the publication task becomes a proposed intent
and is not committed. A Granite path counts as live evidence only when its receipt says
`measurement_kind="live-watsonx"` and contains provider-derived usage. Test-double receipts never
count.

## Twenty-two exposed tools

| Tool | Purpose | Evidence boundary |
|---|---|---|
| `finite_capabilities` | Versioned inventory and declared limitations | Local introspection |
| `finite_granite_preflight` | Validate watsonx configuration and admitted generation bound | No provider call |
| `finite_run` | Start a durable fixture or explicit Granite-backed run | Backend label is explicit |
| `finite_status` | Read one run's durable lifecycle state | Same run ID required |
| `finite_explain_run` | Return public-fact events and manifest metadata | Never chain-of-thought |
| `finite_verify_run` | Verify one durable run independently | No worker recall |
| `finite_preflight` | Produce a conservative Miami EOC admission certificate | Zero external calls |
| `finite_simulate` | Produce one deterministic schedule trace | Simulation only |
| `finite_verify` | Reconstruct and fail closed on a fresh schedule trace | Simulation only |
| `finite_registered_faults` | Inspect preregistered fault definitions | Registration is not execution |
| `finite_context_drill` | Test hostile-as-data packing and cap refusal | Local deterministic data |
| `finite_effect_drill` | Test approval, ambiguity, and exactly-once behavior | Simulation target only |
| `finite_stormshift_validate` | Validate the fictional typed workflow and adversarial fixtures | Bounded validator semantics |
| `finite_fault_experiment` | Run the registered paired simulator experiment | Descriptive model evidence |
| `finite_executor_drill` | Execute, restart, and reconstruct local fixture work | Trusted fixtures only |
| `finite_quota_corpus` | Replay RPM/TPM/concurrency/reset/retry accounting | Declared quota model |
| `finite_replanning_drill` | Apply a residual capacity loss without erasing history | Modelled local controller |
| `finite_decision_explanation_drill` | Bind one public-fact explanation to every scheduler event | No hidden reasoning |
| `finite_physical_admission_drill` | Check CPU/RAM/VRAM/storage/network/RTT/egress limits | Declared estimates, no energy |
| `finite_adaptive_recovery_drill` | Exercise durable runtime revision, fault, and recovery | Local single coordinator |
| `finite_framework_conformance_drill` | Run neutral and pinned LangGraph witnesses plus loss ledger | PageAgent remains not executed |
| `finite_artifact_integrity_drill` | Verify artifact lineage, sealing, and mutation refusal | Local artifact store |

There is intentionally no generic shell/browser tool and no external effect-commit tool. Untrusted
content cannot broaden FINITE's capabilities through MCP.

## Bob prompt sequence

The exact session may vary, but a useful evidence-producing sequence is:

```text
Inspect FINITE's capability report and release contract. Identify one real defect or missing test.
Make a substantive correction, run the focused tests, and explain which claim boundary changed.

Now call finite_preflight with the default envelope and again with max_tokens=1. Do not call a
worker for the refused case. Start the StormShift fixture with finite_run, poll finite_status,
inspect public facts with finite_explain_run, and verify the same run with finite_verify_run.
Preserve the run ID and returned digests.
```

For a live Granite session, ask Bob to call `finite_granite_preflight` first. Never paste API keys
into the prompt or build log.

## Evidence capture

Only real Bob sessions belong in `docs/bob-build-log.md`. Capture:

- date, time zone, Bob version, and workspace commit;
- the entrant prompt and a concise description of Bob's material contribution;
- changed files and the human review/correction;
- exact tests and their results;
- MCP tool names, one run ID, and returned non-secret digests;
- screenshot or video timestamps stored outside the public repository when they include personal
  or account data; and
- a redacted digest/reference that can be placed in the final evidence manifest.

Never backfill Bob evidence from Codex output, tests, configuration files, or intended future work.
