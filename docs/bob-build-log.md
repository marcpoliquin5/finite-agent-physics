# IBM Bob build log

This log contains only real IBM Bob sessions. Never fabricate, infer, or backfill an entry from
Codex work, repository configuration, non-Bob test output, or an intended session.

## 2026-07-24 19:38-19:57 ET - B1: complete MCP and production-survival validation

- **Bob version and session reference:** Bob Shell `1.0.6`; accepted sessions
  `10d422d9-fa9b-4540-8ed5-4446ebdce914`,
  `d33ee762-824f-4532-b886-13d664bb7791`,
  `18f769ce-4a3b-45bd-bd5d-dfc4ee0f0156`,
  `652c9b87-60e7-4999-b0a8-3b6960dc001f`, and
  `6d520ec3-94d0-4900-8f19-f80e972a9317`.
- **Workspace branch and starting commit:** public `main`,
  `2be8f80f55f92f934d1c376b2650b70ff051c4ab`.
- **Goal given to Bob:** invoke and validate the entire 23-tool FINITE MCP surface, including
  negative admission, durable lifecycle, fault/recovery, survival, framework, integrity, and
  explicit Granite-boundary paths.
- **Exact prompt/task:** preserved by the Bob session records and summarized in
  [`bob-live-validation-2026-07-24.md`](bob-live-validation-2026-07-24.md).
- **Bob's proposed plan:** not counted as evidence; only calls present in Bob telemetry were
  accepted.
- **Material files Bob created or changed:** none. Bob's material contribution was independent
  execution and testing.
- **Regression Bob reproduced:** a prompt can answer without invoking MCP; the first invented
  12-tool response was rejected because telemetry showed only `attempt_completion`. Explicit
  per-tool allowlisting produced genuine calls.
- **Human review, corrections, and rejected suggestions:** rejected the invented 12-tool result
  and the no-tool attempt; required exact MCP call telemetry and authentic 23-tool output.
- **Focused and full verification performed:** all 23 unique MCP tools; 60/60 survival trials;
  450 fault-experiment records; fixture lifecycle; real pinned LangGraph witness; tamper,
  crash/replay, quota, physical-cap, context, and effect drills.
- **MCP lifecycle calls and one run ID:** `finite_preflight`, `finite_run`, `finite_status`,
  `finite_explain_run`, and `finite_verify_run`; run
  `bob-shell-fixture-20260724-01`.
- **Non-secret evidence/result digests:** raw Bob-session SHA-256 values are preserved in the
  linked validation record.
- **Screenshot/video reference and timestamps:** Bob's local session history retained; public
  video still pending.
- **Accepted commit:** tested executable commit
  `2be8f80f55f92f934d1c376b2650b70ff051c4ab`.
- **Why the contribution was material:** IBM Bob itself connected to the project MCP server and
  exercised the complete surface, including fail-closed and repeated production-survival paths.
- **Remaining limitations or blockers:** no watsonx credentials were available; Granite
  preflight and the explicit probe refused before dispatch, so no live-Granite receipt exists.

## Entry template

### YYYY-MM-DD HH:MM ET - Bx: work package title

- **Bob version and session reference:**
- **Workspace branch and starting commit:**
- **Goal given to Bob:**
- **Exact prompt/task:**
- **Bob's proposed plan:**
- **Material files Bob created or changed:**
- **Regression Bob reproduced:**
- **Human review, corrections, and rejected suggestions:**
- **Focused and full verification performed:**
- **MCP lifecycle calls and one run ID:**
- **Non-secret evidence/result digests:**
- **Screenshot/video reference and timestamps:**
- **Accepted commit:**
- **Why the contribution was material:**
- **Remaining limitations or blockers:**
