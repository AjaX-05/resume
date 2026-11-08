$procs = Get-NetTCPConnection -LocalPort 8002 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique
foreach ($p in $procs) {
    try {
        Stop-Process -Id $p -Force -ErrorAction Stop
        Write-Host "Killed PID $p"
    } catch {
        Write-Host "Could not kill PID $p : $_"
    }
}
Write-Host "Done"
