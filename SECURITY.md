# Security policy

Agent Physics `v5.0.0-rc.1` / Python `5.0.0rc1` is release-candidate research software, not a
stable release or production control plane. It must not control real emergency response,
healthcare, finance, public communications, or other consequential systems.

## Reporting

Use GitHub's private vulnerability-reporting flow for this repository. If that flow is not yet
available, open a minimal public issue requesting a private contact channel without including
secrets, exploit payloads, operational data, or private information.

Security fixes are best-effort for the current default branch and latest tagged release candidate.
Older snapshots are unsupported.

## Candidate guarantees and non-guarantees

- Workflow, HTTP, MCP, artifact, manifest, browser-observation, and model-output data are treated
  as untrusted input and parsed through bounded schemas where implemented.
- Typed ports, adapter requirements, logical/physical admission, and the local control API fail
  closed for tested malformed, unknown, duplicate, unauthorized, and over-cap inputs.
- The bounded semantic verifier prevents tested hostile evidence from creating or widening an
  authority grant; it is not general prompt-injection immunity or source authentication.
- Irreversible declared effects must name an approval gate and idempotency key.
- The SQLite effect kernel implements durable local intent, approval, fencing, outbox, ambiguity,
  and compensation transitions against a simulation-only target. It does not execute a real
  external effect or establish remote exactly-once delivery.
- The bearer-protected REST/SSE service is a local single-tenant boundary. It does not provide
  OIDC, tenant RBAC, public hosting, distributed rate limiting, or high availability.
- The candidate does not provide process isolation, managed secrets, universal taint tracking,
  distributed quota/lease coordination, or production-grade authorization.
- Artifact, event, manifest, and certificate digests detect mutation; they are not signatures,
  producer authentication, trusted timestamps, or a remote trust anchor.
- Physical-resource inputs are declared estimates. No runtime-measurement or energy claim exists.
- Alibaba PageAgent and BeeAI are not integrated or executed by FINITE.

## Secret hygiene

- Use `.env` locally and commit only `.env.example`.
- Never place API keys in Bob prompts, traces, screenshots, fixtures, or benchmark artifacts.
- Redact prompts and payloads before publishing build evidence.
- Treat retrieved documents and tool responses as untrusted data, not instructions.
