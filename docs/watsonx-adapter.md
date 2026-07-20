# IBM watsonx.ai / Granite adapter

The optional adapter uses IBM's `ModelInference` API and produces a receipt labeled
`live-watsonx`. IBM documents the constructor, bounded generation parameters, async methods,
and returned generation result in the
[watsonx.ai Python SDK](https://ibm.github.io/watsonx-ai-python-sdk/fm_model_inference.html).
IBM's [programmatic inference guide](https://dataplatform.cloud.ibm.com/docs/content/wsj/analyze-data/fm-prompt-notebook.html?context=wx)
requires a service URL, API key, project or space ID, and model ID.

## Configuration

```powershell
python -m pip install -e ".[watsonx]"
$env:WATSONX_URL="https://REGION.ml.cloud.ibm.com"
$env:WATSONX_API_KEY="..."
$env:WATSONX_PROJECT_ID="..."
$env:WATSONX_MODEL_ID="IBM_GRANITE_MODEL_AVAILABLE_IN_YOUR_REGION"
```

The adapter intentionally has no default region or model ID because availability changes by
account and region. It never serializes the API key. SDK retries are disabled so the future
FINITE executor, not a hidden client loop, can reserve and settle every attempt.

## Evidence boundary

Unit tests use an injected fake response and are not IBM inference evidence. A benchmark may
be labeled live only when the real SDK path runs with an available Granite model and preserves
the raw, redacted receipt. Missing token counts remain `null`; they are never fabricated as
zero. The adapter is not yet wired into the runtime scheduler.
