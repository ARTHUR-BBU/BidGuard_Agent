$repoRoot = Split-Path -Parent $PSScriptRoot

Start-Process -FilePath "uv" `
    -ArgumentList @("run", "uvicorn", "app.main:app", "--reload", "--port", "8000") `
    -WorkingDirectory (Join-Path $repoRoot "backend") `
    -WindowStyle Hidden

Start-Process -FilePath "npm.cmd" `
    -ArgumentList @("run", "dev", "--", "--port", "5173") `
    -WorkingDirectory (Join-Path $repoRoot "frontend") `
    -WindowStyle Hidden

Write-Host "Backend health check: http://localhost:8000/api/health"
Write-Host "Frontend: http://localhost:5173"
