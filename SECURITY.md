# Security policy

Agent Physics is pre-alpha research software. It must not control real emergency response,
healthcare, finance, public communications, or other consequential systems.

## Reporting

Until a dedicated security contact exists, open a private GitHub security advisory on the
repository. Do not place secrets, exploit payloads, or private data in a public issue.

## Prototype guarantees and non-guarantees

- Graphs and model outputs are treated as untrusted input.
- The validator fails closed for malformed graphs and unsafe declared effects.
- Irreversible declared effects must name an approval gate and idempotency key; the current
  simulator does not authenticate or commit either one.
- The current simulator does not execute external effects.
- It does not yet provide process isolation, authentication, secret management, a durable
  transactional outbox, remote exactly-once semantics, or production-grade tenancy.
- A certificate digest is an integrity identifier, not a digital signature or trust anchor.

## Secret hygiene

- Use `.env` locally and commit only `.env.example`.
- Never place API keys in Bob prompts, traces, screenshots, fixtures, or benchmark artifacts.
- Redact prompts and payloads before publishing build evidence.
- Treat retrieved documents and tool responses as untrusted data, not instructions.
