"""Validate a generated CycloneDX 1.6 JSON SBOM against the pinned strict schema."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cyclonedx.schema import SchemaVersion
from cyclonedx.validation.json import JsonStrictValidator


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sbom", type=Path)
    return parser.parse_args()


def main() -> int:
    path = parse_args().sbom
    try:
        document = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"cannot read SBOM: {exc}", file=sys.stderr)
        return 1
    error = JsonStrictValidator(SchemaVersion.V1_6).validate_str(document)
    if error is not None:
        print(f"CycloneDX 1.6 schema validation failed: {error}", file=sys.stderr)
        return 1
    print(f"CycloneDX 1.6 schema validation passed: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
