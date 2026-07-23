import pytest

from agent_physics.examples import miami_eoc_graph
from agent_physics.run_store import Usage
from agent_physics.serialization import canonical_json, content_digest, normalize
from agent_physics.stormshift_runtime import stormshift_envelope


def test_direct_dataclass_normalization_preserves_registered_canonical_contracts() -> None:
    graph = miami_eoc_graph()

    assert content_digest(graph) == "cc5a33b97835993f118129825a2da5d64688e8a38491b7d22dc660776f9fd699"
    assert content_digest(stormshift_envelope()) == (
        "6be26480b40fceedcf0bb8c5496bc7214bdb151d81859be06bd1b9c6b94aeac5"
    )
    assert len(canonical_json(graph)) == 19_741


def test_canonical_json_rejects_non_finite_numbers() -> None:
    with pytest.raises(ValueError, match="Out of range float"):
        canonical_json({"unsafe": float("nan")})


def test_normalize_rejects_dataclass_types_instead_of_serializing_defaults() -> None:
    with pytest.raises(TypeError, match="dataclass instance"):
        normalize(Usage)
