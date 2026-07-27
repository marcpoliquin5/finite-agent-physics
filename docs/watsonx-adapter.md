# IBM watsonx.ai / Granite adapter

FINITE's optional Granite path uses IBM's `ModelInference` API and binds one SDK request to one
admitted runtime attempt. IBM documents the constructor, bounded generation parameters, async
options, and response shape in the
[watsonx.ai Python SDK](https://ibm.github.io/watsonx-ai-python-sdk/fm_model_inference.html).
IBM's [programmatic inference guide](https://dataplatform.cloud.ibm.com/docs/content/wsj/analyze-data/fm-prompt-notebook.html?context=wx)
describes the required service URL, API key, project or space, and model ID.

For the secure, interactive PowerShell-to-Bob path, use the
[watsonx operator handoff](watsonx-operator-handoff.md). It prompts for the API key without echoing
or persisting it, runs a call-free preflight, requires explicit live-call authorization, and
launches Bob with the bounded MCP runbook in the same process.

When IBM Cloud credentials or project access are unavailable, use the
[IBM offline contract certification](ibm-offline-certification.md). It runs IBM Bob's MCP
connection check and the distributed IBM SDK request/response path with external sockets blocked.
It is stronger than an adapter-only mock but remains non-live evidence.

## Configuration

```powershell
python -m pip install -e ".[watsonx]"
$env:WATSONX_URL="https://REGION.ml.cloud.ibm.com"
$env:WATSONX_API_KEY="..."
$env:WATSONX_PROJECT_ID="..."
$env:WATSONX_MODEL_ID="GRANITE_MODEL_AVAILABLE_TO_THIS_PROJECT"
```

There is deliberately no default region or model. Availability and account entitlements can
change. The API key is never included in configuration summaries, receipts, run outputs, or
release artifacts.

Preflight the exact environment without making an inference call:

```powershell
python -c "from agent_physics.bob_lifecycle import default_bob_run_service; print(default_bob_run_service().granite_preflight())"
```

The intended judged path is Bob calling `finite_granite_preflight`, then `finite_run` with the
explicit Granite backend, followed by `finite_status`, `finite_explain_run`, and
`finite_verify_run` for one run ID.

## Runtime ownership

`WatsonxGraniteAdapter` owns exactly one bounded generation request. It passes `validate=False`
because IBM's default validation performs remote model-catalog requests during construction. It
also passes `max_retries=0`. FINITE, rather than SDK catalog or retry traffic, therefore owns
attempt count, worst-case reservation, deadline, retry, and settlement. Model availability is
resolved by the admitted generation request itself.

`WatsonxTaskWorker` binds that request to the admitted task and profile:

- the selected provider must be `watsonx.ai`;
- the generation token bound cannot exceed the admitted output reservation;
- rendered prompt bytes cannot exceed the admitted context reservation;
- dependency outputs are serialized as untrusted data and cannot change authority;
- declared writes are rejected before the provider call;
- provider token usage is required for settlement; missing counts fail closed;
- cost is the admitted profile upper bound, not an assertion about provider billing;
- validated outputs and receipt fields enter durable run state; and
- resume reuses a completed durable output instead of recalling the model.

The SDK call is synchronous and runs in a thread. Python cannot safely hard-kill that request.
FINITE refuses late settlement after cancellation or deadline expiry, but production hard
cancellation requires a process-isolated adapter.

## Redacted receipt

A genuine receipt records:

- schema and `measurement_kind="live-watsonx"`;
- provider and exact model ID;
- request and response SHA-256 digests;
- measured adapter latency;
- provider-reported input and output tokens when supplied;
- stop reason and usage-completeness flag; and
- generated output plus a separately bound output digest inside the durable worker record.

The release evidence should additionally bind the receipt to the FINITE run ID, task ID, attempt,
workflow/manifest identity, validator result, exact commit, and Bob session reference. Any account
or personal evidence may be kept private, but its redacted digest and reviewer reference must be
preserved.

## Evidence boundary

Unit tests inject a fake inference factory and label every resulting receipt
`injected-test-double`. The separate credential-free contract uses that injection seam to
construct IBM's real `ModelInference`, then intercepts only the final HTTP POST. Together they
prove adapter shape, the IBM-documented wire payload, response parsing, redaction, bounding,
settlement, validation, and resume semantics. They are not live IBM inference evidence.

A result may be described as live Granite evidence only when:

1. the real IBM SDK path runs using entrant-owned credentials;
2. the configured model is an IBM Granite model available to that project;
3. provider-reported usage is complete enough for FINITE settlement;
4. the redacted receipt and durable run verify at the release commit; and
5. the associated genuine Bob lifecycle evidence is captured without secrets.

Until those five conditions pass, stable `v5.0.0` remains blocked and no performance comparison
may use the fake-adapter result.
