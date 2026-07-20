"use client";

import { useEffect, useMemo, useState } from "react";
import artifactEnvelopeJson from "./demo-artifact.json";

type ScenarioId = "nominal" | "provider" | "workers";
type DigestState = "checking" | "verified" | "mismatch";

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
  measurement_kind: string;
  claim_status: string;
  fictional_fixture: boolean;
  external_systems_called: boolean;
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
          <span className="mcp-label">BOB MCP - 10 TOOLS</span>
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

      <section className="evidence-rack" aria-labelledby="evidence-title">
        <header>
          <div>
            <span className="panel-kicker">REGISTERED EVIDENCE / DESCRIPTIVE ONLY</span>
            <h2 id="evidence-title">No cherry-picked victory lap.</h2>
          </div>
          <span className="trace-digest">
            {shortDigest(artifact.registered_fault_experiment.experiment_config_digest)}
          </span>
        </header>
        <div className="evidence-grid">
          <article><strong>{pressureStateCount.toLocaleString()}</strong><span>kernel-generated pressure states</span></article>
          <article><strong>{artifact.registered_fault_experiment.raw_record_count}</strong><span>complete experiment records</span></article>
          <article><strong>{artifact.registered_fault_experiment.paired_seed_count}</strong><span>frozen paired seeds</span></article>
          <article><strong>{artifact.registered_fault_experiment.condition_count}</strong><span>control + fault conditions</span></article>
          <article><strong>{artifact.registered_fault_experiment.policy_count}</strong><span>simulator policies</span></article>
          <article><strong>0</strong><span>live or external calls represented</span></article>
        </div>
        <p>
          Adaptive is the paired-analysis baseline. Static-parallel and sequential are development
          references, not tuned external-framework baselines. Revision provenance is
          {" "}<b>{artifact.registered_fault_experiment.revision_provenance}</b>.
        </p>
      </section>

      <footer>
        <p><strong>Agents can reason.</strong> FINITE makes them keep promises.</p>
        <div>
          <span>Digest-bound fixture</span>
          <span>Rendered output checked</span>
          <span>Apache-2.0</span>
          <span>Alpha evidence build</span>
        </div>
      </footer>
    </main>
  );
}
