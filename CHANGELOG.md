# Changelog

All notable project changes will be documented here. The project follows semantic-versioning
intent. `v5.0.0-rc.1` is a release-candidate label and `5.0.0rc1` is its Python distribution
version; neither is a stable `v5.0.0` declaration.

## v5.0.0-rc.1 candidate (unreleased)

### Added

- Strict workflow v2 contracts with typed artifact ports, physical-resource units, and an adapter
  capability ABI that fails before dispatch when required semantics are absent.
- Logical and physical admission, including CPU, RAM/VRAM, storage, network, bandwidth, RTT,
  egress, overflow checks, and an explicit unsupported-energy boundary.
- Integer resource and provider-quota ledgers, residual replanning, and an integrated adaptive
  crash/restart drill with conservative unknown-use accounting and call-free replay.
- Durable SQLite run/effect/artifact state, restart-safe content-addressed deduplication,
  attempt-linked provenance, and an independent sealed whole-run verifier.
- Bounded semantic-safety checks and a deterministic adversarial corpus covering controlled
  citations, bilingual facts, freshness, URL policy, authority separation, and static
  accessibility declarations.
- Twenty-two local IBM Bob-callable MCP tools, the durable preflight/run/status/explain/verify
  lifecycle, and a bounded watsonx worker seam. No genuine Bob or live-watsonx receipt is implied.
- Bearer-protected local REST submit/status/inspect/cancel/approve routes and resumable SSE.
- Exact neutral framework round trips, explicit LangGraph semantic-loss accounting, a conditional
  pinned LangGraph witness, and a metric-free PageAgent `not-executed` benchmark row.
- Deterministic release-candidate generation and offline verification with wheel/sdist inspection,
  checksums, SBOM, unsigned provenance, and cross-platform CI gates.

### Security

- Fail-closed contract parsing, strict evidence schemas, approval-gated irreversible effects,
  fencing, idempotency, exact CORS/auth checks, taint separation, and adversarial replay tests.

### Known release blockers

- Genuine IBM Bob evidence, a same-run live Granite receipt, public GitHub commit/tag/CI evidence,
  anonymous or explicitly judge-shared hosting, eligibility/SkillsBuild evidence, a verified
  three-minute video, and the final submission receipt are not complete.
- The current Sites URL is owner-only, and no public API deployment has been verified.
- Stable `v5.0.0` remains blocked until every release-manifest gate passes.
