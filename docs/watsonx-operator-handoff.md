# Secure watsonx + Bob operator handoff

This is the shortest supported path from an IBM Cloud account to one bounded, receipt-producing
Granite request through IBM Bob and FINITE.

The launcher keeps the API key in the current PowerShell process only. It does not write a `.env`
file, persist the key to the user profile, pass the key on a command line, or include it in Bob's
prompt. The original process environment is restored when Bob exits.

## What must already exist

- IBM Bob Shell is installed and authenticated.
- `ibm-watsonx-ai` is installed (`python -m pip install -e ".[watsonx]"`).
- The repository's `.bob/mcp.json` enables the `finite-agent-physics` STDIO server.
- The IBM Cloud account has access to a watsonx.ai project and an IBM Granite model.

## The four values

1. **Regional API URL.** Use the public API endpoint for the region containing the project:
   - Dallas: `https://us-south.ml.cloud.ibm.com`
   - Frankfurt: `https://eu-de.ml.cloud.ibm.com`
   - London: `https://eu-gb.ml.cloud.ibm.com`
   - Tokyo: `https://jp-tok.ml.cloud.ibm.com`
   - Sydney: `https://au-syd.ml.cloud.ibm.com`
   - Toronto: `https://ca-tor.ml.cloud.ibm.com`
2. **Project ID.** In the watsonx project, open **Manage**, then **General**, and copy
   **Project ID** from **Details**.
3. **Granite model ID.** The launcher defaults to the current multitenant IBM model
   `ibm/granite-4-h-small`. Availability varies by region and account.
4. **IBM Cloud API key.** In IBM Cloud, open **Manage > Access (IAM) > API keys**, create a
   dedicated key, and copy or download it once. Never paste it into chat, GitHub, a Bob prompt, or
   a committed file.

Official references:

- [IBM Cloud CLI installation](https://cloud.ibm.com/docs/cli?topic=cli-install-ibmcloud-cli)
- [Programmatic watsonx credentials](https://www.ibm.com/docs/en/watsonx/saas?topic=resources-credentials-programmatic-access)
- [Finding the project ID](https://www.ibm.com/docs/en/watsonx/saas?topic=resources-finding-project-id)
- [Supported watsonx foundation models](https://www.ibm.com/docs/en/watsonx/saas?topic=solutions-supported-models)
- [watsonx API regional endpoints](https://cloud.ibm.com/apidocs/watsonx-ai)

## One command

From the repository root in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_watsonx_bob.ps1
```

The launcher asks for the regional URL, project ID, Granite model ID, and a hidden API key. It then:

1. validates the SDK and required configuration;
2. runs FINITE's call-free Granite feasibility preflight;
3. displays the admitted resource envelope;
4. requires the exact confirmation `LIVE`;
5. launches Bob in default approval mode;
6. asks Bob to execute one bounded `granite-probe` call;
7. asks Bob to retrieve status, a public explanation, and whole-run verification;
8. restores the original environment when Bob exits.

To stop before any provider request:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_watsonx_bob.ps1 -PreflightOnly
```

## Honest completion rule

The run counts as live Granite evidence only if the persisted public receipt says
`measurement_kind="live-watsonx"`, contains provider-reported input and output token usage, passes
the output validator and whole-run verifier, and is tied to the reviewed run and commit. An
authentication failure, unavailable model, missing usage field, test double, or preflight result is
not live Granite evidence.
