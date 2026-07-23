# Competitive landscape and evidence-bound gap audit

**Snapshot:** July 22, 2026, 18:16-18:18 EDT (22:16-22:18 UTC).

This is a point-in-time repository audit, not a leaderboard. Star and fork counters came from the
public GitHub REST repository endpoint. Default-branch activity came from the GitHub commit
endpoint. Release values came from GitHub's releases/latest endpoint. Feature summaries came from
each official repository's README at the exact default-branch commit linked below. Licenses were
checked against GitHub metadata and the root license file.

Counters will drift. Stars indicate attention, not correctness, safety, production readiness, or
architectural superiority. A README claim is evidence that a project advertises a capability; it
is not independent proof that the capability works. Likewise, a capability absent from an audited
README may exist elsewhere. License labels are descriptive, not legal advice.

## Bottom line

FINITE is not presently ahead of the leading repositories as a shipped product, ecosystem, or
community. Its defensible wedge is narrower and technically meaningful:

> FINITE is a framework-neutral constraint and evidence control layer: admit, degrade, refuse,
> reserve, settle, fence effects, and independently verify what happened under finite resources.

That position complements rather than replaces the leading systems:

- LangChain supplies components and integrations.
- LangGraph, Microsoft Agent Framework, CrewAI, PydanticAI, and the OpenAI Agents SDK supply
  increasingly capable agent/workflow runtimes.
- Dify, Langflow, n8n, Agno, and DeerFlow supply product and deployment surfaces.
- PageAgent and browser-use supply visible browser action surfaces.
- LlamaIndex supplies a broad data, retrieval, and document-agent layer.
- FINITE's locally executable distinction is joint finite-resource admission plus a deeper
  evidence/effect protocol. It has not proved that peers cannot implement the same controls.

The winning strategy is therefore a governed integration layer with executable evidence, not an
unsupported claim that every other framework is obsolete.

## Dated GitHub census

The cohort includes the repositories requested for comparison and additional high-attention,
directly relevant orchestration, agent-platform, and browser-agent peers. It intentionally excludes
prompt collections, tutorials, vertical applications, and coding-agent configuration packs even
when their star counts are higher.

| Official repository | Stars / forks at snapshot | Default-branch HEAD | Latest GitHub release | Root license |
|---|---:|---|---|---|
| [n8n](https://github.com/n8n-io/n8n) | 197,506 / 59,546 ([API](https://api.github.com/repos/n8n-io/n8n)) | [2026-07-22](https://github.com/n8n-io/n8n/commit/4044d58d130fd2f86e906a1f41cb561ff2c8e31b) | [n8n@2.31.5, Jul 22](https://github.com/n8n-io/n8n/releases/tag/n8n%402.31.5) | [Sustainable Use; enterprise-file exceptions](https://github.com/n8n-io/n8n/blob/4044d58d130fd2f86e906a1f41cb561ff2c8e31b/LICENSE.md) |
| [Langflow](https://github.com/langflow-ai/langflow) | 152,224 / 9,626 ([API](https://api.github.com/repos/langflow-ai/langflow)) | [2026-07-22](https://github.com/langflow-ai/langflow/commit/bfc82247a6c003ecb3a7a03f426273a3be921b55) | [v1.10.2, Jul 7](https://github.com/langflow-ai/langflow/releases/tag/v1.10.2) | [MIT](https://api.github.com/repos/langflow-ai/langflow/license) |
| [Dify](https://github.com/langgenius/dify) | 149,808 / 23,607 ([API](https://api.github.com/repos/langgenius/dify)) | [2026-07-22](https://github.com/langgenius/dify/commit/4da4fa72cd9d011559fad3746547c935941724b2) | [1.16.0, Jul 17](https://github.com/langgenius/dify/releases/tag/1.16.0) | [Modified Apache-2.0 with additional conditions](https://github.com/langgenius/dify/blob/4da4fa72cd9d011559fad3746547c935941724b2/LICENSE) |
| [LangChain](https://github.com/langchain-ai/langchain) | 142,337 / 23,686 ([API](https://api.github.com/repos/langchain-ai/langchain)) | [2026-07-22](https://github.com/langchain-ai/langchain/commit/6a97222c1ea2a4d9bc2de234995219e4f25220dc) | [langchain-core==1.5.0, Jul 21](https://github.com/langchain-ai/langchain/releases/tag/langchain-core%3D%3D1.5.0)¹ | [MIT](https://api.github.com/repos/langchain-ai/langchain/license) |
| [browser-use](https://github.com/browser-use/browser-use) | 106,124 / 11,671 ([API](https://api.github.com/repos/browser-use/browser-use)) | [2026-07-20](https://github.com/browser-use/browser-use/commit/2be09b6c5eb702a9287684b42b27e7042a1aba29) | [0.13.6, Jul 17](https://github.com/browser-use/browser-use/releases/tag/0.13.6) | [MIT](https://api.github.com/repos/browser-use/browser-use/license) |
| [DeerFlow](https://github.com/bytedance/deer-flow) | 77,614 / 10,566 ([API](https://api.github.com/repos/bytedance/deer-flow)) | [2026-07-22](https://github.com/bytedance/deer-flow/commit/cb698832deaf876d204045a68d79acedcbb1d26c) | [v2.0.0, Jun 25](https://github.com/bytedance/deer-flow/releases/tag/v2.0.0) | [MIT](https://api.github.com/repos/bytedance/deer-flow/license) |
| [MetaGPT](https://github.com/FoundationAgents/MetaGPT) | 69,472 / 8,858 ([API](https://api.github.com/repos/FoundationAgents/MetaGPT)) | [2026-01-21](https://github.com/FoundationAgents/MetaGPT/commit/11cdf466d042aece04fc6cfd13b28e1a70341b1f) | [v0.8.1, Apr 2024](https://github.com/FoundationAgents/MetaGPT/releases/tag/v0.8.1) | [MIT](https://api.github.com/repos/FoundationAgents/MetaGPT/license) |
| [AutoGen](https://github.com/microsoft/autogen) | 59,903 / 9,018 ([API](https://api.github.com/repos/microsoft/autogen)) | [2026-04-06](https://github.com/microsoft/autogen/commit/027ecf0a379bcc1d09956d46d12d44a3ad9cee14) | [python-v0.7.5, Sep 2025](https://github.com/microsoft/autogen/releases/tag/python-v0.7.5) | [CC-BY-4.0](https://github.com/microsoft/autogen/blob/027ecf0a379bcc1d09956d46d12d44a3ad9cee14/LICENSE) |
| [CrewAI](https://github.com/crewAIInc/crewAI) | 55,969 / 7,919 ([API](https://api.github.com/repos/crewAIInc/crewAI)) | [2026-07-21](https://github.com/crewAIInc/crewAI/commit/3bb87532da925ed05aa17ddc541b0a9514c8f054) | [1.15.5, Jul 20](https://github.com/crewAIInc/crewAI/releases/tag/1.15.5) | [MIT](https://api.github.com/repos/crewAIInc/crewAI/license) |
| [LlamaIndex](https://github.com/run-llama/llama_index) | 51,017 / 7,795 ([API](https://api.github.com/repos/run-llama/llama_index)) | [2026-07-21](https://github.com/run-llama/llama_index/commit/7359b1acc74563f715d4463ace39fb4dc73d79af) | [v0.14.23, Jun 24](https://github.com/run-llama/llama_index/releases/tag/v0.14.23) | [MIT](https://api.github.com/repos/run-llama/llama_index/license) |
| [Agno](https://github.com/agno-agi/agno) | 41,364 / 5,678 ([API](https://api.github.com/repos/agno-agi/agno)) | [2026-07-22](https://github.com/agno-agi/agno/commit/1e03b4ef350f7c2706abc553a208e88b3f1e81e1) | [v2.8.0, Jul 20](https://github.com/agno-agi/agno/releases/tag/v2.8.0) | [Apache-2.0](https://api.github.com/repos/agno-agi/agno/license) |
| [LangGraph](https://github.com/langchain-ai/langgraph) | 37,873 / 6,358 ([API](https://api.github.com/repos/langchain-ai/langgraph)) | [2026-07-21](https://github.com/langchain-ai/langgraph/commit/31f90df3e6b0268fa77fd2d118a917d420b84a68) | [1.2.9, Jul 10](https://github.com/langchain-ai/langgraph/releases/tag/1.2.9) | [MIT](https://api.github.com/repos/langchain-ai/langgraph/license) |
| [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) | 28,087 / 4,363 ([API](https://api.github.com/repos/openai/openai-agents-python)) | [2026-07-22](https://github.com/openai/openai-agents-python/commit/1e8d506a32ea7b84f3a5a811e101378c0b1bc137) | [v0.18.3, Jul 17](https://github.com/openai/openai-agents-python/releases/tag/v0.18.3) | [MIT](https://api.github.com/repos/openai/openai-agents-python/license) |
| [PageAgent](https://github.com/alibaba/page-agent) | 27,492 / 2,409 ([API](https://api.github.com/repos/alibaba/page-agent)) | [2026-07-22](https://github.com/alibaba/page-agent/commit/3510a769c8ac28914520f04e8cd4f3044e40dfe3) | [v1.12.2, Jul 16](https://github.com/alibaba/page-agent/releases/tag/v1.12.2) | [MIT](https://api.github.com/repos/alibaba/page-agent/license) |
| [PydanticAI](https://github.com/pydantic/pydantic-ai) | 18,736 / 2,399 ([API](https://api.github.com/repos/pydantic/pydantic-ai)) | [2026-07-22](https://github.com/pydantic/pydantic-ai/commit/2cb7a16128d8ce9a4f35b6a95c8cec6da325dbc7) | [v2.15.0, Jul 22](https://github.com/pydantic/pydantic-ai/releases/tag/v2.15.0) | [MIT](https://api.github.com/repos/pydantic/pydantic-ai/license) |
| [Microsoft Agent Framework](https://github.com/microsoft/agent-framework) | 12,313 / 2,063 ([API](https://api.github.com/repos/microsoft/agent-framework)) | [2026-07-22](https://github.com/microsoft/agent-framework/commit/61802723ff7a6decd53c7900a02adb5390c98f06) | [python-1.12.0, Jul 21](https://github.com/microsoft/agent-framework/releases/tag/python-1.12.0) | [MIT](https://api.github.com/repos/microsoft/agent-framework/license) |

¹ LangChain is a monorepo; this is the value returned by GitHub's latest-release endpoint, not a
claim that every package in the repository has that version.

AutoGen is not archived, but its [README at the audited commit](https://github.com/microsoft/autogen/blob/027ecf0a379bcc1d09956d46d12d44a3ad9cee14/README.md)
labels it maintenance mode and directs new projects to Microsoft Agent Framework. Its historic
adoption remains relevant; its successor is the current Microsoft comparison.

## What the official repositories advertise

These are compact README summaries, not independent performance assessments.

| Repository surface | Verified README capabilities |
|---|---|
| [n8n README](https://github.com/n8n-io/n8n/blob/4044d58d130fd2f86e906a1f41cb561ff2c8e31b/README.md) | Visual canvas plus JavaScript/Python, multi-step agents, human approvals, observability, self-host/cloud, RBAC/audit trails, 1,500+ integrations, and 9,000+ templates. |
| [Langflow README](https://github.com/langflow-ai/langflow/blob/bfc82247a6c003ecb3a7a03f426273a3be921b55/README.md) | Visual authoring, multi-agent orchestration, major models/vector databases/tools, API deployment, MCP-server deployment, and observability integrations. |
| [Dify README](https://github.com/langgenius/dify/blob/4da4fa72cd9d011559fad3746547c935941724b2/README.md) | Visual workflows, broad model support, RAG, agent capabilities, model management, observability, cloud, Docker/self-host, and deployment recipes. |
| [LangChain README](https://github.com/langchain-ai/langchain/blob/6a97222c1ea2a4d9bc2de234995219e4f25220dc/README.md) | Interoperable components and extensive integrations; advanced orchestration is routed to LangGraph, while evals, observability, and deployment are routed to LangSmith. |
| [browser-use README](https://github.com/browser-use/browser-use/blob/2be09b6c5eb702a9287684b42b27e7042a1aba29/README.md) | Python browser agent/CLI, page navigation and form/data tasks, hosted cloud option, model choices, custom tools, MCP, and published browser benchmarks. |
| [DeerFlow README](https://github.com/bytedance/deer-flow/blob/cb698832deaf876d204045a68d79acedcbb1d26c/README.md) | Long-horizon research/coding/content agent with sandboxes, memory, tools, skills, subagents, messaging, database-backed state, web UI, and deployment guidance. |
| [MetaGPT README](https://github.com/FoundationAgents/MetaGPT/blob/11cdf466d042aece04fc6cfd13b28e1a70341b1f/README.md) | Role-based software-company multi-agent collaboration, tutorials, research workflows, and packaged Python usage. Its release and HEAD dates are older than most of this cohort. |
| [AutoGen README](https://github.com/microsoft/autogen/blob/027ecf0a379bcc1d09956d46d12d44a3ad9cee14/README.md) | AgentChat/Core/Extensions, multi-agent patterns, Studio, and a benchmark package, with an explicit maintenance-mode/migration boundary. |
| [CrewAI README](https://github.com/crewAIInc/crewAI/blob/3bb87532da925ed05aa17ddc541b0a9514c8f054/README.md) | Role-based Crews plus event-driven Flows, state/branching/routing, memory, knowledge, checkpointing, async execution, MCP/A2A, human input, and commercial control-plane options. |
| [LlamaIndex README](https://github.com/run-llama/llama_index/blob/7359b1acc74563f715d4463ace39fb4dc73d79af/README.md) | Core plus more than 300 integration packages, retrieval/data abstractions, Workflows, and a document-agent/OCR platform spanning parse, extract, index/RAG, and deployed agents. |
| [Agno README](https://github.com/agno-agi/agno/blob/1e03b4ef350f7c2706abc553a208e88b3f1e81e1/README.md) | Agent runtime/control plane, REST and MCP, database storage/traces, JWT RBAC, context providers, human approval, OpenTelemetry, audit logs, 100+ integrations, and deployment templates. |
| [LangGraph README](https://github.com/langchain-ai/langgraph/blob/31f90df3e6b0268fa77fd2d118a917d420b84a68/README.md) | Low-level long-running stateful graphs, durable execution, human interrupts/state editing, short/long-term memory, branching/subgraphs/streaming, and LangSmith observability/deployment. |
| [OpenAI Agents SDK README](https://github.com/openai/openai-agents-python/blob/1e8d506a32ea7b84f3a5a811e101378c0b1bc137/README.md) | Agents, handoffs, tools/MCP, guardrails, human-in-the-loop, tracing, realtime agents, and persistent sandbox agents for longer work. |
| [PageAgent README](https://github.com/alibaba/page-agent/blob/3510a769c8ac28914520f04e8cd4f3044e40dfe3/README.md) | One-line or npm in-page GUI agent, text/DOM interaction, mainstream/local model support, Chrome extension for multi-page work, and beta MCP server. |
| [PydanticAI README](https://github.com/pydantic/pydantic-ai/blob/2cb7a16128d8ce9a4f35b6a95c8cec6da325dbc7/README.md) | Typed dependencies/outputs, provider support, OpenTelemetry observability, evals, reusable capabilities, MCP/UI event streams, human tool approval, durable execution, and structured streaming. |
| [Microsoft Agent Framework README](https://github.com/microsoft/agent-framework/blob/61802723ff7a6decd53c7900a02adb5390c98f06/README.md) | Python/.NET agents, graph workflows, sequential/concurrent/handoff/group patterns, checkpointing, streaming, human-in-the-loop, time travel, OpenTelemetry, DevUI, and hosting samples. |

## FINITE proof ledger

The proof level matters more than feature count. “Executable local” means repository tests exercise
the behavior. It does not mean deployed, distributed, live-model, or externally audited.

| FINITE capability | Current proof level | Repository evidence | Boundary |
|---|---|---|---|
| Deadline/token/cost/context/concurrency/provider co-admission and refusal | Executable local | [scheduler tests](../tests/test_scheduler.py), [feasibility tests](../tests/test_feasibility.py) | Deterministic models and declared profiles, not live performance. |
| Integer reservation, settlement, refund, stress replay | Executable local | [resource-ledger tests](../tests/test_resource_ledger_10k.py), [provider-quota tests](../tests/test_provider_quota.py) | Single-process declared quota model, not authenticated provider telemetry. |
| Durable run ledger, bounded retries, restart/resume, usage accounting | Executable local | [executor tests](../tests/test_executor.py), [run-store tests](../tests/test_run_store.py) | SQLite and one active executor per run; no distributed lease. |
| Active adaptive control | Executable but narrow | [adaptive-runtime tests](../tests/test_adaptive_runtime.py) | Single-flight local workers and caller-supplied capacity/reset facts; not general distributed executor mutation. |
| Effect intent, exact-scope grant, fencing, idempotency, outbox, ambiguity | Executable local | [effect-kernel tests](../tests/test_effects.py) | Built-in commit adapter is simulation-only; no production target is called. |
| Durable content-addressed artifacts and lineage | Executable local | [artifact-store tests](../tests/test_artifact_store.py) | Digest integrity is not a producer signature or external source attestation. |
| Independent sealed whole-run verification | Executable local | [whole-run verifier tests](../tests/test_whole_run_verifier.py) | Verifies supplied sealed records; it does not establish truth of an external source. |
| Typed authenticated HTTP/SSE control plane | Executable local | [control API tests](../tests/test_control_api.py), [service tests](../tests/test_control_service.py) | Local ASGI/SQLite service; no public deployment, OIDC, tenant RBAC, or distributed ownership proof. |
| Adapter capability negotiation | Executable local | [adapter-capability tests](../tests/test_adapter_capabilities.py) | Prevents known semantic mismatch; cannot prove an adapter's declaration is truthful. |
| Framework-neutral wrapper plus LangGraph witness | Static wrapper plus one executable peer witness | [framework-conformance tests](../tests/test_framework_conformance.py), [LangGraph baseline tests](../tests/test_langgraph_baseline.py) | Actual pinned LangGraph 1.2.9 conformance only; no speed/reliability/cost superiority result. |
| watsonx/Granite task worker | Executor-connected seam with injected tests | [watsonx worker tests](../tests/test_watsonx_worker.py) | Live mode exists, but no genuine live receipt is checked in at this snapshot. |
| IBM Bob lifecycle/MCP | Twenty-two tested local tools and lifecycle seam | [MCP tests](../tests/test_mcp_tools.py), [Bob lifecycle tests](../tests/test_bob_lifecycle.py) | Tests cannot prove a genuine IBM Bob session occurred. |
| Page action governance | Static PageAgent-style contract only | [framework-conformance tests](../tests/test_framework_conformance.py) | Alibaba PageAgent and BeeAI are not imported or executed. No browser action is performed. |
| Semantic/numeric/bilingual validation | Executable bounded checks | [semantic-safety tests](../tests/test_semantic_safety.py) | No general entailment, translation-quality, or live-model semantic correctness claim. |
| Release evidence gating | Executable local manifest validator | [release-manifest tests](../tests/test_release_manifest.py) | Genuine Bob, live watsonx, GitHub, deployment, video, and submission evidence still require the entrant/external systems. |

### What is actually differentiated

The following are defensible implementation distinctions, not universal peer-absence claims:

1. Joint admission across time, token, cost, context, concurrency, provider capacity, optional
   value, and modeled success constraints before dispatch.
2. Componentwise reservation/settlement and deterministic refusal rather than treating budgets as
   prompt advice.
3. Exact-scope irreversible-effect grants bound to run, intent digest, action, resource, principal,
   validity interval, and a fenced state transition.
4. Independent, digest-bound evidence verification that joins events, resources, artifacts, claims,
   context obligations, and effects.
5. Fail-closed capability negotiation that makes cancellation/checkpoint/streaming/usage/effect
   semantics explicit before a worker is admitted.

Several peers already advertise durable execution, human approval, guardrails, tracing, evals,
checkpointing, or deployment. FINITE must not present those generic capabilities as unique.

## PageAgent versus FINITE

PageAgent and FINITE solve different layers. The [PageAgent limitations source](https://github.com/alibaba/page-agent/blob/3510a769c8ac28914520f04e8cd4f3044e40dfe3/packages/website/src/pages/docs/introduction/limitations/page.tsx),
[security-permissions source](https://github.com/alibaba/page-agent/blob/3510a769c8ac28914520f04e8cd4f3044e40dfe3/packages/website/src/pages/docs/advanced/security-permissions/page.tsx),
and [MCP package](https://github.com/alibaba/page-agent/blob/3510a769c8ac28914520f04e8cd4f3044e40dfe3/packages/mcp/README.md)
were checked at the same immutable commit.

| Dimension | Alibaba PageAgent | FINITE at this snapshot | Honest consequence |
|---|---|---|---|
| Primary job | Execute natural-language actions inside web pages | Admit and govern typed task graphs under finite envelopes | Complementary layers, not substitutes. |
| First value | One-line script/npm, docs, extension, demo | Python project and local commands/service | PageAgent has the stronger product on-ramp. |
| Action substrate | Real DOM/page actions | Fixture/model workers and proposal-only effect boundary | FINITE lacks an actual browser executor. |
| Browser topology | Current page plus extension-supported multi-page work | Framework-neutral DAG | Different execution topology. |
| Security surface | Page permissions, allow/block/confirmation controls | Typed effects, exact grants, fencing, idempotency, evidence | Different controls; no cross-project superiority is measured. |
| Finite-resource admission | Not a headline claim in audited PageAgent sources | Locally executable co-admission/refusal | A promising FINITE governance wedge, not proof PageAgent cannot add it. |
| Durable restart/evidence | Not PageAgent's primary README proposition | SQLite resume plus sealed evidence verification | FINITE has local proof; PageAgent has direct browser usefulness. |
| Integration status | Shipping PageAgent packages | PageAgent-style static contract only | **No Alibaba PageAgent integration can be claimed yet.** |
| Adoption/distribution | 27,492 stars, npm/extension/releases | No Git remote, public release, or users at audit time | FINITE has a major trust/distribution gap. |

The high-value demo remains a governed PageAgent, but this is a design target rather than completed
integration:

~~~mermaid
flowchart LR
    Bob["IBM Bob"] --> Contract["FINITE typed contract"]
    Contract --> Admit{"Admit, degrade, or refuse"}
    Admit -->|refuse| Evidence["Sealed decision evidence"]
    Admit -->|admit| Proposal["PageAgent action proposal"]
    Proposal --> Policy["Origin, element, quota, and effect policy"]
    Policy -->|safe action| Execute["PageAgent execution"]
    Policy -->|consequential write| Approval["Exact preview and scoped approval"]
    Approval --> Execute
    Execute --> Receipt["DOM, action, usage, and effect receipt"]
    Receipt --> Evidence
~~~

An acceptance gate for that adapter requires origin/page binding, tainted-DOM handling, secret
redaction, action allowlists, typed effect classification, exact approval, idempotency-aware retry,
provider settlement, cancellation/ambiguity representation, content-digested receipts, and a
sealed replay. None of those requirements is evidence that the adapter already exists.

## LangChain and LangGraph versus FINITE

LangChain's audited README routes advanced orchestration to LangGraph. The fair comparison is
LangChain for components/integrations, LangGraph for stateful graph execution, and FINITE for
finite-resource/evidence control.

| Dimension | LangChain | LangGraph | FINITE |
|---|---|---|---|
| Main abstraction | Models, tools, retrieval, interoperable components | Long-running stateful graphs | Typed contracts, envelopes, reservations, effects, evidence |
| Integration breadth | Core strength | LangChain ecosystem, can stand alone | Few production adapters |
| Graph semantics | Advanced control routed to LangGraph | Branching, subgraphs, memory, streaming | Validated DAG; no general loop/subgraph runtime |
| Durable execution | Ecosystem-dependent | First-class advertised capability | Executable SQLite restart/resume in bounded local scope |
| Human control | Components/ecosystem | Interrupt and state inspection/editing | Exact-scope effect approval plus control API |
| Observability/deployment | LangSmith ecosystem | LangSmith ecosystem | Local sealed evidence and ASGI service; no hosted product |
| Pre-dispatch co-admission | Not a headline README claim | Not a headline README claim | First-class local admission/refusal |
| Resource settlement | Application/provider layer in audited README | Application/provider layer in audited README | First-class integer ledger and replay |
| External-effect protocol | Tools/middleware/application | Interrupts/application logic | Intent/grant/fence/outbox/ambiguity model |
| Current proof | Mature public ecosystem | Mature public runtime | Alpha local kernel with bounded tests |

FINITE's pinned LangGraph comparator executes real StateGraph and SQLite checkpoint behavior on the
same deterministic StormShift fixture. It verifies a semantic/conformance slice only. There is no
tuned latency, cost, reliability, or quality result, and therefore no defensible “faster than
LangGraph” claim.

## Other requested peers versus FINITE

| Peer | Where the official repository is strong | FINITE relationship | Material FINITE gap |
|---|---|---|---|
| AutoGen / Microsoft Agent Framework | Established multi-agent APIs; successor adds Python/.NET graphs, checkpoints, streaming, HITL, OTel, DevUI, and hosting | FINITE can be a policy/evidence envelope around a workflow runtime | No MAF adapter, broad orchestration patterns, hosting, or enterprise ecosystem |
| CrewAI | Accessible role-based Crews and event-driven Flows with memory, checkpointing, MCP/A2A, and human input | FINITE can govern crew/flow dispatch and effects | No CrewAI adapter, templates, training ecosystem, or commercial operations surface |
| Dify / Langflow / n8n | Visual product, model/tool integrations, deployment, templates, observability, collaboration | FINITE could become a preflight/effect/evidence service called by these platforms | No visual authoring, connector catalog, multi-tenant product, or public deployment |
| LlamaIndex | Data/retrieval/document-agent abstractions and 300+ integrations | FINITE can constrain document-agent runs and bind source artifacts | No LlamaIndex adapter or equivalent ingestion/retrieval breadth |
| Agno | REST/MCP runtime, storage, RBAC, human approval, OTel, audit, integrations, deploy templates | Closest product/control-plane comparison in the cohort | FINITE lacks RBAC, integration breadth, deployment maturity, and public users |
| PydanticAI | Strong typing, evals, OTel, MCP/UI, approval, durable execution, streaming | A strong typed runtime over which FINITE must prove added quantitative control | No PydanticAI adapter; generic “typed/durable/HITL” claims are not differentiators |
| OpenAI Agents SDK | Lightweight agents/handoffs, guardrails, HITL, tracing, realtime and sandbox agents | FINITE can govern budgets/effects and verify a run independent of provider/runtime | No adapter or paired benchmark; much smaller integration/product surface |
| DeerFlow / browser-use | Direct long-horizon and browser task usefulness, sandboxes/memory/UI/cloud | Compelling action substrates for a governed FINITE demo | No actual integration, browser receipt, hostile-content test, or live comparative trial |
| MetaGPT | Recognizable role-based multi-agent research pattern and large historic adoption | Useful semantic-conformance target | No adapter; older release cadence means stars alone should not drive priority |

## Forty-five gaps to close

This is a prioritized backlog, not forty-five promises for the hackathon. P0 eligibility and genuine
evidence outrank adding another framework badge.

### P0: submission and genuine proof

1. **Public repository — open at audit.** Add a Git remote, push the reviewed branch, establish the
   default branch, and verify anonymous clone.
2. **Fresh-clone release gate — partial.** Run install, tests, lint, console build, artifact
   generation, and verifier from an empty checkout.
3. **Genuine IBM Bob provenance — external.** Capture a substantive planning/coding/testing session
   and the exact MCP lifecycle for one run.
4. **Live Granite receipt — external.** Run the bounded watsonx worker against Granite and retain a
   redacted, digest-bound receipt.
5. **SkillsBuild completion — external.** Capture the entrant's required completion evidence.
6. **Challenge compliance — open.** Re-read current official rules, deadline/time zone, submission
   fields, team/eligibility, licensing, and judging rubric.
7. **Fresh artifacts — open.** Regenerate the console and judge bundle; the current console artifact
   became stale when the MCP surface changed from 13 to 18 tools.
8. **Public deployment — open.** Deploy the authenticated REST/SSE service and test from a clean,
   unauthenticated browser plus an authorized client.
9. **Deterministic demo reset — partial.** Provide one command that clears only demo state, runs the
   sealed fallback, and never touches unrelated files.
10. **Three-minute video — external.** Record problem, live constraint shock, approval boundary,
    independent verification, Bob evidence, and claim limitations.
11. **Submission copy — open.** Prepare concise problem, innovation, architecture, IBM use, impact,
    repo, deployment, video, and verification instructions.
12. **Genuine-evidence manifest — partial.** Fill external evidence slots and make release readiness
    fail until every required item is public or appropriately attested.

### P1: prove the technical wedge

13. **Actual PageAgent adapter.** Replace the PageAgent-style static contract with an imported,
    version-pinned, executed adapter witness.
14. **Hostile-page defense.** Show DOM prompt injection cannot broaden tools, origin, effect scope,
    approval, or resource envelope.
15. **Browser receipts.** Bind page identity, selected element, action, before/after state, usage,
    ambiguity, and redaction into sealed evidence.
16. **Main-executor adaptive mutation.** Integrate the bounded active controller with the general
    executor without replaying settled tasks or crossing effect boundaries.
17. **Live quota telemetry.** Convert authenticated provider headers/receipts into durable control
    events while distinguishing observation from declared model.
18. **Distributed run ownership.** Add leases/fencing across processes; SQLite single-owner
    assumptions are not distributed coordination.
19. **Production effect adapter.** Implement a recoverable idempotent target with reconciliation;
    keep irreversible delivery gated.
20. **Fair live comparator.** Use identical models, prompts, tools, validators, cache, retry policy,
    faults, and hardware with raw paired traces.
21. **Additional executable framework witness.** Add one of Microsoft Agent Framework, PydanticAI,
    or CrewAI with explicit semantic-loss accounting.
22. **Richer graph topology.** Add or explicitly reject loops, dynamic fan-out, subgraphs, and
    conditional branches in the workflow IR.
23. **Live semantic validation.** Ground model output against real sources without promoting
    deterministic numeric checks into general entailment claims.
24. **Evidence authenticity.** Add producer identity/signatures or trusted attestations beyond
    content digests.
25. **Production identity.** Add OIDC/service identities, scoped authorization, tenant isolation,
    rotation, and audit—not one bearer token.
26. **OpenTelemetry export.** Correlate traces with run/event/artifact/effect digests.
27. **Failure corpus expansion.** Add process death, SQLite lock pressure, malformed receipts,
    partial streams, clock skew, and network ambiguity.
28. **SLO load evidence — partial local pass.** A digest-bound real-loopback proof now exercises
    two 32-way rounds, admission/control caps, proposal-only effect isolation, and call-free replay
    with disclosed local timings. Add independent hardware reproduction, concurrent SSE-client
    backpressure, and longer-duration soak evidence before making a capacity claim.
29. **Cross-process restart drill.** Demonstrate recovery from a killed service, not just object
    reconstruction in one test process.
30. **Independent reproduction.** Have a clean environment or second person execute the release
    verifier without planner objects or private state.

### P2: earn adoption

31. Publish a versioned Python package with locked compatibility metadata.
32. Publish a minimal container and documented state-volume/secret model.
33. Publish a searchable documentation site with architecture and trust boundaries.
34. Reduce first successful run to one short, reliable command.
35. Add small examples before adding more large fictional scenarios.
36. Add reusable workflow templates with expected resource/effect envelopes.
37. Build production adapters for at least two model providers and two tool/action surfaces.
38. Define a stable adapter/plugin SDK and compatibility test kit.
39. Publish a versioned compatibility and semantic-loss matrix.
40. Complete a threat model covering prompt injection, confused deputy, SSRF, tenant escape, secret
    leakage, approval replay, and evidence forgery.
41. Add maintainers, governance, triage policy, and a vulnerability-response process.
42. Automate tags, changelog, package/container publication, provenance, and rollback.
43. Publish migration guides from plain Python and at least one established runtime.
44. Add privacy-aware operational telemetry and support diagnostics.
45. Earn external proof: independent users, reproducible issue reports, integrations, and references.

## Claims the repository can and cannot make

### Defensible now

- FINITE implements and locally tests joint finite-resource admission/refusal, reservation and
  settlement, provider-quota replay, durable local execution, and effect-intent controls.
- FINITE executes a pinned LangGraph 1.2.9 semantic/conformance witness on a deterministic fixture.
- FINITE provides a local authenticated ASGI REST/SSE service and independent sealed-evidence
  verifiers.
- FINITE exposes an executor-connected watsonx worker and IBM Bob MCP lifecycle whose default/test
  paths make no live-provider claim.
- The project documents simulation, fixture, external-evidence, and deployment boundaries.

### Not defensible yet

- “FINITE is better, faster, cheaper, safer, or more reliable than LangChain, LangGraph, PageAgent,
  or any cohort project.”
- “FINITE integrates Alibaba PageAgent” or “FINITE executed a PageAgent browser action.”
- “IBM Bob built or ran this project” before genuine entrant-owned Bob evidence exists.
- “The checked-in evidence proves a live Granite call” before a real redacted receipt exists.
- “Production-ready,” “distributed,” “exactly once,” or “externally audited.”
- “Category leader” based on a private local worktree with no public users or release.

## Reproduction and provenance

Each census row can be rechecked using only public primary GitHub endpoints:

~~~text
GET https://api.github.com/repos/{owner}/{repo}
GET https://api.github.com/repos/{owner}/{repo}/commits/{default_branch}
GET https://api.github.com/repos/{owner}/{repo}/releases/latest
GET https://api.github.com/repos/{owner}/{repo}/license
~~~

Requests used GitHub REST API version 2022-11-28. The immutable commit links in the tables are the
feature/activity provenance; release and license links are direct official GitHub URLs. No
third-party star-history site, comparison blog, benchmark summary, or search-result snippet was
used as evidence.
