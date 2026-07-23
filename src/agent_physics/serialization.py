"""Canonical serialization helpers for hashes and evidence manifests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any


def normalize(value: Any) -> Any:
    """Convert supported contracts into a stable JSON-compatible structure."""

    if is_dataclass(value):
        if isinstance(value, type):
            # Preserve dataclasses.asdict's instance-only boundary. Serializing
            # a class could otherwise mistake defaults for an actual contract.
            raise TypeError("normalize requires a dataclass instance, not a class")
        # ``dataclasses.asdict`` recursively copies the complete object tree and
        # then ``normalize`` used to traverse that copied tree a second time.
        # Normalize each field directly while retaining the same lexical key
        # order, avoiding duplicate work for large evidence records.
        return {
            field.name: normalize(getattr(value, field.name))
            for field in sorted(fields(value), key=lambda item: item.name)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): normalize(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [normalize(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"cannot canonically serialize {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        normalize(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def content_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
