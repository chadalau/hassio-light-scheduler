<#
.SYNOPSIS
  Run the Light Scheduler integration tests against a real Home Assistant.

.DESCRIPTION
  Builds (or reuses) the test image and runs pytest inside it with the repo
  mounted read-only, so a test can never write into the working tree. Any
  argument is passed straight to pytest.

  ErrorActionPreference is deliberately left alone: docker writes its build
  progress to stderr, and "Stop" would turn a normal build into a terminating
  error. Success is decided by $LASTEXITCODE instead.

.EXAMPLE
  .\tests_integration\run.ps1
  .\tests_integration\run.ps1 -k ownership -v
#>
param([Parameter(ValueFromRemainingArguments = $true)] $PytestArgs)

$repo = Split-Path -Parent $PSScriptRoot

docker build -f "$repo\tests_integration\Dockerfile" -t light-scheduler-tests $repo
if ($LASTEXITCODE -ne 0) {
    Write-Host "docker build falhou" -ForegroundColor Red
    exit $LASTEXITCODE
}

$command = @("pytest", "tests_integration", "-q", "-o", "asyncio_mode=auto", "-p", "no:cacheprovider")
if ($PytestArgs) { $command += $PytestArgs }

docker run --rm -v "${repo}:/workspace:ro" -w /workspace light-scheduler-tests @command
exit $LASTEXITCODE
