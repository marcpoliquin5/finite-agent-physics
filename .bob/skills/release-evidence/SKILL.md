---
name: release-evidence
description: Audit an Agent Physics release or hackathon submission for reproducibility, IBM Bob evidence, benchmark support, and public-repository hygiene
---

<Steps>
<Step>
Verify the public README, architecture, license, install steps, demo command, and challenge requirements.
</Step>
<Step>
Map every headline claim to raw traces, aggregation code, model IDs, seeds, sample sizes,
confidence intervals, and the exact commit used.
</Step>
<Step>
Verify that IBM Bob made material, honestly documented contributions and that Granite is a
real runtime path rather than branding. Do not infer or invent missing evidence.
</Step>
<Step>
Run lint, unit, integration, deterministic replay, security, and demo smoke checks.
</Step>
<Step>
Produce a blocking/non-blocking release checklist and leave the release blocked for any
unsupported claim, secret, missing attribution, or unreproducible result.
</Step>
</Steps>
