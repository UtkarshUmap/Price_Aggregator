# ============================================================
#  scrape_all.ps1  —  refresh every product in products.txt
#  one at a time, store results in the scraper's SQLite DB.
#
#  USAGE:
#    1. Start the server in another window:
#         uvicorn app.main:app --host 0.0.0.0 --port 8000
#    2. Edit $key below to match API_KEYS in your .env
#    3. Run:  .\scrape_all.ps1
# ============================================================

# ---- EDIT THESE ----
$key      = "change-me-to-a-long-random-string"        # must match API_KEYS in .env
$pincode  = "413001"
$file     = ".\products.txt"
$delaySec = 8                      # gap between scrapes — don't go much lower
$baseUrl  = "http://localhost:8000"
$platforms = "blinkit,zepto,instamart"
# --------------------

if (-not (Test-Path $file)) {
    Write-Host "Can't find $file — put it next to this script." -ForegroundColor Red
    exit 1
}

$products = Get-Content $file | Where-Object { $_.Trim() -ne "" }
$total = $products.Count
$headers = @{ "X-API-Key" = $key }

Write-Host "Scraping $total products at ${delaySec}s spacing (~$([math]::Round($total*$delaySec/60)) min)`n"

$i = 0
$okCount = 0
foreach ($p in $products) {
    $i++
    $name = $p.Trim()
    $q = [uri]::EscapeDataString($name)
    $url = "$baseUrl/refresh?query=$q&pincode=$pincode&platforms=$platforms"

    try {
        $res = Invoke-RestMethod -Uri $url -Method Post -Headers $headers -TimeoutSec 120

        # count how many results came back per platform
        $counts = @()
        foreach ($plat in $res.results.PSObject.Properties.Name) {
            $n = @($res.results.$plat).Count
            $counts += "${plat}:$n"
        }
        $summary = $counts -join "  "
        Write-Host ("[{0}/{1}] {2,-30} {3}" -f $i, $total, $name, $summary) -ForegroundColor Green
        $okCount++
    }
    catch {
        Write-Host ("[{0}/{1}] {2,-30} FAILED: {3}" -f $i, $total, $name, $_.Exception.Message) -ForegroundColor Red
    }

    if ($i -lt $total) { Start-Sleep -Seconds $delaySec }
}

Write-Host "`nDone. $okCount/$total requests completed without error." -ForegroundColor Cyan
Write-Host "Check what landed in the DB with:  python check_db.py"