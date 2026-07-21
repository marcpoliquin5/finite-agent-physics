# IBM Bob session runbook

This is a launch plan, not evidence. Only actions actually performed in IBM Bob may be copied
into `bob-build-log.md`. The objective is to give Bob material ownership of planning,
implementation, testing, and iteration before submission—not to manufacture a branding trail.

## Before the first session

1. Install the repository in the Python environment Bob will launch: `pip install -e ".[dev]"`.
2. Open this repository as the Bob workspace and review `.bob/mcp.json` plus Bob's MCP panel.
3. Confirm the `finite-agent-physics` server starts with no auto-approved tools.
4. Start a screen recording or capture timestamped screenshots that show Bob, the prompt, tool
   calls, changed files, and verification output.
5. Use a fresh `bob/` Git branch or clearly identify Bob's commits; do not rewrite Codex commits
   as Bob work.

## B1 — Constraint invariant audit and accepted correction

Give Bob this task:

> Read `PROGRAM.md`, `docs/limitations.md`, `src/agent_physics/scheduler.py`,
> `src/agent_physics/executor.py`, and the associated tests. Use the project
> `constraint-review` skill. Find at least one concrete counterexample where a documented hard
> promise, restart invariant, or explanation can be violated or mislabeled. Reproduce it with a
> failing test, implement the narrowest correct fix, and run focused plus full verification. Do
> not weaken a contract or rename modeled evidence as measured evidence.

Acceptance:

- Bob identifies the issue from code rather than being handed a predetermined patch.
- A failing regression test precedes the accepted correction.
- Human review records rejected or modified Bob suggestions.
- The accepted Bob commit is separate and references exact tests.

## B2 — Bob calls FINITE as an orchestration client

Ask Bob to use MCP, not a pasted terminal result:

> Call `finite_capabilities`. Then call `finite_preflight` with the default envelope and with
> `max_tokens=1`. Call `finite_executor_drill`, `finite_stormshift_validate` once nominally and
> once with `stale-artifact`, `finite_quota_corpus`, `finite_replanning_drill`,
> `finite_decision_explanation_drill` in refused mode, `finite_fault_experiment` with revision
> `bob-evidence-v1`, and `finite_verify`. Compare the returned measurement labels, run/effect
> boundaries, raw record count, and digests. Explain which result is a conservative refusal and
> which claims remain unsupported. Do not call or imply any external effect or hidden reasoning.

Acceptance:

- Screenshot/video shows genuine Bob tool-call UI and tool names.
- Returned quota, replan, explanation, trace, and experiment digests are retained in the log.
- Bob correctly states `awaiting_effects`, `PROPOSED`, structural-only, deterministic-simulation,
  and no-external-call boundaries.
- No MCP tool is globally auto-approved merely to make the recording easier.

## B3 — Live Granite/watsonx adapter integration

Only run this package when the entrant has their own watsonx credentials and an available
Granite model. Never paste credentials into Bob prompts, source files, screenshots, or logs.

> Inspect `watsonx.py`, `stormshift_runtime.py`, executor reservations, and the adapter tests.
> Design and implement the smallest capability-safe path that executes one bounded fictional
> synthesis task through the real Granite model while keeping admission and retry ownership in
> FINITE. Persist a redacted receipt with model ID, latency, provider-reported usage when
> available, request/response digests, validator result, and an explicit `live-watsonx` label.
> Add an offline fake path for CI. Refuse the live claim when required receipt fields or
> credentials are missing.

Acceptance:

- Bob contributes material adapter/runtime code or a substantive correction to it.
- Real credentials remain environment-only and never enter Git.
- Test-double output is never labeled live.
- One real redacted receipt is saved outside source control first, reviewed, then added only if
  it contains no sensitive payload or identifier.

## B4 — Console accessibility and evidence review

> Audit the Physics Console as a keyboard and screen-reader user. Verify focus visibility,
> heading order, fieldset/range labels, live decision announcements, reduced motion, mobile
> layout, color contrast, and that every simulated/model/live distinction is readable without
> color. Implement concrete fixes and extend the rendered-output tests. Do not change kernel
> numbers or claims in the frontend.

Acceptance:

- Bob changes and tests at least one meaningful accessibility or evidence-boundary issue.
- Kernel-generated artifact values remain authoritative.
- The session includes before/after evidence and human review.

## B5 — Fair baseline or explicit negative result

> Use `BENCHMARK.md` as a preregistration. Implement one honest external-framework baseline over
> identical task implementations, prompts, tools, model menu, validators, cache rules, and fault
> seeds. Preserve raw traces and report negative or null results. Do not compare FINITE's
> simulator to a deliberately untuned toy and do not publish a superiority statement from fewer
> than the registered trials.

Acceptance:

- Framework versions and exact commit are pinned.
- Identical-work checks fail closed.
- Raw results and aggregation code are committed before narrative claims.
- If time or credits prevent fairness, Bob records why the comparison was omitted.

## B6 — Bob release audit

> Use the project `release-evidence` skill. Run `python scripts/verify_release.py`. Map every
> README and project-page claim to the judge bundle, raw record digest, code, tests, exact commit,
> and real Bob/live receipts. Mark unsupported, stale, private, or inaccessible material as a
> blocker. Do not waive Bob, SkillsBuild, public repository, video, or eligibility requirements.

Acceptance:

- Bob produces a blocking/non-blocking audit and fixes at least one confirmed release issue.
- The final Bob session references the exact submitted commit and judge-bundle digest.
- The entrant verifies public links in a signed-out browser after Bob's review.

## Build-log discipline

For every accepted session, copy the template in `bob-build-log.md` and fill it from actual
artifacts. Link Bob screenshots/video locally until public release, list exact files and tests,
and include the accepted commit. A session with no material accepted contribution can still be
logged honestly, but it does not by itself satisfy the core-component requirement.
