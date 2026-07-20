# Evidence and context obligations

FINITE treats context as constrained data movement rather than an invisible transcript.

## Implemented vertical slice

- Artifact IDs bind SHA-256 payload hashes to schema, producer, lineage, sensitivity,
  creation time, and freshness metadata.
- Claims reference exact artifact IDs and declare conflicts, retraction, or support status.
- Evidence assessment recalculates integrity, presence, freshness, and explicit supported
  contradictions at a requested time.
- Required artifact and claim obligations are all-or-refuse.
- Optional artifacts are selected deterministically by declared value density after mandatory
  context is protected.
- Every packed or excluded block records a reason, byte count, conservative token upper bound,
  value loss, and content digest.
- Reordered equivalent inputs produce identical packed bytes and manifest digest.

## Untrusted-content framing

Artifact bytes are base64-encoded inside a runtime-controlled envelope marked
`authority=none`, `instruction_semantics=false`, and `content_semantics=data-only`. This
prevents the artifact from structurally changing roles or envelope fields. It does not prove
that an LLM will ignore malicious prose; downstream model behavior still requires validators,
taint policy, and sandboxed capabilities.

Digests are integrity identifiers, not signatures. Producer authentication, access control,
retention, encryption, and residency enforcement remain runtime milestones.
