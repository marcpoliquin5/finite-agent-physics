# IBM Bob Shell validation — 2026-07-24

This record preserves a genuine IBM Bob Shell testing contribution against executable commit
`2be8f80f55f92f934d1c376b2650b70ff051c4ab`. It is Bob evidence, but it is **not** a live
watsonx/Granite receipt, a stable-v5 declaration, or proof of an external effect.

## Environment and installer provenance

- IBM Bob Shell: `1.0.6`
- Node.js: `24.13.1`
- Host shell: Windows PowerShell
- Workspace: public `main` branch of `marcpoliquin5/finite-agent-physics`
- Project MCP result: `finite-agent-physics: python -m agent_physics.mcp_server (stdio) -
  Connected`
- Official installer URL:
  `https://bob.ibm.com/download/bobshell.ps1`
- Downloaded installer SHA-256:
  `691DB83B722FF52A944918DA58D32CEC6A7D13C81E7E0C7A1D7C00CC0F4479B2`
- IBM-hosted `bobshell-1.0.6.tgz` SHA-256:
  `6EC51ABEC4251D41EC45709030988B90BAA659F535FC8D14DD003023DD163A5B`

The PowerShell installer was not Authenticode-signed. Before execution it was downloaded rather
than piped directly into `iex`, hashed, and inspected. It fetched the version marker and package
from IBM Cloud Object Storage and installed the package through npm. No encoded payload,
credential-harvesting command, Defender change, or registry edit was found in that inspection.

## Genuine Bob sessions

Bob stores the original session JSON locally. The table records full session IDs and SHA-256
digests of those originals. The raw files are not committed because they include Bob-private
`thoughts` fields and machine-local metadata. A pattern scan of all seven session files found zero
GitHub PATs, bearer-token values, or API/access/refresh-token value fields.

| Session | Purpose | Bob-observed result | Raw-session SHA-256 |
|---|---|---|---|
| `10d422d9-fa9b-4540-8ed5-4446ebdce914` | Capability discovery | Bob telemetry recorded one successful `finite_capabilities` call and returned the authentic 23-tool inventory. | `EF5879D23188614C57876813E6E95D01552BA682FE58BE387B1F5289E188CCBE` |
| `d33ee762-824f-4532-b886-13d664bb7791` | Durable lifecycle | Two preflights, Granite preflight, fixture run, status, explanation, and verification were invoked. The fixture produced 34 events, stopped at `awaiting_effects`, and passed all eight control-ledger checks. | `2DCA22DBC94C399220637CE466A6E388CEC60B0C9EF58B55AAB54C7B04EBAF7C` |
| `18f769ce-4a3b-45bd-bd5d-dfc4ee0f0156` | Safety and fault suite | Bob telemetry recorded all 13 requested MCP calls. The hard-crash effect applied once; all five injected StormShift faults were rejected; the 450-record paired fault experiment and durable executor drill passed. | `69678790344885B7EF23F4E0A9A36556040695209CFD76EACC9A21CA14FA357C` |
| `652c9b87-60e7-4999-b0a8-3b6960dc001f` | Systems proof suite | Bob telemetry recorded all 10 requested MCP calls. Quota/replanning/explanation/physical/recovery/framework/artifact proofs passed, and Production Survival passed 60/60 trials with zero provider calls and zero duplicate effects. | `B52E4E3408B15BE454158BB3EFAFB69E9B7B5513057487E54382B0CEE5B39D37` |
| `6d520ec3-94d0-4900-8f19-f80e972a9317` | Explicit Granite boundary | Bob invoked `finite_run` in `granite-probe` mode. It failed before provider dispatch because the four required watsonx variables were absent. No Granite response was fabricated. | `0CFEB51F0E3C0447C3A608D0C1F822CD7D899B555244FAF5B94D5B8A2B74384F` |

Together, the accepted sessions invoked every one of the 23 unique FINITE MCP tools. Repeated calls
also covered all six StormShift validation variants and all three decision-explanation modes.

## Negative controls retained

The first capability prompt, session `a5bb17cf-c60b-4eb0-99b4-961d62fa8464`, returned an invented
12-tool payload. Its Bob telemetry showed only `attempt_completion`, so the result was rejected
and is not counted as MCP evidence. A later attempt,
`53b57350-3a34-45ee-9aaf-2644aa1a5146`, correctly reported that project MCP tools were unavailable
without an explicit `--allowed-tools` allowlist. The accepted reruns name every permitted tool and
their telemetry records the actual MCP calls.

## Independent PowerShell release verification

After the Bob sessions, `python scripts/verify_release.py` ran in a fresh ephemeral environment:

- 1,018/1,018 Python tests passed; zero failures, errors, skips, xfails, or disabled tests.
- Statement coverage: `93.820941%`; branch coverage: `85.774135%`.
- The independently verified loopback load proof passed 64/64 records.
- Ruff, dependency consistency, Bandit, pip-audit, license policy, console build/test/lint, and
  npm audit passed.
- Bandit reported zero medium/high findings; pip-audit and npm audit found no known
  vulnerabilities.
- The source tree remained clean.

The hardened image was then built and exercised locally: it became healthy and ready, enforced
401 without its test bearer token, accepted the authorized request, ran as UID 10001, used a
read-only root filesystem, dropped all capabilities, and enabled `no-new-privileges`. Digest-pinned
Trivy scans found zero source, configuration, secret, OS-package, or Python-package findings at
the configured all-severity gate.

## Remaining IBM boundary

The Bob contribution and MCP lifecycle are now real. Live Granite remains blocked because these
four secrets were absent from Bob's environment:

- `WATSONX_URL`
- `WATSONX_API_KEY`
- `WATSONX_PROJECT_ID`
- `WATSONX_MODEL_ID`

When those values are injected through the operator's secret mechanism, rerun
`finite_granite_preflight`, then `finite_run(mode="granite-probe")`, `finite_status`,
`finite_explain_run`, and `finite_verify_run` for one preserved run ID. Retain the redacted
provider-usage, latency, model, output/artifact digest, validator, Bob-session, and commit
bindings. Until that happens, the only accurate claim is: **genuine IBM Bob Shell invoked and
tested the complete local MCP surface; live watsonx/Granite execution is not yet evidenced.**

## Publication closeout

The historical Bob sessions above remain bound to executable commit `2be8f80`; they were not
relabelled as executions against later commits. Subsequent durability and test-fixture fixes,
the 1,020-test isolated gate, the final public commit, all-green GitHub workflow, and retained
GitHub artifacts are recorded separately in
[`publication-closeout-2026-07-24.md`](publication-closeout-2026-07-24.md).
