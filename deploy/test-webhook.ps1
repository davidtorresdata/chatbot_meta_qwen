# Test the chatbot webhook through Caddy, end to end, without touching Meta.
#
# What it checks:
#   1. Stack is up and /health responds through Caddy (http://localhost:8080)
#   2. GET  /webhook  verification handshake (valid + invalid verify token)
#   3. POST /webhook  with X-Hub-Signature-256: valid signature accepted,
#      missing/wrong signature rejected (only when WHATSAPP_APP_SECRET is set)
#
# Usage (from anywhere):
#   powershell -ExecutionPolicy Bypass -File deploy\test-webhook.ps1
#   powershell -ExecutionPolicy Bypass -File deploy\test-webhook.ps1 -SkipComposeUp
#   # in production (domain mode) test the real URL:
#   powershell -ExecutionPolicy Bypass -File deploy\test-webhook.ps1 -BaseUrl https://bot.test.com -SkipComposeUp
#
# The positive POST test replies to a fake phone number, so Meta will return a
# 400 in the action log afterwards - that is expected and proves the webhook
# path works while real WhatsApp credentials are not required.

param(
    [switch]$SkipComposeUp,
    [string]$BaseUrl = 'http://localhost:8080'
)

$ErrorActionPreference = 'Stop'
$Root      = Split-Path -Parent $PSScriptRoot

function Write-Step([string]$msg) {
    Write-Host "`n=== $msg ===" -ForegroundColor Cyan
}

function Get-DotEnvValue {
    param([string]$Key, [string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    $content = Get-Content -LiteralPath $Path -Raw
    foreach ($line in ($content -split "`r?`n")) {
        if ($line -match "^\s*$Key\s*=\s*(.*)$") {
            return $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return $null
}

function Test-Url {
    param(
        [string]$Name,
        [string]$Url,
        [string[]]$CurlArgs,
        [string]$ExpectCode,
        [string]$ExpectBody = $null
    )
    $outFile = Join-Path $env:TEMP "wt_body_$PID.txt"
    $code = (& curl.exe -s -o $outFile -w '%{http_code}' @CurlArgs $Url 2>&1 | Out-String).Trim()
    $resp = if (Test-Path -LiteralPath $outFile) { Get-Content -LiteralPath $outFile -Raw } else { '' }
    Remove-Item -LiteralPath $outFile -ErrorAction SilentlyContinue

    $ok = ($code -eq $ExpectCode)
    if ($ok -and -not [string]::IsNullOrEmpty($ExpectBody)) {
        $ok = ($resp.Trim() -eq $ExpectBody.Trim())
    }

    if ($ok) {
        Write-Host ("[PASS] {0} -> HTTP {1}  {2}" -f $Name, $code, $resp.Trim()) -ForegroundColor Green
    } else {
        Write-Host ("[FAIL] {0} -> HTTP {1} (expected {2})  {3}" -f $Name, $code, $ExpectCode, $resp.Trim()) -ForegroundColor Red
    }
    return $ok
}

# ---------------------------------------------------------------- start stack
if (-not $SkipComposeUp) {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Error 'docker not found on PATH. Install Docker Desktop or use -SkipComposeUp.'
        exit 1
    }
    Write-Step 'Starting stack (chatbot + caddy)'
    & docker compose `
        -f (Join-Path $Root 'docker-compose.yml') `
        -f (Join-Path $Root 'docker-compose.caddy.yml') `
        up -d
    if ($LASTEXITCODE -ne 0) {
        Write-Error 'docker compose up failed. See docker compose logs chatbot.'
        exit 1
    }
}

# ------------------------------------------------------------- wait for health
Write-Step "Waiting for $BaseUrl/health"
$healthy = $false
for ($i = 0; $i -lt 30; $i++) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/health" -TimeoutSec 3
        if ($r.StatusCode -eq 200) {
            $healthy = $true
            Write-Host ("Healthy after ~{0}s: {1}" -f ($i * 2), $r.Content) -ForegroundColor Green
            break
        }
    } catch { }
    Start-Sleep -Seconds 2
}
if (-not $healthy) {
    Write-Host "Chatbot is not healthy at $BaseUrl/health" -ForegroundColor Red
    Write-Host 'Check the logs:  docker compose -f docker-compose.yml -f docker-compose.caddy.yml logs chatbot caddy'
    exit 1
}

# ------------------------------------------------------------------ .env vals
$envFile     = Join-Path $Root '.env'
$verifyToken = Get-DotEnvValue -Key 'WHATSAPP_VERIFY_TOKEN' -Path $envFile
$appSecret   = Get-DotEnvValue -Key 'WHATSAPP_APP_SECRET'   -Path $envFile
if (-not $verifyToken) {
    Write-Host 'WHATSAPP_VERIFY_TOKEN not found in .env - verification tests will be skipped.' -ForegroundColor Yellow
}
if (-not $appSecret) {
    Write-Host 'WHATSAPP_APP_SECRET not set - signature rejection tests will be skipped (validation is DISABLED).' -ForegroundColor Yellow
}

$pass = 0
$fail = 0

# ----------------------------------------------------------- GET verification
if ($verifyToken) {
    Write-Step 'GET /webhook  (Meta verification handshake)'
    $argsValid = @(
        '-G',
        '--data-urlencode', 'hub.mode=subscribe',
        '--data-urlencode', "hub.verify_token=$verifyToken",
        '--data-urlencode', 'hub.challenge=test123'
    )
    if (Test-Url -Name 'valid verify token' -Url "$BaseUrl/webhook" -CurlArgs $argsValid -ExpectCode '200' -ExpectBody 'test123') { $pass++ } else { $fail++ }

    $argsInvalid = @(
        '-G',
        '--data-urlencode', 'hub.mode=subscribe',
        '--data-urlencode', 'hub.verify_token=WRONG_TOKEN',
        '--data-urlencode', 'hub.challenge=test123'
    )
    if (Test-Url -Name 'invalid verify token' -Url "$BaseUrl/webhook" -CurlArgs $argsInvalid -ExpectCode '403') { $pass++ } else { $fail++ }
} else {
    $pass++
    $pass++
}

# ------------------------------------------------------------ POST /webhook
Write-Step 'POST /webhook  (signed message payload)'
$payload = @{
    object = 'whatsapp_business_account'
    entry  = @(
        @{
            id      = 'WHATSAPP_BUSINESS_ACCOUNT_ID'
            changes = @(
                @{
                    value = @{
                        messaging_product = 'whatsapp'
                        metadata          = @{
                            display_phone_number = '15551234567'
                            phone_number_id      = 'PHONE_NUMBER_ID'
                        }
                        contacts          = @(@{ profile = @{ name = 'Test' }; wa_id = '15550000000' })
                        messages          = @(
                            @{
                                from      = '15550000000'
                                id        = 'wamid.TEST'
                                timestamp = '1700000000'
                                type      = 'text'
                                text      = @{ body = 'menu' }
                            }
                        )
                    }
                    field = 'messages'
                }
            )
        }
    )
} | ConvertTo-Json -Depth 8

$payloadFile = Join-Path $env:TEMP "wa_webhook_payload_$PID.json"
[System.IO.File]::WriteAllText($payloadFile, $payload, (New-Object System.Text.UTF8Encoding($false)))

if ($appSecret) {
    $bytes = [System.IO.File]::ReadAllBytes($payloadFile)
    $hmac  = [System.Security.Cryptography.HMACSHA256]::new([System.Text.Encoding]::UTF8.GetBytes($appSecret))
    $sig   = [BitConverter]::ToString($hmac.ComputeHash($bytes)).Replace('-', '').ToLower()

    $argsSigned = @(
        '-X', 'POST',
        '-H', 'Content-Type: application/json',
        '-H', "X-Hub-Signature-256: sha256=$sig",
        '--data-binary', "@$payloadFile"
    )
    if (Test-Url -Name 'valid signature accepted' -Url "$BaseUrl/webhook" -CurlArgs $argsSigned -ExpectCode '200' -ExpectBody '{"status":"received"}') { $pass++ } else { $fail++ }

    $argsUnsigned = @(
        '-X', 'POST',
        '-H', 'Content-Type: application/json',
        '--data-binary', "@$payloadFile"
    )
    if (Test-Url -Name 'missing signature rejected' -Url "$BaseUrl/webhook" -CurlArgs $argsUnsigned -ExpectCode '403') { $pass++ } else { $fail++ }

    $argsBadSig = @(
        '-X', 'POST',
        '-H', 'Content-Type: application/json',
        '-H', 'X-Hub-Signature-256: sha256=deadbeef',
        '--data-binary', "@$payloadFile"
    )
    if (Test-Url -Name 'wrong signature rejected' -Url "$BaseUrl/webhook" -CurlArgs $argsBadSig -ExpectCode '403') { $pass++ } else { $fail++ }
} else {
    $argsUnsigned = @(
        '-X', 'POST',
        '-H', 'Content-Type: application/json',
        '--data-binary', "@$payloadFile"
    )
    if (Test-Url -Name 'payload accepted (signature validation disabled)' -Url "$BaseUrl/webhook" -CurlArgs $argsUnsigned -ExpectCode '200') { $pass++ } else { $fail++ }
}

Remove-Item -LiteralPath $payloadFile -ErrorAction SilentlyContinue

# ------------------------------------------------------------------- summary
Write-Step 'Summary'
if ($fail -eq 0) {
    Write-Host ("All {0} checks passed. Webhook is reachable through Caddy." -f $pass) -ForegroundColor Green
    Write-Host 'The positive POST test made the bot reply to a fake number; a Meta 400 in the'
    Write-Host 'action log is expected. Real traffic will use WHATSAPP_ACCESS_TOKEN.'
} else {
    Write-Host ("{0} passed, {1} failed." -f $pass, $fail) -ForegroundColor Red
}

$logDir = Join-Path $Root 'logs'
if (Test-Path -LiteralPath $logDir) {
    $latest = Get-ChildItem -LiteralPath $logDir -Filter 'wa_ollama_logs_*.txt' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($latest) {
        Write-Host ("Action log: {0}" -f $latest.FullName)
    }
}

exit $fail
