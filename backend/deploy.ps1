# Deploy Setup Readiness to AWS (SAM + optional Amplify notes)
# Prereq: IAM user needs CloudFormation, S3, IAM, Lambda, API Gateway, DynamoDB, Amplify.
# Easiest: attach AWS managed policy AdministratorAccess (or PowerUserAccess + IAMFullAccess) in console.

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
$Backend = $PSScriptRoot

$py312 = & py -3.12 -c "import sys; print(sys.executable)"
$pyDir = Split-Path $py312
$env:Path = "$pyDir;C:\Users\Yash\AppData\Local\Python\bin;" + $env:Path

$envFile = Join-Path $Root ".env"
if (-not (Test-Path $envFile)) {
  Write-Error "Missing $envFile — add GROQ_API_KEY=..."
}

$groq = $null
Get-Content $envFile | ForEach-Object {
  if ($_ -match '^\s*GROQ_API_KEY\s*=\s*(.+)\s*$') {
    $groq = $Matches[1].Trim().Trim('"').Trim("'")
  }
}
if (-not $groq) { Write-Error "GROQ_API_KEY not found in .env" }

Set-Location $Backend
Write-Host "sam build..."
sam build

Write-Host "sam deploy (Groq key from .env, not printed)..."
sam deploy `
  --no-confirm-changeset `
  --no-fail-on-empty-changeset `
  --parameter-overrides "BedrockDisabled=1 GroqApiKey=$groq GroqModel=openai/gpt-oss-20b LlmProvider=auto"

Write-Host ""
Write-Host "Stack outputs:"
aws cloudformation describe-stacks --stack-name setup-readiness --query "Stacks[0].Outputs" --output table

$api = aws cloudformation describe-stacks --stack-name setup-readiness --query "Stacks[0].Outputs[?OutputKey=='ApiBaseUrl'].OutputValue" --output text
Write-Host ""
Write-Host "ApiBaseUrl = $api"
Write-Host "Next:"
Write-Host "  1. Amplify: set VITE_API_URL=$api and VITE_AGENT_URL=http://127.0.0.1:9877"
Write-Host "  2. Laptop agent: python agent/setup_check.py serve --api $api --port 9877"
Write-Host "  3. curl $api/health"
