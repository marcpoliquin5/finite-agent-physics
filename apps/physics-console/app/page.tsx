"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import artifactEnvelopeJson from "./demo-artifact.json";

type ScenarioId = "nominal" | "provider" | "workers";
type DigestState = "checking" | "verified" | "mismatch";
type RuntimeConnectionState = "idle" | "launching" | "streaming" | "settled" | "error";

type LiveRunStatus = {
  run_id: string;
  state: string;
  event_count: number;
  last_event_id: string;
  pending_effect_count?: number;
};

type LiveEvent = {
  id: string;
  sequence: number;
  event_id: string;
  type: string;
  task_id: string | null;
  occurred_at_ms: number;
};

type RunEntry = {
  id: string;
  label: string;
  lane: "fixture" | "granite" | "effect";
  start: number;
  end: number;
  mandatory: boolean;
};

type Witness = {
  label: string;
  short_label: string;
  binding_annotation: string;
  decision_annotation: string;
  provider_cap: number;
  workers: number;
  certificate_digest: string;
  trace_digest: string;
  trace_verified: boolean;
  result: {
    success: boolean;
    makespan_ms: number;
    model_bound_ms: number;
    total_tokens: number;
    total_cost_microusd: number;
    total_context_bytes: number;
    modeled_success_probability: number;
    entries: RunEntry[];
  };
};

type Decision = {
  status: "feasible" | "degraded" | "refused";
  failure_reason: string | null;
  certificate_digest: string;
  projected_makespan_ms: number;
  model_bound_ms: number;
};

type ArtifactPayload = {
  schema_version: string;
  release_generation: string;
  measurement_kind: string;
  claim_status: string;
  fictional_fixture: boolean;
  external_systems_called: boolean;
  bob_mcp_tool_count: number;
  witnesses: Record<ScenarioId, Witness>;
  decisions: Record<ScenarioId, Record<string, Decision>>;
  protected_minima: {
    tokens: number;
    cost_microusd: number;
    context_bytes: number;
  };
  mandatory_task_count: number;
  total_task_count: number;
  stormshift_structural_validation: {
    passed: boolean;
    report_digest: string;
    digest_verified: boolean;
    scope: string;
    limitations: string;
  };
  registered_fault_experiment: {
    measurement_kind: string;
    claim_status: string;
    revision_provenance: string;
    raw_record_count: number;
    paired_seed_count: number;
    condition_count: number;
    policy_count: number;
    experiment_config_digest: string;
    comparison_scope: string;
  };
  independent_effect_drill: {
    measurement_kind: string;
    injected_fault: string;
    final_state: string;
    physical_apply_count: number;
    external_effects_possible: boolean;
  };
  resource_ledger_stress: {
    transition_count: number;
    independent_replay_passed: boolean;
    trace_digest: string;
    scope: string;
  };
  provider_quota_stress: {
    model_scope: string;
    aggregate_guard_scope: string;
    logical_calls: number;
    admission_requests: number;
    settled_calls: number;
    refused_admissions: number;
    reset_suppressed_retries: number;
    event_count: number;
    event_digest: string;
  };
  replanning_witness: {
    event_count: number;
    final_revision: number;
    first_disposition: string;
    first_reason_code: string;
    shed_task_ids: string[];
    second_disposition: string;
    second_reason_code: string;
    state_chain_verified: boolean;
    first_decision_digest: string;
    second_decision_digest: string;
    scope: string;
  };
  decision_explanation_evidence: {
    case_count: number;
    record_count: number;
    one_record_per_event: boolean;
    reasoning_access: boolean;
    bundle_ids: string[];
    scope: string;
  };
  physical_resource_admission: {
    declared_physical_cap_count: number;
    coverage_dimension_count: number;
    summary_digest: string;
    report: {
      status: string;
      checks: { passed: boolean }[];
      transport_rtt_critical_path_lower_bound_ms: number;
    };
    energy_boundary: { status: string; unit: string };
  };
  adaptive_crash_restart_recovery: {
    final_status: string;
    controller_record_count: number;
    replay_passed: boolean;
    worker_calls_during_replay: number;
    external_provider_calls: number;
    summary_digest: string;
  };
  bounded_semantic_safety: {
    bounded_check_count: number;
    adversarial_mutation_count: number;
    adversarial_refused_count: number;
    baseline_passed: boolean;
    summary_digest: string;
  };
  artifact_store_restart_integrity: {
    artifact_count: number;
    verification_passed: boolean;
    deduplication_preserved: boolean;
    summary_digest: string;
  };
  framework_conformance_loss_accounting: {
    neutral: { semantic_loss_count: number; round_trip_exact: boolean };
    langgraph: { semantic_loss_count: number; manifest_digest_verified: boolean };
    optional_framework_execution: string;
    summary_digest: string;
  };
  release_and_whole_run_verifier_boundaries: {
    release_ready_claim: boolean;
    release_manifest: { capability_id_count: number; release_gate_id_count: number };
    whole_run_verifier: { independent_of_scheduler_executor_provider_and_planner: boolean };
    summary_digest: string;
  };
  v5_evidence_boundaries: {
    live_bob_session_present: boolean;
    live_watsonx_or_granite_calls: number;
    public_deployment_receipt_present: boolean;
    release_ready_claim: boolean;
    summary_digest: string;
  };
};

type ArtifactEnvelope = {
  schema_version: string;
  digest_algorithm: "sha256";
  sha256: string;
  canonical_payload: string;
};

const artifactEnvelope = artifactEnvelopeJson as ArtifactEnvelope;
const artifact = JSON.parse(artifactEnvelope.canonical_payload) as ArtifactPayload;
const pressureStateCount = Object.values(artifact.decisions).reduce(
  (total, states) => total + Object.keys(states).length,
  0,
);
const formatSeconds = (milliseconds: number) => `${(milliseconds / 1000).toFixed(2)}s`;
const formatUsd = (microUsd: number) => `$${(microUsd / 1_000_000).toFixed(5)}`;
const shortDigest = (digest: string) => `sha256: ${digest.slice(0, 7)}...${digest.slice(-7)}`;

function normalizedApiBase(raw: string): string {
  const parsed = new URL(raw.trim());
  const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(parsed.hostname);
  if (parsed.protocol !== "https:" && !(parsed.protocol === "http:" && loopback)) {
    throw new Error("Use HTTPS, or HTTP only for a loopback FINITE service.");
  }
  if (parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error("The service URL cannot contain credentials, query text, or a fragment.");
  }
  parsed.pathname = parsed.pathname.replace(/\/+$/, "");
  return parsed.toString().replace(/\/$/, "");
}

async function responseJson(response: Response): Promise<Record<string, unknown>> {
  const payload = (await response.json()) as Record<string, unknown>;
  if (!response.ok) {
    const error = payload.error as { message?: unknown } | undefined;
    throw new Error(typeof error?.message === "string" ? error.message : `HTTP ${response.status}`);
  }
  return payload;
}

async function verifyArtifactDigest(): Promise<boolean> {
  const bytes = new TextEncoder().encode(artifactEnvelope.canonical_payload);
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  const observed = Array.from(new Uint8Array(hash))
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
  return observed === artifactEnvelope.sha256;
}

export default function Home() {
  const [scenario, setScenario] = useState<ScenarioId>("nominal");
  const [deadline, setDeadline] = useState(12_000);
  const [costCap, setCostCap] = useState(16_000);
  const [digestState, setDigestState] = useState<DigestState>("checking");
  const [apiBase, setApiBase] = useState("http://127.0.0.1:8080");
  const [apiToken, setApiToken] = useState("");
  const [runtimeState, setRuntimeState] = useState<RuntimeConnectionState>("idle");
  const [runtimeMessage, setRuntimeMessage] = useState(
    "Connect to an entrant-run FINITE service to execute the bundled reference workflow.",
  );
  const [liveStatus, setLiveStatus] = useState<LiveRunStatus | null>(null);
  const [liveEvents, setLiveEvents] = useState<LiveEvent[]>([]);
  const runtimeAbort = useRef<AbortController | null>(null);
  const witness = artifact.witnesses[scenario];
  const decision = artifact.decisions[scenario][`${deadline}:${costCap}`];

  useEffect(() => {
    let active = true;
    verifyArtifactDigest()
      .then((valid) => {
        if (active) setDigestState(valid ? "verified" : "mismatch");
      })
      .catch(() => {
        if (active) setDigestState("mismatch");
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(
    () => () => {
      runtimeAbort.current?.abort();
    },
    [],
  );

  const runtimeHeaders = (json = false): HeadersInit => {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (json) headers["Content-Type"] = "application/json";
    if (apiToken) headers.Authorization = `Bearer ${apiToken}`;
    return headers;
  };

  const refreshLiveStatus = async (base: string, runId: string): Promise<LiveRunStatus> => {
    const response = await fetch(`${base}/v1/runs/${encodeURIComponent(runId)}/status`, {
      headers: runtimeHeaders(),
      credentials: "omit",
      referrerPolicy: "no-referrer",
    });
    return (await responseJson(response)) as LiveRunStatus;
  };

  const streamRunEvents = async (base: string, runId: string, signal: AbortSignal) => {
    const response = await fetch(
      `${base}/v1/runs/${encodeURIComponent(runId)}/events?after=0`,
      {
        headers: { ...runtimeHeaders(), Accept: "text/event-stream" },
        credentials: "omit",
        referrerPolicy: "no-referrer",
        signal,
      },
    );
    if (!response.ok || !response.body) {
      await responseJson(response);
      throw new Error("The event stream was unavailable.");
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n");
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";
      for (const frame of frames) {
        const data = frame
          .split("\n")
          .find((line) => line.startsWith("data: "))
          ?.slice(6);
        if (!data) continue;
        const event = JSON.parse(data) as LiveEvent;
        setLiveEvents((current) => [...current, event].slice(-12));
      }
      if (done) break;
    }
  };

  const launchReferenceRun = async () => {
    runtimeAbort.current?.abort();
    const abort = new AbortController();
    runtimeAbort.current = abort;
    setRuntimeState("launching");
    setRuntimeMessage("Loading the digest-bound StormShift workflow from the control plane...");
    setLiveEvents([]);
    setLiveStatus(null);
    try {
      const base = normalizedApiBase(apiBase);
      const referenceResponse = await fetch(`${base}/v1/reference-workflows/stormshift`, {
        headers: runtimeHeaders(),
        credentials: "omit",
        referrerPolicy: "no-referrer",
        signal: abort.signal,
      });
      const reference = await responseJson(referenceResponse);
      if (typeof reference.workflow !== "object" || reference.workflow === null) {
        throw new Error("The control plane returned an invalid reference workflow.");
      }
      const runId = `console-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
      const submitResponse = await fetch(`${base}/v1/runs`, {
        method: "POST",
        headers: runtimeHeaders(true),
        body: JSON.stringify({ run_id: runId, workflow: reference.workflow }),
        credentials: "omit",
        referrerPolicy: "no-referrer",
        signal: abort.signal,
      });
      await responseJson(submitResponse);
      setRuntimeState("streaming");
      setRuntimeMessage("Runtime accepted the envelope. Streaming its durable event ledger.");
      setLiveStatus(await refreshLiveStatus(base, runId));
      await streamRunEvents(base, runId, abort.signal);
      const settled = await refreshLiveStatus(base, runId);
      setLiveStatus(settled);
      setRuntimeState("settled");
      setRuntimeMessage(
        settled.state === "awaiting_effects"
          ? "Execution stopped at the approval boundary. No external effect was committed."
          : `The durable run settled as ${settled.state}.`,
      );
    } catch (error) {
      if (abort.signal.aborted) return;
      setRuntimeState("error");
      setRuntimeMessage(error instanceof Error ? error.message : "Runtime connection failed.");
    }
  };

  const cancelLiveRun = async () => {
    if (!liveStatus) return;
    try {
      const base = normalizedApiBase(apiBase);
      const response = await fetch(
        `${base}/v1/runs/${encodeURIComponent(liveStatus.run_id)}/cancel`,
        {
          method: "POST",
          headers: runtimeHeaders(true),
          body: JSON.stringify({ reason: "Physics Console operator request" }),
          credentials: "omit",
          referrerPolicy: "no-referrer",
        },
      );
      await responseJson(response);
      setRuntimeMessage("A durable cooperative-cancellation request was recorded.");
    } catch (error) {
      setRuntimeState("error");
      setRuntimeMessage(error instanceof Error ? error.message : "Cancellation failed.");
    }
  };

  const state = useMemo(() => {
    if (!decision) {
      return {
        refused: true,
        reason: "No pinned preflight result exists for this envelope.",
        status: "REFUSED - UNPINNED ENVELOPE",
        slack: 0,
      };
    }
    const refused = decision.status === "refused";
    return {
      refused,
      reason: decision.failure_reason ?? "Every declared check passes in the pinned model.",
      status: refused ? "REFUSED - ZERO DISPATCH" : "ADMITTED - PINNED MODEL",
      slack: deadline - decision.projected_makespan_ms,
    };
  }, [deadline, decision]);

  const scale = Math.max(8_000, deadline, witness.result.makespan_ms);
  const deadlineMarker = Math.min(100, (deadline / scale) * 100);
  const events = state.refused
    ? [
        ["00", "PIN", `Artifact digest ${digestState}`],
        ["01", "PREFLIGHT", "Pinned Python certificate selected for this exact envelope"],
        ["02", "REFUSE", state.reason],
        ["03", "BOUNDARY", "0 model calls - 0 tool calls - 0 external effects"],
      ]
    : [
        ["00", "PIN", `Artifact digest ${digestState}`],
        ["01", "ROUTE", witness.decision_annotation],
        ["02", "VALIDATE", "StormShift structural fixture checks are separately digest-bound"],
        ["03", "BOUNDARY", "Publication remains an intent; crash drill shown separately"],
      ];

  const effectStages = state.refused
    ? [["--", "NOT CREATED", "admission refused"]]
    : [
        ["01", "PROPOSED", "payload bound"],
        ["02", "PREPARED", "fenced"],
        ["03", "APPROVED", "exact scope"],
        ["04", "COMMITTED", "simulated target"],
      ];

  const reset = () => {
    setScenario("nominal");
    setDeadline(12_000);
    setCostCap(16_000);
  };

  return (
    <main className="console-shell">
      <header className="topbar">
        <div className="brand-lockup" aria-label="FINITE Agent Physics">
          <span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>
          <div>
            <strong>FINITE</strong>
            <span>Agent Physics control plane</span>
          </div>
        </div>
        <div className="topbar-meta">
          <span className="sim-label"><i /> deterministic simulation</span>
          <span className="commit-label">ARTIFACT {digestState.toUpperCase()}</span>
          <span className="mcp-label">BOB MCP - {artifact.bob_mcp_tool_count} TOOLS</span>
        </div>
      </header>

      <section className="mission-heading" aria-labelledby="mission-title">
        <div>
          <span className="eyebrow">STORMSHIFT / MIAMI-DADE / FICTIONAL FIXTURES</span>
          <h1 id="mission-title">Keep the promises.<br /><em>Change the plan.</em></h1>
        </div>
        <p>
          A constraint-carrying response workflow under deadline, provider, context,
          reliability, and effect pressure. Every value is pinned simulation data - never
          live emergency information.
        </p>
      </section>

      <section className={`verdict ${state.refused ? "verdict-refused" : "verdict-admitted"}`} aria-live="polite">
        <div className="verdict-state">
          <span className="verdict-light" aria-hidden="true" />
          <div>
            <span>CONTROL DECISION</span>
            <strong>{state.status}</strong>
          </div>
        </div>
        <div className="verdict-reason">
          <span>{state.refused ? "Preflight reason" : "Operator annotation"}</span>
          <strong>{state.refused ? state.reason : witness.binding_annotation}</strong>
        </div>
        <div className="verdict-spend">
          <span>Authorized spend</span>
          <strong>{state.refused ? "0 tokens" : `${witness.result.total_tokens.toLocaleString()} tokens`}</strong>
          <small>{state.refused ? "refused before dispatch" : `${formatUsd(witness.result.total_cost_microusd)} modeled`}</small>
        </div>
      </section>

      <section className="metrics-grid" aria-label="Run envelope metrics">
        <article className="metric-card metric-primary">
          <span>Modeled finish</span>
          <strong>{state.refused ? "--" : formatSeconds(decision.projected_makespan_ms)}</strong>
          <small>{state.refused ? "no admitted execution" : `${formatSeconds(Math.max(0, state.slack))} modeled slack`}</small>
        </article>
        <article className="metric-card">
          <span>Planning-model bound</span>
          <strong>{formatSeconds(decision?.model_bound_ms ?? witness.result.model_bound_ms)}</strong>
          <small>selected p95 estimates - not physical runtime</small>
        </article>
        <article className="metric-card">
          <span>Modeled reliability</span>
          <strong>{state.refused ? "--" : `${(witness.result.modeled_success_probability * 100).toFixed(2)}%`}</strong>
          <small>independent profile model - floor 90%</small>
        </article>
        <article className="metric-card">
          <span>Required work</span>
          <strong>{artifact.mandatory_task_count} / {artifact.total_task_count}</strong>
          <small>1 optional branch - protected in the planning model</small>
        </article>
        <article className="metric-card">
          <span>Context movement</span>
          <strong>{(witness.result.total_context_bytes / 1000).toFixed(1)} KB</strong>
          <small>modeled bytes - actual execution not shown</small>
        </article>
      </section>

      <section className="workspace-grid">
        <article className="panel timeline-panel">
          <header className="panel-header">
            <div>
              <span className="panel-kicker">PINNED MODEL WITNESS</span>
              <h2>{witness.label}</h2>
            </div>
            <div className="legend" aria-label="Timeline legend">
              <span><i className="legend-fixture" /> fixture</span>
              <span><i className="legend-granite" /> modeled Granite</span>
              <span><i className="legend-effect" /> simulated effect</span>
            </div>
          </header>

          <div className="timeline-scale" aria-hidden="true">
            {[0, 0.25, 0.5, 0.75, 1].map((part) => (
              <span key={part} style={{ left: `${part * 100}%` }}>{formatSeconds(scale * part)}</span>
            ))}
          </div>

          <div className={`gantt ${state.refused ? "gantt-withheld" : ""}`}>
            <div className="deadline-line" style={{ left: `${deadlineMarker}%` }}>
              <span>deadline</span>
            </div>
            {witness.result.entries.map((entry) => (
              <div className="gantt-row" key={entry.id}>
                <div className="task-label">
                  <i className={entry.mandatory ? "task-required" : "task-optional"} />
                  <span>{entry.label}</span>
                </div>
                <div className="task-track">
                  <div
                    className={`task-bar task-${entry.lane}`}
                    style={{
                      left: `${(entry.start / scale) * 100}%`,
                      width: `${Math.max(1.5, ((entry.end - entry.start) / scale) * 100)}%`,
                    }}
                    title={`${entry.label}: ${entry.start}-${entry.end} ms`}
                  >
                    <span>{entry.end - entry.start} ms</span>
                  </div>
                </div>
              </div>
            ))}
            {state.refused && (
              <div className="withheld-overlay">
                <strong>EXECUTION WITHHELD</strong>
                <span>Pinned candidate shown for diagnosis only. Nothing dispatches.</span>
              </div>
            )}
          </div>
        </article>

        <aside className="panel pressure-panel">
          <header className="panel-header">
            <div>
              <span className="panel-kicker">PINNED PRESSURE GRID</span>
              <h2>Change the envelope</h2>
            </div>
            <button className="reset-button" type="button" onClick={reset}>Reset</button>
          </header>

          <fieldset className="scenario-switcher">
            <legend>Capacity state</legend>
            {(Object.keys(artifact.witnesses) as ScenarioId[]).map((id) => (
              <button
                key={id}
                type="button"
                className={scenario === id ? "scenario-active" : ""}
                aria-pressed={scenario === id}
                onClick={() => setScenario(id)}
              >
                {artifact.witnesses[id].short_label}
              </button>
            ))}
          </fieldset>

          <label className="range-control">
            <span><b>Hard deadline</b><output>{formatSeconds(deadline)}</output></span>
            <input
              type="range"
              min="5000"
              max="12000"
              step="1000"
              value={deadline}
              onChange={(event) => setDeadline(Number(event.target.value))}
            />
            <small>Every step maps to a Python preflight certificate</small>
          </label>

          <label className="range-control">
            <span><b>Cost ceiling</b><output>{costCap.toLocaleString()} micro-USD</output></span>
            <input
              type="range"
              min="5000"
              max="16000"
              step="250"
              value={costCap}
              onChange={(event) => setCostCap(Number(event.target.value))}
            />
            <small>Pinned minimum: {artifact.protected_minima.cost_microusd.toLocaleString()} micro-USD</small>
          </label>

          <div className="pressure-facts">
            <div><span>Global workers</span><strong>{witness.workers}</strong></div>
            <div><span>Modeled Granite cap</span><strong>{witness.provider_cap}</strong></div>
            <div><span>Certificate status</span><strong>{decision?.status ?? "missing"}</strong></div>
            <div><span>External systems called</span><strong>0</strong></div>
          </div>

          <button className="impossible-button" type="button" onClick={() => setDeadline(5_000)}>
            <span>Inject impossible 5s deadline</span>
            <b>EXPECT ZERO-DISPATCH REFUSAL -&gt;</b>
          </button>
        </aside>
      </section>

      <section className="live-rack" aria-labelledby="live-runtime-title">
        <header>
          <div>
            <span className="panel-kicker">OPTIONAL LIVE CONTROL PLANE / REST + SSE</span>
            <h2 id="live-runtime-title">Run the reference workflow. Watch every transition.</h2>
          </div>
          <span className={`runtime-chip runtime-${runtimeState}`}>
            {runtimeState.toUpperCase()}
          </span>
        </header>
        <div className="live-layout">
          <form
            className="runtime-connect"
            onSubmit={(event) => {
              event.preventDefault();
              void launchReferenceRun();
            }}
          >
            <label>
              <span>FINITE service origin</span>
              <input
                type="url"
                inputMode="url"
                value={apiBase}
                onChange={(event) => setApiBase(event.target.value)}
                spellCheck={false}
                required
              />
            </label>
            <label>
              <span>Bearer token <small>optional on loopback</small></span>
              <input
                type="password"
                value={apiToken}
                onChange={(event) => setApiToken(event.target.value)}
                autoComplete="off"
                placeholder="Kept only in this page memory"
              />
            </label>
            <div className="runtime-actions">
              <button
                className="launch-button"
                type="submit"
                disabled={runtimeState === "launching" || runtimeState === "streaming"}
              >
                {runtimeState === "launching" ? "Negotiating..." : "Launch bounded run"}
              </button>
              <button
                className="cancel-button"
                type="button"
                disabled={!liveStatus || runtimeState !== "streaming"}
                onClick={() => void cancelLiveRun()}
              >
                Request cancel
              </button>
            </div>
            <p>{runtimeMessage}</p>
            <small>
              Browser access requires an exact CORS origin allowlist. The token is never added
              to the URL or the sealed demo artifact.
            </small>
          </form>
          <div className="runtime-ledger" aria-live="polite">
            <div className="runtime-summary">
              <div><span>Run ID</span><strong>{liveStatus?.run_id ?? "not launched"}</strong></div>
              <div><span>Durable state</span><strong>{liveStatus?.state ?? "--"}</strong></div>
              <div><span>Events</span><strong>{liveEvents.length || liveStatus?.event_count || 0}</strong></div>
              <div><span>External commits</span><strong>0 by API contract</strong></div>
            </div>
            <ol className="live-events">
              {liveEvents.length === 0 ? (
                <li className="live-empty">
                  <span>--</span><b>WAITING</b><p>No runtime event has crossed this browser boundary.</p>
                </li>
              ) : (
                liveEvents.map((event) => (
                  <li key={`${event.sequence}-${event.event_id}`}>
                    <span>{String(event.sequence).padStart(2, "0")}</span>
                    <b>{event.type}</b>
                    <p>{event.task_id ?? "run"}</p>
                  </li>
                ))
              )}
            </ol>
          </div>
        </div>
      </section>

      <section className="lower-grid">
        <article className="panel decision-panel">
          <header className="panel-header">
            <div>
              <span className="panel-kicker">DIGEST-BOUND DECISION ARTIFACT</span>
              <h2>Facts, scope, and limitations</h2>
            </div>
            <span className="trace-digest">{shortDigest(decision?.certificate_digest ?? witness.certificate_digest)}</span>
          </header>
          <ol className="event-stream">
            {events.map(([sequence, kind, detail]) => (
              <li key={`${sequence}-${kind}`}>
                <span className="event-sequence">{sequence}</span>
                <b>{kind}</b>
                <p>{detail}</p>
              </li>
            ))}
          </ol>
        </article>

        <article className="panel effect-panel">
          <header className="panel-header">
            <div>
              <span className="panel-kicker">{state.refused ? "SELECTED RUN EFFECT PATH" : "INDEPENDENT EFFECT CRASH DRILL"}</span>
              <h2>{state.refused ? "No intent. No apply." : "One intent. One simulated apply."}</h2>
            </div>
            <span className="simulation-chip">NO EXTERNAL WRITE</span>
          </header>
          <div className="effect-flow" aria-label="Effect intent state machine">
            {effectStages.map(([number, name, note], index) => (
              <div className="effect-stage" key={name}>
                <span>{number}</span>
                <div><b>{name}</b><small>{note}</small></div>
                {index < effectStages.length - 1 && <i aria-hidden="true">-&gt;</i>}
              </div>
            ))}
          </div>
          <div className="effect-proof">
            <div><span>Injected condition</span><strong>{state.refused ? "none - run refused" : artifact.independent_effect_drill.injected_fault}</strong></div>
            <div><span>Simulated apply count</span><strong>{state.refused ? 0 : artifact.independent_effect_drill.physical_apply_count}</strong></div>
            <div><span>Relationship</span><strong>{state.refused ? "selected run" : "separate deterministic rehearsal"}</strong></div>
            <div><span>Delivery boundary</span><strong>simulation-only adapter</strong></div>
          </div>
        </article>
      </section>

      <section className="recovery-rack" aria-labelledby="recovery-title">
        <header>
          <div>
            <span className="panel-kicker">MODELED RECOVERY / MONOTONIC STATE CHAIN</span>
            <h2 id="recovery-title">Adapt once. Refuse before breaking promises.</h2>
          </div>
          <span className={`simulation-chip ${artifact.replanning_witness.state_chain_verified ? "" : "simulation-chip-alert"}`}>
            {artifact.replanning_witness.state_chain_verified ? "CHAIN VERIFIED" : "CHAIN INVALID"}
          </span>
        </header>
        <div className="recovery-flow">
          <article>
            <span>REV 01 / MODELED WATSONX CAPACITY DROP</span>
            <strong>{artifact.replanning_witness.first_disposition.toUpperCase()}</strong>
            <p>
              Shed <b>{artifact.replanning_witness.shed_task_ids.join(", ")}</b>; preserve
              every unfinished mandatory task under the remaining envelope.
            </p>
            <small>{shortDigest(artifact.replanning_witness.first_decision_digest)}</small>
          </article>
          <i aria-hidden="true">-&gt;</i>
          <article className="recovery-refusal">
            <span>REV 02 / SECOND CAPACITY DROP</span>
            <strong>{artifact.replanning_witness.second_disposition.toUpperCase()}</strong>
            <p>
              Further loss makes the residual plan inadmissible. FINITE preserves settled
              usage and refuses instead of silently dropping required work.
            </p>
            <small>{shortDigest(artifact.replanning_witness.second_decision_digest)}</small>
          </article>
        </div>
        <p>
          This is deterministic residual-graph planning over caller-supplied progress—not live
          executor mutation or provider telemetry.
        </p>
      </section>

      <section className="v5-rack" aria-labelledby="v5-title">
        <header>
          <div>
            <span className="panel-kicker">V5 INVARIANT STACK / EXECUTABLE LOCAL EVIDENCE</span>
            <h2 id="v5-title">More than orchestration: admission, recovery, meaning, lineage.</h2>
          </div>
          <span className="trace-digest">
            {shortDigest(artifact.v5_evidence_boundaries.summary_digest)}
          </span>
        </header>
        <div className="v5-grid">
          <article>
            <span>PHYSICAL ADMISSION</span>
            <strong>{artifact.physical_resource_admission.declared_physical_cap_count} CAPS</strong>
            <p>
              CPU, RAM, VRAM, storage, network, bandwidth, RTT, and egress cost admitted
              before dispatch. Energy stays explicitly {artifact.physical_resource_admission.energy_boundary.status}.
            </p>
            <small>{shortDigest(artifact.physical_resource_admission.summary_digest)}</small>
          </article>
          <article>
            <span>ACTIVE RECOVERY</span>
            <strong>{artifact.adaptive_crash_restart_recovery.final_status.toUpperCase()}</strong>
            <p>
              {artifact.adaptive_crash_restart_recovery.controller_record_count} durable controller
              records survive a crash; replay makes {artifact.adaptive_crash_restart_recovery.worker_calls_during_replay}
              {" "}worker calls.
            </p>
            <small>{shortDigest(artifact.adaptive_crash_restart_recovery.summary_digest)}</small>
          </article>
          <article>
            <span>BOUNDED SEMANTIC SAFETY</span>
            <strong>{artifact.bounded_semantic_safety.adversarial_refused_count}/{artifact.bounded_semantic_safety.adversarial_mutation_count}</strong>
            <p>
              Adversarial citation, number, bilingual, freshness, URL, authority, taint, and
              declared-accessibility mutations are refused.
            </p>
            <small>{shortDigest(artifact.bounded_semantic_safety.summary_digest)}</small>
          </article>
          <article>
            <span>ARTIFACT LINEAGE</span>
            <strong>{artifact.artifact_store_restart_integrity.verification_passed ? "RESTART VERIFIED" : "INVALID"}</strong>
            <p>
              Content identity, parents, transformation provenance, restart reads, and exact
              deduplication are checked together.
            </p>
            <small>{shortDigest(artifact.artifact_store_restart_integrity.summary_digest)}</small>
          </article>
          <article>
            <span>FRAMEWORK CONFORMANCE</span>
            <strong>{artifact.framework_conformance_loss_accounting.langgraph.semantic_loss_count} LOSSES NAMED</strong>
            <p>
              Neutral round-trip is exact. LangGraph conversion records every narrowed or
              metadata-only semantic instead of claiming equivalence.
            </p>
            <small>{shortDigest(artifact.framework_conformance_loss_accounting.summary_digest)}</small>
          </article>
          <article className="v5-boundary-card">
            <span>RELEASE GATE</span>
            <strong>{artifact.release_and_whole_run_verifier_boundaries.release_ready_claim ? "READY" : "EVIDENCE REQUIRED"}</strong>
            <p>
              {artifact.release_and_whole_run_verifier_boundaries.release_manifest.capability_id_count}
              {" "}capability checks and {artifact.release_and_whole_run_verifier_boundaries.release_manifest.release_gate_id_count}
              {" "}integrated gates refuse to mint live Bob, Granite, GitHub, or deployment proof.
            </p>
            <small>{shortDigest(artifact.release_and_whole_run_verifier_boundaries.summary_digest)}</small>
          </article>
        </div>
      </section>

      <section className="evidence-rack" aria-labelledby="evidence-title">
        <header>
          <div>
            <span className="panel-kicker">REGISTERED EVIDENCE / DESCRIPTIVE ONLY</span>
            <h2 id="evidence-title">Eighteen signals. One sealed artifact.</h2>
          </div>
          <span className="trace-digest">
            {shortDigest(artifact.registered_fault_experiment.experiment_config_digest)}
          </span>
        </header>
        <div className="evidence-grid">
          <article><strong>{pressureStateCount.toLocaleString()}</strong><span>kernel-generated pressure states</span></article>
          <article><strong>{artifact.resource_ledger_stress.transition_count.toLocaleString()}</strong><span>resource-ledger transitions</span></article>
          <article><strong>{artifact.provider_quota_stress.logical_calls.toLocaleString()}</strong><span>declared-quota logical calls</span></article>
          <article><strong>{artifact.decision_explanation_evidence.record_count}</strong><span>post-hoc numeric explanation records</span></article>
          <article><strong>{artifact.replanning_witness.final_revision}</strong><span>chained modeled replans</span></article>
          <article><strong>{artifact.registered_fault_experiment.raw_record_count}</strong><span>complete experiment records</span></article>
          <article><strong>{artifact.registered_fault_experiment.paired_seed_count}</strong><span>frozen paired seeds</span></article>
          <article><strong>{artifact.registered_fault_experiment.condition_count}</strong><span>control + fault conditions</span></article>
          <article><strong>{artifact.registered_fault_experiment.policy_count}</strong><span>simulator policies</span></article>
          <article><strong>{artifact.bob_mcp_tool_count}</strong><span>Bob-facing local tools</span></article>
          <article><strong>{artifact.provider_quota_stress.reset_suppressed_retries}</strong><span>reset-window retry suppressions</span></article>
          <article><strong>{artifact.physical_resource_admission.coverage_dimension_count}</strong><span>physical-resource coverage dimensions</span></article>
          <article><strong>{artifact.adaptive_crash_restart_recovery.controller_record_count}</strong><span>durable adaptive controller records</span></article>
          <article><strong>{artifact.bounded_semantic_safety.adversarial_mutation_count}</strong><span>semantic adversarial mutations refused</span></article>
          <article><strong>{artifact.artifact_store_restart_integrity.artifact_count}</strong><span>restart-verified lineage artifacts</span></article>
          <article><strong>{artifact.framework_conformance_loss_accounting.langgraph.semantic_loss_count}</strong><span>LangGraph semantic losses made explicit</span></article>
          <article><strong>{artifact.release_and_whole_run_verifier_boundaries.release_manifest.capability_id_count}</strong><span>release-manifest capability IDs</span></article>
          <article><strong>0</strong><span>live or external calls represented</span></article>
        </div>
        <p>
          The quota guard and replanner are declared local models; explanation records contain
          public post-hoc facts, never chain-of-thought. Adaptive is the paired-analysis baseline;
          static-parallel and sequential are development references, not tuned external-framework
          baselines. V5 local evidence is not a substitute for entrant-owned Bob, Granite,
          GitHub, deployment, or submission receipts. Revision provenance is
          {" "}<b>{artifact.registered_fault_experiment.revision_provenance}</b>.
        </p>
      </section>

      <footer>
        <p><strong>Agents can reason.</strong> FINITE makes them keep promises.</p>
        <div>
          <span>Digest-bound fixture</span>
          <span>Rendered output checked</span>
          <span>Apache-2.0</span>
          <span>V5 local evidence candidate</span>
        </div>
      </footer>
    </main>
  );
}
