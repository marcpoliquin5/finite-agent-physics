"""Optional IBM watsonx.ai Granite adapter with explicit live receipts."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from time import monotonic_ns
from typing import Any, Callable, Mapping

from .serialization import content_digest


class WatsonxConfigurationError(ValueError):
    """Raised when live watsonx configuration is absent or incomplete."""


class WatsonxResponseError(RuntimeError):
    """Raised when watsonx returns a response without the required result shape."""


@dataclass(frozen=True, slots=True)
class WatsonxConfig:
    url: str
    api_key: str
    project_id: str
    model_id: str

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> "WatsonxConfig":
        values = environment if environment is not None else os.environ
        names = (
            "WATSONX_URL",
            "WATSONX_API_KEY",
            "WATSONX_PROJECT_ID",
            "WATSONX_MODEL_ID",
        )
        missing = [name for name in names if not values.get(name, "").strip()]
        if missing:
            raise WatsonxConfigurationError(
                "missing required watsonx environment variables: " + ", ".join(missing)
            )
        return cls(
            url=values["WATSONX_URL"].strip(),
            api_key=values["WATSONX_API_KEY"].strip(),
            project_id=values["WATSONX_PROJECT_ID"].strip(),
            model_id=values["WATSONX_MODEL_ID"].strip(),
        )

    def public_dict(self) -> dict[str, str]:
        """Return configuration safe for logs; the API key is never serialized."""

        return {
            "url": self.url,
            "project_id": self.project_id,
            "model_id": self.model_id,
        }


@dataclass(frozen=True, slots=True)
class WatsonxInferenceReceipt:
    schema_version: str
    measurement_kind: str
    provider: str
    model_id: str
    request_digest: str
    response_digest: str
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None
    stop_reason: str | None
    generated_text: str

    @property
    def usage_complete(self) -> bool:
        return self.input_tokens is not None and self.output_tokens is not None

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["usage_complete"] = self.usage_complete
        return payload


class WatsonxGraniteAdapter:
    """Make one bounded watsonx generation call and preserve an auditable receipt.

    Retry ownership stays outside the SDK (`max_retries=0`) so FINITE can account for every
    attempt. Tests inject a fake factory; a real SDK import occurs only on live construction.
    """

    def __init__(
        self,
        config: WatsonxConfig,
        inference_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.config = config
        self._factory = inference_factory or self._sdk_factory
        self._measurement_kind = (
            "injected-test-double" if inference_factory is not None else "live-watsonx"
        )

    @staticmethod
    def _sdk_factory(**kwargs: Any) -> Any:
        try:
            from ibm_watsonx_ai.foundation_models import ModelInference
        except ImportError as error:  # pragma: no cover - depends on optional live extra
            raise RuntimeError(
                'Install the live adapter with: pip install -e ".[watsonx]"'
            ) from error
        return ModelInference(**kwargs)

    def generate(
        self,
        prompt: str,
        *,
        max_new_tokens: int = 256,
        guardrails: bool = True,
    ) -> WatsonxInferenceReceipt:
        if not prompt.strip():
            raise ValueError("prompt cannot be empty")
        if max_new_tokens <= 0:
            raise ValueError("max_new_tokens must be positive")
        parameters = {
            "decoding_method": "greedy",
            "max_new_tokens": max_new_tokens,
        }
        model = self._factory(
            model_id=self.config.model_id,
            credentials={"url": self.config.url, "apikey": self.config.api_key},
            project_id=self.config.project_id,
            params=parameters,
            max_retries=0,
        )
        started_ns = monotonic_ns()
        response = model.generate(
            prompt=prompt,
            params=parameters,
            guardrails=guardrails,
        )
        latency_ms = max(0, (monotonic_ns() - started_ns) // 1_000_000)
        if not isinstance(response, dict) or not isinstance(response.get("results"), list):
            raise WatsonxResponseError("watsonx response is missing a results list")
        if not response["results"] or not isinstance(response["results"][0], dict):
            raise WatsonxResponseError("watsonx response contains no result object")
        result = response["results"][0]
        generated_text = result.get("generated_text")
        if not isinstance(generated_text, str):
            raise WatsonxResponseError("watsonx result is missing generated_text")

        def optional_integer(name: str) -> int | None:
            value = result.get(name)
            return value if isinstance(value, int) and value >= 0 else None

        request_record = {
            "provider": "watsonx.ai",
            "model_id": self.config.model_id,
            "project_id": self.config.project_id,
            "prompt": prompt,
            "parameters": parameters,
            "guardrails": guardrails,
        }
        stop_reason = result.get("stop_reason")
        return WatsonxInferenceReceipt(
            schema_version="finite-watsonx-receipt/v1",
            measurement_kind=self._measurement_kind,
            provider="watsonx.ai",
            model_id=self.config.model_id,
            request_digest=content_digest(request_record),
            response_digest=content_digest(response),
            latency_ms=latency_ms,
            input_tokens=optional_integer("input_token_count"),
            output_tokens=optional_integer("generated_token_count"),
            stop_reason=stop_reason if isinstance(stop_reason, str) else None,
            generated_text=generated_text,
        )
