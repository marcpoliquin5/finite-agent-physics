# IBM offline contract certification

This certification is the strongest honest IBM-path test that does not require an IBM Cloud
account. It executes IBM Bob Shell's own MCP discovery command, the official Model Context
Protocol client, and IBM's real `ibm-watsonx-ai` request/response implementation. It does not
rename local output as a live Granite result.

Run the complete Windows certification from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\test_ibm_offline.ps1
```

Evidence is written under `artifacts/ibm-offline-certification/` and includes the Bob version,
Bob MCP connection status, JUnit XML, the redacted watsonx SDK wire report, and a summary.

## What the IBM boundary is built on

The FINITE-to-Bob boundary is a project-level `.bob/mcp.json` entry using local STDIO. IBM's
[MCP documentation](https://bob.ibm.com/docs/shell/configuration/mcp/mcp-bobshell) defines the
`command`, `args`, `cwd`, and `disabled` fields used by this repository. IBM's
[transport documentation](https://bob.ibm.com/docs/ide/configuration/mcp/server-transports)
specifies that Bob starts the server as a child process and exchanges newline-delimited JSON-RPC
2.0 messages over STDIN and STDOUT.

The FINITE-to-watsonx boundary is IBM's `ibm-watsonx-ai` `ModelInference` class. The official
[SDK repository](https://github.com/IBM/watsonx-ai-python-sdk) and
[SDK documentation](https://ibm.github.io/watsonx-ai-python-sdk/fm_model_inference.html) describe
that interface. IBM's [REST documentation](https://www.ibm.com/docs/en/watsonx/saas?topic=resources-rest-api)
and [text-generation example](https://www.ibm.com/docs/en/watsonx/w-and-w/2.2.0?topic=code-text-generation)
define the `/ml/v1/text/generation` request with `input`, `parameters`, `model_id`, and
`project_id`.

## Why this is not copied from an IBM test suite

The public `IBM/watsonx-ai-python-sdk` repository contains the documentation repository, not
IBM's internal SDK tests. The official 1.5.3 and 1.6.0 source distributions inspected on
2026-07-27 likewise contain no `tests/` directory or `test_*.py` files:

| Distribution | Source archive SHA-256 |
| --- | --- |
| `ibm-watsonx-ai==1.5.3` | `610c982416e18479e2029d16062e992d42a5454b6db5ed68541aa53b8f3bfa54` |
| `ibm-watsonx-ai==1.6.0` | `707dbfe8c641a0053114037c2e56f85fa20028c50af20d96338c3ac92185e0d7` |

FINITE therefore does not claim to run unavailable IBM internal tests. Instead, the contract gate
runs the distributed IBM SDK itself and replaces only its final HTTP transport.

## Executed layers

| Layer | Credential-free proof |
| --- | --- |
| IBM Bob installation | `bob --version` must succeed. |
| IBM Bob project configuration | `bob mcp list` must report `finite-agent-physics` as `Connected` over `stdio`. |
| MCP protocol | The official Python MCP client performs initialization, discovers exactly 23 tools, invokes all 23, checks structured output, and preserves one durable lifecycle. |
| Isolation | The MCP subprocess test blocks external sockets and child-process creation. |
| IBM SDK versions | GitHub CI repeats the contract with exact SDK versions 1.5.3 and 1.6.0. |
| SDK construction | `validate=False` prevents unadmitted catalog lookups and `max_retries=0` prevents hidden retries. |
| IBM request builder | The real SDK builds one HTTPS POST to `/ml/v1/text/generation`, including its API-version query. |
| Generation contract | The captured request must contain the exact model ID, project ID, prompt, greedy eight-token bound, and HAP input/output moderation fields. |
| IBM response parser | The real SDK parses a documented response containing `results`, generated text, stop reason, and provider-style token counts. |
| FINITE receipt | The adapter creates digests and a usage-complete receipt labeled `injected-test-double`, never `live-watsonx`. |
| Durable execution | Worker tests enforce admission, settlement, cancellation, tamper rejection, and resume without a duplicate model attempt. |

The SDK contract installs a socket guard before construction. Any unexpected external connection
fails the test. The deterministic final HTTP response is supplied to IBM's actual response parser,
not directly to FINITE.

## Defect closed by the contract

IBM SDK versions 1.5.3 and 1.6.0 default `ModelInference(validate=True)`. For a foundation model,
that constructor retrieves the model catalog before generation. FINITE previously disabled SDK
retries but did not disable that catalog validation, so one admitted generation could be preceded
by unaccounted network requests.

FINITE now passes both `validate=False` and `max_retries=0`. Model availability is resolved by the
single admitted generation request. This preserves FINITE's attempt, deadline, and resource
accounting at the provider boundary.

## Evidence boundary

Passing this certification proves:

- Bob can parse the committed project configuration and connect to the FINITE MCP server;
- the complete MCP surface works through the standardized STDIO client;
- supported IBM SDK releases build the documented request accepted by FINITE;
- FINITE consumes the documented response shape, settles provider-style usage, and resumes safely;
- the offline path leaks no API key and makes no provider network call.

It does not prove:

- IBM IAM authentication, project entitlement, regional model availability, or billing;
- a real Granite output, provider latency, quality, safety behavior, or uptime;
- a genuine live-watsonx receipt; or
- hackathon eligibility, judging, deployment, video, or submission.

Those remain distinct external evidence classes. The repository must continue to describe the
offline report as `offline-official-sdk-contract`, never as live IBM execution.
