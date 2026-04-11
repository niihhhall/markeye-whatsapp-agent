# ============================================================
# Calendly Webhook Registration Script
# 1. Generate a Personal Access Token at:
#    https://calendly.com/integrations/api_webhooks
# 2. Paste it below and run this script in PowerShell
# ============================================================

$TOKEN = "PASTE_YOUR_TOKEN_HERE"   # <-- only thing to change

$WEBHOOK_URL = "https://YOUR_MARKEYE_URL.railway.app/calendly-webhook"
$HEADERS = @{
    "Authorization" = "Bearer $TOKEN"
    "Content-Type"  = "application/json"
}

# Step 1: Get your organisation URI
Write-Host "`n[1/3] Fetching your Calendly user info..." -ForegroundColor Cyan
$me = Invoke-RestMethod -Uri "https://api.calendly.com/users/me" -Headers $HEADERS
$orgUri = $me.resource.current_organization
Write-Host "      Org URI: $orgUri" -ForegroundColor Green

# Step 2: Register the webhook
Write-Host "`n[2/3] Registering webhook at $WEBHOOK_URL ..." -ForegroundColor Cyan
$body = @{
    url          = $WEBHOOK_URL
    events       = @("invitee.created")
    organization = $orgUri
    scope        = "organization"
} | ConvertTo-Json

$result = Invoke-RestMethod -Method Post `
    -Uri "https://api.calendly.com/webhook_subscriptions" `
    -Headers $HEADERS `
    -Body $body

Write-Host "`n[3/3] Done!" -ForegroundColor Green
Write-Host "      Webhook ID  : $($result.resource.uri)"
Write-Host "      Status      : $($result.resource.state)"
Write-Host "      Callback URL: $($result.resource.callback_url)"
