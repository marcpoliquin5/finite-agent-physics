# IBM Bob primary-development workstream

The challenge requires IBM Bob to be a core development tool. Architecture, code, and tests
created elsewhere do not remove that obligation. The entrant must perform and record real Bob
sessions that materially shape the submitted system.

The current release candidate already contains the kernel, durable runtime, control API, console,
adapter seam, verifier, and release tooling. Bob work should therefore find and improve real
remaining risk rather than reimplementing completed components or adding ceremonial line count.

Copy-ready prompts and evidence rules are in [`bob-session-runbook.md`](bob-session-runbook.md).

## Bob-owned v5 work packages

1. **B1 - Adversarial invariant audit.** Find a genuine counterexample in compiler, admission,
   adaptive recovery, settlement, artifact lineage, or effect authority; add the failing test and
   the accepted correction.
2. **B2 - Same-run MCP lifecycle.** Invoke capability, preflight, run, status, explain, and verify
   through Bob for one exact release-candidate run ID and interpret every evidence label correctly.
3. **B3 - Live Granite integration review.** Review the bounded watsonx worker, run one real Granite
   attempt, harden any discovered defect, and preserve a redacted same-run receipt.
4. **B4 - Accessibility and failure recovery.** Exercise the live console as a keyboard and
   screen-reader user, trigger a real client/API error, and contribute at least one accepted fix or
   regression test.
5. **B5 - Benchmark red-team.** Audit identical-work fingerprints, warmup separation, failures in
   denominators, interval calculation, framework conversion loss, and PageAgent non-equivalence;
   repair a real issue or record a defensible negative result.
6. **B6 - Release audit.** Verify package contents, clean-clone commands, claims, public links,
   checksums, SBOM/provenance, secrets, and submission artifacts at the exact candidate commit.

## Minimum evidence for an accepted package

- timestamp, time zone, Bob version, and session/screenshot reference;
- starting branch and commit;
- exact prompt and Bob's plan;
- files Bob created or changed and why;
- the defect, counterexample, or meaningful judgment Bob supplied;
- human corrections and rejected suggestions;
- focused and full tests with outcomes;
- accepted commit SHA;
- MCP tool/run/digest evidence when applicable; and
- a concise explanation of why the contribution was substantive.

A session that only reads files, repeats an existing test, or restates this workstream may be logged
honestly but does not satisfy the core-development requirement.
