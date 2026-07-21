# Security policy

Agent Physics is pre-alpha research software. It must not control real emergency response,
healthcare, finance, public communications, or other consequential systems.

## Reporting

Use GitHub's private vulnerability-reporting flow for this repository. If that flow is not yet
available, open a minimal public issue requesting a private contact channel without including
secrets, exploit payloads, operational data, or private information.

Security fixes are best-effort for the current default branch and latest tagged pre-release.
Older snapshots are unsupported.

## Prototype guarantees and non-guarantees

- Graphs and model outputs are treated as untrusted input.
- The validator fails closed for malformed graphs and unsafe declared effects.
- Irreversible declared effects must name an approval gate and idempotency key.
- The SQLite effect kernel implements durable local intent, approval, fencing, outbox, ambiguity,
  and compensation transitions against a simulation-only target. It does not execute a real
  external effect or establish remote exactly-once delivery.
- The prototype does not yet provide process isolation, authenticated multi-tenancy, managed
  secrets, distributed quota/lease coordination, or production-grade authorization.
- A certificate digest is an integrity identifier, not a digital signature or trust anchor.

## Secret hygiene

- Use `.env` locally and commit only `.env.example`.
- Never place API keys in Bob prompts, traces, screenshots, fixtures, or benchmark artifacts.
- Redact prompts and payloads before publishing build evidence.
- Treat retrieved documents and tool responses as untrusted data, not instructions.
