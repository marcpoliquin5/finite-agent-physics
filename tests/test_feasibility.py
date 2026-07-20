from dataclasses import replace

from agent_physics import FeasibilityAnalyzer, FeasibilityStatus, verify_conservation
from agent_physics.examples import miami_eoc_envelope, miami_eoc_graph


def test_preflight_certificate_is_stable_and_verifiable() -> None:
    analyzer = FeasibilityAnalyzer()
    first, first_result = analyzer.analyze(miami_eoc_graph(), miami_eoc_envelope())
    second, second_result = analyzer.analyze(miami_eoc_graph(), miami_eoc_envelope())
    assert first.status is FeasibilityStatus.FEASIBLE
    assert first.certificate_digest == second.certificate_digest
    assert first.schedule_digest == second.schedule_digest
    assert first.verify_digest()
    assert first_result.as_dict() == second_result.as_dict()


def test_tampered_certificate_fails_digest_check() -> None:
    certificate, _ = FeasibilityAnalyzer().analyze(miami_eoc_graph(), miami_eoc_envelope())
    tampered = replace(certificate, projected_makespan_ms=certificate.projected_makespan_ms + 1)
    assert not tampered.verify_digest()


def test_unadmitted_envelope_is_conservatively_refused() -> None:
    constrained = replace(miami_eoc_envelope(), max_tokens=1)
    certificate, _ = FeasibilityAnalyzer().analyze(miami_eoc_graph(), constrained)
    assert certificate.status is FeasibilityStatus.REFUSED
    assert certificate.failure_reason is not None


def test_conservation_report_independently_recalculates_totals() -> None:
    graph = miami_eoc_graph()
    envelope = miami_eoc_envelope()
    _, result = FeasibilityAnalyzer().analyze(graph, envelope)
    report = verify_conservation(graph, envelope, result)
    assert report.passed
    assert report.trace_digest
    assert not report.violations
