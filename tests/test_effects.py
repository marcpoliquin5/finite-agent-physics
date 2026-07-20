from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from agent_physics.contracts import EffectClass
from agent_physics.effects import (
    AmbiguousCommit,
    ApprovalAuthority,
    ApprovalRequired,
    EffectState,
    IdempotencyConflict,
    InvalidApproval,
    InvalidTransition,
    SQLiteEffectBroker,
    SimulatedEffectAdapter,
    SimulatedProcessCrash,
    StaleFence,
)


APPROVAL_SECRET = b"approval-test-secret-with-at-least-32-bytes"


def _broker(
    path: Path,
    broker_id: str,
    *,
    clock: list[int] | None = None,
) -> SQLiteEffectBroker:
    return SQLiteEffectBroker(
        path,
        broker_id=broker_id,
        trusted_approval_keys={"safety-office": APPROVAL_SECRET},
        clock_ms=(lambda: clock[0]) if clock is not None else None,
    )


def _propose(
    broker: SQLiteEffectBroker,
    *,
    key: str,
    run_id: str = "run-1",
    effect_class: EffectClass = EffectClass.IDEMPOTENT_WRITE,
):
    return broker.propose(
        run_id=run_id,
        action="publish_shelter_notice",
        resource="miami-eoc/notices",
        effect_class=effect_class,
        idempotency_key=key,
        payload={"language": "en", "message": "Shelter 7 is open"},
        compensation_action=(
            "retract_shelter_notice"
            if effect_class is EffectClass.REVERSIBLE_WRITE
            else None
        ),
    )


def _policy_approved(broker: SQLiteEffectBroker, *, key: str, effect_class=EffectClass.IDEMPOTENT_WRITE):
    proposed = _propose(broker, key=key, effect_class=effect_class)
    prepared = broker.prepare(proposed.intent_id)
    approved = broker.approve(prepared.intent_id, prepared.fencing_token)
    return approved, approved.fencing_token


def test_irreversible_effect_needs_authenticated_exact_scope_grant(tmp_path: Path) -> None:
    now = [1_000]
    broker = _broker(tmp_path / "effects.db", "broker-a", clock=now)
    authority = ApprovalAuthority("safety-office", APPROVAL_SECRET)
    proposed = _propose(
        broker,
        key="irreversible-1",
        effect_class=EffectClass.IRREVERSIBLE_WRITE,
    )
    assert proposed.state is EffectState.PROPOSED
    prepared = broker.prepare(proposed.intent_id)
    assert prepared.state is EffectState.PREPARED

    with pytest.raises(ApprovalRequired):
        broker.approve(prepared.intent_id, prepared.fencing_token)

    wrong_scope = authority.issue(
        prepared,
        principal="incident-commander@example.org",
        now_ms=now[0],
        ttl_ms=100,
        resource="somewhere-else",
    )
    with pytest.raises(InvalidApproval, match="exact effect scope"):
        broker.approve(prepared.intent_id, prepared.fencing_token, wrong_scope)

    valid = authority.issue(
        prepared,
        principal="incident-commander@example.org",
        now_ms=now[0],
        ttl_ms=100,
    )
    forged = replace(valid, signature="0" * len(valid.signature))
    with pytest.raises(InvalidApproval, match="signature"):
        broker.approve(prepared.intent_id, prepared.fencing_token, forged)

    approved = broker.approve(prepared.intent_id, prepared.fencing_token, valid)
    assert approved.state is EffectState.APPROVED
    assert approved.approval_grant_id == valid.grant_id
    assert broker.approve(prepared.intent_id, prepared.fencing_token, valid) == approved

    adapter = SimulatedEffectAdapter()
    committed = broker.commit(prepared.intent_id, prepared.fencing_token, adapter)
    assert committed.state is EffectState.COMMITTED
    assert broker.commit(prepared.intent_id, prepared.fencing_token, adapter) == committed
    assert adapter.physical_apply_count("irreversible-1") == 1


def test_expired_approval_cannot_authorize_or_commit(tmp_path: Path) -> None:
    now = [10_000]
    broker = _broker(tmp_path / "effects.db", "broker-a", clock=now)
    authority = ApprovalAuthority("safety-office", APPROVAL_SECRET)
    proposed = _propose(
        broker,
        key="expiring",
        effect_class=EffectClass.IRREVERSIBLE_WRITE,
    )
    prepared = broker.prepare(proposed.intent_id)
    grant = authority.issue(
        prepared,
        principal="incident-commander@example.org",
        now_ms=now[0],
        ttl_ms=10,
    )
    now[0] = 10_010
    with pytest.raises(InvalidApproval, match="currently valid"):
        broker.approve(prepared.intent_id, prepared.fencing_token, grant)

    now[0] = 20_000
    fresh = authority.issue(
        prepared,
        principal="incident-commander@example.org",
        now_ms=now[0],
        ttl_ms=10,
    )
    broker.approve(prepared.intent_id, prepared.fencing_token, fresh)
    now[0] = 20_010
    with pytest.raises(InvalidApproval, match="currently valid"):
        broker.commit(
            prepared.intent_id,
            prepared.fencing_token,
            SimulatedEffectAdapter(),
        )


def test_global_idempotency_is_race_safe_across_brokers_and_runs(tmp_path: Path) -> None:
    database = tmp_path / "effects.db"
    broker_a = _broker(database, "broker-a")
    broker_b = _broker(database, "broker-b")

    def propose(broker: SQLiteEffectBroker, run_id: str):
        return _propose(broker, key="global-key", run_id=run_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        future_a = pool.submit(propose, broker_a, "run-a")
        future_b = pool.submit(propose, broker_b, "run-b")
        intent_a = future_a.result()
        intent_b = future_b.result()

    assert intent_a.intent_id == intent_b.intent_id
    proposed_events = [
        event for event in broker_a.pending_outbox() if event.event_type == "effect.proposed"
    ]
    assert len(proposed_events) == 1

    with pytest.raises(IdempotencyConflict):
        broker_b.propose(
            run_id="run-c",
            action="publish_shelter_notice",
            resource="miami-eoc/notices",
            effect_class=EffectClass.IDEMPOTENT_WRITE,
            idempotency_key="global-key",
            payload={"message": "different operation"},
        )


def test_new_broker_fence_invalidates_stale_commit(tmp_path: Path) -> None:
    database = tmp_path / "effects.db"
    broker_a = _broker(database, "broker-a")
    broker_b = _broker(database, "broker-b")
    approved, stale_token = _policy_approved(broker_a, key="fenced")
    assert approved.fence_version == 1

    claimed = broker_b.acquire_fence(approved.intent_id)
    assert claimed.fence_version == 2
    assert claimed.fence_owner == "broker-b"
    adapter = SimulatedEffectAdapter()
    with pytest.raises(StaleFence):
        broker_a.commit(approved.intent_id, stale_token, adapter)
    assert adapter.physical_apply_count("fenced") == 0

    committed = broker_b.commit(approved.intent_id, claimed.fencing_token, adapter)
    assert committed.state is EffectState.COMMITTED
    assert adapter.physical_apply_count("fenced") == 1


def test_soft_ambiguous_outcome_reconciles_without_second_apply(tmp_path: Path) -> None:
    broker = _broker(tmp_path / "effects.db", "broker-a")
    approved, token = _policy_approved(broker, key="soft-crash")
    adapter = SimulatedEffectAdapter()
    adapter.arm_ambiguous_after_apply("soft-crash")

    with pytest.raises(AmbiguousCommit):
        broker.commit(approved.intent_id, token, adapter)
    assert broker.get(approved.intent_id).state is EffectState.AMBIGUOUS
    assert adapter.physical_apply_count("soft-crash") == 1

    reconciled = broker.commit(approved.intent_id, token, adapter)
    assert reconciled.state is EffectState.COMMITTED
    assert adapter.physical_apply_count("soft-crash") == 1
    assert broker.commit(approved.intent_id, token, adapter) == reconciled
    committed_events = [
        event for event in broker.pending_outbox() if event.event_type == "effect.committed"
    ]
    assert len(committed_events) == 1
    assert committed_events[0].payload["details"] == {
        "reconciled": True,
        "target_replay": False,
    }


def test_hard_crash_rolls_back_sqlite_then_replay_deduplicates_target(tmp_path: Path) -> None:
    database = tmp_path / "effects.db"
    broker_a = _broker(database, "broker-a")
    approved, token = _policy_approved(broker_a, key="hard-crash")
    adapter = SimulatedEffectAdapter()
    adapter.arm_process_crash_after_apply("hard-crash")

    with pytest.raises(SimulatedProcessCrash):
        broker_a.commit(approved.intent_id, token, adapter)
    assert broker_a.get(approved.intent_id).state is EffectState.APPROVED
    assert adapter.physical_apply_count("hard-crash") == 1
    assert not any(
        event.event_type == "effect.committed" for event in broker_a.pending_outbox()
    )

    broker_b = _broker(database, "broker-b")
    recovered = broker_b.acquire_fence(approved.intent_id)
    committed = broker_b.commit(approved.intent_id, recovered.fencing_token, adapter)
    assert committed.state is EffectState.COMMITTED
    assert adapter.physical_apply_count("hard-crash") == 1
    assert len(
        [event for event in broker_b.pending_outbox() if event.event_type == "effect.committed"]
    ) == 1


def test_reversible_compensation_and_replays_are_idempotent(tmp_path: Path) -> None:
    broker = _broker(tmp_path / "effects.db", "broker-a")
    approved, token = _policy_approved(
        broker,
        key="reversible",
        effect_class=EffectClass.REVERSIBLE_WRITE,
    )
    adapter = SimulatedEffectAdapter()
    committed = broker.commit(approved.intent_id, token, adapter)
    assert committed.state is EffectState.COMMITTED

    compensated = broker.compensate(approved.intent_id, token, adapter)
    assert compensated.state is EffectState.COMPENSATED
    assert broker.compensate(approved.intent_id, token, adapter) == compensated
    assert adapter.physical_apply_count("reversible") == 1
    assert adapter.physical_compensation_count("reversible") == 1

    irreversible, irreversible_token = _policy_approved(broker, key="not-reversible")
    broker.commit(irreversible.intent_id, irreversible_token, adapter)
    with pytest.raises(InvalidTransition, match="only reversible"):
        broker.compensate(irreversible.intent_id, irreversible_token, adapter)


def test_transactional_outbox_survives_restart_and_ack_is_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "effects.db"
    broker_a = _broker(database, "broker-a")
    intent = _propose(broker_a, key="outbox")
    event = broker_a.pending_outbox()[0]
    assert event.intent_id == intent.intent_id
    assert event.event_type == "effect.proposed"

    broker_b = _broker(database, "broker-b")
    assert broker_b.pending_outbox()[0].event_id == event.event_id
    broker_b.mark_outbox_published(event.event_id)
    broker_b.mark_outbox_published(event.event_id)
    assert broker_a.pending_outbox() == ()
    with pytest.raises(KeyError):
        broker_a.mark_outbox_published("missing-event")


def test_only_exact_simulated_adapter_type_is_accepted(tmp_path: Path) -> None:
    class PretendExternalAdapter(SimulatedEffectAdapter):
        pass

    broker = _broker(tmp_path / "effects.db", "broker-a")
    approved, token = _policy_approved(broker, key="adapter-boundary")
    with pytest.raises(TypeError, match="simulation-only"):
        broker.commit(approved.intent_id, token, PretendExternalAdapter())
