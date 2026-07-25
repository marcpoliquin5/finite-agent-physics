[CmdletBinding()]
param(
    [ValidateRange(1, 1024)]
    [int]$MaxNewTokens = 128,

    [string]$Instruction = (
        "Return exactly one sentence confirming that this is a bounded fictional " +
        "StormShift orchestration probe. Do not provide emergency advice, real-world " +
        "actions, or external side effects."
    ),

    [switch]$PreflightOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$watsonxNames = @(
    "WATSONX_URL",
    "WATSONX_API_KEY",
    "WATSONX_PROJECT_ID",
    "WATSONX_MODEL_ID"
)
$originalValues = @{}
foreach ($name in $watsonxNames) {
    $originalValues[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}

function Read-ValueWithDefault {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Label,

        [Parameter(Mandatory = $true)]
        [string]$Default
    )

    $value = Read-Host "$Label [$Default]"
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $Default
    }
    return $value.Trim()
}

function Read-RequiredValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    $value = Read-Host $Label
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "$Label is required."
    }
    return $value.Trim()
}

$plainApiKey = $null

try {
    Push-Location $repositoryRoot

    $python = Get-Command python -ErrorAction Stop
    $bob = Get-Command bob -ErrorAction Stop
    python -c "import ibm_watsonx_ai" 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw 'The watsonx SDK is missing. Run: python -m pip install -e ".[watsonx]"'
    }

    $watsonxUrl = $env:WATSONX_URL
    if ([string]::IsNullOrWhiteSpace($watsonxUrl)) {
        $watsonxUrl = Read-ValueWithDefault `
            -Label "watsonx regional API URL" `
            -Default "https://us-south.ml.cloud.ibm.com"
    }

    $projectId = $env:WATSONX_PROJECT_ID
    if ([string]::IsNullOrWhiteSpace($projectId)) {
        $projectId = Read-RequiredValue -Label "watsonx project ID"
    }

    $modelId = $env:WATSONX_MODEL_ID
    if ([string]::IsNullOrWhiteSpace($modelId)) {
        $modelId = Read-ValueWithDefault `
            -Label "Granite API model ID" `
            -Default "ibm/granite-4-h-small"
    }

    $plainApiKey = $env:WATSONX_API_KEY
    if ([string]::IsNullOrWhiteSpace($plainApiKey)) {
        $secureApiKey = Read-Host "IBM Cloud API key (hidden)" -AsSecureString
        $apiKeyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureApiKey)
        try {
            $plainApiKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($apiKeyPointer)
        }
        finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($apiKeyPointer)
        }
    }
    if ([string]::IsNullOrWhiteSpace($plainApiKey)) {
        throw "IBM Cloud API key is required."
    }

    $parsedUrl = $null
    if (
        -not [Uri]::TryCreate($watsonxUrl, [UriKind]::Absolute, [ref]$parsedUrl) -or
        $parsedUrl.Scheme -ne "https"
    ) {
        throw "watsonx regional API URL must be an absolute HTTPS URL."
    }
    $allowedWatsonxHosts = @(
        "us-south.ml.cloud.ibm.com",
        "eu-de.ml.cloud.ibm.com",
        "eu-gb.ml.cloud.ibm.com",
        "jp-tok.ml.cloud.ibm.com",
        "au-syd.ml.cloud.ibm.com",
        "ca-tor.ml.cloud.ibm.com",
        "ap-south-1.aws.wxai.ibm.com"
    )
    if ($parsedUrl.Host -notin $allowedWatsonxHosts) {
        throw (
            "watsonx regional API URL must use a documented IBM public endpoint. " +
            "Received host: $($parsedUrl.Host)"
        )
    }
    if ($projectId -notmatch "^[A-Za-z0-9-]{36}$") {
        throw "watsonx project ID must be a 36-character IBM project identifier."
    }
    if (-not $modelId.StartsWith("ibm/granite-", [StringComparison]::Ordinal)) {
        throw "Granite API model ID must start with 'ibm/granite-'."
    }

    $env:WATSONX_URL = $watsonxUrl.Trim()
    $env:WATSONX_API_KEY = $plainApiKey.Trim()
    $env:WATSONX_PROJECT_ID = $projectId.Trim()
    $env:WATSONX_MODEL_ID = $modelId.Trim()

    $preflightProgram = @"
import json
from agent_physics.bob_lifecycle import default_bob_run_service
result = default_bob_run_service().granite_preflight(max_new_tokens=$MaxNewTokens)
print(json.dumps(result, indent=2, sort_keys=True))
"@

    Write-Host ""
    Write-Host "Running call-free FINITE Granite preflight..."
    $preflightOutput = & $python.Source -c $preflightProgram
    if ($LASTEXITCODE -ne 0) {
        throw "FINITE Granite preflight failed."
    }
    $preflightOutput | Write-Host

    if ($PreflightOnly) {
        Write-Host ""
        Write-Host "Preflight passed. No watsonx generation request was made."
        return
    }

    Write-Host ""
    Write-Host "The next step authorizes one bounded Granite generation request."
    Write-Host "Maximum generated tokens: $MaxNewTokens"
    $authorization = Read-Host "Type LIVE to authorize it, or press Enter to stop"
    if ($authorization -cne "LIVE") {
        Write-Host "Stopped after preflight. No watsonx generation request was made."
        return
    }

    $runId = "watsonx-live-" + [DateTime]::UtcNow.ToString("yyyyMMdd-HHmmss")
    $bobSessionReference = "entrant-authorized:$runId"
    $bobPrompt = @"
Use only the MCP server named finite-agent-physics for this validation.
Never inspect, print, echo, persist, or expose WATSONX_API_KEY or any environment secret.

1. Call finite_granite_preflight with max_new_tokens=$MaxNewTokens.
2. If and only if preflight passes, call finite_run with:
   run_id="$runId"
   mode="granite-probe"
   instruction="$Instruction"
   max_new_tokens=$MaxNewTokens
   bob_session_ref="$bobSessionReference"
3. Call finite_status for "$runId".
4. Call finite_explain_run for "$runId" with include_payloads=false.
5. Call finite_verify_run for "$runId".
6. Return only the public redacted receipt, verification result, run ID, and any honest failure.

Stop immediately if a gate fails. Do not retry a provider request and do not invoke external effects.
"@

    Write-Host ""
    Write-Host "Launching IBM Bob with the bounded FINITE Granite runbook..."
    & $bob.Source `
        --trust `
        --approval-mode default `
        --allowed-mcp-server-names finite-agent-physics `
        --prompt-interactive $bobPrompt
    if ($LASTEXITCODE -ne 0) {
        throw "IBM Bob exited with code $LASTEXITCODE."
    }
}
finally {
    $plainApiKey = $null
    foreach ($name in $watsonxNames) {
        [Environment]::SetEnvironmentVariable($name, $originalValues[$name], "Process")
    }
    if ((Get-Location).Path -eq $repositoryRoot) {
        Pop-Location
    }
}
