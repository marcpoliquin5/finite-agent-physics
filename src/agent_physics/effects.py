"""Durable effect intents with approval, fencing, and a transactional outbox.

This module deliberately ships only an in-memory simulated adapter. It cannot
perform a network request, mutate a cloud resource, send a message, or charge a
card. The SQLite broker records *authority to attempt* an effect; it is not the
effect itself.

Limitations are explicit:

* SQLite provides single-database durability, not distributed consensus.
* HMAC grants model authenticated authority but are not a human identity system.
* The outbox provides durable at-least-once publication, not exactly-once delivery.
* A production adapter must durably enforce idempotency and fencing at its target.
* Crash ambiguity is simulated. Recovery can only be as strong as an adapter's
  target-side idempotency and status probe.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final, cast

from .contracts import EffectClass


LIMITATIONS: Final[tuple[str, ...]] = (
    "SQLite is a single-database coordination boundary, not distributed consensus.",
    "HMAC approval keys must be replaced or backed by production IAM and key custody.",
    "Transactional outbox delivery is at-least-once; consumers must deduplicate event IDs.",
    "Production targets must durably enforce idempotency and fencing themselves.",
    "The included adapter is simulation-only and never performs an external write.",
)
SQLITE_BUSY_TIMEOUT_MS: Final[int] = 30_000


class EffectKernelError(RuntimeError):
    """Base class for durable effect kernel failures."""


class IntentNotFound(EffectKernelError):
    """The requested effect intent does not exist."""


class InvalidTransition(EffectKernelError):
    """The requested state transition is not legal."""


class IdempotencyConflict(EffectKernelError):
    """An idempotency key was reused for a different effect."""


class StaleFence(EffectKernelError):
    """A broker tried to mutate an intent using stale ownership."""


class ApprovalRequired(EffectKernelError):
    """An irreversible effect lacks a valid external approval grant."""


class InvalidApproval(EffectKernelError):
    """An approval grant is forged, expired, or outside its exact scope."""


class AdapterConflict(EffectKernelError):
    """The simulated target observed inconsistent idempotent operations."""


class AmbiguousCommit(EffectKernelError):
    """The simulated target may have applied an effect before acknowledgement."""


class SimulatedProcessCrash(BaseException):
    """A hard crash injected after target application but before SQLite commit."""


class _SimulatedAmbiguousOutcome(RuntimeError):
    """Internal soft fault injected after target application."""


class EffectState(str, Enum):
    """Persisted effect-intent states."""

    PROPOSED = "proposed"
    PREPARED = "prepared"
    APPROVED = "approved"
    COMMITTED = "committed"
    ABORTED = "aborted"
    AMBIGUOUS = "ambiguous"
    COMPENSATED = "compensated"


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("values must be canonical JSON data") from exc


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def scoped_effect_idempotency_key(
    *,
    run_id: str,
    task_id: str,
    attempt: int,
    declared_key: str | None,
) -> str:
    """Derive the broker/target key while retaining the declared key as audit data."""

    if not run_id or not task_id:
        raise ValueError("run_id and task_id are required for effect idempotency")
    if type(attempt) is not int or attempt <= 0:
        raise ValueError("effect attempt must be a positive integer")
    if declared_key is not None and (type(declared_key) is not str or not declared_key):
        raise ValueError("declared effect idempotency key must be a non-empty string")
    scope = {
        "schema_version": "finite-effect-idempotency/v1",
        "run_id": run_id,
        "task_id": task_id,
        "attempt": attempt,
        "declared_idempotency_key": declared_key,
    }
    return "finite-effect/v1:" + _sha256(_canonical_json(scope))


@dataclass(frozen=True, slots=True)
class FencingToken:
    """Monotonic, broker-bound ownership token for one effect intent."""

    intent_id: str
    version: int
    owner: str


@dataclass(frozen=True, slots=True)
class EffectIntent:
    """Immutable view of one persisted effect intent."""

    intent_id: str
    run_id: str
    action: str
    resource: str
    effect_class: EffectClass
    idempotency_key: str
    payload_json: str
    compensation_action: str | None
    state: EffectState
    fence_version: int
    fence_owner: str | None
    approval_grant_id: str | None
    last_error: str | None
    created_at_ms: int
    updated_at_ms: int

    @property
    def payload(self) -> dict[str, object]:
        return cast(dict[str, object], json.loads(self.payload_json))

    @property
    def effect_digest(self) -> str:
        """Digest binding approval to the immutable proposed operation."""

        return _sha256(
            _canonical_json(
                {
                    "intent_id": self.intent_id,
                    "run_id": self.run_id,
                    "action": self.action,
                    "resource": self.resource,
                    "effect_class": self.effect_class.value,
                    "idempotency_key": self.idempotency_key,
                    "payload": self.payload,
                    "compensation_action": self.compensation_action,
                }
            )
        )

    @property
    def fencing_token(self) -> FencingToken:
        if self.fence_owner is None or self.fence_version <= 0:
            raise InvalidTransition(f"intent {self.intent_id!r} is not fenced")
        return FencingToken(self.intent_id, self.fence_version, self.fence_owner)


@dataclass(frozen=True, slots=True)
class ApprovalGrant:
    """Authenticated approval issued outside the workflow graph.

    The signature binds an approver, exact intent, run, digest, resource, action,
    and validity interval. A graph's declaration that approval is required never
    creates or substitutes for this grant.
    """

    grant_id: str
    key_id: str
    principal: str
    intent_id: str
    run_id: str
    effect_digest: str
    resource: str
    action: str
    not_before_ms: int
    expires_at_ms: int
    signature: str

    def claims(self) -> dict[str, object]:
        return {
            "grant_id": self.grant_id,
            "key_id": self.key_id,
            "principal": self.principal,
            "intent_id": self.intent_id,
            "run_id": self.run_id,
            "effect_digest": self.effect_digest,
            "resource": self.resource,
            "action": self.action,
            "not_before_ms": self.not_before_ms,
            "expires_at_ms": self.expires_at_ms,
        }


class ApprovalAuthority:
    """Small HMAC authority used to model a separately authenticated approver."""

    def __init__(self, key_id: str, secret: bytes) -> None:
        if not key_id:
            raise ValueError("approval key_id cannot be empty")
        if len(secret) < 32:
            raise ValueError("approval secrets must contain at least 32 bytes")
        self.key_id = key_id
        self._secret = bytes(secret)

    def issue(
        self,
        intent: EffectIntent,
        *,
        principal: str,
        now_ms: int,
        ttl_ms: int,
        resource: str | None = None,
        action: str | None = None,
        grant_id: str | None = None,
    ) -> ApprovalGrant:
        """Issue a signed exact-scope grant.

        ``resource`` and ``action`` overrides exist so tests and integrations can
        prove that a correctly signed but wrongly scoped grant is rejected.
        """

        if not principal:
            raise ValueError("approval principal cannot be empty")
        if ttl_ms <= 0:
            raise ValueError("approval ttl_ms must be positive")
        unsigned = ApprovalGrant(
            grant_id=grant_id or str(uuid.uuid4()),
            key_id=self.key_id,
            principal=principal,
            intent_id=intent.intent_id,
            run_id=intent.run_id,
            effect_digest=intent.effect_digest,
            resource=resource if resource is not None else intent.resource,
            action=action if action is not None else intent.action,
            not_before_ms=now_ms,
            expires_at_ms=now_ms + ttl_ms,
            signature="",
        )
        signature = hmac.new(
            self._secret,
            _canonical_json(unsigned.claims()).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return ApprovalGrant(**unsigned.claims(), signature=signature)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    """A durable, at-least-once event written with an intent transition."""

    sequence: int
    event_id: str
    intent_id: str
    event_type: str
    payload_json: str
    created_at_ms: int
    published_at_ms: int | None

    @property
    def payload(self) -> dict[str, object]:
        return cast(dict[str, object], json.loads(self.payload_json))


@dataclass(frozen=True, slots=True)
class SimulatedApplication:
    """One application recorded solely inside the simulated adapter."""

    intent_id: str
    effect_digest: str
    idempotency_key: str
    fence_version: int


class SimulatedEffectAdapter:
    """In-memory target simulator; this class has no external-write mechanism."""

    def __init__(self) -> None:
        self._applications: dict[str, SimulatedApplication] = {}
        self._compensated: set[str] = set()
        self._physical_apply_counts: dict[str, int] = {}
        self._physical_compensation_counts: dict[str, int] = {}
        self._highest_fences: dict[str, int] = {}
        self._soft_crashes: set[str] = set()
        self._hard_crashes: set[str] = set()
        self._lock = threading.RLock()

    def arm_ambiguous_after_apply(self, idempotency_key: str) -> None:
        """Inject one caught failure after simulated target application."""

        with self._lock:
            self._soft_crashes.add(idempotency_key)

    def arm_process_crash_after_apply(self, idempotency_key: str) -> None:
        """Inject one BaseException after application so SQLite rolls back."""

        with self._lock:
            self._hard_crashes.add(idempotency_key)

    def execute(self, intent: EffectIntent, token: FencingToken) -> bool:
        """Apply once in memory; return ``False`` for an idempotent replay."""

        with self._lock:
            highest = self._highest_fences.get(intent.intent_id, 0)
            if token.version < highest:
                raise StaleFence(
                    f"adapter rejected fence {token.version}; observed {highest}"
                )
            self._highest_fences[intent.intent_id] = token.version
            existing = self._applications.get(intent.idempotency_key)
            if existing is not None:
                if (
                    existing.intent_id != intent.intent_id
                    or existing.effect_digest != intent.effect_digest
                ):
                    raise AdapterConflict("target idempotency key maps to another effect")
                return False

            self._applications[intent.idempotency_key] = SimulatedApplication(
                intent_id=intent.intent_id,
                effect_digest=intent.effect_digest,
                idempotency_key=intent.idempotency_key,
                fence_version=token.version,
            )
            self._physical_apply_counts[intent.idempotency_key] = 1
            if intent.idempotency_key in self._hard_crashes:
                self._hard_crashes.remove(intent.idempotency_key)
                raise SimulatedProcessCrash(
                    "simulated process death after target apply and before SQLite commit"
                )
            if intent.idempotency_key in self._soft_crashes:
                self._soft_crashes.remove(intent.idempotency_key)
                raise _SimulatedAmbiguousOutcome("target applied without acknowledgement")
            return True

    def was_applied(self, intent: EffectIntent) -> bool:
        with self._lock:
            existing = self._applications.get(intent.idempotency_key)
            if existing is None:
                return False
            if existing.intent_id != intent.intent_id or existing.effect_digest != intent.effect_digest:
                raise AdapterConflict("target status belongs to another effect")
            return True

    def compensate(self, intent: EffectIntent, token: FencingToken) -> bool:
        """Record an in-memory compensation once; never call an external system."""

        with self._lock:
            if intent.effect_class is not EffectClass.REVERSIBLE_WRITE:
                raise InvalidTransition("only reversible effects can be compensated")
            if not self.was_applied(intent):
                raise InvalidTransition("cannot compensate an effect not observed at target")
            highest = self._highest_fences.get(intent.intent_id, 0)
            if token.version < highest:
                raise StaleFence(
                    f"adapter rejected fence {token.version}; observed {highest}"
                )
            self._highest_fences[intent.intent_id] = token.version
            if intent.idempotency_key in self._compensated:
                return False
            self._compensated.add(intent.idempotency_key)
            self._physical_compensation_counts[intent.idempotency_key] = 1
            return True

    def physical_apply_count(self, idempotency_key: str) -> int:
        with self._lock:
            return self._physical_apply_counts.get(idempotency_key, 0)

    def physical_compensation_count(self, idempotency_key: str) -> int:
        with self._lock:
            return self._physical_compensation_counts.get(idempotency_key, 0)


class SQLiteEffectBroker:
    """SQLite-backed state machine and transactional outbox for effect intents."""

    _WRITES: Final[frozenset[EffectClass]] = frozenset(
        {
            EffectClass.IDEMPOTENT_WRITE,
            EffectClass.REVERSIBLE_WRITE,
            EffectClass.IRREVERSIBLE_WRITE,
        }
    )

    def __init__(
        self,
        database_path: str | Path,
        *,
        broker_id: str | None = None,
        trusted_approval_keys: Mapping[str, bytes] | None = None,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self.database_path = str(database_path)
        if self.database_path == ":memory:":
            raise ValueError("a durable broker requires a filesystem SQLite database")
        self.broker_id = broker_id or str(uuid.uuid4())
        if not self.broker_id:
            raise ValueError("broker_id cannot be empty")
        self._trusted_keys = {
            key_id: bytes(secret) for key_id, secret in (trusted_approval_keys or {}).items()
        }
        if any(not key_id or len(secret) < 32 for key_id, secret in self._trusted_keys.items()):
            raise ValueError("trusted approval keys require names and at least 32 bytes")
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=SQLITE_BUSY_TIMEOUT_MS / 1_000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
        return connection

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS effect_intents (
                    intent_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    resource TEXT NOT NULL,
                    effect_class TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    compensation_action TEXT,
                    state TEXT NOT NULL,
                    fence_version INTEGER NOT NULL DEFAULT 0,
                    fence_owner TEXT,
                    approval_grant_id TEXT,
                    last_error TEXT,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS approval_grants (
                    grant_id TEXT PRIMARY KEY,
                    key_id TEXT NOT NULL,
                    principal TEXT NOT NULL,
                    intent_id TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL,
                    effect_digest TEXT NOT NULL,
                    resource TEXT NOT NULL,
                    action TEXT NOT NULL,
                    not_before_ms INTEGER NOT NULL,
                    expires_at_ms INTEGER NOT NULL,
                    signature TEXT NOT NULL,
                    FOREIGN KEY (intent_id) REFERENCES effect_intents(intent_id)
                );

                CREATE TABLE IF NOT EXISTS effect_outbox (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    intent_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL,
                    published_at_ms INTEGER,
                    FOREIGN KEY (intent_id) REFERENCES effect_intents(intent_id)
                );
                CREATE INDEX IF NOT EXISTS idx_effect_outbox_pending
                    ON effect_outbox(published_at_ms, sequence);
                """
            )
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _intent_from_row(row: sqlite3.Row) -> EffectIntent:
        return EffectIntent(
            intent_id=row["intent_id"],
            run_id=row["run_id"],
            action=row["action"],
            resource=row["resource"],
            effect_class=EffectClass(row["effect_class"]),
            idempotency_key=row["idempotency_key"],
            payload_json=row["payload_json"],
            compensation_action=row["compensation_action"],
            state=EffectState(row["state"]),
            fence_version=row["fence_version"],
            fence_owner=row["fence_owner"],
            approval_grant_id=row["approval_grant_id"],
            last_error=row["last_error"],
            created_at_ms=row["created_at_ms"],
            updated_at_ms=row["updated_at_ms"],
        )

    @staticmethod
    def _grant_from_row(row: sqlite3.Row) -> ApprovalGrant:
        return ApprovalGrant(
            grant_id=row["grant_id"],
            key_id=row["key_id"],
            principal=row["principal"],
            intent_id=row["intent_id"],
            run_id=row["run_id"],
            effect_digest=row["effect_digest"],
            resource=row["resource"],
            action=row["action"],
            not_before_ms=row["not_before_ms"],
            expires_at_ms=row["expires_at_ms"],
            signature=row["signature"],
        )

    def _load(self, connection: sqlite3.Connection, intent_id: str) -> EffectIntent:
        row = connection.execute(
            "SELECT * FROM effect_intents WHERE intent_id = ?", (intent_id,)
        ).fetchone()
        if row is None:
            raise IntentNotFound(intent_id)
        return self._intent_from_row(row)

    def _enqueue(
        self,
        connection: sqlite3.Connection,
        intent: EffectIntent,
        event_type: str,
        details: Mapping[str, object] | None = None,
    ) -> None:
        payload = {
            "intent_id": intent.intent_id,
            "run_id": intent.run_id,
            "state": intent.state.value,
            "fence_version": intent.fence_version,
            "details": dict(details or {}),
        }
        connection.execute(
            """
            INSERT INTO effect_outbox (
                event_id, intent_id, event_type, payload_json, created_at_ms
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                intent.intent_id,
                event_type,
                _canonical_json(payload),
                self._clock_ms(),
            ),
        )

    def _set_state(
        self,
        connection: sqlite3.Connection,
        intent_id: str,
        state: EffectState,
        *,
        approval_grant_id: str | None = None,
        last_error: str | None = None,
    ) -> EffectIntent:
        connection.execute(
            """
            UPDATE effect_intents
            SET state = ?, approval_grant_id = COALESCE(?, approval_grant_id),
                last_error = ?, updated_at_ms = ?
            WHERE intent_id = ?
            """,
            (state.value, approval_grant_id, last_error, self._clock_ms(), intent_id),
        )
        return self._load(connection, intent_id)

    @staticmethod
    def _assert_fence(intent: EffectIntent, token: FencingToken) -> None:
        if (
            token.intent_id != intent.intent_id
            or token.version != intent.fence_version
            or token.owner != intent.fence_owner
        ):
            raise StaleFence(
                f"stale fence for {intent.intent_id!r}: expected "
                f"{intent.fence_owner}/{intent.fence_version}"
            )

    def get(self, intent_id: str) -> EffectIntent:
        connection = self._connect()
        try:
            return self._load(connection, intent_id)
        finally:
            connection.close()

    def propose(
        self,
        *,
        run_id: str,
        action: str,
        resource: str,
        effect_class: EffectClass,
        idempotency_key: str,
        payload: Mapping[str, object],
        compensation_action: str | None = None,
        intent_id: str | None = None,
    ) -> EffectIntent:
        """Persist a globally idempotent effect proposal.

        An exact replay returns the original intent even from another run. Reusing
        the key for any different immutable operation is a hard conflict.
        """

        if not run_id or not action or not resource or not idempotency_key:
            raise ValueError("run_id, action, resource, and idempotency_key are required")
        if effect_class not in self._WRITES:
            raise ValueError("the durable effect kernel accepts write effects only")
        if effect_class is EffectClass.REVERSIBLE_WRITE and not compensation_action:
            raise ValueError("reversible effects require a compensation action")
        payload_json = _canonical_json(dict(payload))
        new_id = intent_id or str(uuid.uuid4())
        now_ms = self._clock_ms()
        with self._transaction() as connection:
            existing_row = connection.execute(
                "SELECT * FROM effect_intents WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing_row is not None:
                existing = self._intent_from_row(existing_row)
                same_operation = (
                    existing.action == action
                    and existing.resource == resource
                    and existing.effect_class is effect_class
                    and existing.payload_json == payload_json
                    and existing.compensation_action == compensation_action
                )
                if not same_operation:
                    raise IdempotencyConflict(
                        f"idempotency key {idempotency_key!r} maps to another operation"
                    )
                return existing
            connection.execute(
                """
                INSERT INTO effect_intents (
                    intent_id, run_id, action, resource, effect_class,
                    idempotency_key, payload_json, compensation_action, state,
                    created_at_ms, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id,
                    run_id,
                    action,
                    resource,
                    effect_class.value,
                    idempotency_key,
                    payload_json,
                    compensation_action,
                    EffectState.PROPOSED.value,
                    now_ms,
                    now_ms,
                ),
            )
            intent = self._load(connection, new_id)
            self._enqueue(connection, intent, "effect.proposed")
            return intent

    def prepare(self, intent_id: str) -> EffectIntent:
        """Move a proposal to PREPARED and issue the first fencing token."""

        with self._transaction() as connection:
            intent = self._load(connection, intent_id)
            if intent.state is not EffectState.PROPOSED:
                raise InvalidTransition(
                    f"cannot prepare {intent.intent_id!r} from {intent.state.value}"
                )
            connection.execute(
                """
                UPDATE effect_intents
                SET state = ?, fence_version = fence_version + 1,
                    fence_owner = ?, updated_at_ms = ?
                WHERE intent_id = ?
                """,
                (
                    EffectState.PREPARED.value,
                    self.broker_id,
                    self._clock_ms(),
                    intent_id,
                ),
            )
            prepared = self._load(connection, intent_id)
            self._enqueue(connection, prepared, "effect.prepared")
            return prepared

    def acquire_fence(self, intent_id: str) -> EffectIntent:
        """Transfer unfinished intent ownership to this broker with a higher token."""

        with self._transaction() as connection:
            intent = self._load(connection, intent_id)
            if intent.state not in {
                EffectState.PREPARED,
                EffectState.APPROVED,
                EffectState.AMBIGUOUS,
            }:
                raise InvalidTransition(
                    f"cannot fence {intent.intent_id!r} from {intent.state.value}"
                )
            connection.execute(
                """
                UPDATE effect_intents
                SET fence_version = fence_version + 1, fence_owner = ?, updated_at_ms = ?
                WHERE intent_id = ?
                """,
                (self.broker_id, self._clock_ms(), intent_id),
            )
            fenced = self._load(connection, intent_id)
            self._enqueue(
                connection,
                fenced,
                "effect.fence_acquired",
                {"owner": self.broker_id},
            )
            return fenced

    def _verify_grant(
        self,
        intent: EffectIntent,
        grant: ApprovalGrant,
        *,
        enforce_time: bool,
    ) -> None:
        secret = self._trusted_keys.get(grant.key_id)
        if secret is None:
            raise InvalidApproval(f"untrusted approval key {grant.key_id!r}")
        expected = hmac.new(
            secret,
            _canonical_json(grant.claims()).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, grant.signature):
            raise InvalidApproval("approval signature authentication failed")
        if not grant.principal:
            raise InvalidApproval("approval principal cannot be empty")
        exact_scope = (
            grant.intent_id == intent.intent_id
            and grant.run_id == intent.run_id
            and grant.effect_digest == intent.effect_digest
            and grant.resource == intent.resource
            and grant.action == intent.action
        )
        if not exact_scope:
            raise InvalidApproval("approval grant does not match the exact effect scope")
        now_ms = self._clock_ms()
        if grant.expires_at_ms <= grant.not_before_ms:
            raise InvalidApproval("approval validity interval is empty")
        if enforce_time and not grant.not_before_ms <= now_ms < grant.expires_at_ms:
            raise InvalidApproval("approval grant is not currently valid")

    def _stored_grant(
        self,
        connection: sqlite3.Connection,
        intent: EffectIntent,
    ) -> ApprovalGrant:
        if intent.approval_grant_id is None:
            raise ApprovalRequired("irreversible effect has no stored approval grant")
        row = connection.execute(
            "SELECT * FROM approval_grants WHERE grant_id = ?",
            (intent.approval_grant_id,),
        ).fetchone()
        if row is None:
            raise ApprovalRequired("stored approval grant is missing")
        return self._grant_from_row(row)

    def approve(
        self,
        intent_id: str,
        token: FencingToken,
        grant: ApprovalGrant | None = None,
    ) -> EffectIntent:
        """Advance PREPARED to APPROVED; irreversible writes need a real grant."""

        with self._transaction() as connection:
            intent = self._load(connection, intent_id)
            if intent.state in {
                EffectState.APPROVED,
                EffectState.AMBIGUOUS,
                EffectState.COMMITTED,
                EffectState.COMPENSATED,
            }:
                if intent.effect_class is EffectClass.IRREVERSIBLE_WRITE and (
                    grant is None or grant.grant_id != intent.approval_grant_id
                ):
                    raise InvalidApproval("approval replay does not match the stored grant")
                return intent
            if intent.state is not EffectState.PREPARED:
                raise InvalidTransition(
                    f"cannot approve {intent.intent_id!r} from {intent.state.value}"
                )
            self._assert_fence(intent, token)
            grant_id: str | None = None
            if intent.effect_class is EffectClass.IRREVERSIBLE_WRITE:
                if grant is None:
                    raise ApprovalRequired("irreversible effects require an approval grant")
                self._verify_grant(intent, grant, enforce_time=True)
                try:
                    connection.execute(
                        """
                        INSERT INTO approval_grants (
                            grant_id, key_id, principal, intent_id, run_id,
                            effect_digest, resource, action, not_before_ms,
                            expires_at_ms, signature
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            grant.grant_id,
                            grant.key_id,
                            grant.principal,
                            grant.intent_id,
                            grant.run_id,
                            grant.effect_digest,
                            grant.resource,
                            grant.action,
                            grant.not_before_ms,
                            grant.expires_at_ms,
                            grant.signature,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise InvalidApproval("approval grant was already consumed") from exc
                grant_id = grant.grant_id
            elif grant is not None:
                raise InvalidApproval("non-irreversible effects use policy approval, not grants")
            approved = self._set_state(
                connection,
                intent_id,
                EffectState.APPROVED,
                approval_grant_id=grant_id,
            )
            self._enqueue(
                connection,
                approved,
                "effect.approved",
                {"approval_grant_id": grant_id, "approval_kind": "grant" if grant else "policy"},
            )
            return approved

    @staticmethod
    def _require_simulator(adapter: SimulatedEffectAdapter) -> None:
        if type(adapter) is not SimulatedEffectAdapter:
            raise TypeError("only the built-in simulation-only adapter is accepted")

    def commit(
        self,
        intent_id: str,
        token: FencingToken,
        adapter: SimulatedEffectAdapter,
    ) -> EffectIntent:
        """Commit once or reconcile an ambiguous simulated target outcome."""

        self._require_simulator(adapter)
        ambiguity: AmbiguousCommit | None = None
        result: EffectIntent
        with self._transaction() as connection:
            intent = self._load(connection, intent_id)
            if intent.state in {EffectState.COMMITTED, EffectState.COMPENSATED}:
                return intent
            if intent.state not in {EffectState.APPROVED, EffectState.AMBIGUOUS}:
                raise InvalidTransition(
                    f"cannot commit {intent.intent_id!r} from {intent.state.value}"
                )
            self._assert_fence(intent, token)

            already_applied = intent.state is EffectState.AMBIGUOUS and adapter.was_applied(intent)
            if intent.effect_class is EffectClass.IRREVERSIBLE_WRITE:
                stored = self._stored_grant(connection, intent)
                self._verify_grant(intent, stored, enforce_time=not already_applied)

            if already_applied:
                result = self._set_state(
                    connection, intent_id, EffectState.COMMITTED, last_error=None
                )
                self._enqueue(
                    connection,
                    result,
                    "effect.committed",
                    {"reconciled": True, "target_replay": False},
                )
            else:
                try:
                    applied_now = adapter.execute(intent, token)
                except _SimulatedAmbiguousOutcome as exc:
                    result = self._set_state(
                        connection,
                        intent_id,
                        EffectState.AMBIGUOUS,
                        last_error=str(exc),
                    )
                    self._enqueue(
                        connection,
                        result,
                        "effect.ambiguous",
                        {"reason": str(exc)},
                    )
                    ambiguity = AmbiguousCommit(str(exc))
                else:
                    result = self._set_state(
                        connection, intent_id, EffectState.COMMITTED, last_error=None
                    )
                    self._enqueue(
                        connection,
                        result,
                        "effect.committed",
                        {"reconciled": not applied_now, "target_replay": not applied_now},
                    )
        if ambiguity is not None:
            raise ambiguity
        return result

    def compensate(
        self,
        intent_id: str,
        token: FencingToken,
        adapter: SimulatedEffectAdapter,
    ) -> EffectIntent:
        """Compensate a committed or target-confirmed ambiguous reversible write."""

        self._require_simulator(adapter)
        with self._transaction() as connection:
            intent = self._load(connection, intent_id)
            if intent.state is EffectState.COMPENSATED:
                return intent
            if intent.state not in {EffectState.COMMITTED, EffectState.AMBIGUOUS}:
                raise InvalidTransition(
                    f"cannot compensate {intent.intent_id!r} from {intent.state.value}"
                )
            self._assert_fence(intent, token)
            if intent.effect_class is not EffectClass.REVERSIBLE_WRITE:
                raise InvalidTransition("only reversible effects can be compensated")
            if not intent.compensation_action:
                raise InvalidTransition("reversible effect lacks a compensation action")
            adapter.compensate(intent, token)
            compensated = self._set_state(
                connection, intent_id, EffectState.COMPENSATED, last_error=None
            )
            self._enqueue(
                connection,
                compensated,
                "effect.compensated",
                {"compensation_action": intent.compensation_action},
            )
            return compensated

    def abort(self, intent_id: str, token: FencingToken | None = None) -> EffectIntent:
        """Abort before target application; ambiguous or committed effects cannot abort."""

        with self._transaction() as connection:
            intent = self._load(connection, intent_id)
            if intent.state is EffectState.ABORTED:
                return intent
            if intent.state not in {
                EffectState.PROPOSED,
                EffectState.PREPARED,
                EffectState.APPROVED,
            }:
                raise InvalidTransition(
                    f"cannot abort {intent.intent_id!r} from {intent.state.value}"
                )
            if intent.state is not EffectState.PROPOSED:
                if token is None:
                    raise StaleFence("prepared effects require a fencing token to abort")
                self._assert_fence(intent, token)
            aborted = self._set_state(connection, intent_id, EffectState.ABORTED)
            self._enqueue(connection, aborted, "effect.aborted")
            return aborted

    def pending_outbox(self, *, limit: int = 100) -> tuple[OutboxEvent, ...]:
        """Read pending outbox messages in stable commit order."""

        if limit <= 0:
            raise ValueError("outbox limit must be positive")
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT * FROM effect_outbox
                WHERE published_at_ms IS NULL
                ORDER BY sequence
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return tuple(
                OutboxEvent(
                    sequence=row["sequence"],
                    event_id=row["event_id"],
                    intent_id=row["intent_id"],
                    event_type=row["event_type"],
                    payload_json=row["payload_json"],
                    created_at_ms=row["created_at_ms"],
                    published_at_ms=row["published_at_ms"],
                )
                for row in rows
            )
        finally:
            connection.close()

    def mark_outbox_published(self, event_id: str) -> None:
        """Idempotently acknowledge a published outbox event."""

        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE effect_outbox
                SET published_at_ms = ?
                WHERE event_id = ? AND published_at_ms IS NULL
                """,
                (self._clock_ms(), event_id),
            )
            if cursor.rowcount == 0:
                exists = connection.execute(
                    "SELECT 1 FROM effect_outbox WHERE event_id = ?", (event_id,)
                ).fetchone()
                if exists is None:
                    raise KeyError(event_id)
