# Durable effect kernel

The implemented effect kernel separates an agent's proposed action from authority to commit
it. Every write becomes a SQLite-backed `EffectIntent` with an explicit lifecycle:

```text
PROPOSED -> PREPARED -> APPROVED -> COMMITTED
    |           |          |
    +---------> ABORTED <---+

APPROVED -> AMBIGUOUS -> COMMITTED
COMMITTED -> COMPENSATED   (reversible effects only)
```

## Implemented safety properties

- An irreversible effect needs a separately issued, HMAC-authenticated, exact-scope,
  expiring approval grant.
- A graph declaration cannot mint that grant.
- The SQLite schema enforces one global idempotency key per immutable operation.
- Broker ownership uses a monotonic fencing token; stale brokers cannot settle an intent.
- State transitions and outbox events commit in one SQLite transaction.
- Approval, commit, compensation, and outbox acknowledgment replays are idempotent.
- Injected post-apply crashes reconcile through target-side idempotency without a second
  simulated physical application.

## Boundary

The repository accepts only the exact built-in `SimulatedEffectAdapter`; subclasses and
external adapters are rejected. Therefore the tests prove the local state machine and the
simulated target contract, not remote exactly-once delivery. SQLite is one-database
coordination, HMAC is a model of separately authenticated authority rather than human IAM,
and outbox delivery is at least once. A production target must enforce idempotency and
fencing durably itself.

The current Miami EOC workflow never publishes a real alert.
