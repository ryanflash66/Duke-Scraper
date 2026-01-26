param(
    [string]$Place = "raleigh",
    [ValidateSet("csv", "geojson", "both")]
    [string]$Format = "both",
    [string]$OutputDir = "$PSScriptRoot\data",
    [ValidateSet("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "TRACE")]
    [string]$LogLevel = "DEBUG",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ExtraArgs
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
}

$arguments = @(
    "-m"
    "collector"
    "--place"
    $Place
    "--format"
    $Format
    "--output-dir"
    $OutputDir
    "--log-level"
    $LogLevel
)

if ($ExtraArgs) {
    $arguments += $ExtraArgs
}

Push-Location $PSScriptRoot
try {
    & python @arguments
}
finally {
    Pop-Location
}
