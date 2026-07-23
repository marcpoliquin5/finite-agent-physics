from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import replace
from pathlib import Path

import pytest

from agent_physics.contracts import EffectClass
from agent_physics.effects import (
    AdapterConflict,
    ApprovalAuthority,
    FencingToken,
    IntentNotFound,
    InvalidApproval,
    InvalidTransition,
    SQLiteEffectBroker,
    SimulatedEffectAdapter,
    StaleFence,
)


SECRET = b"adversarial-approval-secret-at-least-32-bytes"


def _broker(path: Path, broker_id: str = "adversarial-broker") -> SQLiteEffectBroker:
    return SQLiteEffectBroker(
        path,
        broker_id=broker_id,
        trusted_approval_keys={"authority": SECRET},
        clock_ms=lambda: 1_000,
    )


def _propose(
    broker: SQLiteEffectBroker,
    key: str,
    effect_class: EffectClass = EffectClass.IDEMPOTENT_WRITE,
):
    return broker.propose(
        run_id="run-adversarial",
        action="write-fixture",
        resource="simulation://effects",
        effect_class=effect_class,
        idempotency_key=key,
        payload={"fixture": True},
        compensation_action=(
            "undo-fixture" if effect_class is EffectClass.REVERSIBLE_WRITE else None
        ),
    )


def _approved(
    broker: SQLiteEffectBroker,
    key: str,
    effect_class: EffectClass = EffectClass.IDEMPOTENT_WRITE,
):
    proposed = _propose(broker, key, effect_class)
    prepared = broker.prepare(proposed.intent_id)
    approved = broker.approve(prepared.intent_id, prepared.fencing_token)
    return approved, approved.fencing_token


def _resign(grant):  # type: ignore[no-untyped-def]
    payload = json.dumps(
        grant.claims(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return replace(
        grant,
        signature=hmac.new(SECRET, payload.encode("utf-8"), hashlib.sha256).hexdigest(),
    )


def test_authority_and_broker_constructor_boundaries_reject_weak_configuration(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="key_id"):
        ApprovalAuthority("", SECRET)
    with pytest.raises(ValueError, match="at least 32 bytes"):
        ApprovalAuthority("authority", b"short")
    with pytest.raises(ValueError, match="filesystem"):
        SQLiteEffectBroker(":memory:")
    with pytest.raises(ValueError, match="trusted approval keys"):
        SQLiteEffectBroker(tmp_path / "weak.db", trusted_approval_keys={"": SECRET})
    with pytest.raises(ValueError, match="trusted approval keys"):
        SQLiteEffectBroker(tmp_path / "short.db", trusted_approval_keys={"authority": b"short"})


def test_proposal_and_approval_input_contracts_fail_before_state_transition(tmp_path: Path) -> None:
    broker = _broker(tmp_path / "effects.db")
    authority = ApprovalAuthority("authority", SECRET)

    with pytest.raises(IntentNotFound):
        broker.get("missing-intent")
    with pytest.raises(ValueError, match="required"):
        broker.propose(
            run_id="",
            action="write",
            resource="simulation://effects",
            effect_class=EffectClass.IDEMPOTENT_WRITE,
            idempotency_key="missing-run",
            payload={},
        )
    with pytest.raises(ValueError, match="write effects only"):
        broker.propose(
            run_id="run",
            action="read",
            resource="simulation://effects",
            effect_class=EffectClass.READ,
            idempotency_key="read",
            payload={},
        )
    with pytest.raises(ValueError, match="compensation action"):
        broker.propose(
            run_id="run",
            action="write",
            resource="simulation://effects",
            effect_class=EffectClass.REVERSIBLE_WRITE,
            idempotency_key="missing-compensation",
            payload={},
        )
    with pytest.raises(ValueError, match="canonical JSON"):
        broker.propose(
            run_id="run",
            action="write",
            resource="simulation://effects",
            effect_class=EffectClass.IDEMPOTENT_WRITE,
            idempotency_key="nan",
            payload={"value": float("nan")},
        )

    intent = _propose(broker, "approval-input")
    with pytest.raises(InvalidTransition, match="not fenced"):
        _ = intent.fencing_token
    with pytest.raises(ValueError, match="principal"):
        authority.issue(intent, principal="", now_ms=1_000, ttl_ms=10)
    with pytest.raises(ValueError, match="ttl_ms"):
        authority.issue(intent, principal="reviewer", now_ms=1_000, ttl_ms=0)


def test_broker_state_machine_rejects_invalid_replays_fences_and_abort_paths(
    tmp_path: Path,
) -> None:
    broker = _broker(tmp_path / "effects.db")
    proposed = _propose(broker, "state-machine")

    with pytest.raises(InvalidTransition, match="cannot commit"):
        broker.commit(
            proposed.intent_id, FencingToken(proposed.intent_id, 1, "x"), SimulatedEffectAdapter()
        )
    with pytest.raises(InvalidTransition, match="cannot compensate"):
        broker.compensate(
            proposed.intent_id,
            FencingToken(proposed.intent_id, 1, "x"),
            SimulatedEffectAdapter(),
        )
    with pytest.raises(InvalidTransition, match="cannot fence"):
        broker.acquire_fence(proposed.intent_id)

    direct_abort = _propose(broker, "direct-abort")
    assert broker.abort(direct_abort.intent_id).state.value == "aborted"

    prepared = broker.prepare(proposed.intent_id)
    with pytest.raises(InvalidTransition, match="cannot prepare"):
        broker.prepare(prepared.intent_id)
    with pytest.raises(StaleFence, match="require a fencing token"):
        broker.abort(prepared.intent_id)
    with pytest.raises(StaleFence, match="stale fence"):
        broker.abort(prepared.intent_id, FencingToken(prepared.intent_id, 99, "other"))
    aborted = broker.abort(prepared.intent_id, prepared.fencing_token)
    assert broker.abort(aborted.intent_id) == aborted
    with pytest.raises(InvalidTransition, match="cannot approve"):
        broker.approve(aborted.intent_id, prepared.fencing_token)

    committed, token = _approved(broker, "cannot-abort-commit")
    broker.commit(committed.intent_id, token, SimulatedEffectAdapter())
    with pytest.raises(InvalidTransition, match="cannot abort"):
        broker.abort(committed.intent_id, token)
    with pytest.raises(ValueError, match="outbox limit"):
        broker.pending_outbox(limit=0)


def test_approval_verification_rejects_every_authentication_and_scope_failure(
    tmp_path: Path,
) -> None:
    authority = ApprovalAuthority("authority", SECRET)
    broker = _broker(tmp_path / "effects.db")
    proposed = _propose(broker, "approval", EffectClass.IRREVERSIBLE_WRITE)
    prepared = broker.prepare(proposed.intent_id)
    valid = authority.issue(
        prepared,
        principal="reviewer",
        now_ms=1_000,
        ttl_ms=100,
        grant_id="shared-grant-id",
    )

    untrusted_authority = ApprovalAuthority("untrusted", SECRET)
    untrusted = untrusted_authority.issue(
        prepared,
        principal="reviewer",
        now_ms=1_000,
        ttl_ms=100,
    )
    with pytest.raises(InvalidApproval, match="untrusted"):
        broker.approve(prepared.intent_id, prepared.fencing_token, untrusted)

    empty_principal = _resign(replace(valid, principal=""))
    with pytest.raises(InvalidApproval, match="principal"):
        broker.approve(prepared.intent_id, prepared.fencing_token, empty_principal)

    empty_interval = _resign(replace(valid, expires_at_ms=valid.not_before_ms))
    with pytest.raises(InvalidApproval, match="interval is empty"):
        broker.approve(prepared.intent_id, prepared.fencing_token, empty_interval)

    approved = broker.approve(prepared.intent_id, prepared.fencing_token, valid)
    with pytest.raises(InvalidApproval, match="replay"):
        broker.approve(approved.intent_id, approved.fencing_token, None)

    policy_proposed = _propose(broker, "policy-with-grant")
    policy_prepared = broker.prepare(policy_proposed.intent_id)
    policy_grant = authority.issue(
        policy_prepared,
        principal="reviewer",
        now_ms=1_000,
        ttl_ms=100,
    )
    with pytest.raises(InvalidApproval, match="policy approval"):
        broker.approve(policy_prepared.intent_id, policy_prepared.fencing_token, policy_grant)

    second = _propose(broker, "duplicate-grant", EffectClass.IRREVERSIBLE_WRITE)
    second = broker.prepare(second.intent_id)
    duplicate_id = authority.issue(
        second,
        principal="reviewer",
        now_ms=1_000,
        ttl_ms=100,
        grant_id="shared-grant-id",
    )
    with pytest.raises(InvalidApproval, match="already consumed"):
        broker.approve(second.intent_id, second.fencing_token, duplicate_id)


def test_simulated_adapter_rejects_stale_conflicting_and_invalid_compensation(
    tmp_path: Path,
) -> None:
    # Broker-created immutable intents keep this test on the same production representation.
    broker = _broker(tmp_path / "effects.db")
    first = _propose(broker, "adapter-key")
    other = replace(first, intent_id="different-intent")
    reversible = _propose(broker, "reversible-adapter", EffectClass.REVERSIBLE_WRITE)

    adapter = SimulatedEffectAdapter()
    assert not adapter.was_applied(first)
    high = FencingToken(first.intent_id, 2, "owner")
    low = FencingToken(first.intent_id, 1, "owner")
    assert adapter.execute(first, high)
    with pytest.raises(StaleFence):
        adapter.execute(first, low)
    with pytest.raises(AdapterConflict, match="idempotency key maps to another effect"):
        adapter.execute(other, FencingToken(other.intent_id, 2, "owner"))
    with pytest.raises(AdapterConflict, match="status belongs to another effect"):
        adapter.was_applied(other)

    with pytest.raises(InvalidTransition, match="only reversible"):
        adapter.compensate(first, high)
    with pytest.raises(InvalidTransition, match="not observed"):
        adapter.compensate(
            reversible,
            FencingToken(reversible.intent_id, 1, "owner"),
        )
    reversible_token = FencingToken(reversible.intent_id, 3, "owner")
    assert adapter.execute(reversible, reversible_token)
    with pytest.raises(StaleFence):
        adapter.compensate(
            reversible,
            FencingToken(reversible.intent_id, 2, "owner"),
        )
    assert adapter.compensate(reversible, reversible_token)
    assert not adapter.compensate(reversible, reversible_token)
