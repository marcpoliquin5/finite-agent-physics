# FINITE Physics Console

Evidence-first demonstration surface for FINITE, the constraint-native agent
orchestration kernel. The interface exposes the deterministic StormShift
simulation, feasibility witness, resource pressure controls, and effect-safety
boundary used in the hackathon demonstration. Its evidence rack exposes the
complete 450-record paired deterministic design without presenting the
development-reference policies as external-framework baselines.

All incident data shown by this site is fictional. The current console is a
deterministic simulation and does not execute or publish emergency actions.

## Development

Requires Node.js 22.13 or newer.

Regenerate the digest-bound artifact from the repository root before building:

```bash
python scripts/export_console_artifact.py
```

```bash
npm ci
npm run dev
npm run build
npm test
npm run lint
```

The app uses vinext and Cloudflare Workers. Hosting resource declarations live
in `.openai/hosting.json`.
