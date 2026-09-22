# Setup PowerShell script for MexTradeBot-Coordinador Scheduled Task on Windows VPS
#
# Configura una tarea programada que ejecuta run_coordinador.py cada 15 minutos desatendida.

$TaskName = "MexTradeBot-Coordinador"
$WorkingDir = "C:\MTB\tradebotbuilder"
$VenvPython = "C:\MTB\tradebotbuilder\.venv\Scripts\python.exe"
$ScriptPath = "$WorkingDir\run_coordinador.py"

Write-Host "Configurando Tarea Programada: $TaskName..." -ForegroundColor Cyan

# Eliminar tarea previa si existe
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

$Action = New-ScheduledTaskAction -Execute $VenvPython -Argument $ScriptPath -WorkingDirectory $WorkingDir
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15)
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RunOnlyIfNetworkAvailable

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "Ejecutor del Agente Coordinador Master Trader cada 15m"

Write-Host "✅ Tarea programada $TaskName creada con éxito." -ForegroundColor Green
