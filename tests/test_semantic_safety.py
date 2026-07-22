from dataclasses import replace

from agent_physics.semantic_safety import (
    SEMANTIC_SAFETY_LIMITATIONS,
    UNTRUSTED_EVIDENCE,
    StaticRenderAccessibilityFacts,
    StormShiftSemanticSafetyVerifier,
    adversarial_mutation_corpus,
    build_reference_semantic_bundle,
    contrast_ratio,
    corpus_digest,
)


def test_reference_bundle_passes_every_bounded_check_deterministically() -> None:
    bundle = build_reference_semantic_bundle()
    verifier = StormShiftSemanticSafetyVerifier()

    first = verifier.verify(bundle)
    replay = verifier.verify(bundle)

    assert first.passed
    assert all(check.passed for check in first.checks)
    assert first == replay
    assert first.bundle_digest == bundle.bundle_digest
    assert first.verify_digest()


def test_report_carries_claim_boundaries_and_digest_binds_them() -> None:
    report = StormShiftSemanticSafetyVerifier().verify(build_reference_semantic_bundle())

    assert any("no general natural-language" in item for item in report.limitations)
    assert any("no browser DOM" in item for item in report.limitations)
    assert any("not authenticated" in item for item in report.limitations)
    assert any("not authorization" in item for item in report.limitations)
    assert not replace(report, limitations=report.limitations[:-1]).verify_digest()


def test_literal_citation_is_bound_to_supplied_text_and_controlled_fact() -> None:
    bundle = build_reference_semantic_bundle()
    english = bundle.claims[0]
    unsupported = replace(
        bundle,
        claims=(
            replace(
                english,
                citation=replace(
                    english.citation,
                    exact_quote="FACT[evacuation|total]=999 people",
                ),
            ),
            bundle.claims[1],
        ),
    )

    check = (
        StormShiftSemanticSafetyVerifier()
        .verify(unsupported)
        .check("citation-support-controlled-facts")
    )

    assert not check.passed
    assert any("absent from supplied artifact text" in item for item in check.findings)

    embedded = replace(
        bundle,
        artifacts=(
            replace(
                bundle.artifacts[0],
                text=bundle.artifacts[0].text.replace(
                    english.citation.exact_quote,
                    f"ATTACKER-PREFIX{english.citation.exact_quote}",
                ),
            ),
        ),
    )
    embedded_check = (
        StormShiftSemanticSafetyVerifier()
        .verify(embedded)
        .check("citation-support-controlled-facts")
    )
    assert not embedded_check.passed


def test_natural_language_number_and_localized_unit_drift_fail_closed() -> None:
    bundle = build_reference_semantic_bundle()
    spanish = bundle.claims[1]
    verifier = StormShiftSemanticSafetyVerifier()

    wrong_number = verifier.verify(
        replace(bundle, claims=(bundle.claims[0], replace(spanish, statement="181 personas")))
    )
    wrong_unit = verifier.verify(
        replace(bundle, claims=(bundle.claims[0], replace(spanish, statement="180 rutas")))
    )
    extra_number = verifier.verify(
        replace(
            bundle,
            claims=(bundle.claims[0], replace(spanish, statement="180 personas, nivel 2")),
        )
    )

    check_id = "key-fact-number-unit-consistency"
    assert not wrong_number.check(check_id).passed
    assert not wrong_unit.check(check_id).passed
    assert not extra_number.check(check_id).passed


def test_bilingual_equivalence_is_exactly_structured_fact_equivalence() -> None:
    bundle = build_reference_semantic_bundle()
    spanish = replace(bundle.claims[1], predicate_key="available")
    report = StormShiftSemanticSafetyVerifier().verify(
        replace(bundle, claims=(bundle.claims[0], spanish))
    )

    check = report.check("bilingual-structured-fact-equivalence")
    assert not check.passed
    assert any("controlled fact tuples differ" in item for item in check.findings)
    assert any("semantic-equivalence" in item for item in report.limitations)


def test_freshness_rejects_expired_future_and_malformed_windows() -> None:
    bundle = build_reference_semantic_bundle()
    artifact = bundle.artifacts[0]
    verifier = StormShiftSemanticSafetyVerifier()

    expired = verifier.verify(
        replace(bundle, artifacts=(replace(artifact, fresh_until_ms=99_999),))
    )
    future = verifier.verify(
        replace(bundle, artifacts=(replace(artifact, observed_at_ms=100_001),))
    )
    boolean_time = verifier.verify(
        replace(bundle, artifacts=(replace(artifact, fresh_until_ms=True),))
    )

    check_id = "cited-artifact-freshness"
    assert not expired.check(check_id).passed
    assert not future.check(check_id).passed
    assert not boolean_time.check(check_id).passed


def test_url_policy_is_exact_and_rejects_credentials_ports_and_backslashes() -> None:
    bundle = build_reference_semantic_bundle()
    artifact = bundle.artifacts[0]
    verifier = StormShiftSemanticSafetyVerifier()
    unsafe = (
        "http://stormshift.invalid/evidence",
        "https://stormshift.invalid.evil.example/evidence",
        "https://user:pass@stormshift.invalid/evidence",
        "https://stormshift.invalid:8443/evidence",
        "https://stormshift.invalid\\@evil.example/evidence",
    )

    for url in unsafe:
        report = verifier.verify(replace(bundle, artifacts=(replace(artifact, source_url=url),)))
        assert not report.check("url-protocol-host-allowlist").passed, url


def test_untrusted_instruction_text_cannot_create_or_escalate_authority() -> None:
    bundle = build_reference_semantic_bundle()
    artifact = bundle.artifacts[0]
    action = bundle.proposed_actions[0]
    verifier = StormShiftSemanticSafetyVerifier()

    assert artifact.trust_label == UNTRUSTED_EVIDENCE
    assert "authorize publish-alert" in artifact.text
    assert verifier.verify(bundle).check("authority-taint-separation").passed

    evidence_authority = verifier.verify(
        replace(
            bundle,
            proposed_actions=(replace(action, authority_grant_id=artifact.artifact_id),),
        )
    )
    escalated = verifier.verify(
        replace(
            bundle,
            proposed_actions=(replace(action, capability="publish:alert"),),
        )
    )

    check_id = "authority-taint-separation"
    assert not evidence_authority.check(check_id).passed
    assert any(
        "untrusted evidence as authority" in item
        for item in evidence_authority.check(check_id).findings
    )
    assert not escalated.check(check_id).passed


def test_tainted_expired_and_wrong_issuer_grants_are_rejected() -> None:
    bundle = build_reference_semantic_bundle()
    artifact = bundle.artifacts[0]
    grant = bundle.authority_grants[0]
    verifier = StormShiftSemanticSafetyVerifier()
    mutations = (
        replace(grant, derived_from_artifact_ids=(artifact.artifact_id,)),
        replace(grant, expires_at_ms=bundle.as_of_ms - 1),
        replace(grant, issuer="artifact-author"),
    )

    for mutated in mutations:
        report = verifier.verify(replace(bundle, authority_grants=(mutated,)))
        assert not report.check("authority-taint-separation").passed


def test_static_accessibility_declarations_cover_required_facts() -> None:
    bundle = build_reference_semantic_bundle()
    check = (
        StormShiftSemanticSafetyVerifier()
        .verify(bundle)
        .check("declared-render-accessibility-structural-only")
    )

    assert check.passed
    facts = bundle.accessibility
    assert facts.document_language == "en"
    assert facts.title
    assert any(item.role == "main" for item in facts.landmarks)
    assert all(
        item.alt_text == "" if item.decorative else bool(item.alt_text) for item in facts.images
    )


def test_accessibility_declarations_fail_for_missing_structure_and_bad_contrast() -> None:
    bundle = build_reference_semantic_bundle()
    facts = bundle.accessibility
    content_image = replace(facts.images[0], alt_text=None)
    weak_contrast = replace(
        facts.contrasts[0],
        foreground_hex="#777777",
        background_hex="#888888",
    )
    malformed = replace(
        facts,
        document_language="",
        title=" ",
        landmarks=tuple(item for item in facts.landmarks if item.role != "main"),
        images=(content_image, facts.images[1]),
        contrasts=(weak_contrast, facts.contrasts[1]),
    )

    check = (
        StormShiftSemanticSafetyVerifier()
        .verify(replace(bundle, accessibility=malformed))
        .check("declared-render-accessibility-structural-only")
    )

    assert not check.passed
    assert any("document language" in item for item in check.findings)
    assert any("document title" in item for item in check.findings)
    assert any("main landmark" in item for item in check.findings)
    assert any("alt text" in item for item in check.findings)
    assert any("below 4.5:1" in item for item in check.findings)


def test_contrast_math_is_recomputed_from_opaque_srgb_declarations() -> None:
    assert contrast_ratio("#000000", "#FFFFFF") == 21.0
    assert contrast_ratio("#777777", "#888888") < 4.5


def test_adversarial_corpus_is_deterministic_and_every_attack_is_caught() -> None:
    first = adversarial_mutation_corpus()
    replay = adversarial_mutation_corpus()
    verifier = StormShiftSemanticSafetyVerifier()

    assert len(first) >= 15
    assert corpus_digest(first) == corpus_digest(replay)
    assert len({item.mutation_id for item in first}) == len(first)
    assert len({item.mutation_digest for item in first}) == len(first)
    for mutation in first:
        report = verifier.verify(mutation.bundle)
        assert not report.passed, mutation.mutation_id
        assert report.verify_digest(), mutation.mutation_id
        for check_id in mutation.expected_failed_checks:
            assert not report.check(check_id).passed, (
                mutation.mutation_id,
                check_id,
            )


def test_malformed_runtime_values_return_failed_reports_instead_of_raising() -> None:
    bundle = build_reference_semantic_bundle()
    malformed_artifact = replace(
        bundle.artifacts[0],
        text=object(),  # type: ignore[arg-type]
        observed_at_ms="yesterday",  # type: ignore[arg-type]
    )
    malformed_accessibility = replace(
        bundle.accessibility,
        contrasts=(object(),),  # type: ignore[arg-type]
    )

    report = StormShiftSemanticSafetyVerifier().verify(
        replace(
            bundle,
            artifacts=(malformed_artifact,),
            accessibility=malformed_accessibility,
        )
    )

    assert not report.passed
    assert report.bundle_digest
    assert report.verify_digest()
    assert not report.check("input-contract").passed
    assert not report.check("citation-support-controlled-facts").passed
    assert not report.check("cited-artifact-freshness").passed
    assert not report.check("declared-render-accessibility-structural-only").passed


def test_limitations_constant_is_nonempty_and_explicitly_structural() -> None:
    assert SEMANTIC_SAFETY_LIMITATIONS
    assert any("declarations only" in item for item in SEMANTIC_SAFETY_LIMITATIONS)
    assert any("translation-quality" in item for item in SEMANTIC_SAFETY_LIMITATIONS)
    assert any("cryptographically" in item for item in SEMANTIC_SAFETY_LIMITATIONS)
    assert StaticRenderAccessibilityFacts.__doc__ is not None
