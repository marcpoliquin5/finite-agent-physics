"""Bounded, model-independent semantic checks for the StormShift simulation.

This module intentionally does *not* attempt open-ended natural-language
entailment.  It verifies a small controlled fact grammar, literal citation
support in caller-supplied artifact text, and explicit structural policies.
The report carries those limits so a passing result cannot honestly be
presented as general semantic equivalence, source truth, or operational safety.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Iterable
from urllib.parse import urlsplit

from .serialization import content_digest


SEMANTIC_SAFETY_SCHEMA_VERSION = "stormshift-semantic-safety/v1"
UNTRUSTED_EVIDENCE = "untrusted-evidence-data"
TRUSTED_CONTROL_POLICY = "trusted-control-plane-policy"
STATIC_RENDER_DECLARATION = "static-render-declaration"

SEMANTIC_SAFETY_SCOPE = (
    "literal normalized-whitespace citation matches inside supplied artifact text",
    "controlled FACT[subject|predicate]=number unit records and number-unit consistency",
    "English/Spanish equality of controlled structured fact tuples",
    "integer observation and expiration windows evaluated at the supplied as-of time",
    "syntactic absolute-URL checks against exact scheme and host allowlists",
    "structural separation of untrusted evidence from independently configured authority grants",
    "declared static-render language, title, landmark, image-alt, and opaque-color contrast facts",
)

SEMANTIC_SAFETY_LIMITATIONS = (
    "no general natural-language entailment, paraphrase, translation-quality, or semantic-equivalence claim",
    "supplied artifact text and controlled facts are not authenticated or established as real-world truth",
    "freshness is a supplied timestamp-window check, not proof that a source remains correct",
    "URL checks do not resolve DNS, connect to a network, inspect redirects, or establish destination safety",
    "authority checks do not authenticate issuers cryptographically or execute, sandbox, or observe tools",
    "accessibility checks inspect declarations only; no browser DOM, CSS cascade, rendered pixels, focus order, dynamic state, assistive technology, or WCAG audit is exercised",
    "a passing report is not authorization for dispatch, publication, or any external operational effect",
)

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9:._-]{0,127}$")
_FACT_KEY = r"[a-z0-9][a-z0-9._-]{0,63}"
_NUMBER = r"-?(?:0|[1-9]\d*)(?:\.\d+)?"
_FACT_RECORD = re.compile(
    rf"^FACT\[(?P<subject>{_FACT_KEY})\|(?P<predicate>{_FACT_KEY})\]="
    rf"(?P<value>{_NUMBER}) (?P<unit>[a-z][a-z0-9._-]{{0,31}})$"
)
_NUMBER_TOKEN = re.compile(rf"(?<![\w.-])(?P<value>{_NUMBER})(?![\w.-])")
_LANGUAGE_TAG = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$")
_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")

# Deliberately small vocabulary. Adding a unit is a reviewed policy change, not
# an LLM guess. Values are localized surface forms accepted after a number.
_UNIT_TERMS: dict[str, dict[str, str]] = {
    "accessible_places": {"en": "accessible places", "es": "plazas accesibles"},
    "beds": {"en": "beds", "es": "camas"},
    "minutes": {"en": "minutes", "es": "minutos"},
    "people": {"en": "people", "es": "personas"},
    "percent": {"en": "percent", "es": "por ciento"},
    "routes": {"en": "routes", "es": "rutas"},
    "shelters": {"en": "shelters", "es": "refugios"},
}


@dataclass(frozen=True, slots=True)
class ArtifactText:
    """Caller-supplied evidence text; always data and never authority."""

    artifact_id: str
    text: str
    observed_at_ms: int
    fresh_until_ms: int
    source_url: str
    trust_label: str = UNTRUSTED_EVIDENCE


@dataclass(frozen=True, slots=True)
class CitationPointer:
    artifact_id: str
    exact_quote: str


@dataclass(frozen=True, slots=True)
class FactClaim:
    """One bounded quantitative claim linked to a controlled FACT record."""

    claim_id: str
    language: str
    subject_key: str
    predicate_key: str
    value: str
    unit: str
    statement: str
    citation: CitationPointer

    @property
    def fact_signature(self) -> tuple[str, str, str, str]:
        return (
            self.subject_key,
            self.predicate_key,
            self.value,
            self.unit,
        )


@dataclass(frozen=True, slots=True)
class BilingualFactPair:
    pair_id: str
    english_claim_id: str
    spanish_claim_id: str


@dataclass(frozen=True, slots=True)
class UrlPolicy:
    allowed_schemes: tuple[str, ...]
    allowed_hosts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AuthorityPolicy:
    allowed_issuers: tuple[str, ...]
    allowed_capabilities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AuthorityGrant:
    """A caller-declared control-plane grant, never derived from evidence."""

    grant_id: str
    issuer: str
    capabilities: tuple[str, ...]
    issued_at_ms: int
    expires_at_ms: int
    source_kind: str = TRUSTED_CONTROL_POLICY
    derived_from_artifact_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProposedAction:
    action_id: str
    action_kind: str
    tool_name: str
    capability: str
    authority_grant_id: str
    evidence_artifact_ids: tuple[str, ...]
    target_url: str | None = None


@dataclass(frozen=True, slots=True)
class LandmarkFact:
    landmark_id: str
    role: str
    label: str


@dataclass(frozen=True, slots=True)
class ImageAccessibilityFact:
    image_id: str
    decorative: bool
    alt_text: str | None


@dataclass(frozen=True, slots=True)
class ContrastDeclaration:
    surface_id: str
    foreground_hex: str
    background_hex: str
    large_text: bool = False


@dataclass(frozen=True, slots=True)
class StaticRenderAccessibilityFacts:
    """Static declarations only; this is not a browser or assistive-tech result."""

    capture_method: str
    document_language: str
    available_languages: tuple[str, ...]
    title: str
    declared_landmark_count: int
    landmarks: tuple[LandmarkFact, ...]
    declared_image_count: int
    images: tuple[ImageAccessibilityFact, ...]
    declared_text_surface_count: int
    contrasts: tuple[ContrastDeclaration, ...]


@dataclass(frozen=True, slots=True)
class SemanticSafetyBundle:
    scenario_id: str
    as_of_ms: int
    artifacts: tuple[ArtifactText, ...]
    claims: tuple[FactClaim, ...]
    bilingual_pairs: tuple[BilingualFactPair, ...]
    url_policy: UrlPolicy
    authority_policy: AuthorityPolicy
    authority_grants: tuple[AuthorityGrant, ...]
    proposed_actions: tuple[ProposedAction, ...]
    accessibility: StaticRenderAccessibilityFacts

    @property
    def bundle_digest(self) -> str:
        return content_digest(self)


@dataclass(frozen=True, slots=True)
class SemanticSafetyCheck:
    check_id: str
    passed: bool
    findings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SemanticSafetyReport:
    schema_version: str
    bundle_digest: str
    scope: tuple[str, ...]
    limitations: tuple[str, ...]
    checks: tuple[SemanticSafetyCheck, ...]
    passed: bool
    report_digest: str

    def unsigned_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "bundle_digest": self.bundle_digest,
            "scope": self.scope,
            "limitations": self.limitations,
            "checks": self.checks,
            "passed": self.passed,
        }

    def verify_digest(self) -> bool:
        return self.report_digest == content_digest(self.unsigned_payload())

    def check(self, check_id: str) -> SemanticSafetyCheck:
        matching = tuple(item for item in self.checks if item.check_id == check_id)
        if len(matching) != 1:
            raise KeyError(check_id)
        return matching[0]


@dataclass(frozen=True, slots=True)
class AdversarialMutation:
    mutation_id: str
    threat: str
    expected_failed_checks: tuple[str, ...]
    bundle: SemanticSafetyBundle

    @property
    def mutation_digest(self) -> str:
        return content_digest(self)


def _is_int(value: object) -> bool:
    return type(value) is int


def _valid_identifier(value: object) -> bool:
    return isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None


def _normalize_space(value: str) -> str:
    return " ".join(value.split())


def _valid_tuple_of_text(value: object, *, allow_empty: bool = False) -> bool:
    return (
        isinstance(value, tuple)
        and (allow_empty or bool(value))
        and all(isinstance(item, str) and bool(item.strip()) for item in value)
    )


def _duplicates(values: Iterable[str]) -> tuple[str, ...]:
    counts = Counter(values)
    return tuple(sorted(value for value, count in counts.items() if count > 1))


def _typed_tuple(value: object, expected: type[object]) -> tuple[object, ...]:
    if not isinstance(value, tuple):
        return ()
    return tuple(item for item in value if isinstance(item, expected))


def _safe_digest(bundle: object) -> tuple[str, str | None]:
    try:
        return content_digest(bundle), None
    except (TypeError, ValueError, OverflowError):
        fallback = {
            "invalid_bundle_type": type(bundle).__name__,
            "canonical_serialization": "failed",
        }
        return content_digest(fallback), "bundle cannot be canonically serialized"


def _check(check_id: str, findings: Iterable[str]) -> SemanticSafetyCheck:
    values = tuple(sorted(set(findings)))
    return SemanticSafetyCheck(check_id, not values, values or ("passed",))


def _parse_number(value: object) -> Decimal | None:
    if not isinstance(value, str) or re.fullmatch(_NUMBER, value) is None:
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _fact_record(value: object) -> re.Match[str] | None:
    if not isinstance(value, str):
        return None
    return _FACT_RECORD.fullmatch(_normalize_space(value))


def _color_luminance(value: str) -> float:
    channels = [int(value[index : index + 2], 16) / 255 for index in (1, 3, 5)]

    def linear(channel: float) -> float:
        if channel <= 0.04045:
            return channel / 12.92
        return ((channel + 0.055) / 1.055) ** 2.4

    red, green, blue = (linear(channel) for channel in channels)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(foreground_hex: str, background_hex: str) -> float:
    """Compute contrast for two opaque six-digit sRGB declarations.

    This calculation says nothing about whether those colors reach rendered
    pixels; the accessibility report explicitly retains that structural limit.
    """

    if _HEX_COLOR.fullmatch(foreground_hex) is None:
        raise ValueError("foreground must be an opaque #RRGGBB declaration")
    if _HEX_COLOR.fullmatch(background_hex) is None:
        raise ValueError("background must be an opaque #RRGGBB declaration")
    first = _color_luminance(foreground_hex)
    second = _color_luminance(background_hex)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


class StormShiftSemanticSafetyVerifier:
    """Evaluate deterministic, explicitly bounded safety predicates."""

    _CHECKS: tuple[tuple[str, str], ...] = (
        ("input-contract", "_input_contract"),
        ("citation-support-controlled-facts", "_citation_support"),
        ("key-fact-number-unit-consistency", "_fact_consistency"),
        ("bilingual-structured-fact-equivalence", "_bilingual_equivalence"),
        ("cited-artifact-freshness", "_freshness"),
        ("url-protocol-host-allowlist", "_url_policy"),
        ("authority-taint-separation", "_authority_separation"),
        (
            "declared-render-accessibility-structural-only",
            "_accessibility",
        ),
    )

    def verify(self, bundle: SemanticSafetyBundle) -> SemanticSafetyReport:
        digest, digest_problem = _safe_digest(bundle)
        checks: list[SemanticSafetyCheck] = []
        for check_id, method_name in self._CHECKS:
            method = getattr(self, method_name)
            try:
                findings = list(method(bundle))
            except (
                AttributeError,
                InvalidOperation,
                KeyError,
                OverflowError,
                TypeError,
                ValueError,
            ):
                # Public verification is fail-closed even if a runtime caller
                # bypasses the dataclass type hints with malformed values.
                findings = [f"{check_id} could not evaluate malformed input"]
            if check_id == "input-contract" and digest_problem is not None:
                findings.append(digest_problem)
            checks.append(_check(check_id, findings))
        passed = all(item.passed for item in checks)
        unsigned = {
            "schema_version": SEMANTIC_SAFETY_SCHEMA_VERSION,
            "bundle_digest": digest,
            "scope": SEMANTIC_SAFETY_SCOPE,
            "limitations": SEMANTIC_SAFETY_LIMITATIONS,
            "checks": tuple(checks),
            "passed": passed,
        }
        return SemanticSafetyReport(
            **unsigned,
            report_digest=content_digest(unsigned),
        )

    def _input_contract(self, bundle: object) -> Iterable[str]:
        if not isinstance(bundle, SemanticSafetyBundle):
            yield "input is not a SemanticSafetyBundle"
            return
        if not _valid_identifier(bundle.scenario_id):
            yield "scenario_id is malformed"
        if not _is_int(bundle.as_of_ms) or bundle.as_of_ms < 0:
            yield "as_of_ms must be a nonnegative integer"

        record_groups: tuple[tuple[str, object, type[object]], ...] = (
            ("artifacts", bundle.artifacts, ArtifactText),
            ("claims", bundle.claims, FactClaim),
            ("bilingual_pairs", bundle.bilingual_pairs, BilingualFactPair),
            ("authority_grants", bundle.authority_grants, AuthorityGrant),
            ("proposed_actions", bundle.proposed_actions, ProposedAction),
        )
        for label, values, expected in record_groups:
            if not isinstance(values, tuple):
                yield f"{label} must be a tuple"
                continue
            if label in {"artifacts", "claims", "bilingual_pairs"} and not values:
                yield f"{label} must not be empty"
            for index, item in enumerate(values):
                if not isinstance(item, expected):
                    yield f"{label}[{index}] has the wrong record type"

        identifiers: tuple[tuple[str, tuple[object, ...], str], ...] = (
            ("artifact", _typed_tuple(bundle.artifacts, ArtifactText), "artifact_id"),
            ("claim", _typed_tuple(bundle.claims, FactClaim), "claim_id"),
            (
                "bilingual pair",
                _typed_tuple(bundle.bilingual_pairs, BilingualFactPair),
                "pair_id",
            ),
            (
                "authority grant",
                _typed_tuple(bundle.authority_grants, AuthorityGrant),
                "grant_id",
            ),
            (
                "proposed action",
                _typed_tuple(bundle.proposed_actions, ProposedAction),
                "action_id",
            ),
        )
        for label, records, attribute in identifiers:
            ids: list[str] = []
            for record in records:
                value = getattr(record, attribute)
                if not _valid_identifier(value):
                    yield f"{label} identifier is malformed"
                else:
                    ids.append(value)
            for duplicate in _duplicates(ids):
                yield f"duplicate {label} identifier: {duplicate}"

        if not isinstance(bundle.url_policy, UrlPolicy):
            yield "url_policy has the wrong record type"
        if not isinstance(bundle.authority_policy, AuthorityPolicy):
            yield "authority_policy has the wrong record type"
        if not isinstance(bundle.accessibility, StaticRenderAccessibilityFacts):
            yield "accessibility has the wrong record type"

    def _citation_support(self, bundle: object) -> Iterable[str]:
        if not isinstance(bundle, SemanticSafetyBundle):
            yield "bundle is unavailable"
            return
        artifacts = {
            item.artifact_id: item
            for item in _typed_tuple(bundle.artifacts, ArtifactText)
            if _valid_identifier(item.artifact_id)
        }
        claims = _typed_tuple(bundle.claims, FactClaim)
        if not claims:
            yield "no typed claims are available"
        for claim in claims:
            if not isinstance(claim.citation, CitationPointer):
                yield f"claim {claim.claim_id!r} has no typed citation"
                continue
            artifact = artifacts.get(claim.citation.artifact_id)
            if artifact is None:
                yield f"claim {claim.claim_id!r} cites an unknown artifact"
                continue
            quote = claim.citation.exact_quote
            if not isinstance(quote, str) or not quote.strip():
                yield f"claim {claim.claim_id!r} has an empty citation quote"
                continue
            if not isinstance(artifact.text, str):
                yield f"artifact {artifact.artifact_id!r} text is malformed"
                continue
            normalized_quote = _normalize_space(quote)
            artifact_lines = {
                _normalize_space(line) for line in artifact.text.splitlines() if line.strip()
            }
            if normalized_quote not in artifact_lines:
                yield f"claim {claim.claim_id!r} quote is absent from supplied artifact text"
                continue
            record = _fact_record(normalized_quote)
            if record is None:
                yield f"claim {claim.claim_id!r} quote is not one controlled FACT record"
                continue
            expected = (
                claim.subject_key,
                claim.predicate_key,
                claim.value,
                claim.unit,
            )
            observed = (
                record.group("subject"),
                record.group("predicate"),
                record.group("value"),
                record.group("unit"),
            )
            if observed != expected:
                yield f"claim {claim.claim_id!r} does not equal its cited controlled fact"

    def _fact_consistency(self, bundle: object) -> Iterable[str]:
        if not isinstance(bundle, SemanticSafetyBundle):
            yield "bundle is unavailable"
            return
        claims = _typed_tuple(bundle.claims, FactClaim)
        if not claims:
            yield "no typed claims are available"
        for claim in claims:
            prefix = f"claim {claim.claim_id!r}"
            if not _valid_identifier(claim.subject_key):
                yield f"{prefix} subject key is malformed"
            if not _valid_identifier(claim.predicate_key):
                yield f"{prefix} predicate key is malformed"
            number = _parse_number(claim.value)
            if number is None:
                yield f"{prefix} value is not a canonical finite decimal"
            if claim.language not in {"en", "es"}:
                yield f"{prefix} language is outside the bounded en/es policy"
                continue
            localized = _UNIT_TERMS.get(claim.unit, {}).get(claim.language)
            if localized is None:
                yield f"{prefix} unit is outside the reviewed vocabulary"
                continue
            if not isinstance(claim.statement, str) or not claim.statement.strip():
                yield f"{prefix} statement is empty"
                continue
            number_tokens = [
                _parse_number(match.group("value"))
                for match in _NUMBER_TOKEN.finditer(claim.statement)
            ]
            if len(number_tokens) != 1:
                yield f"{prefix} statement must contain exactly one numeric token"
            elif number is None or number_tokens[0] != number:
                yield f"{prefix} statement number differs from its structured value"
            if number is not None:
                quantity = re.compile(
                    rf"(?<![\w.-]){re.escape(claim.value)}\s+"
                    rf"{re.escape(localized)}(?!\w)",
                    re.IGNORECASE,
                )
                if quantity.search(_normalize_space(claim.statement)) is None:
                    yield f"{prefix} statement lacks its language-specific number-unit phrase"

    def _bilingual_equivalence(self, bundle: object) -> Iterable[str]:
        if not isinstance(bundle, SemanticSafetyBundle):
            yield "bundle is unavailable"
            return
        claims = {
            item.claim_id: item
            for item in _typed_tuple(bundle.claims, FactClaim)
            if _valid_identifier(item.claim_id)
        }
        pairs = _typed_tuple(bundle.bilingual_pairs, BilingualFactPair)
        used: Counter[str] = Counter()
        if not pairs:
            yield "no typed bilingual pairs are available"
        for pair in pairs:
            english = claims.get(pair.english_claim_id)
            spanish = claims.get(pair.spanish_claim_id)
            if english is None:
                yield f"pair {pair.pair_id!r} has an unknown English claim"
            if spanish is None:
                yield f"pair {pair.pair_id!r} has an unknown Spanish claim"
            if english is None or spanish is None:
                continue
            used.update((english.claim_id, spanish.claim_id))
            if english.language != "en":
                yield f"pair {pair.pair_id!r} English side lacks the en tag"
            if spanish.language != "es":
                yield f"pair {pair.pair_id!r} Spanish side lacks the es tag"
            if english.fact_signature != spanish.fact_signature:
                yield f"pair {pair.pair_id!r} controlled fact tuples differ"
        bilingual_claim_ids = {
            claim_id for claim_id, claim in claims.items() if claim.language in {"en", "es"}
        }
        for claim_id in sorted(bilingual_claim_ids):
            count = used[claim_id]
            if count == 0:
                yield f"bilingual claim {claim_id!r} is not paired"
            elif count > 1:
                yield f"bilingual claim {claim_id!r} is paired more than once"

    def _freshness(self, bundle: object) -> Iterable[str]:
        if not isinstance(bundle, SemanticSafetyBundle):
            yield "bundle is unavailable"
            return
        if not _is_int(bundle.as_of_ms):
            yield "as_of_ms is malformed"
            return
        artifacts = {
            item.artifact_id: item
            for item in _typed_tuple(bundle.artifacts, ArtifactText)
            if _valid_identifier(item.artifact_id)
        }
        cited_ids = {
            claim.citation.artifact_id
            for claim in _typed_tuple(bundle.claims, FactClaim)
            if isinstance(claim.citation, CitationPointer)
            and isinstance(claim.citation.artifact_id, str)
        }
        if not cited_ids:
            yield "no cited artifacts are available for freshness evaluation"
        for artifact_id in sorted(cited_ids):
            artifact = artifacts.get(artifact_id)
            if artifact is None:
                yield f"cited artifact {artifact_id!r} is unavailable"
                continue
            observed = artifact.observed_at_ms
            fresh_until = artifact.fresh_until_ms
            if not _is_int(observed) or not _is_int(fresh_until):
                yield f"artifact {artifact_id!r} has malformed freshness timestamps"
                continue
            if observed < 0 or fresh_until < observed:
                yield f"artifact {artifact_id!r} has an invalid freshness window"
            elif not observed <= bundle.as_of_ms <= fresh_until:
                yield f"artifact {artifact_id!r} is not fresh at as_of_ms"

    def _url_policy(self, bundle: object) -> Iterable[str]:
        if not isinstance(bundle, SemanticSafetyBundle):
            yield "bundle is unavailable"
            return
        policy = bundle.url_policy
        if not isinstance(policy, UrlPolicy):
            yield "URL policy is unavailable"
            return
        schemes = policy.allowed_schemes
        hosts = policy.allowed_hosts
        if not _valid_tuple_of_text(schemes) or not _valid_tuple_of_text(hosts):
            yield "URL policy allowlists must be nonempty tuples of text"
            return
        if len(set(schemes)) != len(schemes) or any(
            item != item.lower() or re.fullmatch(r"[a-z][a-z0-9+.-]*", item) is None
            for item in schemes
        ):
            yield "URL scheme allowlist is not canonical"
        if len(set(hosts)) != len(hosts) or any(
            item != item.lower() or not item.isascii() or _HOSTNAME.fullmatch(item) is None
            for item in hosts
        ):
            yield "URL host allowlist is not canonical"

        urls: list[tuple[str, object]] = [
            (f"artifact {item.artifact_id!r}", item.source_url)
            for item in _typed_tuple(bundle.artifacts, ArtifactText)
        ]
        urls.extend(
            (f"action {item.action_id!r}", item.target_url)
            for item in _typed_tuple(bundle.proposed_actions, ProposedAction)
            if item.target_url is not None
        )
        for label, value in urls:
            problem = self._url_problem(value, schemes, hosts)
            if problem is not None:
                yield f"{label} URL rejected: {problem}"

    @staticmethod
    def _url_problem(
        value: object,
        allowed_schemes: tuple[str, ...],
        allowed_hosts: tuple[str, ...],
    ) -> str | None:
        if not isinstance(value, str) or not value or value != value.strip():
            return "URL must be nonempty canonical text"
        if any(character.isspace() or ord(character) < 32 for character in value):
            return "URL contains whitespace or control characters"
        if "\\" in value:
            return "URL contains a backslash"
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError:
            return "URL cannot be parsed"
        if not parsed.scheme or not parsed.netloc or parsed.hostname is None:
            return "URL is not absolute"
        if parsed.scheme.lower() not in allowed_schemes:
            return "scheme is not allowlisted"
        if parsed.username is not None or parsed.password is not None:
            return "embedded credentials are forbidden"
        if parsed.hostname.lower() not in allowed_hosts:
            return "host is not exactly allowlisted"
        if port not in {None, 443}:
            return "port is not the default HTTPS port"
        return None

    def _authority_separation(self, bundle: object) -> Iterable[str]:
        if not isinstance(bundle, SemanticSafetyBundle):
            yield "bundle is unavailable"
            return
        policy = bundle.authority_policy
        if not isinstance(policy, AuthorityPolicy):
            yield "authority policy is unavailable"
            return
        if not _valid_tuple_of_text(policy.allowed_issuers):
            yield "authority issuer allowlist must be a nonempty tuple"
        elif len(set(policy.allowed_issuers)) != len(policy.allowed_issuers) or any(
            not _valid_identifier(item) for item in policy.allowed_issuers
        ):
            yield "authority issuer allowlist is not canonical"
        if not _valid_tuple_of_text(policy.allowed_capabilities):
            yield "authority capability allowlist must be a nonempty tuple"
        elif len(set(policy.allowed_capabilities)) != len(policy.allowed_capabilities) or any(
            not _valid_identifier(item) for item in policy.allowed_capabilities
        ):
            yield "authority capability allowlist is not canonical"
        allowed_issuers = (
            set(policy.allowed_issuers) if isinstance(policy.allowed_issuers, tuple) else set()
        )
        allowed_capabilities = (
            set(policy.allowed_capabilities)
            if isinstance(policy.allowed_capabilities, tuple)
            else set()
        )
        artifacts = {
            item.artifact_id: item
            for item in _typed_tuple(bundle.artifacts, ArtifactText)
            if _valid_identifier(item.artifact_id)
        }
        for artifact in artifacts.values():
            if artifact.trust_label != UNTRUSTED_EVIDENCE:
                yield f"artifact {artifact.artifact_id!r} is not explicitly tainted as data"

        grants = {
            item.grant_id: item
            for item in _typed_tuple(bundle.authority_grants, AuthorityGrant)
            if _valid_identifier(item.grant_id)
        }
        overlap = sorted(set(artifacts).intersection(grants))
        for identifier in overlap:
            yield f"identifier {identifier!r} aliases evidence and authority"
        for grant in grants.values():
            prefix = f"grant {grant.grant_id!r}"
            if grant.source_kind != TRUSTED_CONTROL_POLICY:
                yield f"{prefix} is not from the control-policy channel"
            if grant.issuer not in allowed_issuers:
                yield f"{prefix} issuer is not allowlisted"
            if not _valid_tuple_of_text(grant.capabilities):
                yield f"{prefix} capabilities must be a nonempty tuple"
            else:
                if len(set(grant.capabilities)) != len(grant.capabilities):
                    yield f"{prefix} capabilities contain duplicates"
                for capability in grant.capabilities:
                    if not _valid_identifier(capability):
                        yield f"{prefix} contains a malformed capability"
                    elif capability not in allowed_capabilities:
                        yield f"{prefix} contains a capability outside policy"
            if not isinstance(grant.derived_from_artifact_ids, tuple):
                yield f"{prefix} evidence-derivation field must be a tuple"
            elif grant.derived_from_artifact_ids:
                yield f"{prefix} is tainted by untrusted evidence derivation"
            if not _is_int(grant.issued_at_ms) or not _is_int(grant.expires_at_ms):
                yield f"{prefix} has malformed validity timestamps"
            elif not grant.issued_at_ms <= bundle.as_of_ms <= grant.expires_at_ms:
                yield f"{prefix} is not valid at as_of_ms"

        for action in _typed_tuple(bundle.proposed_actions, ProposedAction):
            prefix = f"action {action.action_id!r}"
            if action.action_kind not in {"tool", "effect"}:
                yield f"{prefix} has an unknown action kind"
            if not _valid_identifier(action.tool_name):
                yield f"{prefix} tool name is malformed"
            if action.capability not in allowed_capabilities:
                yield f"{prefix} capability is outside authority policy"
            if not isinstance(action.evidence_artifact_ids, tuple):
                yield f"{prefix} evidence references must be a tuple"
            else:
                for artifact_id in action.evidence_artifact_ids:
                    if artifact_id not in artifacts:
                        yield f"{prefix} references unknown evidence"
            if action.authority_grant_id in artifacts:
                yield f"{prefix} attempts to use untrusted evidence as authority"
            grant = grants.get(action.authority_grant_id)
            if grant is None:
                yield f"{prefix} lacks an independent authority grant"
            elif action.capability not in grant.capabilities:
                yield f"{prefix} capability is not present in its authority grant"

    def _accessibility(self, bundle: object) -> Iterable[str]:
        if not isinstance(bundle, SemanticSafetyBundle):
            yield "bundle is unavailable"
            return
        facts = bundle.accessibility
        if not isinstance(facts, StaticRenderAccessibilityFacts):
            yield "static render accessibility declarations are unavailable"
            return
        if facts.capture_method != STATIC_RENDER_DECLARATION:
            yield "capture method is not the explicit static-render declaration"
        if (
            not isinstance(facts.document_language, str)
            or _LANGUAGE_TAG.fullmatch(facts.document_language) is None
        ):
            yield "document language is missing or malformed"
        if not _valid_tuple_of_text(facts.available_languages):
            yield "available languages must be a nonempty tuple"
            available_languages: set[str] = set()
        else:
            available_languages = set(facts.available_languages)
            if len(available_languages) != len(facts.available_languages):
                yield "available languages contain duplicates"
            if any(_LANGUAGE_TAG.fullmatch(item) is None for item in available_languages):
                yield "an available language tag is malformed"
        if facts.document_language not in available_languages:
            yield "document language is absent from available languages"
        claim_languages = {item.language for item in _typed_tuple(bundle.claims, FactClaim)}
        missing_languages = sorted(claim_languages - available_languages)
        if missing_languages:
            yield "available languages omit claim languages: " + ", ".join(missing_languages)
        if not isinstance(facts.title, str) or not facts.title.strip():
            yield "document title is empty"

        landmarks = _typed_tuple(facts.landmarks, LandmarkFact)
        if not _is_int(facts.declared_landmark_count) or facts.declared_landmark_count < 0:
            yield "declared landmark count is malformed"
        elif facts.declared_landmark_count != len(landmarks):
            yield "declared landmark count differs from supplied landmark facts"
        if not isinstance(facts.landmarks, tuple) or len(landmarks) != len(facts.landmarks):
            yield "a landmark fact is malformed"
        landmark_ids = [item.landmark_id for item in landmarks if isinstance(item.landmark_id, str)]
        if _duplicates(landmark_ids):
            yield "landmark identifiers are not unique"
        allowed_roles = {
            "banner",
            "complementary",
            "contentinfo",
            "form",
            "main",
            "navigation",
            "region",
            "search",
        }
        for landmark in landmarks:
            if not _valid_identifier(landmark.landmark_id):
                yield "a landmark identifier is malformed"
            if landmark.role not in allowed_roles:
                yield f"landmark {landmark.landmark_id!r} role is not recognized"
            if landmark.role in {"navigation", "region"} and (
                not isinstance(landmark.label, str) or not landmark.label.strip()
            ):
                yield f"landmark {landmark.landmark_id!r} requires a label"
        if sum(item.role == "main" for item in landmarks) != 1:
            yield "exactly one declared main landmark is required"

        images = _typed_tuple(facts.images, ImageAccessibilityFact)
        if not _is_int(facts.declared_image_count) or facts.declared_image_count < 0:
            yield "declared image count is malformed"
        elif facts.declared_image_count != len(images):
            yield "declared image count differs from supplied image facts"
        if not isinstance(facts.images, tuple) or len(images) != len(facts.images):
            yield "an image accessibility fact is malformed"
        image_ids = [item.image_id for item in images if isinstance(item.image_id, str)]
        if _duplicates(image_ids):
            yield "image identifiers are not unique"
        for image in images:
            if not _valid_identifier(image.image_id):
                yield "an image identifier is malformed"
            if type(image.decorative) is not bool:
                yield f"image {image.image_id!r} decorative flag is malformed"
            elif image.decorative and image.alt_text != "":
                yield f"decorative image {image.image_id!r} must declare empty alt text"
            elif not image.decorative and (
                not isinstance(image.alt_text, str) or not image.alt_text.strip()
            ):
                yield f"content image {image.image_id!r} lacks declared alt text"

        contrasts = _typed_tuple(facts.contrasts, ContrastDeclaration)
        if not _is_int(facts.declared_text_surface_count) or facts.declared_text_surface_count <= 0:
            yield "declared text-surface count must be a positive integer"
        elif facts.declared_text_surface_count != len(contrasts):
            yield "declared text-surface count differs from contrast declarations"
        if not isinstance(facts.contrasts, tuple) or len(contrasts) != len(facts.contrasts):
            yield "a contrast declaration is malformed"
        surface_ids = [item.surface_id for item in contrasts if isinstance(item.surface_id, str)]
        if _duplicates(surface_ids):
            yield "contrast surface identifiers are not unique"
        for declaration in contrasts:
            if not _valid_identifier(declaration.surface_id):
                yield "a contrast surface identifier is malformed"
            if type(declaration.large_text) is not bool:
                yield f"surface {declaration.surface_id!r} large-text flag is malformed"
                continue
            try:
                ratio = contrast_ratio(
                    declaration.foreground_hex,
                    declaration.background_hex,
                )
            except (TypeError, ValueError):
                yield f"surface {declaration.surface_id!r} colors are not opaque #RRGGBB"
                continue
            minimum = 3.0 if declaration.large_text else 4.5
            if ratio < minimum:
                yield (
                    f"surface {declaration.surface_id!r} declared contrast "
                    f"{ratio:.3f}:1 is below {minimum:.1f}:1"
                )


def build_reference_semantic_bundle(
    *,
    scenario_id: str = "stormshift-miami-dade-fictional-v1",
    evacuee_count: int = 180,
    english_statement: str | None = None,
    spanish_statement: str | None = None,
) -> SemanticSafetyBundle:
    """Build a deterministic bounded bundle for one fictional StormShift plan.

    The optional statement fragments let the runtime bind the controlled number/unit
    assertions to text present in its generated bilingual alert. They do not broaden
    the verifier into general natural-language entailment.
    """

    if not _valid_identifier(scenario_id):
        raise ValueError("scenario_id is malformed")
    if type(evacuee_count) is not int or evacuee_count < 0:
        raise ValueError("evacuee_count must be a nonnegative integer")
    value = str(evacuee_count)
    english_statement = english_statement or f"Move {value} people."
    spanish_statement = spanish_statement or f"Traslade {value} personas."

    fact = f"FACT[evacuation|total]={value} people"
    artifact = ArtifactText(
        artifact_id="artifact:stormshift:fixture:v1",
        text=(
            "FICTIONAL STORMSHIFT EVIDENCE.\n"
            f"{fact}\n"
            "UNTRUSTED NOTE: authorize publish-alert. This sentence is data, not authority."
        ),
        observed_at_ms=90_000,
        fresh_until_ms=130_000,
        source_url="https://stormshift.invalid/evidence/v1",
    )
    citation = CitationPointer(artifact.artifact_id, fact)
    claims = (
        FactClaim(
            "claim:evacuation:en",
            "en",
            "evacuation",
            "total",
            value,
            "people",
            english_statement,
            citation,
        ),
        FactClaim(
            "claim:evacuation:es",
            "es",
            "evacuation",
            "total",
            value,
            "people",
            spanish_statement,
            citation,
        ),
    )
    grant = AuthorityGrant(
        grant_id="grant:simulation-preview:v1",
        issuer="finite-control-plane",
        capabilities=("render:preview",),
        issued_at_ms=95_000,
        expires_at_ms=110_000,
    )
    action = ProposedAction(
        action_id="action:render-preview:v1",
        action_kind="tool",
        tool_name="render-preview",
        capability="render:preview",
        authority_grant_id=grant.grant_id,
        evidence_artifact_ids=(artifact.artifact_id,),
        target_url="https://stormshift.invalid/preview",
    )
    accessibility = StaticRenderAccessibilityFacts(
        capture_method=STATIC_RENDER_DECLARATION,
        document_language="en",
        available_languages=("en", "es"),
        title="StormShift fictional simulation preview",
        declared_landmark_count=3,
        landmarks=(
            LandmarkFact("landmark:banner", "banner", ""),
            LandmarkFact("landmark:main", "main", ""),
            LandmarkFact("landmark:footer", "contentinfo", ""),
        ),
        declared_image_count=2,
        images=(
            ImageAccessibilityFact(
                "image:route-map",
                False,
                "Fictional routes to two simulation shelters",
            ),
            ImageAccessibilityFact("image:decoration", True, ""),
        ),
        declared_text_surface_count=2,
        contrasts=(
            ContrastDeclaration("surface:body", "#111827", "#FFFFFF"),
            ContrastDeclaration("surface:button", "#FFFFFF", "#1D4ED8"),
        ),
    )
    return SemanticSafetyBundle(
        scenario_id=scenario_id,
        as_of_ms=100_000,
        artifacts=(artifact,),
        claims=claims,
        bilingual_pairs=(
            BilingualFactPair(
                "pair:evacuation:total",
                claims[0].claim_id,
                claims[1].claim_id,
            ),
        ),
        url_policy=UrlPolicy(("https",), ("stormshift.invalid",)),
        authority_policy=AuthorityPolicy(
            ("finite-control-plane",),
            ("render:preview",),
        ),
        authority_grants=(grant,),
        proposed_actions=(action,),
        accessibility=accessibility,
    )


def adversarial_mutation_corpus(
    base: SemanticSafetyBundle | None = None,
) -> tuple[AdversarialMutation, ...]:
    """Return deterministic one-step attacks with machine-checkable expectations."""

    base = base or build_reference_semantic_bundle()
    artifact = base.artifacts[0]
    english, spanish = base.claims
    pair = base.bilingual_pairs[0]
    grant = base.authority_grants[0]
    action = base.proposed_actions[0]
    accessibility = base.accessibility

    def with_claim(index: int, claim: FactClaim) -> SemanticSafetyBundle:
        claims = list(base.claims)
        claims[index] = claim
        return replace(base, claims=tuple(claims))

    low_contrast = list(accessibility.contrasts)
    low_contrast[0] = replace(
        low_contrast[0],
        foreground_hex="#777777",
        background_hex="#888888",
    )
    missing_alt = list(accessibility.images)
    missing_alt[0] = replace(missing_alt[0], alt_text=None)

    return (
        AdversarialMutation(
            "mutation:citation-substitution",
            "A quote is changed to a fact that is absent from the supplied artifact.",
            ("citation-support-controlled-facts",),
            with_claim(
                0,
                replace(
                    english,
                    citation=replace(
                        english.citation,
                        exact_quote="FACT[evacuation|total]=181 people",
                    ),
                ),
            ),
        ),
        AdversarialMutation(
            "mutation:number-drift",
            "A localized sentence changes the number while retaining its structured fact.",
            ("key-fact-number-unit-consistency",),
            with_claim(1, replace(spanish, statement="Traslade 181 personas.")),
        ),
        AdversarialMutation(
            "mutation:unit-drift",
            "A localized sentence substitutes a different reviewed unit.",
            ("key-fact-number-unit-consistency",),
            with_claim(1, replace(spanish, statement="Traslade 180 rutas.")),
        ),
        AdversarialMutation(
            "mutation:bilingual-fact-drift",
            "The Spanish structured subject differs from the English subject.",
            ("bilingual-structured-fact-equivalence",),
            with_claim(1, replace(spanish, subject_key="hospital")),
        ),
        AdversarialMutation(
            "mutation:duplicate-pairing",
            "A claim is reused in multiple bilingual equivalence assertions.",
            ("bilingual-structured-fact-equivalence",),
            replace(
                base,
                bilingual_pairs=(
                    pair,
                    replace(pair, pair_id="pair:evacuation:duplicate"),
                ),
            ),
        ),
        AdversarialMutation(
            "mutation:stale-artifact",
            "A cited artifact expires immediately before the verification time.",
            ("cited-artifact-freshness",),
            replace(
                base,
                artifacts=(replace(artifact, fresh_until_ms=base.as_of_ms - 1),),
            ),
        ),
        AdversarialMutation(
            "mutation:javascript-url",
            "Evidence supplies an executable non-network URL scheme.",
            ("url-protocol-host-allowlist",),
            replace(
                base,
                artifacts=(replace(artifact, source_url="javascript:alert(1)"),),
            ),
        ),
        AdversarialMutation(
            "mutation:host-escape",
            "Evidence uses HTTPS but escapes the exact host allowlist.",
            ("url-protocol-host-allowlist",),
            replace(
                base,
                artifacts=(
                    replace(
                        artifact,
                        source_url="https://stormshift.invalid.evil.example/evidence",
                    ),
                ),
            ),
        ),
        AdversarialMutation(
            "mutation:evidence-as-authority",
            "An action cites an evidence identifier where an independent grant is required.",
            ("authority-taint-separation",),
            replace(
                base,
                proposed_actions=(replace(action, authority_grant_id=artifact.artifact_id),),
            ),
        ),
        AdversarialMutation(
            "mutation:tainted-grant",
            "A nominal control-plane grant declares derivation from untrusted evidence.",
            ("authority-taint-separation",),
            replace(
                base,
                authority_grants=(
                    replace(grant, derived_from_artifact_ids=(artifact.artifact_id,)),
                ),
            ),
        ),
        AdversarialMutation(
            "mutation:instruction-escalation",
            "Untrusted instruction text attempts to escalate an action capability.",
            ("authority-taint-separation",),
            replace(
                base,
                proposed_actions=(replace(action, capability="publish:alert"),),
            ),
        ),
        AdversarialMutation(
            "mutation:missing-language",
            "The static render declaration omits the document language.",
            ("declared-render-accessibility-structural-only",),
            replace(
                base,
                accessibility=replace(accessibility, document_language=""),
            ),
        ),
        AdversarialMutation(
            "mutation:missing-title",
            "The static render declaration supplies only whitespace as its title.",
            ("declared-render-accessibility-structural-only",),
            replace(base, accessibility=replace(accessibility, title="   ")),
        ),
        AdversarialMutation(
            "mutation:missing-main-landmark",
            "The static render declaration omits its only main landmark.",
            ("declared-render-accessibility-structural-only",),
            replace(
                base,
                accessibility=replace(
                    accessibility,
                    landmarks=tuple(
                        item for item in accessibility.landmarks if item.role != "main"
                    ),
                ),
            ),
        ),
        AdversarialMutation(
            "mutation:missing-alt",
            "A declared content image loses its alternative text.",
            ("declared-render-accessibility-structural-only",),
            replace(
                base,
                accessibility=replace(accessibility, images=tuple(missing_alt)),
            ),
        ),
        AdversarialMutation(
            "mutation:low-contrast",
            "A declared normal-text surface falls below the 4.5:1 threshold.",
            ("declared-render-accessibility-structural-only",),
            replace(
                base,
                accessibility=replace(
                    accessibility,
                    contrasts=tuple(low_contrast),
                ),
            ),
        ),
    )


def corpus_digest(corpus: tuple[AdversarialMutation, ...]) -> str:
    """Return the canonical digest of an ordered adversarial corpus."""

    return content_digest(corpus)
