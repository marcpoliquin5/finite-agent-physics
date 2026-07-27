[CmdletBinding()]
param(
    [string]$OutputDirectory = "artifacts/ibm-offline-certification"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repository = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repository

$outputPath = if ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
    $OutputDirectory
} else {
    Join-Path $repository $OutputDirectory
}
New-Item -ItemType Directory -Force -Path $outputPath | Out-Null

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Label,
        [Parameter(Mandatory = $true)]
        [scriptblock]$Command
    )

    Write-Host "==> $Label"
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE."
    }
}

$bobCommand = Get-Command bob -ErrorAction SilentlyContinue
if ($null -eq $bobCommand) {
    throw "IBM Bob Shell is not installed or is not available on PATH."
}

$bobVersionPath = Join-Path $outputPath "bob-version.txt"
$bobMcpPath = Join-Path $outputPath "bob-mcp-list.txt"
$junitPath = Join-Path $outputPath "ibm-offline-pytest-junit.xml"
$contractPath = Join-Path $outputPath "watsonx-sdk-contract.json"
$summaryPath = Join-Path $outputPath "summary.json"

Write-Host "==> IBM Bob Shell version"
$previousErrorPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    $bobVersionOutput = @(& $bobCommand.Source --version 2>&1 | ForEach-Object { $_.ToString() })
    $bobVersionExitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previousErrorPreference
}
if ($bobVersionExitCode -ne 0) {
    throw "IBM Bob Shell version check failed with exit code $bobVersionExitCode."
}
$bobVersionOutput | Tee-Object -FilePath $bobVersionPath

Write-Host "==> IBM Bob MCP configuration and connection"
$ErrorActionPreference = "Continue"
try {
    $bobMcpOutput = @(& $bobCommand.Source mcp list 2>&1 | ForEach-Object { $_.ToString() })
    $bobMcpExitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previousErrorPreference
}
if ($bobMcpExitCode -ne 0) {
    throw "IBM Bob MCP list failed with exit code $bobMcpExitCode."
}
$bobMcpOutput | Tee-Object -FilePath $bobMcpPath
$bobMcpText = $bobMcpOutput -join [Environment]::NewLine
if ($bobMcpText -notmatch "finite-agent-physics:.*\(stdio\).*Connected") {
    throw "IBM Bob did not report the finite-agent-physics STDIO MCP server as Connected."
}

Invoke-CheckedCommand -Label "Python dependency consistency" -Command {
    python -m pip check
}

Invoke-CheckedCommand -Label "IBM adapter, durable worker, Bob lifecycle, and MCP tests" -Command {
    python -m pytest -q `
        tests/test_watsonx.py `
        tests/test_watsonx_worker.py `
        tests/test_bob_lifecycle.py `
        tests/test_mcp_stdio.py `
        contract_tests/test_watsonx_sdk_contract.py `
        "--junitxml=$junitPath" `
        --strict-markers `
        -o xfail_strict=true
}

Invoke-CheckedCommand -Label "Reject nonpassing or skipped IBM tests" -Command {
    python scripts/validate_junit.py $junitPath
}

Invoke-CheckedCommand -Label "Official watsonx SDK offline wire contract" -Command {
    python scripts/run_watsonx_sdk_contract.py --output $contractPath
}

$sdkVersion = python -c "import importlib.metadata as m; print(m.version('ibm-watsonx-ai'))"
if ($LASTEXITCODE -ne 0) {
    throw "Could not read the installed IBM watsonx SDK version."
}

$summary = [ordered]@{
    schema_version = "finite-ibm-offline-certification/v1"
    passed = $true
    measurement_kind = "offline-official-contracts"
    bob = [ordered]@{
        executable = $bobCommand.Source
        mcp_server = "finite-agent-physics"
        transport = "stdio"
        connected = $true
    }
    watsonx = [ordered]@{
        sdk_distribution = "ibm-watsonx-ai"
        sdk_version = $sdkVersion.Trim()
        external_network_calls = 0
        live_provider_calls = 0
    }
    evidence = [ordered]@{
        bob_version = [System.IO.Path]::GetFileName($bobVersionPath)
        bob_mcp_list = [System.IO.Path]::GetFileName($bobMcpPath)
        pytest_junit = [System.IO.Path]::GetFileName($junitPath)
        watsonx_sdk_contract = [System.IO.Path]::GetFileName($contractPath)
    }
    limitation = "This is Bob/MCP and official-SDK compatibility evidence, not a live IBM model receipt."
}
$summary | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $summaryPath -Encoding utf8

Write-Host ""
Write-Host "IBM offline certification PASSED."
Write-Host "Evidence: $outputPath"
Write-Host "No IBM account, credential, model, billing, or external provider call was used."
