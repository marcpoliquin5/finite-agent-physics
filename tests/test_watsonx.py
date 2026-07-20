import pytest

from agent_physics.watsonx import (
    WatsonxConfig,
    WatsonxConfigurationError,
    WatsonxGraniteAdapter,
    WatsonxResponseError,
)


class FakeInference:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def generate(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return self.response


def config() -> WatsonxConfig:
    return WatsonxConfig(
        url="https://example.ml.cloud.ibm.com",
        api_key="secret-not-for-logs",
        project_id="project",
        model_id="ibm/granite-test",
    )


def test_environment_config_fails_closed_and_redacts_secret() -> None:
    with pytest.raises(WatsonxConfigurationError, match="WATSONX_API_KEY"):
        WatsonxConfig.from_environment({})
    assert "secret-not-for-logs" not in str(config().public_dict())


def test_adapter_labels_injected_receipt_and_disables_hidden_retry() -> None:
    fake = FakeInference(
        {
            "results": [
                {
                    "generated_text": "validated response",
                    "input_token_count": 12,
                    "generated_token_count": 4,
                    "stop_reason": "eos_token",
                }
            ]
        }
    )
    construction: list[dict[str, object]] = []

    def factory(**kwargs: object) -> FakeInference:
        construction.append(kwargs)
        return fake

    receipt = WatsonxGraniteAdapter(config(), factory).generate("fixture prompt")
    assert receipt.measurement_kind == "injected-test-double"
    assert receipt.usage_complete
    assert receipt.input_tokens == 12
    assert receipt.output_tokens == 4
    assert construction[0]["max_retries"] == 0
    assert "secret-not-for-logs" not in str(receipt.as_dict())
    assert len(fake.calls) == 1


def test_adapter_rejects_malformed_response_instead_of_fabricating_usage() -> None:
    adapter = WatsonxGraniteAdapter(config(), lambda **_: FakeInference({"results": []}))
    with pytest.raises(WatsonxResponseError, match="no result"):
        adapter.generate("fixture prompt")


def test_adapter_validates_bounded_request() -> None:
    adapter = WatsonxGraniteAdapter(config(), lambda **_: FakeInference({}))
    with pytest.raises(ValueError, match="empty"):
        adapter.generate(" ")
    with pytest.raises(ValueError, match="positive"):
        adapter.generate("prompt", max_new_tokens=0)
