"""Run FINITE's credential-free contract against IBM's real watsonx.ai SDK.

This is deliberately not a live-provider test.  It executes the official
``ibm-watsonx-ai`` request builder, moderation payload builder, retry wrapper, and response
parser while replacing only the final HTTP transport with a deterministic capture.  A socket
guard makes accidental external network access fail immediately.
"""

from __future__ import annotations

import argparse
import json
import socket
from importlib.metadata import version
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import urlparse

from agent_physics.watsonx import WatsonxConfig, WatsonxGraniteAdapter

_ENDPOINT = "https://us-south.ml.cloud.ibm.com"
_MODEL_ID = "ibm/granite-offline-contract"
_PROJECT_ID = "00000000-0000-0000-0000-000000000000"
_PROMPT = "Return the word finite."
_DUMMY_API_KEY = "finite-offline-contract-api-key"
_DUMMY_BEARER = "Bearer finite-offline-contract-token"
_PARAMETERS = {"decoding_method": "greedy", "max_new_tokens": 8}
_SDK_REPOSITORY = "https://github.com/IBM/watsonx-ai-python-sdk"
_REST_CONTRACT = "https://cloud.ibm.com/apidocs/watsonx-ai#text-generation"


class ContractFailure(RuntimeError):
    """Raised when the official SDK no longer satisfies FINITE's admitted contract."""


class _CapturedHttpxClient:
    def __init__(self, delegate: object) -> None:
        self._delegate = delegate
        self.calls: list[dict[str, Any]] = []

    def post(self, *args: object, **kwargs: Any) -> Any:
        if args:
            if len(args) != 1 or "url" in kwargs:
                raise ContractFailure("unexpected positional IBM SDK HTTP arguments")
            kwargs["url"] = args[0]
        required = {"url", "json", "params", "headers"}
        if not required.issubset(kwargs):
            raise ContractFailure("IBM SDK POST omitted required transport fields")
        self.calls.append(dict(kwargs))

        import httpx

        request = httpx.Request(
            "POST",
            str(kwargs["url"]),
            params=kwargs["params"],
            headers=kwargs["headers"],
            json=kwargs["json"],
        )
        response = {
            "model_id": _MODEL_ID,
            "created_at": "2026-07-27T00:00:00Z",
            "results": [
                {
                    "generated_text": "finite",
                    "input_token_count": 6,
                    "generated_token_count": 1,
                    "stop_reason": "eos_token",
                }
            ],
        }
        return httpx.Response(200, request=request, json=response)

    def __getattr__(self, name: str) -> object:
        return getattr(self._delegate, name)


def _blocked_connect(*_args: object, **_kwargs: object) -> None:
    raise ContractFailure("offline SDK contract attempted external network access")


def _sdk_factory(capture_holder: list[_CapturedHttpxClient], **kwargs: Any) -> Any:
    """Construct IBM's real ModelInference with an intercepted HTTP transport."""

    if kwargs.get("validate") is not False:
        raise ContractFailure("SDK catalog validation must be disabled")
    if kwargs.get("max_retries") != 0:
        raise ContractFailure("SDK hidden retries must be disabled")
    if kwargs.get("project_id") != _PROJECT_ID:
        raise ContractFailure("project ID changed before SDK construction")

    from ibm_watsonx_ai import APIClient, Credentials
    from ibm_watsonx_ai.foundation_models import ModelInference

    credentials = Credentials(url=_ENDPOINT, token=_DUMMY_BEARER)
    client = APIClient(credentials=credentials)
    # ``Set.default_project`` performs a remote project lookup.  Assigning the fixture container
    # directly keeps this contract network-free while exercising the official payload builder.
    client.default_project_id = _PROJECT_ID
    capture = _CapturedHttpxClient(client.httpx_client)
    client.httpx_client = capture
    capture_holder.append(capture)
    return ModelInference(
        model_id=kwargs["model_id"],
        api_client=client,
        params=kwargs["params"],
        validate=kwargs["validate"],
        max_retries=kwargs["max_retries"],
    )


def run_contract() -> dict[str, object]:
    """Execute and return a redacted report for the installed IBM SDK."""

    captures: list[_CapturedHttpxClient] = []
    config = WatsonxConfig(
        url=_ENDPOINT,
        api_key=_DUMMY_API_KEY,
        project_id=_PROJECT_ID,
        model_id=_MODEL_ID,
    )

    with (
        patch.object(socket.socket, "connect", _blocked_connect),
        patch.object(socket, "create_connection", _blocked_connect),
    ):
        receipt = WatsonxGraniteAdapter(
            config,
            lambda **kwargs: _sdk_factory(captures, **kwargs),
        ).generate(_PROMPT, max_new_tokens=8, guardrails=True)

    if len(captures) != 1 or len(captures[0].calls) != 1:
        raise ContractFailure("expected exactly one captured IBM SDK generation POST")
    call = captures[0].calls[0]
    parsed = urlparse(str(call["url"]))
    headers = {str(key).lower(): str(value) for key, value in call["headers"].items()}
    body = call["json"]
    query = call["params"]
    if not isinstance(body, dict) or not isinstance(query, dict):
        raise ContractFailure("IBM SDK transport payload is not a JSON object")

    report: dict[str, object] = {
        "schema_version": "finite-watsonx-sdk-contract/v1",
        "measurement_kind": "offline-official-sdk-contract",
        "provider": "watsonx.ai",
        "provenance": {
            "sdk_distribution": "ibm-watsonx-ai",
            "sdk_version": version("ibm-watsonx-ai"),
            "sdk_repository": _SDK_REPOSITORY,
            "rest_contract": _REST_CONTRACT,
        },
        "network": {
            "external_network_enabled": False,
            "captured_http_posts": 1,
            "endpoint_scheme": parsed.scheme,
            "endpoint_host": parsed.hostname,
            "endpoint_path": parsed.path,
            "api_version": query.get("version"),
            "authorization_header_present": "authorization" in headers,
        },
        "request": {
            "body": body,
            "parameters": _PARAMETERS,
            "guardrails_enabled": True,
            "sdk_catalog_validation": False,
            "sdk_hidden_retries": 0,
        },
        "response": {
            "results_count": 1,
            "usage_complete": receipt.usage_complete,
            "input_tokens": receipt.input_tokens,
            "output_tokens": receipt.output_tokens,
            "stop_reason": receipt.stop_reason,
            "generated_text": receipt.generated_text,
        },
        "finite_receipt": {
            "schema_version": receipt.schema_version,
            "measurement_kind": receipt.measurement_kind,
            "provider": receipt.provider,
            "model_id": receipt.model_id,
            "request_digest": receipt.request_digest,
            "response_digest": receipt.response_digest,
            "usage_complete": receipt.usage_complete,
        },
        "limitations": [
            "No IBM account, credential, catalog, model, billing, or provider network call occurred.",
            "This proves SDK wire compatibility and FINITE integration semantics, not live Granite quality.",
            "A genuine live-watsonx receipt remains a separate external evidence class.",
        ],
    }
    serialized = json.dumps(report, sort_keys=True)
    if _DUMMY_API_KEY in serialized or _DUMMY_BEARER in serialized:
        raise ContractFailure("offline credential marker leaked into the contract report")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the credential-free FINITE contract through IBM's official watsonx SDK."
    )
    parser.add_argument("--output", type=Path, help="Optional JSON report path.")
    arguments = parser.parse_args()
    report = run_contract()
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
