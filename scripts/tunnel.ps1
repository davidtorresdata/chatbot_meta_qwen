# Public HTTPS tunnel to the local Caddy instance (http://localhost:8080).
# Use when this machine is behind NAT/CGNAT and a direct HTTPS cert cannot be
# issued. Prints a public URL; point the Meta callback at "<url>/webhook".
#
# Requires cloudflared or ngrok:
#   winget install cloudflared    # or: winget install ngrok
param(
    [string]$Target = 'http://localhost:8080'
)

$cf = Get-Command cloudflared -ErrorAction SilentlyContinue
$ng = Get-Command ngrok -ErrorAction SilentlyContinue
$cfLocal = Join-Path $env:LOCALAPPDATA 'cloudflared\cloudflared.exe'
if (-not $cf -and (Test-Path $cfLocal)) {
    $cf = [pscustomobject]@{ Source = $cfLocal }
}

if (-not $cf -and -not $ng) {
    Write-Error "Neither cloudflared nor ngrok found. Install one:`n  winget install cloudflared`n  or: winget install ngrok"
    exit 1
}

if ($cf) {
    Write-Host "Using cloudflared quick tunnel -> $Target" -ForegroundColor Cyan
    Write-Host "Copy the https://...trycloudflare.com URL for the Meta callback (append /webhook)." -ForegroundColor Yellow
    & $cf.Source tunnel --url $Target
} else {
    Write-Host "Using ngrok -> $Target" -ForegroundColor Cyan
    Write-Host "Copy the https://...ngrok-free.app URL for the Meta callback (append /webhook)." -ForegroundColor Yellow
    & $ng.Source http 8080
}
