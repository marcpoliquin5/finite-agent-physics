import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

const templateRoot = new URL("../", import.meta.url);

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the FINITE evidence console", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  const html = await response.text();
  assert.match(html, /<title>FINITE \| Agent Physics Control Plane<\/title>/i);
  assert.match(html, /Keep the promises/);
  assert.match(html, /deterministic simulation/i);
  assert.match(html, /STORMSHIFT/);
  assert.match(html, /NO EXTERNAL WRITE/);
  assert.match(html, /ARTIFACT\s*(?:<!-- -->)?\s*CHECKING/);
  assert.match(html, /BOB MCP -\s*(?:<!-- -->)?\s*23\s*(?:<!-- -->)?\s*TOOLS/);
  assert.match(html, /Eighteen signals/);
  assert.match(html, /V5 INVARIANT STACK/);
  assert.match(html, /OPTIONAL LIVE CONTROL PLANE/);
  assert.match(html, /Inject budget cut/);
  assert.match(html, /Inject 429 \+ reset/);
  assert.match(html, /Resume verified state/);
  assert.match(html, /Worker\/provider calls during replay/);
  assert.match(html, /Adapt once/);
  assert.match(html, />10,000<\/strong>/);
  assert.match(html, />450<\/strong>/);
  assert.match(html, /http:\/\/localhost:3001\/og-v5\.png/);
  assert.doesNotMatch(html, /WITNESS VERIFIED|\bPROOF\b/);
  assert.doesNotMatch(html, /codex-preview|react-loading-skeleton|Your site is taking shape/i);
});

test("removes disposable starter assets and consumes a verified kernel artifact", async () => {
  const [page, layout, packageJson, artifactSource] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
    readFile(new URL("../app/demo-artifact.json", import.meta.url), "utf8"),
  ]);
  const artifact = JSON.parse(artifactSource);
  const observed = createHash("sha256").update(artifact.canonical_payload).digest("hex");
  await assert.rejects(access(new URL("app/_sites-preview", templateRoot)));
  assert.equal(observed, artifact.sha256);
  const payload = JSON.parse(artifact.canonical_payload);
  assert.equal(payload.registered_fault_experiment.raw_record_count, 450);
  assert.equal(payload.registered_fault_experiment.paired_seed_count, 30);
  assert.equal(payload.registered_fault_experiment.revision_provenance, "caller-supplied-unverified");
  assert.equal(payload.bob_mcp_tool_count, 23);
  assert.equal(payload.release_generation, "v5");
  assert.equal(payload.physical_resource_admission.declared_physical_cap_count, 10);
  assert.equal(payload.bounded_semantic_safety.adversarial_refused_count, 16);
  assert.equal(payload.framework_conformance_loss_accounting.langgraph.semantic_loss_count, 10);
  assert.equal(payload.resource_ledger_stress.transition_count, 10_000);
  assert.equal(payload.provider_quota_stress.logical_calls, 1_200);
  assert.equal(payload.replanning_witness.final_revision, 2);
  assert.equal(payload.decision_explanation_evidence.record_count, 79);
  assert.doesNotMatch(packageJson, /react-loading-skeleton/);
  assert.doesNotMatch(layout, /Starter Project|codex-preview/);
  assert.doesNotMatch(layout, /next\/font/);
  assert.match(layout, /favicon\.svg/);
  assert.match(page, /demo-artifact\.json/);
  assert.doesNotMatch(page, /const nominalEntries|WITNESS VERIFIED/);
  assert.match(page, /pinned simulation data/i);
  assert.match(page, /refused before dispatch/);
  assert.match(page, /simulation-only adapter/);
  assert.match(page, /start_paused: true/);
  assert.match(page, /adaptive-replay/);
  assert.match(page, /\/inspect/);
  assert.match(page, /effect\.run_id !== runId/);
  assert.match(page, /worker_or_provider_calls !== 0/);
  assert.match(page, /external_effects_committed !== 0/);
});
