# IBM Bob session runbook

This is a launch plan, not evidence. Only actions actually performed in IBM Bob may be copied into
`bob-build-log.md`. The objective is material Bob ownership of planning, implementation, testing,
and release review - not a branding trail.

## Before the first session

1. Install the repository in the Python environment Bob launches:
   `python -m pip install -e ".[dev,mcp,api]"`.
2. Open this repository as the Bob workspace and review `.bob/mcp.json` plus Bob's MCP panel.
3. Confirm `finite-agent-physics` starts and exposes 23 tools with none globally auto-approved.
4. Record the starting branch and exact commit.
5. Capture timestamped screen recording or screenshots showing Bob, prompts, tool calls, changed
   files, and verification output; keep account/personal evidence private until redacted.
6. Use a fresh `bob/` branch or clearly separated Bob commits. Never relabel Codex commits as Bob.

## B1 - Adversarial invariant audit

Give Bob this task:

> Read `docs/V5_RELEASE_CONTRACT.md`, `docs/limitations.md`, and the compiler, executor, adaptive
> runtime, physical admission, artifact, verifier, and effect code with their tests. Find one
> concrete counterexample where a hard promise, identity, restart invariant, settlement rule, or
> evidence label can be violated. Reproduce it with a failing test, implement the narrowest correct
> fix, and run focused plus full verification. Do not weaken a contract or rename estimated,
> fixture, simulated, or private evidence as measured/live/public.

Acceptance:

- Bob finds the issue from code rather than being handed a predetermined patch.
- A failing regression test demonstrates the issue before the accepted fix.
- Human review records any rejected or modified Bob suggestion.
- The accepted Bob commit names the invariant and exact tests.

## B2 - Bob calls FINITE as an orchestration client

Ask Bob to use MCP, not pasted terminal output:

> Call `finite_capabilities`. Call `finite_preflight` with the default envelope and with
> `max_tokens=1`; confirm the refusal makes zero external calls. Start the bundled fixture with
> `finite_run`. Preserve its run ID, poll `finite_status`, call `finite_explain_run`, and finish with
> `finite_verify_run` for the same ID. Then call the physical-admission, adaptive-recovery,
> production-survival, framework-conformance, and artifact-integrity drills. Explain every
> simulation, fixture, live, estimated, unsupported, and not-executed label. Do not approve or
> commit an external effect.

Acceptance:

- Genuine Bob UI shows tool names and returned run/digest values.
- One run ID connects run, status, explanation, and verification.
- Bob correctly identifies `awaiting_effects`, `PROPOSED`, energy unsupported, and PageAgent not
  executed/non-equivalent.
- No tool is globally auto-approved merely to simplify recording.

## B3 - Live Granite/watsonx run

Run only with entrant-owned watsonx credentials and an available Granite model. Never paste a key
into Bob prompts, source, screenshots, terminal history intended for publication, or logs.

> Review `watsonx.py`, `watsonx_worker.py`, `bob_lifecycle.py`, executor reservations, and their
> tests. Call `finite_granite_preflight`. If it passes, start one bounded Granite-backed fictional
> task through `finite_run`, then call status, explain, and verify for the same run. Check that SDK
> retries are zero, provider tokens settle inside the admitted reservation, output validation gates
> completion, the receipt is redacted and labeled `live-watsonx`, and resume does not recall the
> model. Find and fix any real defect before accepting the evidence.

Acceptance:

- Credentials remain environment-only and do not appear in Git or evidence.
- The exact Granite model ID and provider usage are present in a reviewed redacted receipt.
- Missing usage, model mismatch, validation failure, or late settlement fails closed.
- Bob contributes material review/code/test work, not only the call itself.

## B4 - Console accessibility and recovery

> Run the API and Physics Console. Use keyboard-only navigation and a screen reader or accessibility
> tree. Verify focus visibility/order, landmarks, labels, status announcements, contrast,
> reduced-motion behavior, 200% zoom/reflow, exact-origin errors, invalid-token recovery, stream
> reconnection, and the distinction between sealed replay and live data. Reproduce one concrete
> issue, fix it, and extend the rendered or browser-level test. Do not move kernel logic into the
> frontend.

Acceptance:

- Bob changes/tests a meaningful accessibility or error-recovery issue.
- Runtime data remains authoritative; frontend code does not recalculate evidence claims.
- Before/after evidence and human review are retained.

## B5 - Fair-comparison red team

> Audit the preregistered comparison contract and runners. Confirm FINITE, plain Python, and the
> exact-pinned LangGraph witness use one workload/payload/validator fingerprint, identical warmup
> and measured seeds, common refusal/effect guardrails, and failures in denominators. Verify Wilson
> and paired-bootstrap summaries from raw records. Confirm PageAgent is hard-coded as not executed
> and cannot acquire a metric. Add a regression for any defect and publish negative or baseline
> wins. Do not create a superiority headline unless the registered threshold passes.

Acceptance:

- Exact versions, environment, seeds, and raw records are bound into evidence.
- Conversion loss or unavailable dependencies fail visibly.
- Bob records negative/null results as readily as FINITE wins.

## B6 - Exact-commit release audit

> Generate and verify the release candidate from a clean commit. Inspect wheel/sdist contents,
> entry points, metadata, checksums, SBOM, provenance, secret scan, dependency audits, license,
> console asset digest, benchmark raw data, and external-evidence placeholders. Perform a fresh
> anonymous clone and the documented judge path. Map every README/project-page sentence to local,
> live, public, or human evidence. Block stable `v5.0.0` for any stale, private, absent, or
> unsupported required record.

Acceptance:

- Bob produces a blocking/non-blocking report and fixes at least one confirmed release issue.
- The final Bob session references the exact candidate commit and evidence digest.
- The entrant independently checks public links in a signed-out browser.

## Build-log discipline

For every accepted session, copy the template in `bob-build-log.md` and fill it only from actual
artifacts. List exact files, tests, run ID, digests, human corrections, and accepted commit. A
session with no material accepted contribution can still be logged honestly, but it does not by
itself satisfy the core-development requirement.
