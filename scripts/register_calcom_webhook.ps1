# ============================================================
# Cal.com Webhook Registration Script
# 1. Generate an API Key at:
#    https://app.cal.com/settings/developer/api-keys
# 2. Paste it below and run this script in PowerShell
# ============================================================

$API_KEY = "PASTE_YOUR_CALCOM_API_KEY_HERE"   # <-- only thing to change

# Adjust this URL to your deployed Markeye instance
$WEBHOOK_URL = "https://YOUR_MARKEYE_URL.railway.app/calcom-webhook"

$HEADERS = @{
    "Content-Type"  = "application/json"
}

# Cal.com API v1 uses query param for API key usually or Bearer token
$QUERY_URL = "https://api.cal.com/v1/webhooks?apiKey=$API_KEY"

Write-Host "`n[1/2] Registering Cal.com webhook at $WEBHOOK_URL ..." -ForegroundColor Cyan

$body = @{
    subscriberUrl = $WEBHOOK_URL
    eventTriggers = @("BOOKING_CREATED", "BOOKING_CANCELLED")
    active        = $true
    payloadTemplate = "{}" # Default JSON payload
} | ConvertTo-Json

try {
    $result = Invoke-RestMethod -Method Post `
        -Uri $QUERY_URL `
        -Headers $HEADERS `
        -Body $body

    Write-Host "`n[2/2] Done!" -ForegroundColor Green
    Write-Host "      Webhook ID   : $($result.webhook.id)"
    Write-Host "      Subscriber   : $($result.webhook.subscriberUrl)"
    Write-Host "      Events       : $($result.webhook.eventTriggers -join ', ')"
}
catch {
    Write-Host "`n[!] Error registering webhook:" -ForegroundColor Red
    $_.Exception.Message
    if ($_.ErrorDetails) { $_.ErrorDetails.Message }
}
