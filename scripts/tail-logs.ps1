# Capture container logs per service into logs/wa_chatbot_<service>_<timestamp>.log
#
# One-shot (dump all current logs per service):
#   powershell -ExecutionPolicy Bypass -File scripts\tail-logs.ps1
#
# Live streaming (Ctrl+C to stop):
#   powershell -ExecutionPolicy Bypass -File scripts\tail-logs.ps1 -Follow
#
# Pick specific services (default: chatbot,ollama,caddy,cloudflared):
#   powershell -ExecutionPolicy Bypass -File scripts\tail-logs.ps1 -Services caddy
#
# Output files land in logs/ (same folder as the chatbot's own action logs):
#   logs/wa_chatbot_caddy_20260804_103000.log
#   logs/wa_chatbot_ollama_20260804_103000.log
#   logs/wa_chatbot_chatbot_20260804_103000.log
#   logs/wa_chatbot_cloudflared_20260804_103000.log
#
# Note: docker compose logs only shows what each container wrote to
# stdout/stderr (i.e. everything after "docker compose up").

param(
    [switch]$Follow,
    [string]$Services = 'chatbot,ollama,caddy,cloudflared'
)

$ErrorActionPreference = 'Stop'
$Root   = Split-Path -Parent $PSScriptRoot   # scripts\ -> repo root
$LogDir = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$stamp       = Get-Date -Format 'yyyyMMdd_HHmmss'
$composeArgs = @(
    '-f', (Join-Path $Root 'docker-compose.yml'),
    '-f', (Join-Path $Root 'docker-compose.caddy.yml'),
    '-f', (Join-Path $Root 'docker-compose.tunnel.yml')
)

$jobs = @()
foreach ($svc in ($Services -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })) {
    $outFile = Join-Path $LogDir ("wa_chatbot_{0}_{1}.log" -f $svc, $stamp)
    $svcArgs = @('compose') + $composeArgs + @('logs', '--no-color', '-t')
    if ($Follow) { $svcArgs += '--follow' }
    $svcArgs += $svc

    Write-Host ("[{0}] writing to {1}" -f $svc, $outFile) -ForegroundColor Cyan
    $job = Start-Job -ArgumentList $svcArgs, $outFile -ScriptBlock {
        param($svcArgs, $outFile)
        & docker @svcArgs 2>&1 | Out-File -FilePath $outFile -Encoding utf8 -Append
    }
    $jobs += , $job
}

if ($Follow) {
    Write-Host 'Streaming live logs. Press Ctrl+C to stop.' -ForegroundColor Yellow
    try {
        $jobs | Wait-Job | Out-Null
    } finally {
        $jobs | Stop-Job -ErrorAction SilentlyContinue
        $jobs | Remove-Job -Force
    }
} else {
    $jobs | Wait-Job | Out-Null
    $jobs | Remove-Job -Force
    Write-Host ("Done. Logs written to {0}" -f $LogDir) -ForegroundColor Green
}
