$ErrorActionPreference = "Continue"
$logPath = "C:\Users\goeln\Documents\Synapse\.cooler-test-watch.log"

while ($true) {
  $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  "`n===== [$timestamp] Running cooler suite =====" | Out-File -FilePath $logPath -Append
  Set-Location -Path "C:\Users\goeln\Documents\synapse\Synapse\backend"
  python -m pytest "test_amazon_cooler_suite.py" -q 2>&1 |
    Out-File -FilePath $logPath -Append
  Start-Sleep -Seconds 3
}
