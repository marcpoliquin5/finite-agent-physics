from scripts.run_watsonx_sdk_contract import run_contract


def test_official_sdk_builds_the_documented_single_generation_request() -> None:
    report = run_contract()

    assert report["schema_version"] == "finite-watsonx-sdk-contract/v1"
    assert report["measurement_kind"] == "offline-official-sdk-contract"
    assert report["provider"] == "watsonx.ai"

    provenance = report["provenance"]
    assert isinstance(provenance, dict)
    assert provenance["sdk_distribution"] == "ibm-watsonx-ai"
    assert provenance["sdk_version"]
    assert provenance["sdk_repository"] == "https://github.com/IBM/watsonx-ai-python-sdk"

    network = report["network"]
    assert isinstance(network, dict)
    assert network == {
        "external_network_enabled": False,
        "captured_http_posts": 1,
        "endpoint_scheme": "https",
        "endpoint_host": "us-south.ml.cloud.ibm.com",
        "endpoint_path": "/ml/v1/text/generation",
        "api_version": network["api_version"],
        "authorization_header_present": True,
    }
    assert network["api_version"]

    request = report["request"]
    assert isinstance(request, dict)
    assert request["sdk_catalog_validation"] is False
    assert request["sdk_hidden_retries"] == 0
    assert request["guardrails_enabled"] is True
    assert request["body"] == {
        "model_id": "ibm/granite-offline-contract",
        "input": "Return the word finite.",
        "moderations": {
            "hap": {
                "input": {"enabled": True},
                "output": {"enabled": True},
            }
        },
        "parameters": {"decoding_method": "greedy", "max_new_tokens": 8},
        "project_id": "00000000-0000-0000-0000-000000000000",
    }


def test_official_sdk_response_reaches_a_redacted_non_live_finite_receipt() -> None:
    report = run_contract()
    response = report["response"]
    receipt = report["finite_receipt"]

    assert isinstance(response, dict)
    assert response == {
        "results_count": 1,
        "usage_complete": True,
        "input_tokens": 6,
        "output_tokens": 1,
        "stop_reason": "eos_token",
        "generated_text": "finite",
    }
    assert isinstance(receipt, dict)
    assert receipt["schema_version"] == "finite-watsonx-receipt/v1"
    assert receipt["measurement_kind"] == "injected-test-double"
    assert receipt["provider"] == "watsonx.ai"
    assert receipt["model_id"] == "ibm/granite-offline-contract"
    assert receipt["usage_complete"] is True
    assert len(receipt["request_digest"]) == 64
    assert len(receipt["response_digest"]) == 64
    assert all("live" not in limitation.lower() or "not live" in limitation.lower()
               for limitation in report["limitations"][:2])
