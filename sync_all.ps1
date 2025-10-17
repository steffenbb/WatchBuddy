$listIds = @(9, 10, 11, 12, 13, 14, 15, 16, 17)

Write-Host "Syncing lists..."

foreach ($listId in $listIds) {
    Write-Host "=== Syncing list $listId ==="
    
    $body = @{
        user_id = 1
        force_full = $true
    } | ConvertTo-Json
    
    try {
        $response = Invoke-RestMethod -Uri "http://localhost:8000/api/smartlists/sync/$listId" -Method Post -Body $body -ContentType "application/json"
        Write-Host "OK List $listId sync initiated" -ForegroundColor Green
    }
    catch {
        Write-Host "ERROR List $listId failed" -ForegroundColor Red
    }
    
    Start-Sleep -Seconds 2
}

Write-Host "=== All sync requests sent ==="
Write-Host "Waiting for syncs to complete..."
Start-Sleep -Seconds 90
Write-Host "Done waiting." -ForegroundColor Green
