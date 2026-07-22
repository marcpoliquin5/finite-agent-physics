from __future__ import annotations

from dataclasses import replace
from decimal import InvalidOperation

import pytest

import agent_physics.semantic_safety as semantic_safety_module
from agent_physics.semantic_safety import (
    STATIC_RENDER_DECLARATION,
    TRUSTED_CONTROL_POLICY,
    UNTRUSTED_EVIDENCE,
    ArtifactText,
    AuthorityGrant,
    AuthorityPolicy,
    BilingualFactPair,
    CitationPointer,
    ContrastDeclaration,
    FactClaim,
    ImageAccessibilityFact,
    LandmarkFact,
    ProposedAction,
    SemanticSafetyBundle,
    StaticRenderAccessibilityFacts,
    StormShiftSemanticSafetyVerifier,
    UrlPolicy,
    build_reference_semantic_bundle,
    contrast_ratio,
)


CHECK_INPUT = "input-contract"
CHECK_CITATION = "citation-support-controlled-facts"
CHECK_FACT = "key-fact-number-unit-consistency"
CHECK_BILINGUAL = "bilingual-structured-fact-equivalence"
CHECK_FRESHNESS = "cited-artifact-freshness"
CHECK_URL = "url-protocol-host-allowlist"
CHECK_AUTHORITY = "authority-taint-separation"
CHECK_ACCESSIBILITY = "declared-render-accessibility-structural-only"


def _findings(bundle: object, check_id: str) -> tuple[str, ...]:
    report = StormShiftSemanticSafetyVerifier().verify(bundle)  # type: ignore[arg-type]
    assert report.verify_digest()
    return report.check(check_id).findings


def _contains(findings: tuple[str, ...], fragment: str) -> bool:
    return any(fragment in finding for finding in findings)


def test_non_bundle_and_internal_checker_errors_fail_closed_without_serialization_crash() -> None:
    verifier = StormShiftSemanticSafetyVerifier()
    report = verifier.verify(object())  # type: ignore[arg-type]

    assert not report.passed
    assert report.verify_digest()
    assert _contains(report.check(CHECK_INPUT).findings, "not a SemanticSafetyBundle")
    assert _contains(report.check(CHECK_INPUT).findings, "cannot be canonically serialized")
    for check_id in (
        CHECK_CITATION,
        CHECK_FACT,
        CHECK_BILINGUAL,
        CHECK_FRESHNESS,
        CHECK_URL,
        CHECK_AUTHORITY,
        CHECK_ACCESSIBILITY,
    ):
        assert _contains(report.check(check_id).findings, "bundle is unavailable")

    class ExplodingVerifier(StormShiftSemanticSafetyVerifier):
        def _citation_support(self, _bundle: object):
            raise ValueError("internal parser detail")

    exploded = ExplodingVerifier().verify(build_reference_semantic_bundle())
    citation = exploded.check(CHECK_CITATION)
    assert not citation.passed
    assert citation.findings == (
        "citation-support-controlled-facts could not evaluate malformed input",
    )
    assert "internal parser detail" not in repr(exploded)


def test_input_contract_rejects_wrong_shapes_empty_required_groups_and_policy_types() -> None:
    base = build_reference_semantic_bundle()
    malformed = replace(
        base,
        scenario_id="INVALID SCENARIO",
        as_of_ms=-1,
        artifacts=[base.artifacts[0]],  # type: ignore[arg-type]
        claims=(),
        bilingual_pairs=(object(),),  # type: ignore[arg-type]
        authority_grants=(object(),),  # type: ignore[arg-type]
        proposed_actions=(object(),),  # type: ignore[arg-type]
        url_policy=object(),  # type: ignore[arg-type]
        authority_policy=object(),  # type: ignore[arg-type]
        accessibility=object(),  # type: ignore[arg-type]
    )

    findings = _findings(malformed, CHECK_INPUT)
    for fragment in (
        "scenario_id is malformed",
        "as_of_ms must be a nonnegative integer",
        "artifacts must be a tuple",
        "claims must not be empty",
        "bilingual_pairs[0] has the wrong record type",
        "authority_grants[0] has the wrong record type",
        "proposed_actions[0] has the wrong record type",
        "url_policy has the wrong record type",
        "authority_policy has the wrong record type",
        "accessibility has the wrong record type",
    ):
        assert _contains(findings, fragment)

    wrong_records = replace(
        base,
        artifacts=(object(),),  # type: ignore[arg-type]
        claims=(object(),),  # type: ignore[arg-type]
        bilingual_pairs=(),
    )
    wrong_findings = _findings(wrong_records, CHECK_INPUT)
    assert _contains(wrong_findings, "artifacts[0] has the wrong record type")
    assert _contains(wrong_findings, "claims[0] has the wrong record type")
    assert _contains(wrong_findings, "bilingual_pairs must not be empty")


def test_input_contract_rejects_malformed_and_duplicate_identifiers_for_every_record_group() -> None:
    base = build_reference_semantic_bundle()
    artifact = base.artifacts[0]
    english, spanish = base.claims
    pair = base.bilingual_pairs[0]
    grant = base.authority_grants[0]
    action = base.proposed_actions[0]

    malformed = replace(
        base,
        artifacts=(replace(artifact, artifact_id="BAD ARTIFACT"),),
        claims=(replace(english, claim_id="BAD CLAIM"), spanish),
        bilingual_pairs=(replace(pair, pair_id="BAD PAIR"),),
        authority_grants=(replace(grant, grant_id="BAD GRANT"),),
        proposed_actions=(replace(action, action_id="BAD ACTION"),),
    )
    malformed_findings = _findings(malformed, CHECK_INPUT)
    for label in (
        "artifact identifier",
        "claim identifier",
        "bilingual pair identifier",
        "authority grant identifier",
        "proposed action identifier",
    ):
        assert _contains(malformed_findings, f"{label} is malformed")

    duplicates = replace(
        base,
        artifacts=(artifact, replace(artifact)),
        claims=(english, spanish, replace(english)),
        bilingual_pairs=(pair, replace(pair)),
        authority_grants=(grant, replace(grant)),
        proposed_actions=(action, replace(action)),
    )
    duplicate_findings = _findings(duplicates, CHECK_INPUT)
    for label in (
        "duplicate artifact identifier",
        "duplicate claim identifier",
        "duplicate bilingual pair identifier",
        "duplicate authority grant identifier",
        "duplicate proposed action identifier",
    ):
        assert _contains(duplicate_findings, label)


def test_citation_contract_rejects_missing_types_targets_quotes_and_non_fact_lines() -> None:
    base = build_reference_semantic_bundle()
    english, spanish = base.claims
    artifact = base.artifacts[0]

    assert _contains(_findings(replace(base, claims=()), CHECK_CITATION), "no typed claims")
    untyped = replace(english, citation=object())  # type: ignore[arg-type]
    assert _contains(
        _findings(replace(base, claims=(untyped, spanish)), CHECK_CITATION),
        "has no typed citation",
    )
    unknown = replace(
        english,
        citation=replace(english.citation, artifact_id="artifact:unknown"),
    )
    assert _contains(
        _findings(replace(base, claims=(unknown, spanish)), CHECK_CITATION),
        "cites an unknown artifact",
    )
    empty = replace(english, citation=replace(english.citation, exact_quote="  "))
    assert _contains(
        _findings(replace(base, claims=(empty, spanish)), CHECK_CITATION),
        "empty citation quote",
    )
    non_fact_line = "This exact line is present but is not a controlled record."
    non_fact_artifact = replace(artifact, text=f"{artifact.text}\n{non_fact_line}")
    non_fact_claim = replace(
        english,
        citation=replace(english.citation, exact_quote=non_fact_line),
    )
    assert _contains(
        _findings(
            replace(
                base,
                artifacts=(non_fact_artifact,),
                claims=(non_fact_claim, spanish),
            ),
            CHECK_CITATION,
        ),
        "not one controlled FACT record",
    )


@pytest.mark.parametrize(
    ("changes", "fragment"),
    [
        ({"subject_key": "BAD SUBJECT"}, "subject key is malformed"),
        ({"predicate_key": "BAD PREDICATE"}, "predicate key is malformed"),
        ({"value": "01"}, "value is not a canonical finite decimal"),
        ({"value": 180}, "value is not a canonical finite decimal"),
        ({"language": "fr"}, "language is outside the bounded en/es policy"),
        ({"unit": "unreviewed_unit"}, "unit is outside the reviewed vocabulary"),
        ({"statement": "   "}, "statement is empty"),
    ],
)
def test_controlled_fact_contract_rejects_malformed_fields(changes, fragment) -> None:
    base = build_reference_semantic_bundle()
    changed = replace(base.claims[0], **changes)
    findings = _findings(replace(base, claims=(changed, base.claims[1])), CHECK_FACT)
    assert _contains(findings, fragment)


def test_private_bounded_parsers_reject_non_text_and_decimal_parser_failure(monkeypatch) -> None:
    assert semantic_safety_module._parse_number(180) is None
    assert semantic_safety_module._fact_record(180) is None

    def invalid_decimal(_value: str):
        raise InvalidOperation

    monkeypatch.setattr(semantic_safety_module, "Decimal", invalid_decimal)
    assert semantic_safety_module._parse_number("180") is None


def test_bilingual_pairing_rejects_missing_unknown_mistagged_and_unpaired_claims() -> None:
    base = build_reference_semantic_bundle()
    no_pairs = _findings(replace(base, bilingual_pairs=()), CHECK_BILINGUAL)
    assert _contains(no_pairs, "no typed bilingual pairs")
    assert _contains(no_pairs, "is not paired")

    pair = base.bilingual_pairs[0]
    unknown = replace(
        pair,
        english_claim_id="claim:missing:en",
        spanish_claim_id="claim:missing:es",
    )
    unknown_findings = _findings(replace(base, bilingual_pairs=(unknown,)), CHECK_BILINGUAL)
    assert _contains(unknown_findings, "unknown English claim")
    assert _contains(unknown_findings, "unknown Spanish claim")

    mistagged = replace(
        base,
        claims=(
            replace(base.claims[0], language="es"),
            replace(base.claims[1], language="en"),
        ),
    )
    mistagged_findings = _findings(mistagged, CHECK_BILINGUAL)
    assert _contains(mistagged_findings, "English side lacks the en tag")
    assert _contains(mistagged_findings, "Spanish side lacks the es tag")


def test_freshness_requires_an_integer_clock_citations_and_valid_available_windows() -> None:
    base = build_reference_semantic_bundle()
    artifact = base.artifacts[0]

    malformed_clock = replace(base, as_of_ms="now")  # type: ignore[arg-type]
    assert _contains(_findings(malformed_clock, CHECK_FRESHNESS), "as_of_ms is malformed")
    no_citations = replace(
        base,
        claims=(
            replace(base.claims[0], citation=object()),  # type: ignore[arg-type]
            replace(base.claims[1], citation=object()),  # type: ignore[arg-type]
        ),
    )
    assert _contains(
        _findings(no_citations, CHECK_FRESHNESS),
        "no cited artifacts are available",
    )
    unavailable = replace(
        base,
        artifacts=(),
        claims=(
            replace(
                base.claims[0],
                citation=replace(base.claims[0].citation, artifact_id="artifact:missing"),
            ),
            base.claims[1],
        ),
    )
    assert _contains(_findings(unavailable, CHECK_FRESHNESS), "is unavailable")
    invalid_window = replace(
        base,
        artifacts=(replace(artifact, observed_at_ms=5, fresh_until_ms=4),),
    )
    assert _contains(_findings(invalid_window, CHECK_FRESHNESS), "invalid freshness window")


def test_url_policy_rejects_missing_noncanonical_allowlists_and_malformed_urls() -> None:
    base = build_reference_semantic_bundle()
    artifact = base.artifacts[0]

    assert _contains(
        _findings(replace(base, url_policy=object()), CHECK_URL),  # type: ignore[arg-type]
        "URL policy is unavailable",
    )
    invalid_shape = replace(
        base,
        url_policy=UrlPolicy([], ("stormshift.invalid",)),  # type: ignore[arg-type]
    )
    assert _contains(
        _findings(invalid_shape, CHECK_URL),
        "allowlists must be nonempty tuples",
    )
    noncanonical = replace(
        base,
        url_policy=UrlPolicy(
            ("HTTPS", "HTTPS"),
            ("StormShift.Invalid", "StormShift.Invalid"),
        ),
    )
    noncanonical_findings = _findings(noncanonical, CHECK_URL)
    assert _contains(noncanonical_findings, "scheme allowlist is not canonical")
    assert _contains(noncanonical_findings, "host allowlist is not canonical")

    cases = (
        ("", "nonempty canonical text"),
        (" https://stormshift.invalid/evidence", "nonempty canonical text"),
        ("https://stormshift.invalid/a b", "whitespace or control"),
        ("https://stormshift.invalid:invalid/path", "cannot be parsed"),
        ("/relative/path", "not absolute"),
    )
    for url, fragment in cases:
        changed = replace(base, artifacts=(replace(artifact, source_url=url),))
        assert _contains(_findings(changed, CHECK_URL), fragment)


def test_authority_policy_rejects_missing_empty_duplicate_and_malformed_allowlists() -> None:
    base = build_reference_semantic_bundle()
    assert _contains(
        _findings(replace(base, authority_policy=object()), CHECK_AUTHORITY),  # type: ignore[arg-type]
        "authority policy is unavailable",
    )
    empty = replace(base, authority_policy=AuthorityPolicy((), ()))
    empty_findings = _findings(empty, CHECK_AUTHORITY)
    assert _contains(empty_findings, "issuer allowlist must be a nonempty tuple")
    assert _contains(empty_findings, "capability allowlist must be a nonempty tuple")

    noncanonical = replace(
        base,
        authority_policy=AuthorityPolicy(
            ("finite-control-plane", "finite-control-plane", "BAD ISSUER"),
            ("render:preview", "render:preview", "BAD CAPABILITY"),
        ),
    )
    findings = _findings(noncanonical, CHECK_AUTHORITY)
    assert _contains(findings, "issuer allowlist is not canonical")
    assert _contains(findings, "capability allowlist is not canonical")


def test_authority_grants_require_control_origin_canonical_capabilities_and_validity() -> None:
    base = build_reference_semantic_bundle()
    artifact = base.artifacts[0]
    grant = base.authority_grants[0]
    action = base.proposed_actions[0]

    untainted = replace(artifact, trust_label="trusted")
    overlap_grant = replace(grant, grant_id=artifact.artifact_id)
    overlap_action = replace(action, authority_grant_id=artifact.artifact_id)
    overlap_bundle = replace(
        base,
        artifacts=(untainted,),
        authority_grants=(overlap_grant,),
        proposed_actions=(overlap_action,),
    )
    overlap_findings = _findings(overlap_bundle, CHECK_AUTHORITY)
    assert _contains(overlap_findings, "not explicitly tainted as data")
    assert _contains(overlap_findings, "aliases evidence and authority")

    malformed_grant = replace(
        grant,
        issuer="not-allowed",
        capabilities=(
            "render:preview",
            "render:preview",
            "BAD CAPABILITY",
            "publish:alert",
        ),
        issued_at_ms="yesterday",  # type: ignore[arg-type]
        expires_at_ms="tomorrow",  # type: ignore[arg-type]
        source_kind="untrusted-channel",
        derived_from_artifact_ids=[artifact.artifact_id],  # type: ignore[arg-type]
    )
    malformed_findings = _findings(
        replace(base, authority_grants=(malformed_grant,)),
        CHECK_AUTHORITY,
    )
    for fragment in (
        "not from the control-policy channel",
        "issuer is not allowlisted",
        "capabilities contain duplicates",
        "contains a malformed capability",
        "contains a capability outside policy",
        "evidence-derivation field must be a tuple",
        "malformed validity timestamps",
    ):
        assert _contains(malformed_findings, fragment)

    empty_capabilities = replace(grant, capabilities=())
    assert _contains(
        _findings(replace(base, authority_grants=(empty_capabilities,)), CHECK_AUTHORITY),
        "capabilities must be a nonempty tuple",
    )


def test_proposed_actions_require_known_tools_evidence_grants_and_granted_capabilities() -> None:
    base = build_reference_semantic_bundle()
    action = base.proposed_actions[0]

    malformed = replace(
        action,
        action_kind="unknown",
        tool_name="BAD TOOL",
        evidence_artifact_ids=[base.artifacts[0].artifact_id],  # type: ignore[arg-type]
        authority_grant_id="grant:missing",
    )
    malformed_findings = _findings(
        replace(base, proposed_actions=(malformed,)),
        CHECK_AUTHORITY,
    )
    for fragment in (
        "unknown action kind",
        "tool name is malformed",
        "evidence references must be a tuple",
        "lacks an independent authority grant",
    ):
        assert _contains(malformed_findings, fragment)

    unknown_evidence = replace(action, evidence_artifact_ids=("artifact:unknown",))
    assert _contains(
        _findings(replace(base, proposed_actions=(unknown_evidence,)), CHECK_AUTHORITY),
        "references unknown evidence",
    )

    expanded_policy = AuthorityPolicy(
        base.authority_policy.allowed_issuers,
        (*base.authority_policy.allowed_capabilities, "publish:alert"),
    )
    ungranted = replace(action, capability="publish:alert")
    ungranted_findings = _findings(
        replace(
            base,
            authority_policy=expanded_policy,
            proposed_actions=(ungranted,),
        ),
        CHECK_AUTHORITY,
    )
    assert _contains(ungranted_findings, "not present in its authority grant")


def test_accessibility_requires_static_capture_language_inventory_and_title() -> None:
    base = build_reference_semantic_bundle()
    facts = base.accessibility
    assert _contains(
        _findings(replace(base, accessibility=object()), CHECK_ACCESSIBILITY),  # type: ignore[arg-type]
        "declarations are unavailable",
    )

    invalid_languages = replace(
        facts,
        capture_method="browser-claim",
        document_language=7,  # type: ignore[arg-type]
        available_languages=[],  # type: ignore[arg-type]
        title="",
    )
    invalid_findings = _findings(
        replace(base, accessibility=invalid_languages),
        CHECK_ACCESSIBILITY,
    )
    for fragment in (
        "capture method",
        "document language is missing or malformed",
        "available languages must be a nonempty tuple",
        "document language is absent",
        "available languages omit claim languages",
        "document title is empty",
    ):
        assert _contains(invalid_findings, fragment)

    noncanonical_languages = replace(
        facts,
        available_languages=("en", "en", "INVALID"),
    )
    language_findings = _findings(
        replace(base, accessibility=noncanonical_languages),
        CHECK_ACCESSIBILITY,
    )
    assert _contains(language_findings, "available languages contain duplicates")
    assert _contains(language_findings, "language tag is malformed")
    assert _contains(language_findings, "omit claim languages: es")


def test_accessibility_rejects_malformed_landmark_counts_records_ids_roles_and_labels() -> None:
    base = build_reference_semantic_bundle()
    facts = base.accessibility

    malformed_shape = replace(
        facts,
        declared_landmark_count=True,
        landmarks=(object(),),  # type: ignore[arg-type]
    )
    malformed_findings = _findings(
        replace(base, accessibility=malformed_shape),
        CHECK_ACCESSIBILITY,
    )
    assert _contains(malformed_findings, "declared landmark count is malformed")
    assert _contains(malformed_findings, "a landmark fact is malformed")

    count_mismatch = replace(facts, declared_landmark_count=99)
    assert _contains(
        _findings(replace(base, accessibility=count_mismatch), CHECK_ACCESSIBILITY),
        "declared landmark count differs",
    )

    landmarks = (
        LandmarkFact("landmark:main", "main", ""),
        LandmarkFact("landmark:duplicate", "navigation", ""),
        LandmarkFact("landmark:duplicate", "region", "Region"),
        LandmarkFact("BAD LANDMARK", "unknown-role", ""),
    )
    malformed_records = replace(
        facts,
        declared_landmark_count=len(landmarks),
        landmarks=landmarks,
    )
    findings = _findings(
        replace(base, accessibility=malformed_records),
        CHECK_ACCESSIBILITY,
    )
    assert _contains(findings, "landmark identifiers are not unique")
    assert _contains(findings, "a landmark identifier is malformed")
    assert _contains(findings, "role is not recognized")
    assert _contains(findings, "requires a label")


def test_accessibility_rejects_malformed_image_counts_records_ids_and_alt_contracts() -> None:
    base = build_reference_semantic_bundle()
    facts = base.accessibility

    malformed_shape = replace(
        facts,
        declared_image_count=True,
        images=(object(),),  # type: ignore[arg-type]
    )
    shape_findings = _findings(
        replace(base, accessibility=malformed_shape),
        CHECK_ACCESSIBILITY,
    )
    assert _contains(shape_findings, "declared image count is malformed")
    assert _contains(shape_findings, "an image accessibility fact is malformed")

    count_mismatch = replace(facts, declared_image_count=99)
    assert _contains(
        _findings(replace(base, accessibility=count_mismatch), CHECK_ACCESSIBILITY),
        "declared image count differs",
    )

    images = (
        ImageAccessibilityFact("image:duplicate", True, "must be empty"),
        ImageAccessibilityFact("image:duplicate", False, "Content"),
        ImageAccessibilityFact("BAD IMAGE", "yes", "Alt"),  # type: ignore[arg-type]
    )
    malformed_records = replace(
        facts,
        declared_image_count=len(images),
        images=images,
    )
    findings = _findings(
        replace(base, accessibility=malformed_records),
        CHECK_ACCESSIBILITY,
    )
    assert _contains(findings, "image identifiers are not unique")
    assert _contains(findings, "an image identifier is malformed")
    assert _contains(findings, "decorative flag is malformed")
    assert _contains(findings, "must declare empty alt text")


def test_accessibility_rejects_malformed_contrast_counts_records_ids_flags_and_colors() -> None:
    base = build_reference_semantic_bundle()
    facts = base.accessibility

    zero_count = replace(facts, declared_text_surface_count=0)
    assert _contains(
        _findings(replace(base, accessibility=zero_count), CHECK_ACCESSIBILITY),
        "count must be a positive integer",
    )
    count_mismatch = replace(facts, declared_text_surface_count=99)
    assert _contains(
        _findings(replace(base, accessibility=count_mismatch), CHECK_ACCESSIBILITY),
        "count differs from contrast declarations",
    )
    malformed_shape = replace(facts, contrasts=(object(),))  # type: ignore[arg-type]
    assert _contains(
        _findings(replace(base, accessibility=malformed_shape), CHECK_ACCESSIBILITY),
        "a contrast declaration is malformed",
    )

    contrasts = (
        ContrastDeclaration("surface:duplicate", "#000", "#FFFFFF"),
        ContrastDeclaration("surface:duplicate", "#000000", "#FFFFFF"),
        ContrastDeclaration("BAD SURFACE", "#000000", "#FFFFFF", "yes"),  # type: ignore[arg-type]
    )
    malformed_records = replace(
        facts,
        declared_text_surface_count=len(contrasts),
        contrasts=contrasts,
    )
    findings = _findings(
        replace(base, accessibility=malformed_records),
        CHECK_ACCESSIBILITY,
    )
    assert _contains(findings, "surface identifiers are not unique")
    assert _contains(findings, "contrast surface identifier is malformed")
    assert _contains(findings, "large-text flag is malformed")
    assert _contains(findings, "colors are not opaque #RRGGBB")

    large_text = ContrastDeclaration("surface:large", "#777777", "#FFFFFF", True)
    large_text_facts = replace(
        facts,
        declared_text_surface_count=1,
        contrasts=(large_text,),
    )
    large_text_findings = _findings(
        replace(base, accessibility=large_text_facts),
        CHECK_ACCESSIBILITY,
    )
    assert not _contains(large_text_findings, "below 3.0:1")


def test_contrast_builder_report_lookup_and_digest_edges() -> None:
    with pytest.raises(ValueError, match="foreground"):
        contrast_ratio("black", "#FFFFFF")
    with pytest.raises(ValueError, match="background"):
        contrast_ratio("#000000", "white")
    with pytest.raises(ValueError, match="scenario_id"):
        build_reference_semantic_bundle(scenario_id="BAD SCENARIO")
    with pytest.raises(ValueError, match="evacuee_count"):
        build_reference_semantic_bundle(evacuee_count=True)

    bundle = build_reference_semantic_bundle()
    report = StormShiftSemanticSafetyVerifier().verify(bundle)
    with pytest.raises(KeyError):
        report.check("missing-check")
    duplicated = replace(report, checks=(*report.checks, report.checks[0]))
    with pytest.raises(KeyError):
        duplicated.check(report.checks[0].check_id)
    assert not replace(report, report_digest="0" * 64).verify_digest()
    assert bundle.bundle_digest == semantic_safety_module.content_digest(bundle)


def test_static_policy_constants_remain_separate_from_untrusted_evidence() -> None:
    assert STATIC_RENDER_DECLARATION != UNTRUSTED_EVIDENCE
    assert TRUSTED_CONTROL_POLICY != UNTRUSTED_EVIDENCE
    grant = AuthorityGrant(
        "grant:test",
        "issuer:test",
        ("capability:test",),
        0,
        1,
    )
    action = ProposedAction(
        "action:test",
        "effect",
        "tool:test",
        "capability:test",
        grant.grant_id,
        (),
    )
    artifact = ArtifactText("artifact:test", "data", 0, 1, "https://example.invalid")
    claim = FactClaim(
        "claim:test",
        "en",
        "subject",
        "predicate",
        "1",
        "people",
        "1 people",
        CitationPointer(artifact.artifact_id, "FACT[subject|predicate]=1 people"),
    )
    bundle = SemanticSafetyBundle(
        "scenario:test",
        0,
        (artifact,),
        (claim,),
        (BilingualFactPair("pair:test", claim.claim_id, claim.claim_id),),
        UrlPolicy(("https",), ("example.invalid",)),
        AuthorityPolicy((grant.issuer,), grant.capabilities),
        (grant,),
        (action,),
        StaticRenderAccessibilityFacts(
            STATIC_RENDER_DECLARATION,
            "en",
            ("en",),
            "Test",
            1,
            (LandmarkFact("landmark:main", "main", ""),),
            0,
            (),
            1,
            (ContrastDeclaration("surface:test", "#000000", "#FFFFFF"),),
        ),
    )
    # Reusing one claim for both languages is rejected; constructing the bundle
    # itself never confers authority or bypasses semantic checks.
    assert not StormShiftSemanticSafetyVerifier().verify(bundle).passed
